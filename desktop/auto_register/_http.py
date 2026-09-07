"""Shared HTTP helpers for auto_register sub-modules.

Provides:
  - _ensure_credential: idempotent credential creation
  - _ensure_model: idempotent model registration
  - _list_ollama_models: Ollama discovery
  - _list_local_ggufs: local GGUF discovery
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# GGUF filenames that strongly suggest a dedicated embedding model.
_EMBEDDING_HINTS = ("nomic", "bge", "e5", "gte", "minilm", "embed", "snowflake")


def _is_embedding_gguf(filename: str) -> bool:
    lower = filename.lower()
    return any(hint in lower for hint in _EMBEDDING_HINTS)


def _ensure_credential(
    *,
    client: httpx.Client,
    existing_names: set[str],
    name: str,
    provider: str,
    modalities: list[str],
    base_url: str | None = None,
) -> str | None:
    """Return the ID of the named credential, creating it if missing.

    Returns None on error.

    v0.6.30 — control-flow bug fix. The previous version:

        if name.lower() in existing_names:
            r = client.get(...)
            for cred in r.json():
                if cred name matches:
                    return cred.get("id")
            # 🔴 loop ended without finding a match? FALL THROUGH to POST below

    That fall-through created a duplicate credential whenever the
    pre-fetched `existing_names` claimed the credential existed but the
    follow-up GET response didn't include it (e.g. case-normalization
    mismatch, unicode normalization, server response variance). The fix
    returns None in that case — caller may log + skip the model
    registration, but cannot accidentally produce a duplicate.
    """
    if name.lower() in existing_names:
        # Already exists — fetch its ID.
        try:
            r = client.get("/api/credentials")
            r.raise_for_status()
            for cred in r.json():
                if cred.get("name", "").lower() == name.lower():
                    cred_id = cred.get("id")
                    # v0.7.193 — refresh the base_url if the caller
                    # passed one that differs from the saved value.
                    #
                    # Why this matters: the desktop launcher allocates
                    # the local llama-cpp / whisper / piper / embed /
                    # memory ports DYNAMICALLY each launch via
                    # find_free_ports(). If the user happens to get a
                    # different port assignment between launches (port
                    # 56918 → 57204), the credential saved by a prior
                    # launch still points at the old port and the
                    # /credentials/{id}/test call connects to a closed
                    # socket. Pre-v0.7.193 the helper just returned
                    # the existing ID without checking; saved URL stayed
                    # stale forever.
                    #
                    # We only PUT when the caller actually passed a
                    # base_url AND it differs — saves an unnecessary
                    # round-trip on the (common) case where the port
                    # happened to match across launches.
                    saved_url = cred.get("base_url")
                    if (
                        base_url is not None
                        and cred_id is not None
                        and saved_url != base_url
                    ):
                        try:
                            put_resp = client.put(
                                f"/api/credentials/{cred_id}",
                                json={"base_url": base_url},
                            )
                            if put_resp.status_code in (200, 204):
                                log.info(
                                    "Refreshed base_url for %r: %r → %r "
                                    "(dynamic port changed across launches)",
                                    name,
                                    saved_url,
                                    base_url,
                                )
                            else:
                                log.warning(
                                    "PUT /credentials/%s base_url → %s: %s "
                                    "(credential will use stale URL)",
                                    cred_id,
                                    put_resp.status_code,
                                    put_resp.text[:200],
                                )
                        except Exception as exc:
                            log.warning(
                                "Could not refresh base_url for %r: %s "
                                "(credential will use stale URL)",
                                name,
                                exc,
                            )
                    return cred_id
            # Loop exited without finding a match — refuse to POST a duplicate.
            log.warning(
                "Credential %r reported as existing but not found in "
                "/api/credentials response; refusing to POST duplicate",
                name,
            )
            return None
        except Exception as exc:
            log.warning("Could not fetch credential id for %r: %s", name, exc)
            return None

    payload: dict = {"name": name, "provider": provider, "modalities": modalities}
    if base_url:
        payload["base_url"] = base_url
    try:
        r = client.post("/api/credentials", json=payload)
        if r.status_code == 201:
            data = r.json()
            log.info(
                "Created credential %r (provider=%s id=%s)",
                name,
                provider,
                data.get("id"),
            )
            return data.get("id")
        else:
            log.warning(
                "POST /credentials %r → %s: %s", name, r.status_code, r.text[:200]
            )
            return None
    except Exception as exc:
        log.warning("Could not create credential %r: %s", name, exc)
        return None


def _ensure_model(
    *,
    client: httpx.Client,
    existing_keys: set[tuple[str, str]],
    name: str,
    provider: str,
    model_type: str,
    credential_id: str | None,
) -> bool:
    """POST /models if (name, type) not already registered.  Returns True if created."""
    if (name.lower(), model_type.lower()) in existing_keys:
        return False

    payload: dict = {"name": name, "provider": provider, "type": model_type}
    if credential_id:
        payload["credential"] = credential_id
    try:
        r = client.post("/api/models", json=payload)
        if r.status_code in (200, 201):
            log.info(
                "Registered model %r (provider=%s type=%s)", name, provider, model_type
            )
            return True
        elif r.status_code == 400 and "already exists" in (r.text or "").lower():
            return False  # duplicate — treat as no-op
        else:
            log.warning("POST /models %r → %s: %s", name, r.status_code, r.text[:200])
            return False
    except Exception as exc:
        log.warning("Could not register model %r: %s", name, exc)
        return False


def _list_ollama_models(
    base_url: str = "http://127.0.0.1:11434", timeout: float = 10.0
) -> list[str]:
    """Return Ollama model names if the daemon is reachable, else []."""
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", []) if "name" in m]
    except Exception:
        pass
    return []


def _list_local_ggufs(model_dir: Path, min_bytes: int = 1 * 1024 * 1024) -> list[str]:
    """Return relative paths of GGUF files >= min_bytes in model_dir."""
    if not model_dir.exists():
        return []
    return sorted(
        str(p.relative_to(model_dir))
        for p in model_dir.rglob("*.gguf")
        if p.is_file() and p.stat().st_size >= min_bytes
    )
