"""Single-format export regeneration for Studio artifacts.

v0.8.119 — POST /studio/artifacts/{artifact_id}/exports/{format} lets a
caller regenerate one export (e.g. a course pack's EPUB or PDF) without
waiting on the whole artifact to be regenerated.

v0.8.124 — POST /studio/notebooks/{notebook_id}/exports/bundle bundles
every completed artifact's exports into one zip written to a host path,
optionally regenerating stale exports first. Desktop pattern: backend and
user share the filesystem, same as the notebook-export endpoints in
api/routers/exports.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from api.routers.exports import _COMPRESSION_BY_NAME, _check_overwrite
from api.routers.filesystem import _resolve_and_validate
from api.schemas.studio import StudioArtifactResponse
from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.exceptions import NotFoundError
from deeper_notebook.studio.generation import persistence

from .common import _artifact_response, _require_evidence_studio, router


@router.post(
    "/artifacts/{artifact_id}/exports/{format}",
    response_model=StudioArtifactResponse,
)
async def regenerate_studio_artifact_export(
    artifact_id: str,
    format: str,
) -> StudioArtifactResponse:
    _require_evidence_studio()
    try:
        artifact = await StudioArtifact.get(artifact_id)
    except (KeyError, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio artifact not found",
        )

    payload = artifact.output_payload if isinstance(artifact.output_payload, dict) else {}
    if artifact.status != "completed" or not str(payload.get("content") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "artifact_not_generated",
                "message": "This artifact has no generated content to export yet.",
            },
        )

    path = await asyncio.to_thread(
        persistence.persist_single_export, artifact, format
    )
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export format: {format!r}",
        )

    canonical_format = persistence.canonical_export_format(format)
    export_paths = dict(artifact.export_paths or {})
    export_paths[canonical_format] = path
    if format != canonical_format:
        export_paths[format] = path
    artifact.export_paths = export_paths
    await artifact.save()
    return _artifact_response(artifact)


# v0.8.124 — batch export: bundle every completed artifact in a notebook
# into one zip.
class NotebookArtifactBundleRequest(BaseModel):
    destination: str = Field(
        ...,
        description="Absolute .zip file path. User's home is auto-expanded.",
    )
    overwrite: bool = Field(
        False, description="Overwrite an existing file at `destination`."
    )
    compression: Literal["deflated", "stored", "bzip2", "lzma"] = Field(
        "deflated", description="Zip compression algorithm."
    )
    regenerate_stale: bool = Field(
        True,
        description=(
            "Regenerate any recorded export that's stale (content changed "
            "since last export) before bundling. Per-format failures are "
            "recorded as warnings rather than failing the whole export."
        ),
    )
    # v0.8.125 — include podcast episode audio/transcript alongside artifact
    # exports. v0.8.126 — episodes are now notebook-scoped (see
    # `_load_notebook_episodes`), so this actually bundles the notebook's
    # episodes rather than always zero.
    include_media: bool = Field(
        True,
        description=(
            "Include podcast episode audio and transcripts in the bundle, "
            "under podcasts/{episode_slug}/."
        ),
    )


class NotebookArtifactBundleResponse(BaseModel):
    destination: str
    file_count: int
    total_bytes: int
    artifact_count: int
    skipped: int
    warnings: list[str] = []
    media_count: int = 0


async def _regenerate_stale_exports(
    artifacts: list[StudioArtifact], warnings: list[str]
) -> None:
    """Refresh every stale, producible export on each completed artifact.
    Mutates `warnings` in place with one message per format that failed to
    regenerate or failed to save — never raises."""
    for artifact in artifacts:
        if artifact.status != "completed":
            continue
        stale_formats = persistence.get_stale_export_formats(artifact)
        if not stale_formats:
            continue
        producible = persistence.producible_export_formats(
            artifact, include_aliases=False
        )
        changed = False
        for export_format in stale_formats:
            canonical = persistence.canonical_export_format(export_format)
            if canonical not in producible:
                continue
            try:
                result = await asyncio.to_thread(
                    persistence.persist_single_export, artifact, export_format
                )
            except Exception as exc:  # noqa: BLE001 — per-format, never fatal
                warnings.append(
                    f"Could not regenerate {export_format!r} export for "
                    f"{artifact.id}: {exc}"
                )
                continue
            if result is not None:
                changed = True
        if changed:
            try:
                await artifact.save()
            except Exception as exc:  # noqa: BLE001 — never fatal
                warnings.append(
                    f"Could not save refreshed exports for {artifact.id}: {exc}"
                )


# v0.8.126 — `PodcastEpisode` now carries `notebook_id`
# (deeper_notebook/podcasts/models.py), set at generation time when a
# notebook is the source, and `PodcastEpisode.get_for_notebook()` queries
# on it directly. Delegate to that instead of the previous always-empty
# stub (v0.8.125) so the bundle's podcast branch actually bundles the
# notebook's episodes. Kept as its own function so exports.py's own
# call site doesn't need to change again if episode lookup grows more
# rules (e.g. excluding failed/in-flight episodes) later.
async def _load_notebook_episodes(notebook_id: str, warnings: list[str]) -> list[Any]:
    """Episodes for the notebook; a lookup failure is a warning, never a 500."""
    from deeper_notebook.podcasts.models import PodcastEpisode

    try:
        return await PodcastEpisode.get_for_notebook(notebook_id)
    except Exception as exc:  # noqa: BLE001 — media is optional in a bundle
        warnings.append(f"Podcast episodes could not be listed: {type(exc).__name__}")
        return []


@router.post(
    "/notebooks/{notebook_id}/exports/bundle",
    response_model=NotebookArtifactBundleResponse,
)
async def export_studio_artifact_bundle(
    notebook_id: str,
    req: NotebookArtifactBundleRequest,
) -> NotebookArtifactBundleResponse:
    _require_evidence_studio()
    artifacts = await StudioArtifact.get_for_notebook(notebook_id)
    if not artifacts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notebook {notebook_id!r} has no Studio artifacts",
        )

    target_zip = _resolve_and_validate(req.destination, must_exist=False)
    if target_zip.exists() and target_zip.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Destination is a directory; pass a .zip file path: {target_zip}"
            ),
        )
    _check_overwrite(target_zip, overwrite=req.overwrite)
    if not target_zip.parent.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Parent directory does not exist: {target_zip.parent}. "
                "Create it via POST /api/fs/mkdir first."
            ),
        )

    warnings: list[str] = []
    if req.regenerate_stale:
        await _regenerate_stale_exports(artifacts, warnings)

    episodes = await _load_notebook_episodes(notebook_id, warnings) if req.include_media else []

    zip_compression = _COMPRESSION_BY_NAME[req.compression]
    try:
        report = await asyncio.to_thread(
            persistence.write_notebook_artifact_bundle,
            artifacts,
            target_zip,
            zip_compression,
            episodes=episodes,
            include_media=req.include_media,
        )
    except OSError as exc:
        try:
            if target_zip.exists():
                target_zip.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write bundle {target_zip}: {exc}",
        )

    return NotebookArtifactBundleResponse(
        destination=str(target_zip),
        file_count=report.file_count,
        total_bytes=report.total_bytes,
        artifact_count=report.artifact_count,
        skipped=report.skipped,
        warnings=[*warnings, *report.warnings],
        media_count=report.media_count,
    )
