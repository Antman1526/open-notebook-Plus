"""Canonical, read-only APIs over the durable vault projection."""

from __future__ import annotations

import asyncio
import re
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.schemas.vault import (
    VaultCanvasEdgeResponse,
    VaultCanvasNodeResponse,
    VaultCanvasResponse,
    VaultFileResponse,
    VaultLinkResponse,
    VaultMountCreateRequest,
    VaultMountDetail,
    VaultMountSummary,
    VaultPageResponse,
    VaultScanResponse,
    VaultTrustImportRequest,
    VaultTrustImportResponse,
    VaultTrustSummaryResponse,
)
from deeper_notebook.vault.canvas import CanvasDocumentError
from deeper_notebook.vault.repository import VaultMountCreate, VaultProjectionError
from deeper_notebook.vault.security import VaultSecurityError, approve_vault_root
from deeper_notebook.vault.trust import TrustManifestError

router = APIRouter()
_KNOWLEDGE_DOCUMENT_ID = re.compile(r"^knowledge_engine_document:[A-Za-z0-9_-]+$")
_KNOWLEDGE_BLOCK_ID = re.compile(r"^knowledge_engine_block:[A-Za-z0-9_-]+$")
_IDENTITY_ENRICHMENT_TIMEOUT_SECONDS = 0.25
_GRAPH_PODCAST_SELECTION_LIMIT = 128


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "vault_service", None)
    if service is None:
        raise _error(status.HTTP_409_CONFLICT, "vault_unavailable")
    return service


def _repository(request: Request) -> Any:
    service = _service(request)
    repository = getattr(service, "_repository", None)
    if repository is None:
        raise _error(status.HTTP_409_CONFLICT, "vault_unavailable")
    return repository


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


def _map_exception(exc: Exception) -> HTTPException:
    message = str(exc)
    if "vault_scan_in_progress" in message:
        return _error(status.HTTP_409_CONFLICT, "vault_scan_in_progress")
    if "vault_read_only" in message or isinstance(exc, PermissionError):
        return _error(status.HTTP_405_METHOD_NOT_ALLOWED, "vault_read_only")
    if isinstance(exc, CanvasDocumentError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "canvas_invalid")
    if isinstance(exc, LookupError) and "canvas_not_found" in message:
        return _error(status.HTTP_404_NOT_FOUND, "canvas_not_found")
    if isinstance(exc, VaultSecurityError) and exc.code == "changed_during_read":
        return _error(status.HTTP_409_CONFLICT, "canvas_source_changed")
    if isinstance(exc, LookupError) and "vault_note_file_not_found" in message:
        return _error(
            status.HTTP_409_CONFLICT,
            "vault_canonical_file_unavailable",
        )
    if isinstance(exc, LookupError) and (
        "vault_page_content_hash_unavailable" in message
    ):
        return _error(status.HTTP_409_CONFLICT, "vault_page_invalid")
    if isinstance(exc, VaultProjectionError):
        return _error(status.HTTP_409_CONFLICT, "vault_page_invalid")
    if isinstance(exc, LookupError):
        code = "vault_page_not_found" if "note" in str(exc) else "vault_not_found"
        return _error(status.HTTP_404_NOT_FOUND, code)
    if isinstance(exc, VaultSecurityError):
        code = (
            "vault_root_invalid"
            if exc.code in {"invalid_root", "unsafe_root"}
            else "vault_root_unapproved"
        )
        return _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT
            if code == "vault_root_invalid"
            else status.HTTP_403_FORBIDDEN,
            code,
        )
    if isinstance(exc, (TrustManifestError, ValueError)):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "vault_root_invalid")
    return _error(status.HTTP_409_CONFLICT, "vault_unavailable")


def _relative_manifest_path(value: str) -> str:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "vault_root_invalid")
    return candidate.as_posix()


def _mount_summary(mount: Any) -> VaultMountSummary:
    return VaultMountSummary(
        id=str(mount.id),
        name=mount.name,
        format_mode=mount.format_mode,
        state=mount.status,
        parent_vault_id=str(mount.parent_vault_id) if mount.parent_vault_id else None,
        watch_enabled=mount.watch_enabled,
    )


def _mount_detail(mount: Any) -> VaultMountDetail:
    return VaultMountDetail(
        **_mount_summary(mount).model_dump(), root_path=mount.root_path
    )


async def _page_identity(
    request: Request, *, legacy_note_id: str, blocks: list[dict[str, Any]]
) -> tuple[str | None, list[dict[str, Any]]]:
    """Optionally enrich a canonical page; engine failures must never block reads."""
    service = getattr(request.app.state, "knowledge_engine_service", None)
    copied_blocks = [dict(block) for block in blocks]
    if service is None:
        return None, copied_blocks
    try:
        keys = tuple(
            dict.fromkeys(
                key
                for block in copied_blocks
                if isinstance(block, dict)
                for key in [block.get("stable_source_id") or block.get("parser_id")]
                if isinstance(key, str)
            )
        )
        resolved = await asyncio.wait_for(
            service.resolve_legacy_page(legacy_note_id=legacy_note_id, block_keys=keys),
            timeout=_IDENTITY_ENRICHMENT_TIMEOUT_SECONDS,
        )
        document_id = getattr(resolved, "document_id", None)
        block_ids = getattr(resolved, "block_ids", None)
        if isinstance(resolved, dict):
            document_id = resolved.get("document_id")
            block_ids = resolved.get("block_ids")
        if (
            not isinstance(document_id, str)
            or _KNOWLEDGE_DOCUMENT_ID.fullmatch(document_id) is None
            or not isinstance(block_ids, dict)
        ):
            return None, copied_blocks
        for block in copied_blocks:
            key = block.get("stable_source_id") or block.get("parser_id")
            block_id = block_ids.get(key) if isinstance(key, str) else None
            if isinstance(block_id, str) and _KNOWLEDGE_BLOCK_ID.fullmatch(block_id):
                block["knowledge_block_id"] = block_id
        return document_id, copied_blocks
    except Exception:
        return None, copied_blocks


async def _graph_document_identities(
    request: Request,
    note_ids: list[str],
) -> dict[str, str]:
    """Read-only bounded legacy-to-unified identities for graph actions.

    A vault graph is intentionally keyed by legacy note IDs. UI actions that
    cross into the unified knowledge engine must not infer or pass source
    paths, so expose only already-projected document IDs. Enrichment is best
    effort and failure leaves the graph itself fully readable.
    """
    service = getattr(request.app.state, "knowledge_engine_service", None)
    if service is None:
        return {}
    unique_note_ids = tuple(dict.fromkeys(note_ids))[:_GRAPH_PODCAST_SELECTION_LIMIT]
    if not unique_note_ids:
        return {}
    try:
        resolved = await asyncio.wait_for(
            asyncio.gather(
                *(
                    service.resolve_legacy_page(
                        legacy_note_id=note_id,
                        block_keys=(),
                    )
                    for note_id in unique_note_ids
                ),
                return_exceptions=True,
            ),
            timeout=_IDENTITY_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except Exception:
        return {}
    identities: dict[str, str] = {}
    for note_id, item in zip(unique_note_ids, resolved, strict=True):
        if isinstance(item, Exception):
            continue
        document_id = (
            item.get("document_id")
            if isinstance(item, dict)
            else getattr(item, "document_id", None)
        )
        if isinstance(document_id, str) and _KNOWLEDGE_DOCUMENT_ID.fullmatch(
            document_id
        ):
            identities[note_id] = document_id
    return identities


@router.post(
    "/vaults", response_model=VaultMountDetail, status_code=status.HTTP_201_CREATED
)
async def create_vault(
    request: Request, payload: VaultMountCreateRequest
) -> VaultMountDetail:
    try:
        # Validate now, then close the descriptor. The service re-opens a fresh,
        # approved descriptor while watching/scanning; no route retains write access.
        with approve_vault_root(payload.path):
            pass
        mount = await _service(request).register_mount(
            VaultMountCreate(
                name=payload.name,
                root_path=payload.path,
                format_mode=payload.format_mode,
                parent_vault_id=payload.parent_vault_id,
                watch_enabled=payload.watch_enabled,
                parser_version="vault-api-v1",
            )
        )
        return _mount_detail(mount)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.post("/vaults/{vault_id}/watch/enable", response_model=VaultMountDetail)
async def enable_vault_watch(request: Request, vault_id: str) -> VaultMountDetail:
    """Enable change detection for an existing read-only mount."""
    try:
        return _mount_detail(await _service(request).enable_watch(vault_id))
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults", response_model=list[VaultMountSummary])
async def list_vaults(request: Request) -> list[VaultMountSummary]:
    try:
        return [
            _mount_summary(mount) for mount in await _repository(request).list_mounts()
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}", response_model=VaultMountDetail)
async def get_vault(request: Request, vault_id: str) -> VaultMountDetail:
    try:
        return _mount_detail(await _repository(request).get_mount(vault_id))
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.post("/vaults/{vault_id}/scan", response_model=VaultScanResponse)
async def scan_vault(request: Request, vault_id: str) -> VaultScanResponse:
    try:
        result = await _service(request).scan(vault_id)
        if result.status == "unavailable":
            raise _error(status.HTTP_409_CONFLICT, "vault_unavailable")
        if result.status == "scanning":
            raise _error(status.HTTP_409_CONFLICT, "vault_scan_in_progress")
        return VaultScanResponse(
            operation_id=result.operation_id,
            state=result.status,
            observed=result.projected + result.unchanged + result.failed,
            parsed=result.projected,
            unchanged=result.unchanged,
            unsupported=0,
            invalid=result.failed,
            missing=0,
            embeddings_pending=result.projected,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}/files", response_model=list[VaultFileResponse])
async def list_files(
    request: Request,
    vault_id: str,
    prefix: str = "",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[VaultFileResponse]:
    try:
        return [
            VaultFileResponse.model_validate(item.model_dump())
            for item in await _repository(request).list_files(
                vault_id, prefix, limit, offset
            )
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}/pages/{note_id}", response_model=VaultPageResponse)
async def get_page(request: Request, vault_id: str, note_id: str) -> VaultPageResponse:
    try:
        page = await _repository(request).get_page(vault_id, note_id)
        if re.fullmatch(r"[0-9a-fA-F]{64}", page.file.content_hash or "") is None:
            raise LookupError("vault_page_content_hash_unavailable")
        document_id, blocks = await _page_identity(
            request, legacy_note_id=note_id, blocks=page.blocks
        )
        return VaultPageResponse(
            knowledge_document_id=document_id,
            file=VaultFileResponse.model_validate(page.file.model_dump()),
            note=page.note,
            blocks=blocks,
            tasks=page.tasks,
            outgoing_links=[
                VaultLinkResponse.model_validate(item.model_dump())
                for item in page.outgoing_links
            ],
            backlinks=[
                VaultLinkResponse.model_validate(item.model_dump())
                for item in page.backlinks
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get(
    "/vaults/{vault_id}/canvases/{relative_path:path}",
    response_model=VaultCanvasResponse,
)
async def get_canvas(
    request: Request, vault_id: str, relative_path: str
) -> VaultCanvasResponse:
    try:
        result = await _service(request).read_canvas(vault_id, relative_path)
        return VaultCanvasResponse(
            file=VaultFileResponse.model_validate(result.file.model_dump()),
            source_hash=result.source_hash,
            nodes=[
                VaultCanvasNodeResponse(
                    id=node.id,
                    type=node.type,
                    x=node.x,
                    y=node.y,
                    width=node.width,
                    height=node.height,
                    text=node.text,
                    file_path=node.file_path,
                    label=node.label,
                )
                for node in result.document.nodes
            ],
            edges=[
                VaultCanvasEdgeResponse(
                    id=edge.id,
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    label=edge.label,
                )
                for edge in result.document.edges
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get(
    "/vaults/{vault_id}/pages/{note_id}/backlinks",
    response_model=list[VaultLinkResponse],
)
async def backlinks(
    request: Request, vault_id: str, note_id: str
) -> list[VaultLinkResponse]:
    try:
        return [
            VaultLinkResponse.model_validate(item.model_dump())
            for item in await _repository(request).backlinks(vault_id, note_id)
        ]
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get(
    "/vaults/{vault_id}/pages/{note_id}/outgoing",
    response_model=list[VaultLinkResponse],
)
async def outgoing(
    request: Request, vault_id: str, note_id: str
) -> list[VaultLinkResponse]:
    try:
        return [
            VaultLinkResponse.model_validate(item.model_dump())
            for item in await _repository(request).outgoing_links(vault_id, note_id)
        ]
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}/graph")
async def graph(
    request: Request,
    vault_id: str,
    center_note_id: str,
    depth: int = Query(1, ge=0, le=8),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, list[dict[str, Any]]]:
    try:
        result = await _repository(request).graph(
            vault_id, center_note_id, depth, limit
        )
        payload = result.model_dump()
        document_ids = await _graph_document_identities(
            request,
            [
                node["id"]
                for node in payload["nodes"]
                if isinstance(node.get("id"), str)
            ],
        )
        for node in payload["nodes"]:
            note_id = node.get("id")
            if isinstance(note_id, str) and note_id in document_ids:
                node["knowledge_document_id"] = document_ids[note_id]
        return payload
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}/receipts")
async def receipts(
    request: Request,
    vault_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    try:
        return [
            item.model_dump()
            for item in await _repository(request).list_receipts(
                vault_id, limit, offset
            )
        ]
    except Exception as exc:
        raise _map_exception(exc) from None


@router.post("/vaults/{vault_id}/trust/import", response_model=VaultTrustImportResponse)
async def import_trust(
    request: Request, vault_id: str, payload: VaultTrustImportRequest
) -> VaultTrustImportResponse:
    try:
        path = _relative_manifest_path(payload.manifest_relative_path)
        result = await _repository(request).import_trust_manifest(vault_id, path)
        return VaultTrustImportResponse.model_validate(result.model_dump())
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get("/vaults/{vault_id}/trust")
async def trust_records(
    request: Request,
    vault_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    try:
        return [
            item.model_dump()
            for item in await _repository(request).list_trust_records(
                vault_id, limit, offset
            )
        ]
    except Exception as exc:
        raise _map_exception(exc) from None


@router.get(
    "/vaults/{vault_id}/trust/summary", response_model=VaultTrustSummaryResponse
)
async def trust_summary(request: Request, vault_id: str) -> VaultTrustSummaryResponse:
    try:
        result = await _repository(request).trust_summary(vault_id)
        return VaultTrustSummaryResponse.model_validate(result.model_dump())
    except Exception as exc:
        raise _map_exception(exc) from None


@router.post("/vaults/{vault_id}/git/init")
async def init_git_repo(request: Request, vault_id: str) -> dict[str, Any]:
    from deeper_notebook.vault.git_sync import init_vault_git

    mount = await _repository(request).get_mount(vault_id)
    if not mount:
        raise HTTPException(status_code=404, detail="Vault not found")
    return init_vault_git(mount.approved_root)


@router.post("/vaults/{vault_id}/git/snapshot")
async def take_git_snapshot(
    request: Request, vault_id: str, message: str | None = None
) -> dict[str, Any]:
    from deeper_notebook.vault.git_sync import create_vault_snapshot

    mount = await _repository(request).get_mount(vault_id)
    if not mount:
        raise HTTPException(status_code=404, detail="Vault not found")
    return create_vault_snapshot(mount.approved_root, message=message)


@router.get("/vaults/{vault_id}/git/history")
async def read_git_history(
    request: Request, vault_id: str, limit: int = Query(15, ge=1, le=100)
) -> list[dict[str, Any]]:
    from deeper_notebook.vault.git_sync import get_vault_history

    mount = await _repository(request).get_mount(vault_id)
    if not mount:
        raise HTTPException(status_code=404, detail="Vault not found")
    return get_vault_history(mount.approved_root, limit=limit)
