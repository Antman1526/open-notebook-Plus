"""Post-startup registration of locally-available models against the upstream API.

Called by __main__.py after Supervisor.start_all() returns. Idempotent:
checks /api/credentials and /api/models first, only creates what's missing.

Endpoint summary (found in api/routers/credentials.py and api/routers/models.py):

  POST /credentials
    body: {name, provider, modalities: [...], base_url?}
    → 201 CredentialResponse {id, name, provider, ...}

  GET /credentials
    → list[CredentialResponse]

  GET /models
    → list[ModelResponse]

  POST /models
    body: {name, provider, type: "language"|"embedding"|..., credential?}
    → ModelResponse

  POST /models/auto-assign
    (no body) — assigns first available model of each type to default slots
    → AutoAssignResult {assigned, skipped, missing}

Public API (all preserved from the old flat module):
  auto_register(...)
  register_voice_models(...)
  register_default_episode_profile(...)
  _list_ollama_models(...)   — re-exported for test patching
  _list_local_ggufs(...)     — re-exported for test patching
"""

from __future__ import annotations

import logging

import httpx

# Re-export sub-module public symbols so existing imports/patches keep working.
from desktop.auto_register._http import (  # noqa: F401
    _ensure_credential,
    _ensure_model,
    _is_embedding_gguf,
    _list_local_ggufs,
    _list_ollama_models,
)
from desktop.auto_register.assigner import SLOTS, assign_all
from desktop.auto_register.capability import ModelDescriptor, score_model
from desktop.auto_register.episode_profile import (
    register_default_episode_profile,  # noqa: F401
)
from desktop.auto_register.llamacpp import register_llamacpp_models
from desktop.auto_register.lmstudio import register_lmstudio_models
from desktop.auto_register.mlx import register_mlx_models
from desktop.auto_register.ollama import register_ollama_models
from desktop.auto_register.osaurus import register_osaurus_models
from desktop.auto_register.speaker_profile import (
    register_default_speaker_profile,  # noqa: F401
)
from desktop.auto_register.voice import register_voice_models  # noqa: F401
from desktop.config import Config

log = logging.getLogger(__name__)

# Upstream's /api/models type → our internal slot kind. Used to filter what's
# eligible for each slot when reading back the registered model list.
_TYPE_TO_KIND = {
    "language": ("chat", "reasoning"),
    "embedding": ("embed",),
    "speech_to_text": ("stt",),
    "text_to_speech": ("tts",),
}

# Our slot name → upstream DefaultModels field name.
_SLOT_TO_DEFAULT_FIELD = {
    "chat": "default_chat_model",
    "tools": "default_tools_model",
    "transformation": "default_transformation_model",
    "large_context": "large_context_model",
    "embedding": "default_embedding_model",
    "tts": "default_text_to_speech_model",
    "stt": "default_speech_to_text_model",
    "reasoning": "default_reasoning_model",  # ONP v0.5 — 8th slot
}


def _assign_capability_aware_defaults(client: httpx.Client) -> None:
    """Score every registered model, pick per slot, PUT to /api/models/defaults.

    NEVER overwrites a slot the user manually set (we read the current defaults
    first and skip any slot that already has a value). Logs the reasoning for
    each pick so users can `cat ~/.deeper-notebook/logs/launcher.log` and
    audit assignments.
    """
    # 1. Read current defaults so manual user overrides are preserved.
    try:
        r = client.get("/api/models/defaults")
        r.raise_for_status()
        current = r.json() or {}
    except Exception as exc:
        log.warning(
            "could not read /api/models/defaults — skipping assignment: %s", exc
        )
        return

    # 2. Read all registered models, score them.
    try:
        r = client.get("/api/models")
        r.raise_for_status()
        all_models = r.json()
    except Exception as exc:
        log.warning("could not read /api/models — skipping assignment: %s", exc)
        return

    # Build a pool of scored descriptors, tagging each with its upstream `id` so
    # we can PUT the id back to /defaults.
    # v0.7.48 — ModelDescriptor imported at module top now (was only used
    # as a string-quoted forward reference before).
    pool: list[tuple[dict, ModelDescriptor]] = []
    for m in all_models:
        name = m.get("name", "")
        if not name:
            continue
        desc = score_model(name)
        pool.append((m, desc))

    # 3. Run the assigner using just descriptors.
    picks = assign_all([d for _, d in pool])

    # 4. Look up upstream model `id` for each pick + build the PUT body.
    desc_to_model = {d.name: m for m, d in pool}
    body: dict[str, str] = {}
    for slot, pick in picks.items():
        upstream_field = _SLOT_TO_DEFAULT_FIELD[slot]
        if current.get(upstream_field):
            log.info(
                "assign %-15s SKIP (manual override: %s)", slot, current[upstream_field]
            )
            continue
        if pick.model is None:
            log.info("assign %-15s MISS (%s)", slot, pick.reason)
            continue
        m = desc_to_model.get(pick.model.name)
        if not m:
            continue
        body[upstream_field] = m.get("id") or m.get("name")
        log.info(
            "assign %-15s → %-40s score=%.2f  %s",
            slot,
            pick.model.name,
            pick.score,
            pick.reason,
        )

    if not body:
        log.info("no slots to assign (all manually set or no eligible models)")
        return

    # 5. PUT to upstream. Tolerate failures — assignment is best-effort.
    try:
        r = client.put("/api/models/defaults", json=body)
        if r.status_code >= 300:
            log.warning(
                "PUT /api/models/defaults returned %s: %s", r.status_code, r.text[:200]
            )
    except Exception as exc:
        log.warning("PUT /api/models/defaults failed (non-fatal): %s", exc)


def _prune_orphan_legacy_credentials(
    client: httpx.Client,
    existing_creds: list[dict],
) -> None:
    """v0.7.208 — Delete orphan credentials left over from the
    v0.6.x → v0.7.194 rename pair.

    Pre-v0.6.x installs created `Local GGUF (llama.cpp)`. v0.6.x
    silently renamed the canonical-form to `llama.cpp (local)`,
    but pre-existing installs never got migrated. v0.7.193's
    auto-register tried to create the new name → ended up with
    BOTH credentials, the old one (with all the model links) +
    the new one (empty orphan pointing at whatever dynamic port
    that launch happened to allocate). v0.7.194 stopped the
    duplicate creation going forward, but didn't clean up the
    orphan rows left in the user's DB.

    This helper detects + DELETES the orphan strictly safely:
      - Name matches the new-canonical-form `llama.cpp (local)`
        (lower-case match for resilience).
      - At least one OTHER credential with the legacy name
        `Local GGUF (llama.cpp)` ALSO exists (otherwise the
        modern-name row IS the canonical row, not an orphan).
      - The credential has 0 models linked (a row with models
        attached is NEVER deleted — losing model links would be
        catastrophic).

    All three constraints together make a false-positive
    functionally impossible. The base_url is intentionally NOT
    checked — dynamic ports change every launch, so "unreachable"
    is too noisy a signal here.
    """
    legacy_name = "local gguf (llama.cpp)"
    modern_name = "llama.cpp (local)"

    legacy_exists = any(
        (c.get("name") or "").lower() == legacy_name for c in existing_creds
    )
    if not legacy_exists:
        # No legacy row → the modern-name row IS the canonical one;
        # don't touch it.
        return

    for cred in existing_creds:
        if (cred.get("name") or "").lower() != modern_name:
            continue
        cred_id = cred.get("id")
        if not cred_id:
            continue
        # Count linked models. The /api/models endpoint returns
        # each model's credential field; filter to this cred id.
        try:
            r = client.get("/api/models")
            r.raise_for_status()
            linked = [m for m in r.json() if (m.get("credential") or "") == cred_id]
        except Exception as exc:
            log.warning(
                "v0.7.208 orphan-prune: could not fetch /api/models "
                "(skipping cred=%s): %s",
                cred_id,
                exc,
            )
            continue
        if linked:
            log.info(
                "v0.7.208 orphan-prune: cred=%s name=%r has %d "
                "linked models; KEEPING (not an orphan).",
                cred_id,
                modern_name,
                len(linked),
            )
            continue
        # Three constraints met — safe to delete.
        try:
            d = client.delete(f"/api/credentials/{cred_id}")
            if d.status_code in (200, 204):
                log.info(
                    "v0.7.208 orphan-prune: deleted orphan cred=%s "
                    "name=%r (0 linked models, legacy "
                    "`%s` row also present)",
                    cred_id,
                    modern_name,
                    legacy_name,
                )
            else:
                log.warning(
                    "v0.7.208 orphan-prune: DELETE for cred=%s "
                    "returned HTTP %d (leaving in place)",
                    cred_id,
                    d.status_code,
                )
        except Exception as exc:
            log.warning(
                "v0.7.208 orphan-prune: DELETE for cred=%s failed: %s",
                cred_id,
                exc,
            )


def auto_register(
    api_base_url: str,
    cfg: Config,
    llamacpp_port: int | None = None,
    *,
    mlx_base_url: str | None = None,
    mlx_model_ref: str | None = None,
    whisper_port: int | None = None,
    piper_port: int | None = None,
    embed_port: int | None = None,
    memory_port: int | None = None,
) -> None:
    """Register Ollama models + local GGUF models against the running API.

    api_base_url: e.g. http://127.0.0.1:55890 — the upstream FastAPI URL.
    cfg: loaded config (gives model_dir, provider preference).
    llamacpp_port: if set, a llama-cpp-python server is running on this port
                   and we should register an openai_compatible credential
                   pointing at http://127.0.0.1:<port>/v1.
    whisper_port: if set, register a Whisper STT credential on this port.
    piper_port: if set, register a Piper TTS credential on this port.
    embed_port: if set, register a local embedding credential on this port.
    memory_port: if set, register a Memory retriever credential on this port.

    Idempotent: safe to call on every startup.  Logs failures; does NOT raise
    (registration failures must not crash the launcher).
    """
    try:
        with httpx.Client(base_url=api_base_url, timeout=15.0) as client:
            _do_register(
                client,
                cfg,
                llamacpp_port,
                mlx_base_url=mlx_base_url,
                mlx_model_ref=mlx_model_ref,
                whisper_port=whisper_port,
                piper_port=piper_port,
                embed_port=embed_port,
                memory_port=memory_port,
            )
    except Exception as exc:
        log.warning("auto_register failed (non-fatal): %s", exc)


def _do_register(
    client: httpx.Client,
    cfg: Config,
    llamacpp_port: int | None,
    *,
    mlx_base_url: str | None = None,
    mlx_model_ref: str | None = None,
    whisper_port: int | None = None,
    piper_port: int | None = None,
    embed_port: int | None = None,
    memory_port: int | None = None,
) -> None:
    """Main registration logic, runs inside an httpx.Client context."""
    # --- 1. Fetch existing credentials and models --------------------------
    existing_cred_names: set[str] = set()
    existing_creds_full: list[dict] = []
    try:
        r = client.get("/api/credentials")
        r.raise_for_status()
        for cred in r.json():
            existing_cred_names.add(cred.get("name", "").lower())
            existing_creds_full.append(cred)
    except Exception as exc:
        log.warning(
            "Could not fetch existing credentials: %s — skipping auto-register", exc
        )
        return

    # v0.7.208 — Orphan-credential cleanup. v0.7.194 fixed the legacy-
    # alias bug going forward (don't create a duplicate "llama.cpp
    # (local)" when "Local GGUF (llama.cpp)" already exists), but
    # pre-v0.7.194 installs still carry a leftover ORPHAN "llama.cpp
    # (local)" credential pointing at a long-dead dynamic port (e.g.
    # 51107 from a launch days ago) with 0 models linked. Every
    # credential listing shows the user a permanently-broken row
    # they can't make sense of. Detect + DELETE on launcher startup.
    #
    # Safety: only delete credentials matching the v0.6.x→v0.7.194
    # rename pair (`llama.cpp (local)`) AND whose URL is unreachable
    # AND whose linked-model count is 0. All three constraints
    # together make a false-positive functionally impossible.
    _prune_orphan_legacy_credentials(client, existing_creds_full)

    existing_model_keys: set[tuple[str, str]] = set()  # (name.lower, type.lower)
    # v0.8.65i — RETRY the initial /api/models fetch instead of bailing on the
    # first error. The whole auto-register (Ollama + llama.cpp GGUF + Osaurus)
    # is skipped if this fetch fails, so a transient startup hiccup — the API not
    # fully warm yet, or a one-off DB/pool blip (e.g. the v0.8.65g pool-poisoning
    # bug) — used to leave ZERO local models registered, so the chat model
    # selector had nothing to pick. A few quick retries make local-model
    # registration reliable so the user can actually select a local model.
    import time as _time

    _models_json = None
    for _attempt in range(5):
        try:
            r = client.get("/api/models")
            r.raise_for_status()
            _models_json = r.json()
            break
        except Exception as exc:
            if _attempt == 4:
                log.warning(
                    "Could not fetch existing models after retries: %s — "
                    "skipping auto-register",
                    exc,
                )
                return
            log.debug(
                "auto-register: /api/models not ready (attempt %d/5): %s; retrying",
                _attempt + 1,
                exc,
            )
            _time.sleep(1.0)
    for m in _models_json or []:
        existing_model_keys.add((m.get("name", "").lower(), m.get("type", "").lower()))

    registered_any = False

    # --- 2. Ollama ----------------------------------------------------------
    # Discover models here so the call to _list_ollama_models is patchable at
    # the desktop.auto_register namespace (matching existing test patch paths).
    ollama_models = _list_ollama_models()
    if register_ollama_models(
        client, ollama_models, existing_cred_names, existing_model_keys
    ):
        registered_any = True

    # --- 3 & 4. llama.cpp / openai_compatible (with or without live server) --
    # Discover GGUFs here so the call is patchable at desktop.auto_register.
    local_ggufs = _list_local_ggufs(cfg.model_dir)
    if register_llamacpp_models(
        client,
        existing_cred_names,
        existing_model_keys,
        model_dir=cfg.model_dir,
        llamacpp_port=llamacpp_port,
        local_ggufs=local_ggufs,
    ):
        registered_any = True

    # --- 4a. Native MLX server launched by Deeper Notebook ------------------
    if register_mlx_models(
        client,
        existing_cred_names,
        existing_model_keys,
        base_url=mlx_base_url,
        model_ref=mlx_model_ref,
    ):
        registered_any = True

    # --- 4b. v0.8.36 — Osaurus (MLX, Apple Silicon) ------------------------
    # If the user is running Osaurus (https://github.com/osaurus-ai/osaurus)
    # on :1337, register it as an openai_compatible credential so the smart
    # router can route to it. Silently no-ops on non-Apple platforms or when
    # Osaurus isn't running — see desktop/auto_register/osaurus.py for the
    # probe details. Mac users with Osaurus get MLX-optimized inference
    # (typically 2-4× llama-cpp throughput) with zero manual config.
    if register_osaurus_models(
        client=client,
        existing_cred_names=existing_cred_names,
        existing_model_keys=existing_model_keys,
    ):
        registered_any = True

    # --- 4c. LM Studio (local OpenAI-compatible, :1234) --------------------
    if register_lmstudio_models(
        client=client,
        existing_cred_names=existing_cred_names,
        existing_model_keys=existing_model_keys,
    ):
        registered_any = True

    # --- 5. v0.3 — voice + embed registration + default episode profile -----
    # MUST run BEFORE the assignment step (was the TTS/STT empty-slot bug pre-v0.5
    # — voice models registered AFTER auto-assign and never reached defaults).
    if any(p is not None for p in (whisper_port, piper_port, embed_port)):
        # v0.6.21 — pass through the already-fetched name/key sets so voice
        # registration is actually idempotent (was creating duplicates on
        # every launch).
        register_voice_models(
            client,
            whisper_port=whisper_port,
            piper_port=piper_port,
            embed_port=embed_port,
            cfg=cfg,
            existing_cred_names=existing_cred_names,
            existing_model_keys=existing_model_keys,
        )
        # v0.7.32 — register local-Piper speaker profiles BEFORE episode
        # profiles. The episode profiles assume the Piper voices exist;
        # the speaker profiles depend on them but are independent of
        # episode_profile. Order doesn't strictly matter, but speaker
        # first reads more naturally in the bootstrap log.
        register_default_speaker_profile(client)
        register_default_episode_profile(client)
        registered_any = True

    # --- v0.4 memory layer --------------------------------------------------
    if memory_port is not None:
        from desktop.auto_register.memory import register_memory_credential

        # v0.6.22 — thread the existing-names set through (same fix as voice.py)
        register_memory_credential(
            client,
            memory_port=memory_port,
            cfg=cfg,
            existing_cred_names=existing_cred_names,
        )

    # --- 6. Capability-aware default assignment (v0.5) ----------------------
    # Replaces upstream's cloud-centric /api/models/auto-assign with a local-
    # model-aware scorer + per-slot recipe. See model_registry.py / capability.py
    # / assigner.py.
    if registered_any:
        _assign_capability_aware_defaults(client)
