"""Phase 1 Task 3 — Aggregated local-model health endpoint.

Frontend's sidebar badge component polls this every 30s; the
launcher also calls it via the in-process function at startup
so launcher.log captures verified-working status.

v0.8.38 — also exposes per-sidecar stderr tail + classified hint
via /healthz/sidecars/{kind}/log. The launcher (v0.8.38) writes
the rolling tail to {DEEPER_NOTEBOOK_LAUNCHER_LOG_DIR}/supervisor.{kind}.tail;
this router reads it on demand.
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from deeper_notebook.environment import normalize_product_environment, resolve_env
from deeper_notebook.local_models import (
    cancel_snapshot_install,
    get_snapshot_install,
    list_snapshot_installs,
    reconcile_snapshot_installs,
    start_snapshot_install,
)
from desktop.data_root import active_data_root

router = APIRouter()


def _safe_settings_text(value: object) -> str:
    if not isinstance(value, str) or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("Settings strings cannot contain control characters.")
    return value


def _local_settings_response(cfg) -> dict[str, object]:
    """Serialize only non-secret local-routing preferences."""
    return {
        "model_dir": str(cfg.model_dir),
        "execution_policy": cfg.execution_policy,
        "compute_profile": cfg.compute_profile,
        "local_model_memory_limit_bytes": cfg.local_model_memory_limit_bytes,
        "role_overrides": cfg.role_overrides,
        "trusted_external_model_roots": list(cfg.trusted_external_model_roots),
    }


@router.get("/api/local-models/settings")
async def local_models_settings_get():
    from desktop.config import default_config_path, load_or_create

    return _local_settings_response(load_or_create(default_config_path()))


@router.put("/api/local-models/settings")
async def local_models_settings_put(body: dict):
    from deeper_notebook.local_models.settings import validate_model_root
    from desktop.config import default_config_path, load_or_create

    path = default_config_path()
    cfg = load_or_create(path)
    try:
        model_dir = validate_model_root(Path(body.get("model_dir", cfg.model_dir)))
        execution_policy = body.get("execution_policy", cfg.execution_policy)
        compute_profile = body.get("compute_profile", cfg.compute_profile)
        if not isinstance(execution_policy, str) or execution_policy not in {
            "strict_local",
            "local_preferred",
            "custom",
        }:
            raise ValueError("Unsupported execution policy.")
        if not isinstance(compute_profile, str) or compute_profile not in {
            "efficient",
            "balanced",
            "maximum_quality",
        }:
            raise ValueError("Unsupported compute profile.")
        memory_limit = body.get(
            "local_model_memory_limit_bytes", cfg.local_model_memory_limit_bytes
        )
        if memory_limit is not None and (
            type(memory_limit) is not int or memory_limit < 0
        ):
            raise ValueError("Memory limit must be non-negative.")
        role_overrides = body.get("role_overrides", cfg.role_overrides)
        trusted_roots = body.get(
            "trusted_external_model_roots", cfg.trusted_external_model_roots
        )
        if not isinstance(role_overrides, dict) or not isinstance(
            trusted_roots, list | tuple
        ):
            raise ValueError("Invalid local model settings.")
        validated_overrides = {
            _safe_settings_text(key): _safe_settings_text(value)
            for key, value in role_overrides.items()
        }
        validated_roots = tuple(_safe_settings_text(root) for root in trusted_roots)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = replace(
        cfg,
        model_dir=model_dir,
        execution_policy=execution_policy,
        compute_profile=compute_profile,
        local_model_memory_limit_bytes=memory_limit,
        role_overrides=validated_overrides,
        trusted_external_model_roots=validated_roots,
    )
    updated.save(path)
    return _local_settings_response(updated)


@router.post("/api/local-models/route-plan")
async def local_models_route_plan(body: dict, request: Request):
    """Plan from injected, already-redacted local facts without transport I/O."""
    from deeper_notebook.local_models.contracts import RouteRequest
    from deeper_notebook.local_models.planner import plan_model_route

    try:
        route_request = RouteRequest(
            role=body["role"],
            required_context_tokens=int(body.get("required_context_tokens", 0)),
            modalities=tuple(body.get("modalities", ["text"])),
            requires_structured_output=bool(
                body.get("requires_structured_output", False)
            ),
            execution_policy=body.get("execution_policy", "strict_local"),
            compute_profile=body.get("compute_profile", "balanced"),
            role_override_model_id=body.get("role_override_model_id"),
            production_override_model_id=body.get("production_override_model_id"),
            memory_reservation_bytes=int(body.get("memory_reservation_bytes", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="Invalid route-plan request."
        ) from exc
    transport = getattr(request.app.state, "local_model_transport", None)
    if transport is not None:
        loopback_endpoint = "http://127.0.0.1/local-models/route-plan"
        transport(loopback_endpoint)
    candidates = getattr(request.app.state, "local_model_route_candidates", ())
    plan = plan_model_route(list(candidates), route_request)
    return plan.receipt()


# v0.8.38 — the launcher's supervisor names map to user-facing "kinds"
# that the frontend uses in the badge popover URL. Keep this map narrow
# so a malicious / typo'd `kind` path-param can't escape the log_dir.
_KIND_TO_SUPERVISOR: dict[str, str] = {
    "chat": "supervisor.llamacpp_chat",
    "embed": "supervisor.llamacpp_embed",
    "whisper": "supervisor.whisper",
    "piper": "supervisor.piper",
    "memory": "supervisor.memory",
}


async def _load_local_credentials() -> list[dict]:
    """Fetch local runtime credentials whose `base_url` points to a
    local sidecar (127.0.0.1 OR localhost). The local-only filter is
    load-bearing — we don't want this endpoint to probe a
    user-configured remote LM Studio or Ollama on a LAN box. That's a
    different concern and would belong on a different surface.

    v0.8.0 Task 3 refactor — was sync + `asyncio.run`; converted
    to async to eliminate the future-refactor footgun (the
    `asyncio.run` would explode if the caller ever became async).
    Also widened the local-sidecar match to include `localhost`
    so users who registered an openai_compatible credential as
    `http://localhost:PORT/v1` (common LM Studio default) are
    picked up by the probe."""
    from deeper_notebook.domain.credential import Credential

    creds = await Credential.get_all()
    return [
        {
            "credential_id": c.id,
            "name": c.name,
            "kind": c.provider,
            "base_url": c.base_url or "",
        }
        for c in creds
        if c.provider in {"openai_compatible", "ollama"}
        and _is_local_sidecar_url(c.base_url or "")
    ]


def _is_local_sidecar_url(url: str) -> bool:
    """Match `http://127.0.0.1[:PORT]...` or `http://localhost[:PORT]...`.
    https:// is intentionally NOT matched today — none of our
    bundled sidecars use TLS, and a user-configured https endpoint
    is more likely to be a remote service than a local sidecar."""
    return url.startswith("http://127.0.0.1") or url.startswith("http://localhost")


@router.get("/api/local-models/health")
async def local_models_health():
    """Active health probe across all local sidecars. Each probe
    has its own ≤9s timeout; total endpoint latency scales with
    the number of registered local credentials (typically 4-5).

    v0.8.20 CRITICAL — `probe_all_local_models` drives sync
    `httpx.Client` requests with up to a 9s per-probe budget. The
    earlier code called it directly inside this `async def` handler,
    so a wedged sidecar (or a couple of them) would block the
    FastAPI event loop for the full 9-45s sweep — every other
    in-flight request (chat SSE streams, status polls, frontend
    badge polls) stalled in lockstep. With the frontend polling
    /api/local-models/health every 30s, a single hung local model
    cascaded into freezing the app for 9s every poll. `to_thread`
    pushes the sync httpx calls onto the default executor so the
    loop keeps serving everyone else."""
    from deeper_notebook.health.local_models import probe_all_local_models

    creds = await _load_local_credentials()
    results = await asyncio.to_thread(probe_all_local_models, creds)
    healthy = sum(1 for r in results if r["status"] == "healthy")
    total_configured = sum(1 for r in results if r["status"] != "not_configured")
    if total_configured == 0:
        overall = "down"
    elif healthy == total_configured:
        overall = "healthy"
    else:
        overall = "degraded"
    return {"overall": overall, "models": results}


@router.get("/api/local-models/inventory")
async def local_models_inventory():
    """v0.8.39 — List GGUF files in the configured model dir with
    metadata (architecture, context length, quant, parameter count,
    file size).

    The model dir is resolved from environment in this order:
      1. `DEEPER_NOTEBOOK_MODEL_DIR` (explicit override)
      2. The launcher-exported `DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT` (set
         in `desktop/launcher.py` session_env in v0.8.39 — but
         falling back gracefully when running the API standalone)
      3. `~/Desktop/AI_Models` (matches `desktop/config.py:default_model_dir`
         POSIX default — the same path the launcher uses out-of-box)

    Returns:
      {
        "model_dir": str,
        "available": bool,    # False when dir doesn't exist
        "models": [
          {
            "name": "qwen2.5-7b-instruct-q4_k_m",
            "path": "/Users/.../qwen2.5-7b-instruct-q4_k_m.gguf",
            "architecture": "qwen2" | null,
            "context_length": 32768 | null,
            "quant": "Q4_K_M" | null,
            "parameter_count_b": 7.0 | null,
            "file_size_bytes": 4368450336
          },
          ...
        ]
      }

    Surfaces only metadata — no mutation, no download. Future
    `POST /local-models/download` (v0.8.39b) + `POST /local-models/set-active`
    (v0.8.39c) extend this read-only foundation.
    """
    from pathlib import Path as _Path

    from deeper_notebook.local_models import enumerate_models

    # Resolve model dir per docstring precedence.
    raw = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw:
        # Final POSIX fallback (matches desktop/config.py:default_model_dir).
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw = str(_Path(home) / "Desktop" / "AI_Models") if home else ""

    if not raw:
        return {"model_dir": "", "available": False, "models": []}

    model_dir = _Path(raw)
    available = model_dir.exists() and model_dir.is_dir()

    # Inventory is sync (filesystem stat per file); push to a thread so
    # the event loop doesn't block on slow disks.
    if not available:
        return {"model_dir": str(model_dir), "available": False, "models": []}

    rows = await asyncio.to_thread(enumerate_models, model_dir)
    launcher_config = _launcher_config_summary(model_dir)
    return {
        "model_dir": str(model_dir),
        "available": True,
        "launcher_config": launcher_config,
        "models": [
            _local_model_to_dict(
                r,
                model_dir=model_dir,
                launcher_config=launcher_config,
            )
            for r in rows
        ],
    }


@router.get("/api/local-models/readiness")
async def local_models_readiness():
    """Return redacted, route-safe readiness facts for Research Core.

    Paths remain exclusive to the Settings inventory endpoint.  This endpoint
    deliberately exposes no provider URL, model-root path, manifest locator,
    prompt, or source material.
    """
    from deeper_notebook.local_models import (
        build_readiness_inventory,
        load_model_manifest,
    )

    model_dir = _configured_model_dir()
    if model_dir is None:
        return {"available": False, "models": []}
    manifest_entries = await asyncio.to_thread(load_model_manifest, model_dir)
    rows = await asyncio.to_thread(
        build_readiness_inventory,
        model_dir,
        manifest_entries=manifest_entries,
        active_model_path=resolve_env("DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL", "").strip(),
    )
    return {
        "available": True,
        "models": [
            {
                "model_id": row.model_id,
                "format": row.format,
                "modality": row.modality,
                "readiness": row.readiness,
                "readiness_reason": row.readiness_reason,
                "measured_tier": row.measured_tier,
                "accepted_roles": list(row.accepted_roles),
                "route_eligible": row.route_eligible,
            }
            for row in rows
        ],
    }


@router.get("/api/local-models/role-routing")
async def local_models_role_routing():
    """Recommend installed local models for each product role.

    This is the first read-only layer of role routing: it does not change
    defaults or hot-swap anything, but it gives the UI and future task router a
    stable contract for chat, source synthesis, coding research, study tools,
    and embedding/retrieval picks.
    """
    from pathlib import Path as _Path

    from deeper_notebook.local_models import (
        build_manifest_reconciliation,
        enumerate_models,
        find_manifest_matches,
        find_unmatched_manifest_entries,
        load_benchmark_history,
        load_model_manifest,
        model_manifest_path,
        recommend_model_roles,
    )

    raw = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw = str(_Path(home) / "Desktop" / "AI_Models") if home else ""

    if not raw:
        return {"model_dir": "", "available": False, "routes": []}

    model_dir = _Path(raw)
    available = model_dir.exists() and model_dir.is_dir()
    if not available:
        return {"model_dir": str(model_dir), "available": False, "routes": []}

    rows = await asyncio.to_thread(enumerate_models, model_dir)
    launcher_config = _launcher_config_summary(model_dir)
    benchmark_history = await asyncio.to_thread(load_benchmark_history, model_dir)
    manifest_entries = await asyncio.to_thread(load_model_manifest, model_dir)
    routes = await asyncio.to_thread(
        recommend_model_roles,
        rows,
        benchmark_history,
        manifest_entries,
    )
    route_matches = [
        find_manifest_matches(route.model, manifest_entries) for route in routes
    ]
    route_alignments = [
        _manifest_alignment_to_dict(route, matches, available=bool(manifest_entries))
        for route, matches in zip(routes, route_matches)
    ]
    unmatched_manifest_entries = find_unmatched_manifest_entries(
        manifest_entries,
        rows,
    )
    manifest_reconciliation = build_manifest_reconciliation(
        manifest_entries,
        rows,
    )
    route_alternatives = [
        _manifest_alternatives_for_route(
            route,
            alignment,
            manifest_reconciliation,
        )
        for route, alignment in zip(routes, route_alignments)
    ]
    return {
        "model_dir": str(model_dir),
        "available": True,
        "manifest": {
            "path": str(model_manifest_path(model_dir)),
            "available": bool(manifest_entries),
            "entry_count": len(manifest_entries),
            "matched_route_count": sum(1 for matches in route_matches if matches),
            "alignment_counts": _manifest_alignment_counts(route_alignments),
            "unmatched_entry_count": len(unmatched_manifest_entries),
            "unmatched_entries": [
                _manifest_entry_to_dict(entry)
                for entry in unmatched_manifest_entries[:10]
            ],
            "reconciliation_counts": _manifest_reconciliation_counts(
                manifest_reconciliation,
            ),
            "reconciliation_entries": [
                _manifest_reconciliation_entry_to_dict(entry)
                for entry in manifest_reconciliation[:100]
            ],
        },
        "routes": [
            {
                "role": route.role,
                "label": route.label,
                "confidence": route.confidence,
                "reason": route.reason,
                "model": _local_model_to_dict(
                    route.model,
                    model_dir=model_dir,
                    launcher_config=launcher_config,
                ),
                "manifest_matches": [
                    _manifest_entry_to_dict(entry) for entry in matches
                ],
                "manifest_alignment": alignment,
                "manifest_alternatives": [
                    _manifest_alternative_to_dict(alternative, route.role)
                    for alternative in alternatives
                ],
                "manifest_alternative_note": _manifest_alternative_note(
                    route,
                    alignment,
                    alternatives,
                    available=bool(manifest_entries),
                ),
            }
            for route, matches, alignment, alternatives in zip(
                routes,
                route_matches,
                route_alignments,
                route_alternatives,
            )
        ],
    }


def _launcher_model_ref(model, model_dir: Path | None):
    if model_dir is None:
        return model.path
    try:
        # Launcher model references are persisted in config.toml and sent by
        # the frontend, so they must not inherit Windows' backslash separator.
        return Path(model.path).resolve().relative_to(model_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return model.path


def _launcher_provider_for_runtime(runtime: str | None) -> str | None:
    normalized = (runtime or "").lower()
    if normalized == "gguf":
        return "llamacpp"
    if normalized == "mlx":
        return "mlx"
    return None


def _launcher_config_summary(model_dir: Path):
    active_gguf_model = resolve_env("DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL", "").strip()
    active_mlx_model = resolve_env("DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL", "").strip()
    config_path = active_data_root() / "config.toml"
    if not config_path.exists():
        return {
            "available": False,
            "path": str(config_path),
            "provider": "",
            "default_model": "",
            "model_dir": "",
            "model_dir_matches_inventory": False,
            "active_gguf_model": active_gguf_model,
            "active_mlx_model": active_mlx_model,
        }
    try:
        raw = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {
            "available": False,
            "path": str(config_path),
            "provider": "",
            "default_model": "",
            "model_dir": "",
            "model_dir_matches_inventory": False,
            "active_gguf_model": active_gguf_model,
            "active_mlx_model": active_mlx_model,
        }

    raw_model_dir = str(raw.get("model_dir") or "")
    matches_inventory = False
    if raw_model_dir:
        try:
            matches_inventory = (
                Path(raw_model_dir).expanduser().resolve() == model_dir.resolve()
            )
        except OSError:
            matches_inventory = False

    return {
        "available": True,
        "path": str(config_path),
        "provider": str(raw.get("provider") or ""),
        "default_model": str(raw.get("default_model") or ""),
        "model_dir": raw_model_dir,
        "model_dir_matches_inventory": matches_inventory,
        "active_gguf_model": active_gguf_model,
        "active_mlx_model": active_mlx_model,
    }


def _local_model_to_dict(
    model,
    model_dir: Path | None = None,
    launcher_config: dict | None = None,
):
    if model is None:
        return None
    capabilities = _local_model_runtime_capabilities(model.runtime)
    launcher_ref = _launcher_model_ref(model, model_dir)
    launcher_provider = _launcher_provider_for_runtime(model.runtime)
    config_provider = (launcher_config or {}).get("provider") or ""
    config_default = (launcher_config or {}).get("default_model") or ""
    active_gguf = (launcher_config or {}).get("active_gguf_model") or ""
    active_mlx = (launcher_config or {}).get("active_mlx_model") or ""
    is_launch_default = bool(
        launcher_provider
        and config_provider == launcher_provider
        and config_default == launcher_ref
    )
    is_live_active = bool(
        (
            (model.runtime or "").lower() == "gguf"
            and active_gguf
            and active_gguf in {model.path, launcher_ref}
        )
        or (
            (model.runtime or "").lower() == "mlx"
            and active_mlx
            and active_mlx in {model.path, launcher_ref}
        )
    )
    if is_live_active:
        activation_mode = "active_now"
        activation_detail = (
            f"This {model.runtime.upper() if model.runtime else 'model'} is the live chat model."
        )
    elif is_launch_default:
        activation_mode = "launch_default"
        activation_detail = "This model is the native launch default."
    elif capabilities["activation_supported"]:
        activation_mode = "live_switch_available"
        activation_detail = "Can switch the live chat model without restart."
    elif capabilities["runnable"]:
        activation_mode = "restart_required"
        activation_detail = "Can be used as the native launch default after restart."
    else:
        activation_mode = "inventory_only"
        activation_detail = capabilities["runtime_note"]
    return {
        "name": model.name,
        "path": model.path,
        "launcher_model_ref": launcher_ref,
        "runtime": model.runtime,
        "runnable": capabilities["runnable"],
        "activation_supported": capabilities["activation_supported"],
        "is_launch_default": is_launch_default,
        "is_live_active": is_live_active,
        "activation_mode": activation_mode,
        "activation_detail": activation_detail,
        "runtime_status": capabilities["runtime_status"],
        "runtime_note": capabilities["runtime_note"],
        "setup_href": capabilities["setup_href"],
        "setup_label": capabilities["setup_label"],
        "architecture": model.metadata.architecture,
        "context_length": model.metadata.context_length,
        "quant": model.metadata.quant,
        "parameter_count_b": model.metadata.parameter_count_b,
        "file_size_bytes": model.metadata.file_size_bytes,
    }


def _manifest_entry_to_dict(entry):
    return {
        "manifest_path": entry.manifest_path,
        "category": entry.category,
        "role": entry.role,
        "repo": entry.repo,
        "local_path": entry.local_path,
        "runtime_type": entry.runtime_type,
        "estimated_status": entry.estimated_status,
        "notes": entry.notes,
    }


def _manifest_row_preview_to_dict(preview):
    return {
        "ok": True,
        "manifest_path": preview.manifest_path,
        "row": preview.row,
        "entry": _manifest_entry_to_dict(preview.entry),
        "duplicate": preview.duplicate,
        "duplicate_entry": (
            _manifest_entry_to_dict(getattr(preview, "duplicate_entry", None))
            if getattr(preview, "duplicate_entry", None)
            else None
        ),
    }


def _manifest_row_apply_to_dict(result):
    data = _manifest_row_preview_to_dict(result)
    data["backup_path"] = result.backup_path
    data["detail"] = (
        "Manifest row applied with backup."
        if result.backup_path
        else "Manifest row applied."
    )
    return data


def _manifest_alignment_to_dict(route, matches, *, available: bool):
    if not available:
        return {
            "status": "no_manifest",
            "label": "No manifest",
            "reason": "No curated AI_Models manifest is available for comparison.",
            "matched_count": 0,
            "primary_count": 0,
        }
    if route.model is None:
        return {
            "status": "missing_model",
            "label": "No local fit",
            "reason": "No local recommendation is available to compare with the manifest.",
            "matched_count": 0,
            "primary_count": 0,
        }

    primary_count = sum(
        1 for entry in matches if str(entry.role).strip().lower().startswith("primary")
    )
    if primary_count:
        return {
            "status": "primary",
            "label": "Manifest primary",
            "reason": "The selected route model matches a curated primary manifest row.",
            "matched_count": len(matches),
            "primary_count": primary_count,
        }
    if matches:
        roles = sorted({entry.role for entry in matches if entry.role})
        role_text = ", ".join(roles) if roles else "curated"
        return {
            "status": "curated",
            "label": "Manifest curated",
            "reason": (
                f"The selected route model appears in the manifest as {role_text}."
            ),
            "matched_count": len(matches),
            "primary_count": 0,
        }

    model_name = getattr(route.model, "name", "Selected model")
    return {
        "status": "untracked",
        "label": "Not in manifest",
        "reason": (
            f"{model_name} is currently recommended, but it is not in the "
            "curated AI_Models manifest."
        ),
        "matched_count": 0,
        "primary_count": 0,
    }


def _manifest_alignment_counts(alignments):
    statuses = {
        "primary": 0,
        "curated": 0,
        "untracked": 0,
        "missing_model": 0,
        "no_manifest": 0,
    }
    for alignment in alignments:
        status = alignment.get("status")
        if status in statuses:
            statuses[status] += 1
    return statuses


def _manifest_alternatives_for_route(route, alignment, reconciliation_entries):
    status = alignment.get("status")
    if status not in {"untracked", "missing_model"}:
        return []

    scored = []
    current_path = str(getattr(route.model, "path", "") or "")
    current_name = str(getattr(route.model, "name", "") or "")
    for item in reconciliation_entries:
        if item.status != "matched":
            continue
        if item.matched_model_path and item.matched_model_path == current_path:
            continue
        if item.matched_model_name and item.matched_model_name == current_name:
            continue
        score = _manifest_role_relevance_score(route.role, item.entry)
        if score <= 0:
            continue
        scored.append((score, item))

    scored.sort(
        key=lambda pair: (
            -pair[0],
            _manifest_role_priority(pair[1].entry.role),
            pair[1].entry.category.lower(),
            pair[1].entry.repo.lower(),
        )
    )
    return [item for _, item in scored[:3]]


def _manifest_alternative_to_dict(item, route_role: str):
    data = _manifest_entry_to_dict(item.entry)
    data.update(
        {
            "matched_model_name": item.matched_model_name,
            "matched_model_path": item.matched_model_path,
            "matched_model_runtime": item.matched_model_runtime,
            "reason": _manifest_alternative_reason(item, route_role),
        }
    )
    return data


def _manifest_alternative_reason(item, route_role: str):
    role = item.entry.role or "curated"
    label = _manifest_route_label(route_role)
    return (
        f"Curated {role} manifest row matched the local scan "
        f"for {item.entry.category}; suggested for {label}."
    )


def _manifest_alternative_note(route, alignment, alternatives, *, available: bool):
    if not available:
        return None
    if alternatives:
        return None
    status = alignment.get("status")
    if status not in {"untracked", "missing_model"}:
        return None
    if route.role == "embedding":
        return (
            "No curated embedding/retrieval manifest row is available yet. "
            "Add an embedding model to the AI_Models manifest to make this "
            "role fully manifest-backed."
        )
    return "No installed curated manifest alternative is available for this role yet."


def _manifest_role_relevance_score(role: str, entry) -> int:
    category = f"{entry.category} {entry.notes}".lower()
    runtime = entry.runtime_type.lower()
    if role == "coding_research":
        score = _keyword_score(
            category, ("coding", "debugging", "terminal", "agentic"), 60
        )
    elif role == "source_synthesis":
        score = _keyword_score(category, ("research", "reasoning", "general chat"), 60)
    elif role == "chat":
        score = _keyword_score(
            category, ("general chat", "creative", "research", "reasoning"), 60
        )
    elif role == "study_fast":
        score = _keyword_score(
            category, ("general chat", "research", "creative", "fable"), 60
        )
    elif role == "embedding":
        score = _keyword_score(category, ("embedding", "retrieval", "embed"), 80)
    else:
        score = 0

    if score <= 0:
        return 0

    role_text = entry.role.lower()
    if role_text.startswith("primary"):
        score += 20
    elif role_text.startswith("backup"):
        score += 12
    elif role_text.startswith("priority"):
        score += 10
    elif role_text.startswith("requested"):
        score += 6

    if runtime in {"mlx", "gguf"}:
        score += 8
    return score


def _manifest_route_label(role: str) -> str:
    labels = {
        "chat": "default chat",
        "source_synthesis": "source synthesis",
        "coding_research": "coding research",
        "study_fast": "fast study tools",
        "embedding": "embedding/retrieval",
    }
    return labels.get(role, role.replace("_", " "))


def _keyword_score(text: str, keywords: tuple[str, ...], amount: int) -> int:
    return amount if any(keyword in text for keyword in keywords) else 0


def _manifest_role_priority(role: str) -> int:
    normalized = role.lower()
    if normalized.startswith("primary"):
        return 0
    if normalized.startswith("backup"):
        return 1
    if normalized.startswith("priority"):
        return 2
    if normalized.startswith("requested"):
        return 3
    return 4


def _manifest_reconciliation_counts(entries):
    return {
        "matched": sum(1 for entry in entries if entry.status == "matched"),
        "missing": sum(1 for entry in entries if entry.status == "missing"),
        "unsupported_runtime": sum(
            1 for entry in entries if entry.status == "unsupported_runtime"
        ),
    }


def _manifest_reconciliation_entry_to_dict(item):
    data = _manifest_entry_to_dict(item.entry)
    data.update(
        {
            "status": item.status,
            "status_reason": item.status_reason,
            "matched_model_name": item.matched_model_name,
            "matched_model_path": item.matched_model_path,
            "matched_model_runtime": item.matched_model_runtime,
            "setup_task": (
                _manifest_setup_task_to_dict(item.setup_task)
                if item.setup_task
                else None
            ),
        }
    )
    return data


def _manifest_setup_task_to_dict(task):
    return {
        "action_type": task.action_type,
        "label": task.label,
        "description": task.description,
        "repo_id": task.repo_id,
        "filename": task.filename,
        "target_path": task.target_path,
        "command": task.command,
        "setup_href": task.setup_href,
    }


def _manifest_recommendation_to_dict(rec):
    return {
        "id": rec.id,
        "label": rec.label,
        "description": rec.description,
        "repo_id": rec.repo_id,
        "filename": rec.filename,
        "runtime_type": rec.runtime_type,
        "target_path": rec.target_path,
        "status": rec.status,
        "tags": rec.tags,
        "approx_size_gb": rec.approx_size_gb,
        "context_length": rec.context_length,
        "setup_task": (
            _manifest_setup_task_to_dict(rec.setup_task) if rec.setup_task else None
        ),
    }


def _snapshot_install_job_to_dict(job):
    return {
        "job_id": job.job_id,
        "repo_id": job.repo_id,
        "target_path": job.target_path,
        "revision": getattr(job, "revision", None),
        "status": job.status,
        "error": job.error,
        "log_tail": job.log_tail,
    }


def _validate_huggingface_repo_id(repo_id: str):
    import re as _re

    if not _re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*",
        repo_id,
    ):
        raise HTTPException(
            status_code=400,
            detail="`repo_id` must be of the form `namespace/name` "
            "(letters, digits, dot, dash, underscore only).",
        )


def _local_model_runtime_capabilities(runtime: str | None):
    normalized = (runtime or "gguf").lower()
    if normalized == "gguf":
        return {
            "runnable": True,
            "activation_supported": True,
            "runtime_status": "runnable",
            "runtime_note": None,
            "setup_href": None,
            "setup_label": None,
        }
    if normalized == "mlx":
        return {
            "runnable": True,
            "activation_supported": True,
            "runtime_status": "runnable",
            "runtime_note": None,
            "setup_href": None,
            "setup_label": None,
        }
    return {
        "runnable": False,
        "activation_supported": False,
        "runtime_status": "inventory_only",
        "runtime_note": (
            "Visible in inventory only. Experimental and Transformers assets "
            "are tracked for curation, but need a runnable local provider "
            "before chat, role routing, or benchmarks."
        ),
        "setup_href": "/settings/launcher-prefs",
        "setup_label": "Open launcher preferences",
    }


def _benchmark_result_to_dict(result):
    return {
        "role": result.role,
        "label": result.label,
        "status": result.status,
        "model_name": result.model_name,
        "model_path": result.model_path,
        "model_runtime": result.model_runtime,
        "model_id": result.model_id,
        "provider": result.provider,
        "latency_ms": result.latency_ms,
        "tokens_per_second": result.tokens_per_second,
        "score": result.score,
        "error": result.error,
    }


def _benchmark_job_to_dict(job):
    return {
        "job_id": job.job_id,
        "roles": job.roles,
        "status": job.status,
        "results": [_benchmark_result_to_dict(result) for result in job.results],
        "error": job.error,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def _configured_model_dir():
    from pathlib import Path as _Path

    raw = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw = str(_Path(home) / "Desktop" / "AI_Models") if home else ""
    if not raw:
        return None
    model_dir = _Path(raw)
    return model_dir if model_dir.exists() and model_dir.is_dir() else None


def _open_path_in_file_manager(path: Path) -> None:
    import subprocess
    import sys

    if sys.platform == "darwin":
        command = ["open", "-R", str(path)]
    elif sys.platform.startswith("win"):
        if path.is_dir():
            command = ["explorer", str(path)]
        else:
            command = ["explorer", "/select,", str(path)]
    else:
        command = ["xdg-open", str(path if path.is_dir() else path.parent)]

    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@router.post("/api/local-models/manifest/rows/preview")
async def local_models_manifest_row_preview(body: dict):
    """Validate one draft AI_Models manifest row without mutating disk."""
    from deeper_notebook.local_models import ManifestRowError, preview_manifest_row

    row = (body.get("row") or "").strip() if isinstance(body, dict) else ""
    if not row:
        raise HTTPException(status_code=400, detail="Body must include `row`.")

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    try:
        preview = await asyncio.to_thread(preview_manifest_row, model_dir, row)
    except ManifestRowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not read manifest: {exc}",
        ) from exc

    return _manifest_row_preview_to_dict(preview)


@router.post("/api/local-models/manifest/rows/apply")
async def local_models_manifest_row_apply(body: dict):
    """Append one validated AI_Models manifest row with a backup."""
    from deeper_notebook.local_models import ManifestRowError, append_manifest_row

    row = (body.get("row") or "").strip() if isinstance(body, dict) else ""
    allow_duplicate = (
        bool(body.get("allow_duplicate")) if isinstance(body, dict) else False
    )
    if not row:
        raise HTTPException(status_code=400, detail="Body must include `row`.")

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    try:
        result = await asyncio.to_thread(
            append_manifest_row,
            model_dir,
            row,
            allow_duplicate=allow_duplicate,
        )
    except ManifestRowError as exc:
        status_code = 409 if "already exists" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not update manifest: {exc}",
        ) from exc

    return _manifest_row_apply_to_dict(result)


@router.post("/api/local-models/reveal")
async def local_models_reveal(body: dict):
    """Reveal a scanned local-model path in the host file manager.

    This is intentionally bounded to the configured model directory so the
    Local Models page can open matched AI_Models rows without becoming a
    general-purpose host filesystem launcher.
    """
    raw_path = (body.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Body must include `path`.")

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    try:
        resolved_model_dir = model_dir.resolve()
        resolved_path = Path(raw_path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not resolve path: {exc}",
        ) from exc

    if not resolved_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Path not found: {resolved_path}",
        )
    if (
        resolved_path != resolved_model_dir
        and resolved_model_dir not in resolved_path.parents
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path must be inside the configured model directory "
                f"({resolved_model_dir})."
            ),
        )

    try:
        await asyncio.to_thread(_open_path_in_file_manager, resolved_path)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open file manager: {exc}",
        ) from exc

    return {
        "ok": True,
        "path": str(resolved_path),
        "detail": "Opened in file manager.",
    }


@router.post("/api/local-models/launch-default")
async def local_models_set_launch_default(body: dict):
    """Persist a provider-backed local model as the native launch default.

    This is intentionally narrower than hot-swap:
    - MLX repos persist as `provider="mlx"` + a relative `default_model`.
    - GGUF files persist as `provider="llamacpp"` + a relative `default_model`.
      Live llama.cpp hot-swap still uses `/local-models/set-active`.
    - inventory-only rows are rejected until their runtime has a launcher
      provider.
    """
    from deeper_notebook.local_models import enumerate_models
    from desktop.config import load_or_create

    requested_ref = (body.get("launcher_model_ref") or "").strip()
    if not requested_ref:
        raise HTTPException(
            status_code=400,
            detail="Body must include `launcher_model_ref`.",
        )

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    rows = await asyncio.to_thread(enumerate_models, model_dir)
    match = next(
        (row for row in rows if _launcher_model_ref(row, model_dir) == requested_ref),
        None,
    )
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Local model not found in inventory: {requested_ref}",
        )

    runtime = (match.runtime or "gguf").lower()
    provider_by_runtime = {
        "gguf": "llamacpp",
        "mlx": "mlx",
    }
    provider = provider_by_runtime.get(runtime)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=f"Launch default is not supported for runtime {runtime!r}.",
        )

    config_path = active_data_root() / "config.toml"
    try:
        cfg = load_or_create(config_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not load native launcher config: {exc}",
        ) from exc

    updated = replace(
        cfg,
        model_dir=model_dir,
        provider=provider,
        default_model=requested_ref,
    )
    await asyncio.to_thread(updated.save, config_path)
    return {
        "ok": True,
        "detail": (
            f"Native launcher default set to {requested_ref}. "
            "Restart Deeper Notebook to apply it."
        ),
        "launcher_config": _launcher_config_summary(model_dir),
    }


@router.post("/api/local-models/benchmarks")
async def local_models_benchmark_start(body: dict):
    """Start a local model benchmark job for recommended roles.

    Jobs benchmark only recommended local models that are also registered as
    language models, so downloaded-but-unregistered files are reported as
    skipped instead of causing confusing runtime errors.
    """
    from deeper_notebook.local_models import start_benchmark

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    roles = body.get("roles") if isinstance(body, dict) else None
    run_inline = bool(body.get("run_inline")) if isinstance(body, dict) else False
    job = await start_benchmark(
        model_dir,
        roles=roles if isinstance(roles, list) else None,
        run_inline=run_inline,
    )
    return _benchmark_job_to_dict(job)


@router.get("/api/local-models/benchmarks")
async def local_models_benchmark_list():
    from deeper_notebook.local_models import list_benchmark_jobs

    return {
        "benchmarks": [_benchmark_job_to_dict(job) for job in list_benchmark_jobs()]
    }


@router.get("/api/local-models/benchmarks/{job_id}")
async def local_models_benchmark_status(job_id: str):
    from deeper_notebook.local_models import get_benchmark_job

    job = get_benchmark_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown benchmark job {job_id!r}.",
        )
    return _benchmark_job_to_dict(job)


@router.get("/api/local-models/recommendations")
async def local_models_recommendations():
    """Return MLX-first manifest recommendations when available.

    The static GGUF list remains the fallback for users without an
    `AI_Models/manifests/model_inventory.md` file.
    """
    from pathlib import Path as _Path

    from deeper_notebook.local_models import (
        RECOMMENDATIONS,
        build_manifest_recommendations,
        enumerate_models,
        load_model_manifest,
        model_manifest_path,
    )

    model_dir = _configured_model_dir()
    if model_dir is None:
        return {"source": "static", "recommendations": RECOMMENDATIONS}

    manifest_entries = await asyncio.to_thread(load_model_manifest, model_dir)
    if not manifest_entries:
        return {
            "source": "static",
            "manifest_path": str(model_manifest_path(model_dir)),
            "recommendations": RECOMMENDATIONS,
        }

    available = model_dir.exists() and model_dir.is_dir()
    models = await asyncio.to_thread(enumerate_models, model_dir) if available else []
    recommendations = await asyncio.to_thread(
        build_manifest_recommendations,
        manifest_entries,
        models,
    )
    return {
        "source": "manifest",
        "manifest_path": str(model_manifest_path(model_dir)),
        "recommendations": [
            _manifest_recommendation_to_dict(rec) for rec in recommendations
        ],
    }


@router.post("/api/local-models/download")
async def local_models_download(body: dict):
    """v0.8.39b — Start a background HuggingFace GGUF download.

    Body: `{repo_id: str, filename: str, target_path?: str}` — typically
    lifted from a recommendation card or manifest setup task. `target_path`
    lets curated AI_Models rows land in their exact nested folder.

    Response: `{job_id: str, status: str, target_path: str, ...}` —
    poll `GET /local-models/downloads/{job_id}` for progress.

    Idempotency: re-POSTing with the same (repo_id, filename) while a
    job is already queued/downloading returns that existing job rather
    than starting a duplicate. Prevents the .part file corruption that
    two concurrent downloaders would cause.

    The target directory is resolved the same way as `inventory` above
    (DEEPER_NOTEBOOK_MODEL_DIR > launcher default > POSIX default).
    """
    from pathlib import Path as _Path

    from deeper_notebook.local_models import start_download

    repo_id = (body.get("repo_id") or "").strip()
    filename = (body.get("filename") or "").strip()
    target_path = (body.get("target_path") or "").strip()
    if not repo_id or not filename:
        raise HTTPException(
            status_code=400,
            detail="Both `repo_id` and `filename` are required.",
        )
    # Defense-in-depth: filename must look like a GGUF file and not
    # contain path separators (so it can't escape dest_dir).
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=400,
            detail="`filename` must not contain path separators.",
        )
    if not filename.lower().endswith(".gguf"):
        raise HTTPException(
            status_code=400,
            detail="`filename` must end in .gguf.",
        )
    # v0.8.66 (audit S-1) — validate repo_id to the HuggingFace
    # `namespace/name` shape. It is interpolated into the download URL
    # (https://huggingface.co/{repo_id}/resolve/main/{filename}); leaving it
    # unsanitized let a caller smuggle path-traversal / query / fragment / `@`
    # sequences into the path. The host is pinned to huggingface.co so this is
    # defense-in-depth (matching the filename guard above), keeping malformed
    # input + traversal out of the composed URL.
    import re as _re

    if not _re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", repo_id
    ):
        raise HTTPException(
            status_code=400,
            detail="`repo_id` must be of the form `namespace/name` "
            "(letters, digits, dot, dash, underscore only).",
        )

    raw = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw = str(_Path(home) / "Desktop" / "AI_Models") if home else ""
    if not raw:
        raise HTTPException(
            status_code=500,
            detail="No model directory configured. Set DEEPER_NOTEBOOK_MODEL_DIR.",
        )
    model_root = _Path(raw).expanduser().resolve()
    dest_dir = model_root

    if target_path:
        resolved_target = _Path(target_path).expanduser().resolve()
        if resolved_target.name != filename:
            raise HTTPException(
                status_code=400,
                detail="`target_path` basename must match `filename`.",
            )
        try:
            resolved_target.relative_to(model_root)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="`target_path` must stay inside the configured model directory.",
            ) from None
        dest_dir = resolved_target.parent

    job = await start_download(repo_id, filename, dest_dir)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "target_path": job.target_path,
        "bytes_downloaded": job.bytes_downloaded,
        "bytes_total": job.bytes_total,
    }


@router.post("/api/local-models/snapshot-installs")
async def local_models_snapshot_install(body: dict):
    """Start a managed Hugging Face snapshot install into AI_Models."""
    repo_id = (body.get("repo_id") or "").strip()
    target_path = (body.get("target_path") or "").strip()
    if not repo_id or not target_path:
        raise HTTPException(
            status_code=400,
            detail="Both `repo_id` and `target_path` are required.",
        )
    _validate_huggingface_repo_id(repo_id)

    model_dir = _configured_model_dir()
    if model_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Model directory not found. Configure DEEPER_NOTEBOOK_MODEL_DIR.",
        )

    try:
        resolved_model_dir = model_dir.resolve()
        resolved_target = Path(target_path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not resolve target path: {exc}",
        ) from exc

    if (
        resolved_target != resolved_model_dir
        and resolved_model_dir not in resolved_target.parents
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Target path must be inside the configured model directory "
                f"({resolved_model_dir})."
            ),
        )
    if resolved_target.exists() and not resolved_target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Target path exists but is not a directory: {resolved_target}",
        )

    job = await start_snapshot_install(repo_id, resolved_target)
    return _snapshot_install_job_to_dict(job)


@router.get("/api/local-models/snapshot-installs")
async def local_models_snapshot_installs_list():
    model_dir = _configured_model_dir()
    if model_dir is not None:
        await reconcile_snapshot_installs(model_dir)
    return {
        "snapshot_installs": [
            _snapshot_install_job_to_dict(job) for job in list_snapshot_installs()
        ]
    }


@router.get("/api/local-models/snapshot-installs/{job_id}")
async def local_models_snapshot_install_status(job_id: str):
    job = get_snapshot_install(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown snapshot install job {job_id!r}.",
        )
    return _snapshot_install_job_to_dict(job)


@router.post("/api/local-models/snapshot-installs/{job_id}/cancel")
async def local_models_snapshot_install_cancel(job_id: str):
    job = get_snapshot_install(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown snapshot install job {job_id!r}.",
        )
    ok, detail = cancel_snapshot_install(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail=detail)
    return {"ok": True, "detail": detail}


@router.post("/api/local-models/downloads/{job_id}/cancel")
async def local_models_download_cancel(job_id: str):
    """v0.8.39e — Request cancellation of an in-flight GGUF download.

    Sets a flag the streaming task checks between 1 MiB chunks. The
    job transitions to `status="cancelled"` and the `.part` file stays
    on disk — a subsequent `POST /local-models/download` for the same
    `(repo_id, filename)` automatically resumes via HTTP Range (see
    v0.8.39e in `_stream_download`).

    Returns:
      200 + `{ok: true, detail: ...}` — cancellation flag set.
      404 if `job_id` is unknown (typo, or job from a previous API
          process; in-memory registry).
      409 if the job is already in a terminal state (completed /
          failed / cancelled) — caller should poll for current status
          rather than retry.
    """
    from deeper_notebook.local_models import cancel_job, get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown download job {job_id!r}.",
        )
    ok, detail = cancel_job(job_id)
    if not ok:
        # Already terminal — 409 Conflict so the client doesn't keep
        # retrying. Status is in the detail string.
        raise HTTPException(status_code=409, detail=detail)
    return {"ok": True, "detail": detail}


@router.get("/api/local-models/downloads")
async def local_models_downloads_list():
    """v0.8.39d — List all known download jobs, reconciling against
    on-disk `.part.meta` sidecars first.

    On a fresh API process the in-memory job registry is empty, but a
    download interrupted by the restart left a `.part` file + sidecar
    on disk. `reconcile_jobs` rebuilds those as `cancelled` (resumable)
    jobs so the frontend can proactively show a "Resume" affordance on
    the Local Models page after a restart — instead of the user having
    to rediscover which model was mid-download.

    Response: `{ "downloads": [ {job_id, status, repo_id, filename,
    target_path, bytes_downloaded, bytes_total, error}, ... ] }`.
    """
    from pathlib import Path as _Path

    from deeper_notebook.local_models import list_jobs, reconcile_jobs

    raw = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw = str(_Path(home) / "Desktop" / "AI_Models") if home else ""

    if raw:
        # Reconcile is async (holds the registry lock); cheap glob + a
        # few file stats, but keep it off any sync path.
        await reconcile_jobs(_Path(raw))

    return {
        "downloads": [
            {
                "job_id": j.job_id,
                "status": j.status,
                "repo_id": j.repo_id,
                "filename": j.filename,
                "target_path": j.target_path,
                "bytes_downloaded": j.bytes_downloaded,
                "bytes_total": j.bytes_total,
                "error": j.error,
            }
            for j in list_jobs()
        ]
    }


@router.get("/api/local-models/downloads/{job_id}")
async def local_models_download_status(job_id: str):
    """v0.8.39b — Poll a download's progress.

    Response: `{job_id, status, repo_id, filename, target_path,
    bytes_downloaded, bytes_total, error}`. `status` cycles through
    queued → downloading → (completed | failed | cancelled).

    404 if the job_id is unknown. After an API restart the in-memory
    registry is empty, but `GET /local-models/downloads` (v0.8.39d)
    reconciles interrupted downloads from disk sidecars first — call
    that to repopulate, then poll individual job IDs.
    """
    from deeper_notebook.local_models import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown download job {job_id!r}.",
        )
    return {
        "job_id": job.job_id,
        "status": job.status,
        "repo_id": job.repo_id,
        "filename": job.filename,
        "target_path": job.target_path,
        "bytes_downloaded": job.bytes_downloaded,
        "bytes_total": job.bytes_total,
        "error": job.error,
    }


@router.get("/api/healthz/sidecars/{kind}/log")
async def sidecar_log(kind: str):
    """v0.8.38 — Return the most recent stderr tail for a local sidecar
    plus a user-friendly classified hint (e.g. "Model file not found")
    derived from the tail content.

    Shape:
      { "kind": "chat",
        "log": "<last ~50 lines, possibly empty>",
        "hint": "Out of memory — try a smaller model" | null,
        "available": true|false }

    `available: false` means we couldn't find a tail file — either the
    launcher hasn't run yet, DEEPER_NOTEBOOK_LAUNCHER_LOG_DIR isn't set
    (the API is running outside the desktop launcher), or this
    sidecar kind never spawned (`embed` on a CPU-only install).

    Used by `LocalModelHealthBadges` popover when a user clicks a red
    badge. Path-traversal-safe: `kind` is validated against a fixed
    allowlist (_KIND_TO_SUPERVISOR) BEFORE composing the filename, so
    "../etc/passwd" can't escape the log dir even if a misconfigured
    proxy strips path-cleaning.
    """
    if kind not in _KIND_TO_SUPERVISOR:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown sidecar kind {kind!r}. Expected one of: "
                f"{', '.join(sorted(_KIND_TO_SUPERVISOR))}."
            ),
        )

    log_dir_str = resolve_env("DEEPER_NOTEBOOK_LAUNCHER_LOG_DIR", "").strip()
    if not log_dir_str:
        # API running standalone (no launcher) — no logs to surface.
        return {"kind": kind, "log": "", "hint": None, "available": False}

    log_dir = Path(log_dir_str)
    tail_path = log_dir / f"{_KIND_TO_SUPERVISOR[kind]}.tail"

    if not tail_path.exists():
        return {"kind": kind, "log": "", "hint": None, "available": False}

    # The tail file is small (≤ 50 lines), but the read is sync; push
    # to a worker thread to keep the event loop snappy in case the
    # filesystem is slow.
    def _read_tail() -> str:
        try:
            data = tail_path.read_bytes()
        except OSError:
            return ""
        # Defensive cap — never return more than 8 KiB even if the file
        # somehow grew past the deque's maxlen (e.g. user manually
        # edited it).
        if len(data) > 8 * 1024:
            data = data[-8 * 1024 :]
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    log_text = await asyncio.to_thread(_read_tail)

    # Classify on the API side so the frontend doesn't have to ship
    # the pattern list. Falls back to None when no pattern matches —
    # UI then renders just the raw tail.
    from deeper_notebook.utils.error_classifier import classify_sidecar_error

    hint = classify_sidecar_error(log_text)

    return {"kind": kind, "log": log_text, "hint": hint, "available": True}


@router.post("/api/local-models/set-active")
async def local_models_set_active(body: dict):
    """v0.8.40b — Hot-swap the active chat GGUF without restarting
    the app.

    Body: `{"path": "/abs/path/to/model.gguf"}` — typically lifted
    from a row in `GET /local-models/inventory`.

    Response: `{ok: bool, path: str, detail: str}`.

    Validation:
      - File must exist + be a regular file + end in `.gguf`.
      - File must live under the configured model_dir (path-traversal
        defense at the API edge AND again at the launcher edge —
        belt-and-braces matching the v0.8.39b download endpoint).

    Failure modes (mirror sidecar_restart):
      - 400 if validation fails or the launcher rejects the swap
        (sidecar never spawned, file gone between API check and
        launcher check, etc).
      - 503 if launcher control plane isn't configured.
      - 502 if launcher control plane is unreachable / returns 5xx.
    """
    from pathlib import Path as _Path

    new_path = (body.get("path") or "").strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="Body must include `path`.")

    p = _Path(new_path)
    if not p.exists():
        raise HTTPException(
            status_code=400,
            detail=f"File not found: {new_path}",
        )
    is_gguf = p.is_file() and p.suffix.lower() == ".gguf"
    is_mlx = p.is_dir() and (p / "config.json").is_file()
    if not is_gguf and not is_mlx:
        raise HTTPException(
            status_code=400,
            detail="`path` must point to a `.gguf` file or an MLX model directory.",
        )

    # Resolve the configured model dir using the same precedence as
    # the inventory + download endpoints — keeps the three in sync.
    raw_dir = (
        resolve_env("DEEPER_NOTEBOOK_MODEL_DIR")
        or resolve_env("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT")
        or ""
    ).strip()
    if not raw_dir:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        raw_dir = str(_Path(home) / "Desktop" / "AI_Models") if home else ""
    if not raw_dir:
        raise HTTPException(
            status_code=500,
            detail="No model directory configured. Set DEEPER_NOTEBOOK_MODEL_DIR.",
        )
    model_dir = _Path(raw_dir).resolve()
    # Path-traversal guard at the API edge. The launcher does the
    # same check independently (defense-in-depth), but failing here
    # gives a faster 400 + no network hop.
    try:
        resolved = p.resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="Could not resolve path.")
    if model_dir not in resolved.parents and resolved.parent != model_dir and resolved != model_dir:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path must be inside the configured model directory ({model_dir})."
            ),
        )

    # Reuse the same control-plane proxy machinery as the restart
    # endpoint.
    control_url = resolve_env("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", "").strip()
    control_token = resolve_env("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", "").strip()
    if not control_url or not control_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "Launcher control plane not available. The API is running "
                "outside the desktop launcher, or the launcher could not "
                "bind its control port. Quit and relaunch the app."
            ),
        )

    import httpx as _httpx

    # Generous read timeout — hot-swap kills + respawns the chat
    # sidecar, which mmap's a multi-GB GGUF. 60s upper bound for
    # cold-disk loads of large quants.
    timeout = _httpx.Timeout(connect=2.0, read=60.0, write=5.0, pool=5.0)
    headers = {"Authorization": f"Bearer {control_token}"}

    def _call_launcher() -> tuple[int, dict]:
        with _httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{control_url}/hot_swap_chat",
                json={"path": str(resolved)},
                headers=headers,
            )
            try:
                body = resp.json()
            except Exception:
                body = {"ok": False, "detail": resp.text[:500]}
            return resp.status_code, body

    try:
        status_code, lbody = await asyncio.to_thread(_call_launcher)
    except _httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Launcher control plane unreachable "
                f"({exc.__class__.__name__}). Relaunch the app."
            ),
        )

    if status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail=lbody.get("error")
            or lbody.get("detail")
            or f"Launcher returned HTTP {status_code}",
        )
    if status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=lbody.get("error")
            or lbody.get("detail")
            or f"Launcher returned HTTP {status_code}",
        )
    if lbody.get("ok", False):
        active_key = (
            "DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL"
            if is_mlx
            else "DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL"
        )
        os.environ.update(
            normalize_product_environment(
                {active_key: str(resolved)}
            )
        )
    return {
        "ok": lbody.get("ok", False),
        "path": str(resolved),
        "detail": lbody.get("detail", ""),
    }


@router.post("/api/healthz/sidecars/{kind}/restart")
async def sidecar_restart(kind: str):
    """v0.8.40 — Restart a local sidecar by proxying to the launcher
    control plane (v0.8.40 `desktop/launcher_control.py:ControlServer`).

    Flow:
      1. Validate `kind` against the same allowlist
         (`_KIND_TO_SUPERVISOR`) the log endpoint uses.
      2. Read `DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL` + `_TOKEN` from env
         (set by the launcher via session_env at boot).
      3. POST `{kind}` to the launcher's `/restart_sidecar` with the
         token in the Authorization header. The launcher kills the
         old Popen's process group and re-spawns with the same args.
      4. Return the launcher's `{ok, kind, detail}` directly.

    Failure modes:
      - 404 if kind not in allowlist.
      - 503 if the control URL isn't set (API running standalone, not
        under the desktop launcher) or the launcher control server
        is unreachable (down/crashed).
      - 502 if the launcher returns a non-2xx response (bad token,
        sidecar wasn't ever spawned, kill timed out, etc.) — we
        surface the launcher's `detail` for diagnosis.

    Defense-in-depth: the path-param allowlist check happens BEFORE
    we touch the network, so a malformed `kind` can't be forwarded
    to the launcher.
    """
    if kind not in _KIND_TO_SUPERVISOR:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown sidecar kind {kind!r}. Expected one of: "
                f"{', '.join(sorted(_KIND_TO_SUPERVISOR))}."
            ),
        )

    control_url = resolve_env("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", "").strip()
    control_token = resolve_env("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", "").strip()
    if not control_url or not control_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "Launcher control plane not available. The API is "
                "running outside the desktop launcher, or the launcher "
                "could not bind its control port. Quit and relaunch the "
                "app to retry."
            ),
        )

    import httpx as _httpx

    # Short-but-realistic timeout — restarts often take 2-5s while the
    # new sidecar binds its port, but a hung launcher shouldn't tie up
    # the API request slot. 15s is generous; a real hang gets a
    # connect/read error which we map to 502 below.
    timeout = _httpx.Timeout(connect=2.0, read=15.0, write=5.0, pool=5.0)
    headers = {"Authorization": f"Bearer {control_token}"}

    def _call_launcher() -> tuple[int, dict]:
        with _httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{control_url}/restart_sidecar",
                json={"kind": kind},
                headers=headers,
            )
            try:
                body = resp.json()
            except Exception:
                body = {"ok": False, "detail": resp.text[:500]}
            return resp.status_code, body

    try:
        status_code, body = await asyncio.to_thread(_call_launcher)
    except _httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Launcher control plane unreachable ({exc.__class__.__name__}). "
                "The launcher may have crashed; relaunch the app."
            ),
        )

    if status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail=body.get("error")
            or body.get("detail")
            or f"Launcher returned HTTP {status_code}",
        )
    # 400/401 from the launcher → bubble as 400 so the UI sees a
    # specific message ("Sidecar never spawned this session", etc).
    if status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=body.get("error")
            or body.get("detail")
            or f"Launcher returned HTTP {status_code}",
        )

    return {
        "kind": kind,
        "ok": body.get("ok", False),
        "detail": body.get("detail", ""),
    }
