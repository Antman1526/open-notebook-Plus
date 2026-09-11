from .common import (
    StudioArtifact,
    StudioArtifactResponse,
    _artifact_response,
    _require_evidence_studio,
    normalize_artifact_id,
    router,
)


@router.get(
    "/artifacts/{artifact_id}/revisions",
    response_model=list[StudioArtifactResponse],
)
async def list_studio_artifact_revisions(
    artifact_id: str,
) -> list[StudioArtifactResponse]:
    _require_evidence_studio()
    artifact_id = normalize_artifact_id(artifact_id)
    revisions = await StudioArtifact.get_revisions(artifact_id)
    return [_artifact_response(revision) for revision in revisions]
