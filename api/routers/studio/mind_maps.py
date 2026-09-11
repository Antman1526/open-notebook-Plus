"""Server-owned actions for structured Evidence Studio mind maps.

The browser may choose a stable child-index path (``0/1/2``), but it never
chooses source ids or SVG markup.  This module resolves the current typed
document on every request so a stale visualization cannot broaden its scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from api.schemas.studio import StudioArtifactResponse
from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.exceptions import NotFoundError
from deeper_notebook.studio import artifact_generation as artifact_generation_service
from deeper_notebook.studio.payloads import parse_payload_document
from deeper_notebook.studio.schemas import MindMapDocument, MindMapNode

from .common import (
    _artifact_response,
    _LegacyPatchSyncRoute,
    _require_evidence_studio,
    _sync_artifact_generation_service_dependencies,
    normalize_artifact_id,
    normalize_notebook_id,
)

router = APIRouter(route_class=_LegacyPatchSyncRoute)

_BRANCH_ARTIFACT_TYPES = Literal[
    "report",
    "study_guide",
    "course_pack",
    "training_guide",
    "briefing",
    "faq",
    "flashcards",
    "quiz",
    "data_table",
    "mind_map",
    "timeline",
    "infographic",
    "slide_deck",
    "podcast_outline",
    "podcast_audio",
    "research_run",
]


class MindMapBranchRequest(BaseModel):
    """The notebook is explicit so accidental cross-notebook actions fail closed."""

    notebook_id: str = Field(min_length=1)


class MindMapBranchCreateRequest(MindMapBranchRequest):
    artifact_type: _BRANCH_ARTIFACT_TYPES
    title: str | None = Field(default=None, max_length=240)


class MindMapBranchContextResponse(BaseModel):
    artifact_id: str
    notebook_id: str
    node_path: str
    label: str
    relationship: str
    citations: list[str]
    source_ids: list[str]
    prompt_context: str


def _stale_node(detail: str = "Mind-map node is no longer present") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "stale_mind_map_node", "message": detail},
    )


async def _load_owned_mind_map(
    artifact_id: str, notebook_id: str
) -> tuple[StudioArtifact, MindMapDocument]:
    _require_evidence_studio()
    artifact_id = normalize_artifact_id(artifact_id)
    notebook_id = normalize_notebook_id(notebook_id)
    try:
        artifact = await StudioArtifact.get(artifact_id)
    except (KeyError, NotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Studio artifact not found"
        ) from exc

    # A 404 avoids disclosing that another notebook owns a known artifact id.
    if str(artifact.notebook_id) != notebook_id or artifact.artifact_type != "mind_map":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Studio artifact not found"
        )

    try:
        document = parse_payload_document(
            artifact.artifact_type, artifact.output_payload
        )
    except Exception as exc:  # Invalid legacy payloads are unusable for stable paths.
        raise _stale_node("Mind-map document is no longer valid") from exc
    if not isinstance(document, MindMapDocument):
        raise _stale_node("Mind-map document is no longer available")
    return artifact, document


def _resolve_child_index_path(document: MindMapDocument, node_path: str) -> MindMapNode:
    """Resolve a path against the current document, not client-provided labels."""
    parts = node_path.split("/")
    if not parts or parts[0] != "0" or any(not part.isdecimal() for part in parts):
        raise _stale_node()

    node = document.root
    for part in parts[1:]:
        index = int(part)
        if index >= len(node.children):
            raise _stale_node()
        node = node.children[index]
    return node


def _branch_citations(node: MindMapNode) -> list[str]:
    markers: list[str] = []

    def visit(current: MindMapNode) -> None:
        for marker in current.citations:
            if marker not in markers:
                markers.append(marker)
        for child in current.children:
            visit(child)

    visit(node)
    return markers


def _citation_source_ids(artifact: StudioArtifact, markers: Iterable[str]) -> list[str]:
    marker_set = set(markers)
    source_ids: list[str] = []
    for citation in artifact.citations:
        marker = str(citation.get("marker", ""))
        source_id = str(citation.get("source_id", ""))
        if marker in marker_set and source_id and source_id not in source_ids:
            source_ids.append(source_id)
    return source_ids


def _branch_context(
    artifact: StudioArtifact, node: MindMapNode, node_path: str
) -> MindMapBranchContextResponse:
    citations = _branch_citations(node)
    source_ids = _citation_source_ids(artifact, citations)
    if not citations or not source_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "mind_map_branch_without_resolved_citations",
                "message": "The selected branch has no citation-resolved source subset.",
            },
        )
    relationship = node.relationship.strip()
    prompt_context = "\n".join(
        [
            "Use only this server-resolved mind-map branch context.",
            f"Artifact ID: {artifact.id}",
            f"Node: {node.label}",
            f"Relationship: {relationship or 'not specified'}",
            f"Citations: {' '.join(citations)}",
            f"Source IDs: {', '.join(source_ids)}",
        ]
    )
    return MindMapBranchContextResponse(
        artifact_id=str(artifact.id),
        notebook_id=str(artifact.notebook_id),
        node_path=node_path,
        label=node.label,
        relationship=relationship,
        citations=citations,
        source_ids=source_ids,
        prompt_context=prompt_context,
    )


@router.post(
    "/artifacts/{artifact_id}/mind-map/branches/{node_path:path}/context",
    response_model=MindMapBranchContextResponse,
)
async def get_mind_map_branch_context(
    artifact_id: str,
    node_path: str,
    payload: MindMapBranchRequest,
) -> MindMapBranchContextResponse:
    artifact, document = await _load_owned_mind_map(artifact_id, payload.notebook_id)
    return _branch_context(
        artifact, _resolve_child_index_path(document, node_path), node_path
    )


@router.post(
    "/artifacts/{artifact_id}/mind-map/branches/{node_path:path}/artifacts",
    response_model=StudioArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mind_map_branch_artifact(
    artifact_id: str,
    node_path: str,
    payload: MindMapBranchCreateRequest,
) -> StudioArtifactResponse:
    artifact, document = await _load_owned_mind_map(artifact_id, payload.notebook_id)
    context = _branch_context(
        artifact, _resolve_child_index_path(document, node_path), node_path
    )
    title = (
        payload.title
        or f"{payload.artifact_type.replace('_', ' ').title()}: {context.label}"
    ).strip()
    child = StudioArtifact(
        notebook_id=context.notebook_id,
        artifact_type=payload.artifact_type,
        title=title,
        status="pending",
        source_ids=context.source_ids,
        prompt=(
            f"Create this from the selected mind-map branch only.\n\n"
            f"{context.prompt_context}"
        ),
    )
    await child.save()
    _sync_artifact_generation_service_dependencies()
    generated = await artifact_generation_service.generate_studio_artifact(
        str(child.id)
    )
    return _artifact_response(generated)


def _svg_for_mind_map(document: MindMapDocument) -> str:
    """Create SVG solely from trusted coordinates and escaped text primitives."""
    rows: list[tuple[MindMapNode, int, int, int | None]] = []

    def visit(node: MindMapNode, depth: int, parent_index: int | None) -> int:
        index = len(rows)
        rows.append((node, depth, index, parent_index))
        for child in node.children:
            visit(child, depth + 1, index)
        return index

    visit(document.root, 0, None)
    width = max(
        720, 220 + (max((depth for _, depth, _, _ in rows), default=0) + 1) * 260
    )
    height = max(180, 48 + len(rows) * 108)
    positions = {
        index: (48 + depth * 260, 44 + index * 108) for _, depth, index, _ in rows
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for _, _, index, parent_index in rows:
        if parent_index is None:
            continue
        x, y = positions[index]
        parent_x, parent_y = positions[parent_index]
        parts.append(
            f'<line x1="{parent_x + 190}" y1="{parent_y + 26}" x2="{x}" y2="{y + 26}" stroke="#94a3b8" stroke-width="2"/>'
        )
    for node, _, index, _ in rows:
        x, y = positions[index]
        label = escape(node.label, quote=False)
        relationship = escape(node.relationship, quote=False)
        parts.append(
            f'<rect x="{x}" y="{y}" width="190" height="52" rx="6" fill="#f8fafc" stroke="#475569"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 23}" font-family="Arial, sans-serif" font-size="13" fill="#0f172a">{label}</text>'
        )
        if relationship:
            parts.append(
                f'<text x="{x + 12}" y="{y + 41}" font-family="Arial, sans-serif" font-size="10" fill="#475569">{relationship}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


@router.get("/artifacts/{artifact_id}/mind-map.svg")
async def export_mind_map_svg(artifact_id: str, notebook_id: str) -> Response:
    _, document = await _load_owned_mind_map(artifact_id, notebook_id)
    return Response(
        content=_svg_for_mind_map(document),
        media_type="image/svg+xml",
        headers={"Content-Disposition": 'attachment; filename="mind-map.svg"'},
    )
