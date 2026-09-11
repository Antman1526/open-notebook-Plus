import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from loguru import logger
from surreal_commands import execute_command_sync, submit_command

from api.command_service import CommandService
from api.models import (
    AssetModel,
    CreateSourceInsightRequest,
    InsightCreationResponse,
    LocatePassageRequest,
    LocatePassageResponse,
    SourceCreate,
    SourceInsightResponse,
    SourceListResponse,
    SourceResponse,
    SourceStatusResponse,
    SourceUpdate,
)
from api.schemas.source_visuals import disabled_visual_status
from api.source_visual_projection import project_source_visuals
from api.utils.iso import iso  # v0.7.181 — Safari-safe datetime serialization

# NOTE (v0.8.100): this import pulls `commands/__init__.py`, which eagerly
# imports every command module (podcast_creator, transformers, content_core).
# In isolation that is ~16.7 s, so it looks like the obvious startup win — it
# is not. Measured A/B on `import api.main`, median of three runs:
#   with this import 3.14 s | stubbed out 3.10 s
# No gain: everything heavy it pulls is already imported by other API modules,
# and run-to-run variance (+/-0.7 s) is larger than the difference. Deferring it
# would move a Pydantic model between packages and risk the command input
# schema for nothing. Leave it.
from commands.source_commands import SourceProcessingInput, process_source_command
from deeper_notebook.config import UPLOADS_FOLDER
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import Asset, Notebook, Source
from deeper_notebook.domain.transformation import Transformation
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import InvalidInputError, NotFoundError
from deeper_notebook.feature_flags import source_visuals_enabled
from deeper_notebook.identity import LEGACY_COMMAND_APP

router = APIRouter()


# v0.7.16 — upload byte cap for /api/sources. Without this, the only
# protection was the Next.js reverse-proxy limit (proxyClientMaxBodySize
# in next.config.ts). Authenticated users hitting the API directly
# (Docker / scripted clients / pywebview shell) had no ceiling and
# could fill the local disk on a runaway upload. Studio router got
# its cap in v0.7.1; this is the same pattern applied to the main
# source endpoint.
#
# Default 500 MB. Local-deploy disks vary widely; a power user with a
# terabyte volume can raise the cap. The minimum (1 MB) guards typo'd
# values like "100" that would reject every legitimate upload.
_SOURCE_UPLOAD_MAX_BYTES_DEFAULT = 500 * 1024 * 1024
_SOURCE_UPLOAD_MIN_BYTES = 1024 * 1024
_LOW_EXTRACTED_TEXT_CHARS = 200


def _source_upload_max_bytes() -> int:
    """Resolve the upload cap from DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES.

    Defensive parsing: garbage or below-minimum values fall back to the
    default with a logged warning. Returns the active cap in bytes.
    """
    raw = resolve_env("DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES")
    if raw is None:
        return _SOURCE_UPLOAD_MAX_BYTES_DEFAULT
    try:
        val = int(raw)
        if val < _SOURCE_UPLOAD_MIN_BYTES:
            logger.warning(
                f"DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES={raw} is below minimum "
                f"{_SOURCE_UPLOAD_MIN_BYTES}; using default "
                f"{_SOURCE_UPLOAD_MAX_BYTES_DEFAULT}"
            )
            return _SOURCE_UPLOAD_MAX_BYTES_DEFAULT
        return val
    except ValueError:
        logger.warning(
            f"DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES={raw!r} is not an int; using "
            f"default {_SOURCE_UPLOAD_MAX_BYTES_DEFAULT}"
        )
        return _SOURCE_UPLOAD_MAX_BYTES_DEFAULT


_SUMMARY_PREVIEW_CHARS = 140


def _summary_preview(content: Optional[str]) -> Optional[str]:
    """v0.8.88 — collapse the auto-summary insight to a one-line card preview."""
    if not content:
        return None
    text = " ".join(str(content).split())
    if not text:
        return None
    if len(text) <= _SUMMARY_PREVIEW_CHARS:
        return text
    return text[: _SUMMARY_PREVIEW_CHARS - 1].rstrip() + "…"


def _extraction_quality(
    extracted_char_count: int | None,
    *,
    status: str | None = None,
) -> str | None:
    if status in {"new", "queued", "running"}:
        return "pending"
    if extracted_char_count is None:
        return None
    if extracted_char_count <= 0:
        return "no_text"
    if extracted_char_count < _LOW_EXTRACTED_TEXT_CHARS:
        return "low_text"
    return "ok"


def _dedupe_strings(values: list[str] | None) -> list[str]:
    deduped: list[str] = []
    for value in values or []:
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _normalized_source_type(source_type: str | None) -> str | None:
    if source_type in {"link", "upload", "text", "web_import", "deep_research_report"}:
        return source_type
    return None


def get_source_type_from_asset(asset: dict[str, Any] | None) -> str:
    if asset and asset.get("url"):
        return "link"
    if asset and asset.get("file_path"):
        return "upload"
    return "text"


def _default_origin_for_type(source_type: str) -> str:
    return {
        "link": "manual_link",
        "upload": "manual_upload",
        "text": "manual_text",
    }.get(source_type, "manual")


def _source_provenance_for_create(
    source_data: SourceCreate,
    *,
    file_path: str | None = None,
    upload_file: UploadFile | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = dict(source_data.provenance or {})
    provenance.setdefault("origin", _default_origin_for_type(source_data.type))

    if source_data.type == "link" and source_data.url:
        parsed = urlparse(source_data.url)
        provenance.setdefault("url", source_data.url)
        if parsed.netloc:
            provenance.setdefault("domain", parsed.netloc)
    elif source_data.type == "upload":
        final_file_path = file_path or source_data.file_path
        if upload_file and upload_file.filename:
            provenance.setdefault("original_filename", upload_file.filename)
        if upload_file and upload_file.content_type:
            provenance.setdefault("content_type", upload_file.content_type)
        if final_file_path:
            provenance.setdefault("file_name", Path(final_file_path).name)
            try:
                provenance.setdefault(
                    "size_bytes", Path(final_file_path).stat().st_size
                )
            except OSError:
                pass
    elif source_data.type == "text":
        if source_data.content:
            provenance.setdefault("character_count", len(source_data.content))

    return provenance


async def _source_notebook_count(source_id: str) -> int:
    try:
        rows = await repo_query(
            "SELECT VALUE count() FROM reference WHERE in = $source_id GROUP ALL",
            {"source_id": ensure_record_id(source_id)},
        )
        if not rows:
            return 0
        first = rows[0]
        if isinstance(first, dict):
            return int(first.get("count") or 0)
        return int(first or 0)
    except Exception as exc:
        logger.warning("Failed to count notebooks for source {}: {}", source_id, exc)
        return 0


def generate_unique_filename(original_filename: str, upload_folder: str) -> str:
    """Generate unique filename like Streamlit app (append counter if file exists)."""
    file_path = Path(upload_folder)
    file_path.mkdir(parents=True, exist_ok=True)

    # Strip directory components to prevent path traversal
    safe_filename = os.path.basename(original_filename)
    if not safe_filename:
        raise ValueError("Invalid filename")

    # Split filename and extension
    stem = Path(safe_filename).stem
    suffix = Path(safe_filename).suffix

    # Check if file exists and generate unique name
    counter = 0
    while True:
        if counter == 0:
            new_filename = safe_filename
        else:
            new_filename = f"{stem} ({counter}){suffix}"

        full_path = file_path / new_filename
        # Verify resolved path stays within upload folder
        resolved = full_path.resolve()
        if not str(resolved).startswith(str(file_path.resolve()) + os.sep):
            raise ValueError("Invalid filename: path traversal detected")
        if not resolved.exists():
            return str(resolved)
        counter += 1


async def save_uploaded_file(
    upload_file: UploadFile,
    max_bytes: Optional[int] = None,
) -> str:
    """Save uploaded file to uploads folder and return file path.

    v0.6.16 — streamed in 1 MiB chunks instead of `await upload_file.read()`
    which buffered the ENTIRE upload into Python memory before writing. With
    Next.js's 100 MB proxy limit (frontend/next.config.ts), that meant up to
    100 MB of RAM per concurrent upload through the proxy, plus an
    additional spike for the `f.write(content)` syscall. Direct API hits
    (Docker / scripted) had no FastAPI body-size limit at all and could OOM
    the worker on a multi-GB file.

    Chunked streaming keeps memory bounded to the chunk size regardless of
    upload total — same memory footprint for 1 MB or 10 GB.

    v0.7.1 — `max_bytes` closes a DoS vector. The Studio router's per-file
    size check used `getattr(f, "size", None)` which is None for HTTP
    requests sent with chunked transfer encoding (no Content-Length).
    A malicious authenticated client could bypass that pre-check and
    stream arbitrarily large files to disk. Now we count bytes as we
    stream and abort mid-write when the cap is exceeded; the existing
    except branch cleans up the partial file.
    """
    if not upload_file.filename:
        raise ValueError("No filename provided")

    _CHUNK = 1024 * 1024  # 1 MiB

    def reserve_upload_path() -> tuple[str, Any]:
        while True:
            candidate = generate_unique_filename(upload_file.filename, UPLOADS_FOLDER)
            try:
                return candidate, open(candidate, "xb")
            except FileExistsError:
                logger.warning(
                    "Upload filename collision while reserving {}; retrying",
                    candidate,
                )

    file_path, f = reserve_upload_path()

    try:
        # Stream chunks straight to disk. The sync open()/write() is fine
        # inside the async handler — each chunk write is microseconds and the
        # await on upload_file.read() yields control back to the loop
        # between chunks.
        written = 0
        with f:
            while True:
                chunk = await upload_file.read(_CHUNK)
                if not chunk:
                    break
                # v0.7.1 — enforce the cap BEFORE writing the chunk so
                # even one chunk past the threshold is rejected.
                if max_bytes is not None and written + len(chunk) > max_bytes:
                    raise ValueError(
                        f"Upload exceeds size limit "
                        f"({max_bytes} bytes); aborted after "
                        f"writing {written} bytes"
                    )
                f.write(chunk)
                written += len(chunk)

        logger.info(f"Saved uploaded file to: {file_path}")
        return file_path
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        # Clean up partial file if it exists
        if os.path.exists(file_path):
            os.unlink(file_path)
        raise


def parse_source_form_data(
    type: str = Form(...),
    notebook_id: Optional[str] = Form(None),
    notebooks: Optional[str] = Form(None),  # JSON string of notebook IDs
    url: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    topics: Optional[str] = Form(None),  # JSON string of source labels
    provenance: Optional[str] = Form(None),  # JSON object with source provenance
    source_type: Optional[str] = Form(None),
    transformations: Optional[str] = Form(None),  # JSON string of transformation IDs
    # v0.7.208 — was Form("false"). A user-visible asymmetry:
    # the frontend's AddSourceDialog defaults `embed=true` (when
    # `default_embedding_option` is "always" or "ask", which is
    # the user-facing default), but the backend Form default was
    # "false". API consumers using curl / direct scripts therefore
    # got an UPLOAD-BUT-DON'T-EMBED behaviour even though every
    # part of the UI assumed sources are searchable after upload.
    # Symptom: a curl upload completed with `embedded=false`,
    # `embedded_chunks=0`, and `status=completed` — looked
    # successful but the source was invisible to vector search.
    # Flip the default to "true" so the API matches user
    # expectation; explicit `-F embed=false` still works for the
    # rare ingest-only flow.
    embed: str = Form("true"),  # Accept as string, convert to bool
    delete_source: str = Form("false"),  # Accept as string, convert to bool
    # Match the Add Source wizard and frontend helper: imports should queue
    # background processing unless the caller explicitly opts into the legacy
    # synchronous path with `async_processing=false`.
    async_processing: str = Form("true"),  # Accept as string, convert to bool
    file: Optional[UploadFile] = File(None),
) -> tuple[SourceCreate, Optional[UploadFile]]:
    """Parse form data into SourceCreate model and return upload file separately."""
    import json

    # Convert string booleans to actual booleans
    def str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "on")

    embed_bool = str_to_bool(embed)
    delete_source_bool = str_to_bool(delete_source)
    async_processing_bool = str_to_bool(async_processing)

    def parse_json_string_list(raw: Optional[str], field_name: str) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid JSON in {field_name} field: {raw}")
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a JSON array of strings",
            ) from exc
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) and item.strip() for item in parsed
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a JSON array of strings",
            )
        return parsed

    def dedupe_strings(values: list[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    notebooks_list = parse_json_string_list(notebooks, "notebooks")
    transformations_list = parse_json_string_list(
        transformations,
        "transformations",
    )
    topics_list = parse_json_string_list(topics, "topics")

    def parse_json_object(raw: Optional[str], field_name: str) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid JSON in {field_name} field: {raw}")
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a JSON object",
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a JSON object",
            )
        return parsed

    provenance_obj = parse_json_object(provenance, "provenance")
    normalized_notebooks = dedupe_strings(
        ([notebook_id] if notebook_id else []) + notebooks_list
    )

    # Create SourceCreate instance
    try:
        source_data = SourceCreate(
            type=type,
            notebook_id=None,
            notebooks=normalized_notebooks,
            url=url,
            content=content,
            title=title,
            topics=_dedupe_strings(topics_list),
            provenance=provenance_obj,
            source_type=_normalized_source_type(source_type),
            file_path=None,  # Will be set later if file is uploaded
            transformations=transformations_list,
            embed=embed_bool,
            delete_source=delete_source_bool,
            async_processing=async_processing_bool,
        )
        pass  # SourceCreate instance created successfully
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except Exception as e:
        logger.error(f"Failed to create SourceCreate instance: {e}")
        raise

    return source_data, file


def _source_notebook_ids(source_data: SourceCreate) -> list[str]:
    """Normalize legacy notebook_id and multi-notebook form fields."""
    notebook_ids: list[str] = []

    if source_data.notebook_id:
        notebook_ids.append(source_data.notebook_id)

    for notebook_id in source_data.notebooks or []:
        if notebook_id not in notebook_ids:
            notebook_ids.append(notebook_id)

    return notebook_ids


@router.get("/sources", response_model=list[SourceListResponse])
async def get_sources(
    notebook_id: Optional[str] = Query(None, description="Filter by notebook ID"),
    label: Optional[str] = Query(None, description="Filter by source label"),
    source_type: Optional[str] = Query(
        None, description="Filter by normalized source type"
    ),
    origin: Optional[str] = Query(
        None, description="Filter by source provenance origin"
    ),
    limit: int = Query(
        50, ge=1, le=100, description="Number of sources to return (1-100)"
    ),
    offset: int = Query(0, ge=0, description="Number of sources to skip"),
    sort_by: str = Query(
        "updated", description="Field to sort by (created or updated)"
    ),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
):
    """Get sources with pagination and sorting support."""
    try:
        # Validate sort parameters
        if sort_by not in ["created", "updated"]:
            raise HTTPException(
                status_code=400, detail="sort_by must be 'created' or 'updated'"
            )
        if sort_order.lower() not in ["asc", "desc"]:
            raise HTTPException(
                status_code=400, detail="sort_order must be 'asc' or 'desc'"
            )
        normalized_filter_type = _normalized_source_type(source_type)
        if source_type and not normalized_filter_type:
            raise HTTPException(
                status_code=400,
                detail=(
                    "source_type must be one of link, upload, text, "
                    "web_import, or deep_research_report"
                ),
            )

        # Build ORDER BY clause
        order_clause = f"ORDER BY {sort_by} {sort_order.upper()}"

        # Build the query
        if notebook_id:
            # Verify notebook exists first
            try:
                notebook = await Notebook.get(notebook_id)
            except NotFoundError:
                raise HTTPException(status_code=404, detail="Notebook not found")
            if not notebook:
                raise HTTPException(status_code=404, detail="Notebook not found")

            # Query sources for specific notebook - include command field with FETCH  # nosec B608
            # v0.8.125 — `full_text ?? ""`: a source whose processing never ran
            # (worker down, reaped command) has full_text = NONE, and
            # string::len(NONE) makes the whole list 500. Found live.
            query = f"""
                SELECT id, asset, created, title, updated, topics, provenance,
                source_type, command,
                string::len(full_text ?? "") AS extracted_char_count,
                (SELECT VALUE count() FROM source_insight WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS insights_count,
                (SELECT VALUE count() FROM reference WHERE in = $parent.id GROUP ALL)[0].count OR 0 AS notebook_count,
                (SELECT VALUE count() FROM source_embedding WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS embedded_chunks,
                (SELECT VALUE content FROM source_insight WHERE source = $parent.id AND insight_type = 'Summary' LIMIT 1)[0] AS summary_preview
                FROM (select value in from reference where out=$notebook_id)
                {order_clause}
                LIMIT $limit START $offset
                FETCH command
            """  # nosec B608
            result = await repo_query(
                query,
                {
                    "notebook_id": ensure_record_id(notebook_id),
                    "limit": limit,
                    "offset": offset,
                },
            )
        else:
            # Query all sources - include command field with FETCH  # nosec B608
            query = f"""
                SELECT id, asset, created, title, updated, topics, provenance,
                source_type, command,
                string::len(full_text ?? "") AS extracted_char_count,
                (SELECT VALUE count() FROM source_insight WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS insights_count,
                (SELECT VALUE count() FROM reference WHERE in = $parent.id GROUP ALL)[0].count OR 0 AS notebook_count,
                (SELECT VALUE count() FROM source_embedding WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS embedded_chunks,
                (SELECT VALUE content FROM source_insight WHERE source = $parent.id AND insight_type = 'Summary' LIMIT 1)[0] AS summary_preview
                FROM source
                {order_clause}
                LIMIT $limit START $offset
                FETCH command
            """  # nosec B608
            result = await repo_query(query, {"limit": limit, "offset": offset})

        # Convert result to response model
        # Command data is already fetched via FETCH command clause
        response_list = []
        for row in result:
            row_topics = row.get("topics") or []
            row_provenance = row.get("provenance") or {}
            row_source_type = row.get("source_type") or get_source_type_from_asset(
                row.get("asset")
            )
            if label and label not in row_topics:
                continue
            if normalized_filter_type and row_source_type != normalized_filter_type:
                continue
            if origin and row_provenance.get("origin") != origin:
                continue
            notebook_count = row.get("notebook_count", 0) or 0

            command = row.get("command")
            command_id = None
            status = None
            processing_info = None
            row_asset = row.get("asset")
            asset_file_path = row_asset.get("file_path") if row_asset else None
            asset_url = row_asset.get("url") if row_asset else None
            file_available = (
                _is_source_file_available(
                    Source(asset=Asset(file_path=asset_file_path, url=asset_url))
                )
                if asset_file_path
                else None
            )

            # Extract status from fetched command object (already resolved by FETCH)
            if command and isinstance(command, dict):
                command_id = str(command.get("id")) if command.get("id") else None
                status = command.get("status")
                # Extract execution metadata from nested result structure
                result_data = command.get("result")
                execution_metadata = (
                    result_data.get("execution_metadata", {})
                    if isinstance(result_data, dict)
                    else {}
                )
                processing_info = {
                    "status": status,
                    "started_at": execution_metadata.get("started_at"),
                    "completed_at": execution_metadata.get("completed_at"),
                    "error": command.get("error_message"),
                    "progress": command.get("progress"),
                    "result": result_data,
                }
            elif command:
                # Command exists but FETCH failed to resolve it (broken reference)
                command_id = str(command)
                status = "unknown"

            extracted_char_count = row.get("extracted_char_count")
            # v0.8.127 — a source processed in-process (v0.8.126 sync path) or
            # one predating command tracking has no command row. If text was
            # extracted, it is complete; report that instead of no status.
            # v0.8.128 — explicit processing_status takes precedence over proxy.
            explicit_status = (row_provenance or {}).get("processing_status")
            if status is None:
                if explicit_status in ("completed", "failed"):
                    status = explicit_status
                elif (extracted_char_count or 0) > 0:
                    status = "completed"
            embedded_chunks = int(row.get("embedded_chunks") or 0)

            response_list.append(
                SourceListResponse(
                    id=row["id"],
                    title=row.get("title"),
                    topics=row_topics,
                    provenance=row_provenance,
                    source_type=row_source_type,
                    notebook_count=notebook_count,
                    is_shared=notebook_count > 1,
                    asset=AssetModel(
                        file_path=asset_file_path,
                        url=asset_url,
                    )
                    if row_asset
                    else None,
                    embedded=embedded_chunks > 0,
                    embedded_chunks=embedded_chunks,
                    insights_count=row.get("insights_count", 0),
                    # v0.8.88 — one-line preview of the auto-summary insight.
                    summary_preview=_summary_preview(row.get("summary_preview")),
                    created=str(row["created"]),
                    updated=str(row["updated"]),
                    file_available=file_available,
                    extracted_char_count=extracted_char_count,
                    extraction_quality=_extraction_quality(
                        extracted_char_count,
                        status=status,
                    ),
                    # Status fields from fetched command
                    command_id=command_id,
                    status=status,
                    processing_info=processing_info,
                )
            )

        if source_visuals_enabled():
            projected = await project_source_visuals(result)
            response_list = [
                item.model_copy(
                    update={
                        "visual": projected[item.id].visual,
                        "visual_status": projected[item.id].visual_status,
                    }
                )
                if item.id in projected
                else item
                for item in response_list
            ]
        else:
            # v0.8.86 — capability sentinel: a packaged client with baked-on
            # visual flags cannot otherwise tell "feature off" from "not yet
            # extracted" (both were null/null) and rendered Refresh/Remove
            # actions that 404 against the disabled router.
            sentinel = disabled_visual_status()
            response_list = [
                item.model_copy(update={"visual_status": sentinel})
                for item in response_list
            ]
        return response_list
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching sources: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching sources")


@router.post("/sources", response_model=SourceResponse)
async def create_source(
    form_data: tuple[SourceCreate, Optional[UploadFile]] = Depends(
        parse_source_form_data
    ),
):
    """Create a new source with support for both JSON and multipart form data."""
    source_data, upload_file = form_data

    # Initialize file_path before try block so exception handlers can reference it
    file_path = None

    try:
        notebook_ids = _source_notebook_ids(source_data)

        # Verify all specified notebooks exist (backward compatibility support)
        for notebook_id in notebook_ids:
            try:
                notebook = await Notebook.get(notebook_id)
            except NotFoundError:
                raise HTTPException(
                    status_code=404, detail=f"Notebook {notebook_id} not found"
                )
            if not notebook:
                raise HTTPException(
                    status_code=404, detail=f"Notebook {notebook_id} not found"
                )

        # Handle file upload if provided
        if upload_file and source_data.type == "upload":
            # v0.7.16 — apply the same byte-cap the Studio router has had
            # since v0.7.1. Without this, an authenticated user can fill
            # the local disk via multi-GB uploads. Default 500 MB matches
            # the typical "very large PDF / dataset / book" ceiling
            # while leaving room for local-deploy disk constraints.
            # Env override: DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES.
            max_bytes = _source_upload_max_bytes()
            try:
                file_path = await save_uploaded_file(upload_file, max_bytes=max_bytes)
            except ValueError as exc:
                # ValueError from save_uploaded_file is the upload-cap
                # path — surface as 413 (Payload Too Large), not 400.
                msg = str(exc)
                if "exceeds size limit" in msg:
                    logger.warning("Source upload rejected (oversize): {}", msg)
                    raise HTTPException(status_code=413, detail=msg)
                logger.error(f"File upload failed: {exc}")
                raise HTTPException(
                    status_code=400, detail=f"File upload failed: {msg}"
                )
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception as e:
                logger.error(f"File upload failed: {e}")
                raise HTTPException(status_code=400, detail="File upload failed")

        # Prepare content_state for processing
        content_state: dict[str, Any] = {}

        if source_data.type == "link":
            if not source_data.url:
                raise HTTPException(
                    status_code=400, detail="URL is required for link type"
                )
            content_state["url"] = source_data.url
        elif source_data.type == "upload":
            # Use uploaded file path or provided file_path (backward compatibility)
            final_file_path = file_path or source_data.file_path
            if not final_file_path:
                raise HTTPException(
                    status_code=400,
                    detail="File upload or file_path is required for upload type",
                )
            # Validate file_path is within the uploads directory to prevent LFI
            uploads_resolved = Path(UPLOADS_FOLDER).resolve()
            file_resolved = Path(final_file_path).resolve()
            if not str(file_resolved).startswith(str(uploads_resolved) + os.sep):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file path: must be within the uploads directory",
                )
            content_state["file_path"] = final_file_path
            content_state["delete_source"] = source_data.delete_source
        elif source_data.type == "text":
            if not source_data.content:
                raise HTTPException(
                    status_code=400, detail="Content is required for text type"
                )
            content_state["content"] = source_data.content
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid source type. Must be link, upload, or text",
            )

        # Validate transformations exist
        transformation_ids = source_data.transformations or []
        for trans_id in transformation_ids:
            transformation = await Transformation.get(trans_id)
            if not transformation:
                raise HTTPException(
                    status_code=404, detail=f"Transformation {trans_id} not found"
                )

        source_topics = _dedupe_strings(source_data.topics)
        normalized_source_type = source_data.source_type or source_data.type
        source_provenance = _source_provenance_for_create(
            source_data,
            file_path=file_path or source_data.file_path,
            upload_file=upload_file,
        )

        # Branch based on processing mode
        if source_data.async_processing:
            # ASYNC PATH: Create source record first, then queue command
            logger.info("Using async processing path")

            # Create source record with asset - let SurrealDB generate the ID
            # Persist asset before save so it's available for retry if processing fails
            if source_data.type == "link":
                source_asset = Asset(url=source_data.url)
            elif source_data.type == "upload":
                source_asset = Asset(file_path=file_path or source_data.file_path)
            else:
                source_asset = None

            source = Source(
                title=source_data.title or "Processing...",
                topics=source_topics,
                provenance=source_provenance,
                source_type=normalized_source_type,
                asset=source_asset,
            )
            await source.save()

            # Add source to notebooks immediately so it appears in the UI
            # The source_graph will skip adding duplicates
            for notebook_id in notebook_ids:
                await source.add_to_notebook(notebook_id)

            try:
                # Import command modules to ensure they're registered
                import commands.source_commands  # noqa: F401

                # Submit command for background processing
                command_input = SourceProcessingInput(
                    source_id=str(source.id),
                    content_state=content_state,
                    notebook_ids=notebook_ids,
                    transformations=transformation_ids,
                    embed=source_data.embed,
                )

                command_id = await CommandService.submit_command_job(
                    "open_notebook",  # app name
                    "process_source",  # command name
                    command_input.model_dump(),
                )

                logger.info(f"Submitted async processing command: {command_id}")

                # Update source with command reference immediately
                # command_id already includes 'command:' prefix
                source.command = ensure_record_id(command_id)
                await source.save()

                # Return source with command info
                response_asset = (
                    AssetModel(
                        file_path=source_asset.file_path,
                        url=source_asset.url,
                    )
                    if source_asset
                    else None
                )
                return SourceResponse(
                    id=source.id or "",
                    title=source.title,
                    topics=source.topics or [],
                    provenance=_source_provenance_value(source),
                    source_type=_source_type_value(source),
                    notebook_count=len(notebook_ids),
                    is_shared=len(notebook_ids) > 1,
                    asset=response_asset,
                    full_text=None,  # Will be populated after processing
                    embedded=False,  # Will be updated after processing
                    embedded_chunks=0,
                    created=iso(source.created),
                    updated=iso(source.updated),
                    command_id=command_id,
                    status="new",
                    processing_info={"async": True, "queued": True},
                )

            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception as e:
                logger.error(f"Failed to submit async processing command: {e}")
                # Clean up source record on command submission failure
                try:
                    await source.delete()
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception:
                    pass
                # Clean up uploaded file if we created it
                if file_path and upload_file:
                    try:
                        os.unlink(file_path)
                    except HTTPException:
                        # v0.7.108 — re-raise typed HTTPExceptions so the next
                        # `except Exception` doesn't clobber them to 500.
                        raise
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=500, detail="Failed to queue processing"
                )

        else:
            # SYNC PATH: Execute synchronously using execute_command_sync
            logger.info("Using sync processing path")

            try:
                # Import command modules to ensure they're registered
                import commands.source_commands  # noqa: F401

                # Create source record - let SurrealDB generate the ID
                source = Source(
                    title=source_data.title or "Processing...",
                    topics=source_topics,
                    provenance=source_provenance,
                    source_type=normalized_source_type,
                )
                await source.save()

                # Add source to notebooks immediately so it appears in the UI
                # The source_graph will skip adding duplicates
                for notebook_id in notebook_ids:
                    await source.add_to_notebook(notebook_id)

                # v0.8.126 — call the command function in-process instead of
                # queueing it through execute_command_sync(). The "sync" path
                # has no surreal-commands-worker process servicing the
                # SurrealDB command queue, so the previous
                # asyncio.to_thread(execute_command_sync, ...) call always
                # blocked for the full timeout and then failed. The @command
                # decorator (surreal_commands.decorators.command) registers a
                # RunnableLambda-wrapped copy in the command registry but
                # returns the original coroutine function unchanged, so
                # process_source_command can be awaited directly here.
                command_input = SourceProcessingInput(
                    source_id=str(source.id),
                    content_state=content_state,
                    notebook_ids=notebook_ids,
                    transformations=transformation_ids,
                    embed=source_data.embed,
                )

                sync_timeout = float(
                    resolve_env("DEEPER_NOTEBOOK_SOURCE_SYNC_TIMEOUT_SEC", "300")
                    or 300
                )

                processing_succeeded = False
                processing_error_message: Optional[str] = None
                try:
                    output = await asyncio.wait_for(
                        process_source_command(command_input),
                        timeout=sync_timeout,
                    )
                    processing_succeeded = output.success
                    processing_error_message = output.error_message
                except asyncio.TimeoutError:
                    processing_error_message = (
                        f"Source processing timed out after {sync_timeout}s"
                    )
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as e:
                    # Any other failure (e.g. a transient error that the
                    # surreal_commands worker would previously have retried)
                    # is treated the same as a failed CommandResult — there
                    # is no worker/retry loop in this in-process path.
                    processing_error_message = str(e)

                if not processing_succeeded:
                    logger.error(f"Sync processing failed: {processing_error_message}")
                    # Clean up source record
                    try:
                        await source.delete()
                    except HTTPException:
                        # v0.7.108 — re-raise typed HTTPExceptions so the next
                        # `except Exception` doesn't clobber them to 500.
                        raise
                    except Exception:
                        try:
                            source.provenance = {
                                **(getattr(source, "provenance", None) or {}),
                                "processing_status": "failed",
                                "processing_error": processing_error_message,
                            }
                            await source.save()
                        except Exception:
                            pass
                    # Clean up uploaded file if we created it
                    if file_path and upload_file:
                        try:
                            os.unlink(file_path)
                        except HTTPException:
                            # v0.7.108 — re-raise typed HTTPExceptions so the next
                            # `except Exception` doesn't clobber them to 500.
                            raise
                        except Exception:
                            pass
                    # v0.7.184 — Don't echo the error message to the
                    # client. Worker error messages can carry SurrealDB
                    # driver frames, file paths, partial RecordIDs —
                    # same info-leak class the v0.7.168/177 podcast_service
                    # sweep closed. logger captures the full picture; the
                    # client gets a generic message. Backend audit #3.
                    logger.error(
                        "Sync source processing failed for source {}: {}",
                        source.id,
                        processing_error_message,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Source processing failed",
                    )

                # Get the processed source
                if not source.id:
                    raise HTTPException(status_code=500, detail="Source ID is missing")
                processed_source = await Source.get(source.id)
                if not processed_source:
                    raise HTTPException(
                        status_code=500, detail="Processed source not found"
                    )

                if (getattr(processed_source, "provenance", None) or {}).get("processing_status") != "completed":
                    prov = dict(getattr(processed_source, "provenance", None) or {})
                    prov["processing_status"] = "completed"
                    processed_source.provenance = prov
                    save_fn = getattr(processed_source, "save", None)
                    if callable(save_fn):
                        res = save_fn()
                        if asyncio.iscoroutine(res):
                            await res

                embedded_chunks = await processed_source.get_embedded_chunks()
                return SourceResponse(
                    id=processed_source.id or "",
                    title=processed_source.title,
                    topics=processed_source.topics or [],
                    provenance=processed_source.provenance or {},
                    source_type=processed_source.source_type,
                    notebook_count=len(notebook_ids),
                    is_shared=len(notebook_ids) > 1,
                    asset=AssetModel(
                        file_path=processed_source.asset.file_path
                        if processed_source.asset
                        else None,
                        url=processed_source.asset.url
                        if processed_source.asset
                        else None,
                    )
                    if processed_source.asset
                    else None,
                    full_text=processed_source.full_text,
                    embedded=embedded_chunks > 0,
                    embedded_chunks=embedded_chunks,
                    created=iso(processed_source.created),
                    updated=iso(processed_source.updated),
                    # No command_id or status for sync processing (legacy behavior)
                )

            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception as e:
                logger.error(f"Sync processing failed: {e}")
                # Clean up uploaded file if we created it
                if file_path and upload_file:
                    try:
                        os.unlink(file_path)
                    except HTTPException:
                        # v0.7.108 — re-raise typed HTTPExceptions so the next
                        # `except Exception` doesn't clobber them to 500.
                        raise
                    except Exception:
                        pass
                raise

    except HTTPException:
        # Clean up uploaded file on HTTP exceptions if we created it
        if file_path and upload_file:
            try:
                os.unlink(file_path)
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception:
                pass
        raise
    except InvalidInputError as e:
        # Clean up uploaded file on validation errors if we created it
        if file_path and upload_file:
            try:
                os.unlink(file_path)
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating source: {str(e)}")
        # Clean up uploaded file on unexpected errors if we created it
        if file_path and upload_file:
            try:
                os.unlink(file_path)
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Error creating source")


@router.post("/sources/json", response_model=SourceResponse)
async def create_source_json(source_data: SourceCreate):
    """Create a new source using JSON payload (legacy endpoint for backward compatibility)."""
    # Convert to form data format and call main endpoint
    form_data = (source_data, None)
    return await create_source(form_data)


async def _resolve_source_file(source_id: str) -> tuple[str, str]:
    source = await Source.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    file_path = source.asset.file_path if source.asset else None
    if not file_path:
        raise HTTPException(status_code=404, detail="Source has no file to download")

    # v0.8.23 SECURITY — use Path.is_relative_to() so a sibling-prefix
    # attack (UPLOADS_FOLDER=/var/uploads vs file_path=/var/uploadsx/...)
    # is correctly rejected. Pre-v0.8.23 this did
    # `resolved_path.startswith(safe_root)` with no trailing separator,
    # which is the v0.6.31 / v0.6.34 / v0.7.2 sibling-prefix bug — the
    # exact pattern that was already fixed in podcasts.py
    # `_resolve_audio_path` (uses is_relative_to). The sources.py
    # helpers were missed in that pass. Vector: a tampered DB row that
    # sets source.asset.file_path to `/var/uploadsbypass/etc-passwd`
    # would pass the old check and be served by FileResponse on
    # GET /sources/{id}/download.
    safe_root = Path(UPLOADS_FOLDER).resolve()
    try:
        resolved_path = Path(file_path).resolve()
    except (OSError, ValueError):
        # Malformed path. Treat as "not found" rather than 500.
        raise HTTPException(status_code=404, detail="File not found on server")

    if not resolved_path.is_relative_to(safe_root):
        logger.warning(
            f"Blocked download outside uploads directory for source "
            f"{source_id}: {resolved_path}"
        )
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server")

    return str(resolved_path), resolved_path.name


def _is_source_file_available(source: Source) -> Optional[bool]:
    if not source or not source.asset or not source.asset.file_path:
        return None

    # v0.8.23 SECURITY — same is_relative_to() fix as _resolve_source_file
    # above. This helper feeds /sources/{id} response's `file_available`
    # field. Pre-v0.8.23 returned True for a sibling-prefix path
    # (/var/uploadsbypass/foo when UPLOADS_FOLDER=/var/uploads), telling
    # the UI the file exists — and the download endpoint would then
    # actually serve it. Same bug, two sites; this is the parallel fix.
    safe_root = Path(UPLOADS_FOLDER).resolve()
    try:
        resolved_path = Path(source.asset.file_path).resolve()
    except (OSError, ValueError):
        return False

    if not resolved_path.is_relative_to(safe_root):
        return False

    return resolved_path.exists()


def _resolve_retry_upload_file_path(file_path: str, source_id: str) -> str:
    safe_root = Path(UPLOADS_FOLDER).resolve()
    try:
        resolved_path = Path(file_path).resolve()
    except (OSError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Original uploaded file is not available for retry",
        )

    if not resolved_path.is_relative_to(safe_root):
        logger.warning(
            f"Blocked retry outside uploads directory for source "
            f"{source_id}: {resolved_path}"
        )
        raise HTTPException(status_code=403, detail="Access to source file denied")

    if not resolved_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Original uploaded file is not available for retry",
        )

    return str(resolved_path)


def _asset_payload(asset: Any | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    return {
        "file_path": getattr(asset, "file_path", None),
        "url": getattr(asset, "url", None),
    }


def _source_type_value(source: Source) -> str | None:
    raw = getattr(source, "source_type", None)
    if isinstance(raw, str):
        return raw
    return get_source_type_from_asset(_asset_payload(getattr(source, "asset", None)))


def _source_provenance_value(source: Source) -> dict[str, Any]:
    raw = getattr(source, "provenance", None)
    return raw if isinstance(raw, dict) else {}


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str):
    """Get a specific source by ID."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Get status information if command exists
        status = None
        processing_info = None
        if source.command:
            try:
                status = await source.get_status()
                processing_info = await source.get_processing_progress()
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Failed to get status for source {source_id}: {e}")
                status = "unknown"
        if status is None:
            source_prov = getattr(source, "provenance", None) or {}
            explicit_status = source_prov.get("processing_status")
            if explicit_status in ("completed", "failed"):
                status = explicit_status
            elif getattr(source, "full_text", None):
                # v0.8.127 — same derivation as the list: extracted text with no
                # command row means processing completed (sync path / legacy).
                status = "completed"

        embedded_chunks = await source.get_embedded_chunks()

        # Get associated notebooks
        notebooks_query = await repo_query(
            "SELECT VALUE out FROM reference WHERE in = $source_id",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        notebook_ids = (
            [str(nb_id) for nb_id in notebooks_query] if notebooks_query else []
        )
        notebook_count = len(notebook_ids)

        # Keep detail/list insight counts consistent with one aggregate.
        insights_count_rows = await repo_query(
            "SELECT VALUE count() FROM source_insight "
            "WHERE source = $source_id GROUP ALL",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        insights_count = _normalize_insights_count(insights_count_rows)

        extracted_char_count = (
            len(source.full_text) if source.full_text is not None else None
        )

        visual = visual_status = None
        if source_visuals_enabled():
            projected = await project_source_visuals(
                [{"id": source.id or source_id, "updated": source.updated}]
            )
            receipt = projected.get(source.id or source_id)
            if receipt is not None:
                visual, visual_status = receipt.visual, receipt.visual_status
        else:  # v0.8.86 — capability sentinel (see the list projection above).
            visual_status = disabled_visual_status()

        return SourceResponse(
            id=source.id or "",
            title=source.title,
            topics=source.topics or [],
            provenance=_source_provenance_value(source),
            source_type=_source_type_value(source),
            notebook_count=notebook_count,
            is_shared=notebook_count > 1,
            asset=AssetModel(
                file_path=source.asset.file_path if source.asset else None,
                url=source.asset.url if source.asset else None,
            )
            if source.asset
            else None,
            full_text=source.full_text,
            embedded=embedded_chunks > 0,
            embedded_chunks=embedded_chunks,
            insights_count=insights_count,
            file_available=_is_source_file_available(source),
            extracted_char_count=extracted_char_count,
            extraction_quality=_extraction_quality(
                extracted_char_count,
                status=status,
            ),
            created=iso(source.created),
            updated=iso(source.updated),
            command_id=str(source.command) if source.command else None,
            status=status,
            processing_info=processing_info,
            notebooks=notebook_ids,
            visual=visual,
            visual_status=visual_status,
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except Exception as e:
        logger.error(f"Error fetching source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source")


def _normalize_insights_count(rows: Any) -> int:
    """Normalize Surreal aggregate rows from scalar or object responses."""
    if not isinstance(rows, list) or not rows or rows[0] is None:
        return 0
    value = rows[0]
    if isinstance(value, dict):
        value = value.get("count", 0)
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@router.head("/sources/{source_id}/download")
async def check_source_file(source_id: str):
    """Check if a source has a downloadable file."""
    try:
        await _resolve_source_file(source_id)
        return Response(status_code=200)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to verify file")


@router.get("/sources/{source_id}/download")
async def download_source_file(source_id: str):
    """Download the original file associated with an uploaded source."""
    try:
        resolved_path, filename = await _resolve_source_file(source_id)
        return FileResponse(
            path=resolved_path,
            filename=filename,
            media_type="application/octet-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download source file")


@router.post(
    "/sources/{source_id}/locate-passage", response_model=LocatePassageResponse
)
async def locate_source_passage(source_id: str, body: LocatePassageRequest):
    """v0.8.78 — locate the passage in a source's extracted text that best
    matches `query` (the citing sentence), for citation jump-to-highlight in the
    source viewer (improvement roadmap, Batch 2).

    Best-effort: returns ``{"match": null}`` when the source has no text or there
    is no decent match, so the frontend can simply open the source at the top.
    """
    from deeper_notebook.utils.citation_offsets import locate_passage

    try:
        source = await Source.get(source_id)
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        raise
    except Exception as e:
        logger.error(f"locate-passage: source fetch failed {source_id}: {e}")
        raise HTTPException(status_code=404, detail="Source not found")

    text = getattr(source, "full_text", None) or ""
    if not text.strip() or not body.query.strip():
        return LocatePassageResponse(match=None)
    match = locate_passage(text, body.query)
    return LocatePassageResponse(match=match)  # type: ignore[arg-type]


@router.get("/sources/{source_id}/status", response_model=SourceStatusResponse)
async def get_source_status(source_id: str):
    """Get processing status for a source."""
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if this is a legacy source (no command) or completed/failed in-process
        if not source.command:
            source_prov = getattr(source, "provenance", None) or {}
            explicit_status = source_prov.get("processing_status")
            if explicit_status == "completed":
                return SourceStatusResponse(
                    status="completed",
                    message="Source processing completed successfully",
                    processing_info=None,
                    command_id=None,
                )
            if explicit_status == "failed":
                return SourceStatusResponse(
                    status="failed",
                    message="Source processing failed",
                    processing_info=None,
                    command_id=None,
                )
            return SourceStatusResponse(
                status=None,
                message="Legacy source (completed before async processing)",
                processing_info=None,
                command_id=None,
            )

        # Get command status and processing info
        try:
            status = await source.get_status()
            processing_info = await source.get_processing_progress()

            # Generate descriptive message based on status
            if status == "completed":
                message = "Source processing completed successfully"
            elif status == "failed":
                message = "Source processing failed"
            elif status == "running":
                message = "Source processing in progress"
            elif status == "queued":
                message = "Source processing queued"
            elif status == "unknown":
                message = "Source processing status unknown"
            else:
                message = f"Source processing status: {status}"

            return SourceStatusResponse(
                status=status,
                message=message,
                processing_info=processing_info,
                command_id=str(source.command) if source.command else None,
            )

        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as e:
            logger.warning(f"Failed to get status for source {source_id}: {e}")
            return SourceStatusResponse(
                status="unknown",
                message="Failed to retrieve processing status",
                processing_info=None,
                command_id=str(source.command) if source.command else None,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching status for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source status")


@router.put("/sources/{source_id}", response_model=SourceResponse)
async def update_source(source_id: str, source_update: SourceUpdate):
    """Update a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Update only provided fields
        if source_update.title is not None:
            source.title = source_update.title
        if source_update.topics is not None:
            source.topics = _dedupe_strings(source_update.topics)
        if source_update.provenance is not None:
            source.provenance = source_update.provenance
        if source_update.source_type is not None:
            source.source_type = source_update.source_type

        await source.save()

        embedded_chunks = await source.get_embedded_chunks()
        notebook_count = await _source_notebook_count(source.id or source_id)
        return SourceResponse(
            id=source.id or "",
            title=source.title,
            topics=source.topics or [],
            provenance=_source_provenance_value(source),
            source_type=_source_type_value(source),
            notebook_count=notebook_count,
            is_shared=notebook_count > 1,
            asset=AssetModel(
                file_path=source.asset.file_path if source.asset else None,
                url=source.asset.url if source.asset else None,
            )
            if source.asset
            else None,
            full_text=source.full_text,
            embedded=embedded_chunks > 0,
            embedded_chunks=embedded_chunks,
            # v0.7.181 — iso() instead of str() for Safari new Date() compat.
            created=iso(source.created),
            updated=iso(source.updated),
        )
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating source")


@router.post("/sources/{source_id}/retry", response_model=SourceResponse)
async def retry_source_processing(source_id: str):
    """Retry processing for a failed or stuck source."""
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if source already has a running command
        if source.command:
            try:
                status = await source.get_status()
                if status in ["running", "queued"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Source is already processing. Cannot retry while processing is active.",
                    )
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception as e:
                logger.warning(
                    f"Failed to check current status for source {source_id}: {e}"
                )
                # Continue with retry if we can't check status

        # Get notebooks that this source belongs to.
        # v0.7.60 — fixed the query columns. The `reference` table is a
        # SurrealDB edge with only `in`/`out` (and `id`), NOT `source` /
        # `notebook` columns. The previous query always returned [], so
        # every retry hit "Source is not associated with any notebooks"
        # 400 and the retry endpoint was effectively dead. Also pass a
        # RecordID, not the raw string.
        query = "SELECT VALUE out FROM reference WHERE in = $source_id"
        references = await repo_query(query, {"source_id": ensure_record_id(source_id)})
        notebook_ids = [str(r) for r in references]

        if not notebook_ids:
            raise HTTPException(
                status_code=400, detail="Source is not associated with any notebooks"
            )

        # Prepare content_state based on source asset
        content_state = {}
        if source.asset:
            if source.asset.file_path:
                retry_file_path = _resolve_retry_upload_file_path(
                    source.asset.file_path,
                    source_id,
                )
                content_state = {
                    "file_path": retry_file_path,
                    "delete_source": False,  # Don't delete on retry
                }
            elif source.asset.url:
                content_state = {"url": source.asset.url}
            else:
                raise HTTPException(
                    status_code=400, detail="Source asset has no file_path or url"
                )
        else:
            # Check if it's a text source by trying to get full_text
            if source.full_text:
                content_state = {"content": source.full_text}
            else:
                raise HTTPException(
                    status_code=400, detail="Cannot determine source content for retry"
                )

        try:
            # Import command modules to ensure they're registered
            import commands.source_commands  # noqa: F401

            # Submit new command for background processing
            command_input = SourceProcessingInput(
                source_id=str(source.id),
                content_state=content_state,
                notebook_ids=notebook_ids,
                transformations=[],  # Use default transformations on retry
                embed=True,  # Always embed on retry
            )

            command_id = await CommandService.submit_command_job(
                "open_notebook",  # app name
                "process_source",  # command name
                command_input.model_dump(),
            )

            logger.info(
                f"Submitted retry processing command: {command_id} for source {source_id}"
            )

            # Update source with new command ID
            # v0.7.24 — drop the `command:` prefix. command_id from
            # submit_command_job already includes the `command:`
            # prefix (see line 518 in the create path: `# command_id
            # already includes 'command:' prefix`). Concatenating it
            # produced `command:command:<uuid>`, which either failed
            # to parse or parsed as a nested RecordID — making
            # subsequent get_status() lookups return None forever.
            # The 409 retry-conflict check at line ~932 was defeated
            # on a second retry because the corrupted RecordID
            # resolved to no row.
            source.command = ensure_record_id(command_id)
            await source.save()

            # Get current embedded chunks count
            embedded_chunks = await source.get_embedded_chunks()
            extracted_char_count = (
                len(source.full_text) if source.full_text is not None else None
            )
            notebook_count = len(notebook_ids)

            # Return updated source response
            return SourceResponse(
                id=source.id or "",
                title=source.title,
                topics=source.topics or [],
                provenance=_source_provenance_value(source),
                source_type=_source_type_value(source),
                notebook_count=notebook_count,
                is_shared=notebook_count > 1,
                asset=AssetModel(
                    file_path=source.asset.file_path if source.asset else None,
                    url=source.asset.url if source.asset else None,
                )
                if source.asset
                else None,
                full_text=source.full_text,
                embedded=embedded_chunks > 0,
                embedded_chunks=embedded_chunks,
                created=iso(source.created),
                updated=iso(source.updated),
                command_id=command_id,
                status="queued",
                processing_info={"retry": True, "queued": True},
                extracted_char_count=extracted_char_count,
                extraction_quality=_extraction_quality(
                    extracted_char_count,
                    status="queued",
                ),
            )

        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as e:
            logger.error(
                f"Failed to submit retry processing command for source {source_id}: {e}"
            )
            raise HTTPException(
                status_code=500, detail="Failed to queue retry processing"
            )

    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except Exception as e:
        logger.error(f"Error retrying source processing for {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrying source processing")


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """Delete a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        await source.delete()

        return {"message": "Source deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting source")


@router.get("/sources/{source_id}/insights", response_model=list[SourceInsightResponse])
async def get_source_insights(source_id: str):
    """Get all insights for a specific source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        insights = await source.get_insights()
        return [
            SourceInsightResponse(
                id=insight.id or "",
                source_id=source_id,
                insight_type=insight.insight_type,
                content=insight.content,
                created=iso(insight.created),
                updated=iso(insight.updated),
            )
            for insight in insights
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching insights for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insights")


@router.post(
    "/sources/{source_id}/insights",
    response_model=InsightCreationResponse,
    status_code=202,
)
async def create_source_insight(source_id: str, request: CreateSourceInsightRequest):
    """
    Start insight generation for a source by running a transformation.

    This endpoint returns immediately with a 202 Accepted status.
    The transformation runs asynchronously in the background via the job queue.
    Poll GET /sources/{source_id}/insights to see when the insight is ready.
    """
    try:
        # Validate source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Validate transformation exists
        transformation = await Transformation.get(request.transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        # Submit transformation as background job (fire-and-forget).
        # v0.7.62 — wrap in asyncio.to_thread for the same reason as
        # v0.7.55 in podcast_service / command_service: surreal_commands'
        # submit_command opens a SYNC SurrealDB WebSocket (sign-in +
        # use + create) and blocks the event loop for the duration of
        # the handshake. Concurrent insight creations otherwise stall
        # every other in-flight request.
        #
        # v0.7.175 — Route through CommandService.submit_command_job
        # instead of bare `asyncio.to_thread(submit_command, ...)`.
        # The bare call had no timeout cap, so a saturated SurrealDB
        # pool / hung WS handshake would block this endpoint
        # indefinitely — pinning a worker pool slot per stuck call.
        # CommandService.submit_command_job already wraps with
        # asyncio.wait_for(timeout=10) at command_service.py:43-51
        # and raises ValueError on timeout, which we surface to the
        # client as HTTP 503 below (rather than 500). Same pattern as
        # the existing call sites at sources.py:520 and :1064 that
        # ALREADY route through CommandService.
        try:
            command_id = await CommandService.submit_command_job(
                "open_notebook",
                "run_transformation",
                {
                    "source_id": source_id,
                    "transformation_id": request.transformation_id,
                },
            )
        except ValueError as exc:
            # CommandService.submit_command_job raises ValueError on
            # timeout (saturated pool). Surface as 503 rather than the
            # generic 500 below — distinguishes "service overloaded,
            # retry shortly" from "unexpected server error".
            logger.warning(
                "Insight submission timed out / failed for source {}: {}",
                source_id,
                exc,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Insight generation service is overloaded "
                    "— please retry in a moment."
                ),
            ) from exc
        logger.info(
            f"Submitted run_transformation command {command_id} for source {source_id}"
        )

        # Return immediately with command_id for status tracking
        return InsightCreationResponse(
            status="pending",
            message="Insight generation started",
            source_id=source_id,
            transformation_id=request.transformation_id,
            command_id=str(command_id),
        )

    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.178 — Let typed exceptions bubble to the global FastAPI
        # handlers in api/main.py (NotFoundError → 404, InvalidInputError
        # → 400). Without this re-raise, the broad `except Exception`
        # below intercepts them and returns a generic 500 — so a missing
        # source / transformation that legitimately should be 404 was
        # showing up to the client as 500. The local `if not source:
        # raise HTTPException(404)` guards above never trigger because
        # `Source.get()` raises NotFoundError instead of returning None
        # (see deeper_notebook/domain/base.py:183).
        raise
    except Exception as e:
        logger.error(f"Error starting insight generation for source {source_id}: {e}")
        raise HTTPException(status_code=500, detail="Error starting insight generation")
