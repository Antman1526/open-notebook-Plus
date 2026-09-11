"""Backward-compatible aggregate facade for the split Studio router."""

from __future__ import annotations

from fastapi.routing import APIRoute

from . import artifacts, common, exports, mind_maps, retention, revisions, workflows
from .common import router

StudioGenerateResponse = workflows.StudioGenerateResponse


_COMPONENTS = (common, artifacts, revisions, workflows, mind_maps, exports, retention)


def _export_legacy_symbols() -> set[str]:
    exported: set[str] = set()
    for component in _COMPONENTS:
        for name, value in vars(component).items():
            if name.startswith("__") or name in {"router", "sys"}:
                continue
            globals()[name] = value
            exported.add(name)
    return exported


_LEGACY_SYMBOLS = _export_legacy_symbols()

# Reloading the legacy module recomputed these environment-driven limits.
# Preserve that observable behavior even though their implementation now lives
# in the workflows component.
workflows._MAX_EXTRACT_CHARS_PER_FILE = workflows._env_int(
    "DEEPER_NOTEBOOK_STUDIO_MAX_FILE_CHARS", 15_000
)
workflows._MAX_COMBINED_CHARS = workflows._env_int(
    "DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS", 60_000
)
common._MAX_EXTRACT_CHARS_PER_FILE = workflows._MAX_EXTRACT_CHARS_PER_FILE
common._MAX_COMBINED_CHARS = workflows._MAX_COMBINED_CHARS
artifacts._MAX_EXTRACT_CHARS_PER_FILE = workflows._MAX_EXTRACT_CHARS_PER_FILE
_MAX_EXTRACT_CHARS_PER_FILE = workflows._MAX_EXTRACT_CHARS_PER_FILE
_MAX_COMBINED_CHARS = workflows._MAX_COMBINED_CHARS

# Source-readiness is shared by artifact generation and workflow approval.
# It remains next to the artifact context logic but is resolved by common's
# queue helper at request time.
common._artifact_not_ready_sources = artifacts._artifact_not_ready_sources


def _sync_legacy_patches() -> None:
    """Mirror package-level legacy patches into split endpoint globals."""
    for name in _LEGACY_SYMBOLS:
        if name not in globals():
            continue
        value = globals()[name]
        for component in _COMPONENTS:
            if name in vars(component):
                setattr(component, name, value)


StudioGenerateResponse.__module__ = __name__

# The legacy module registered endpoints in this order. Keep it stable for
# OpenAPI consumers and the committed route contract while implementations live
# in responsibility-focused modules.
_ROUTE_ORDER = (
    "create_studio_artifact",
    "list_studio_artifacts",
    "list_studio_artifact_revisions",
    "create_studio_workflow_run",
    "list_studio_workflow_runs",
    "approve_studio_workflow_run",
    "get_studio_artifact",
    "update_studio_artifact",
    "generate_studio_artifact",
    "delete_studio_artifact",
    "studio_generate",
    # v0.8.119 — single-format export regeneration.
    "regenerate_studio_artifact_export",
    # v0.8.124 — batch export of every completed artifact as one zip.
    "export_studio_artifact_bundle",
    # v0.8.125 — retention status + dry-run visibility from Settings.
    "get_studio_retention_status",
    "run_studio_retention_dry_run",
)


def _is_mind_map_child_route(route: object) -> bool:
    """Identify routes added by the child router across FastAPI versions."""
    if getattr(route, "original_router", None) is mind_maps.router:
        return True
    return (
        isinstance(route, APIRoute)
        and getattr(route.endpoint, "__module__", None) == mind_maps.__name__
    )


# This module is intentionally reloadable: compatibility tests and local
# development use package-level patches. FastAPI appends included child routes
# on every reload, so remove the prior mind-map group before adding it back.
router.routes[:] = [
    route for route in router.routes if not _is_mind_map_child_route(route)
]
router.include_router(mind_maps.router)
# FastAPI 0.116 keeps child routers lazy as an internal route group. Preserve
# that group after the legacy direct-route order instead of assuming every
# entry exposes ``name`` like APIRoute did in older FastAPI releases.
_DIRECT_ROUTES_BY_NAME = {
    route.name: route for route in router.routes if isinstance(route, APIRoute)
}
_CHILD_ROUTE_GROUPS = [
    route for route in router.routes if not isinstance(route, APIRoute)
]
router.routes[:] = [
    *[_DIRECT_ROUTES_BY_NAME[name] for name in _ROUTE_ORDER],
    *_CHILD_ROUTE_GROUPS,
]
