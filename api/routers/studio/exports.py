"""Single-format export regeneration for Studio artifacts.

v0.8.119 — POST /studio/artifacts/{artifact_id}/exports/{format} lets a
caller regenerate one export (e.g. a course pack's EPUB or PDF) without
waiting on the whole artifact to be regenerated.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, status

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
