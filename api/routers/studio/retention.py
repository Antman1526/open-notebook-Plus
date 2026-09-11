"""v0.8.125 — Studio retention visibility: status + dry-run only.

Makes the Studio artifact retention job (`deeper_notebook.studio.retention`,
OFF by default) visible and testable from Settings without enabling it:

  * GET  /studio/retention/status    — current knobs + last-run report.
  * POST /studio/retention/dry-run   — runs the real pruning logic with
    `dry_run=True` (counts only, zero mutation) so an operator can see what
    a run *would* touch before flipping the interval env var on. Never runs
    a real prune from this endpoint, regardless of the configured
    `DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN` default.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# v0.8.125 — qualified module import, not `from ... import prune_studio_retention`.
# The legacy facade's `_sync_legacy_patches` (api/routers/studio/__init__.py)
# re-copies every exported name from each component's globals on every
# request; a bare function import here would become one of those names and
# get reset to the original on each call, silently undoing a test's
# monkeypatch. Importing the module keeps `retention_service` itself as the
# only exported name, and tests patch functions *on* that module — same
# pattern `exports.py` uses for `persistence`.
from deeper_notebook.studio import retention as retention_service

from .common import router


class RetentionStatusResponse(BaseModel):
    enabled: bool
    interval_hours: float
    revision_keep_per_artifact: int
    stale_export_max_age_days: int
    dry_run_default: bool
    last_run_at: Optional[str] = None
    last_report: Optional[dict] = None


class RetentionDryRunRequest(BaseModel):
    revision_keep_per_artifact: Optional[int] = Field(
        default=None,
        ge=0,
        description="Override the configured revision-keep count for this dry run only.",
    )
    stale_export_max_age_days: Optional[int] = Field(
        default=None,
        ge=0,
        description="Override the configured stale-export age threshold for this dry run only.",
    )


class RetentionDryRunResponse(BaseModel):
    revisions_examined: int
    revisions_deleted: int
    exports_examined: int
    exports_removed: int
    bytes_reclaimed: int
    dry_run: bool
    revision_keep_per_artifact: int
    stale_export_max_age_days: int


@router.get("/retention/status", response_model=RetentionStatusResponse)
async def get_studio_retention_status() -> RetentionStatusResponse:
    return RetentionStatusResponse(**retention_service.get_retention_status())


@router.post("/retention/dry-run", response_model=RetentionDryRunResponse)
async def run_studio_retention_dry_run(
    req: RetentionDryRunRequest,
) -> RetentionDryRunResponse:
    status_snapshot = retention_service.get_retention_status()
    keep = (
        req.revision_keep_per_artifact
        if req.revision_keep_per_artifact is not None
        else status_snapshot["revision_keep_per_artifact"]
    )
    max_age_days = (
        req.stale_export_max_age_days
        if req.stale_export_max_age_days is not None
        else status_snapshot["stale_export_max_age_days"]
    )

    report = await retention_service.prune_studio_retention(
        revision_keep_per_artifact=keep,
        stale_export_max_age_days=max_age_days,
        dry_run=True,
    )

    return RetentionDryRunResponse(
        **report.to_dict(),
        revision_keep_per_artifact=keep,
        stale_export_max_age_days=max_age_days,
    )
