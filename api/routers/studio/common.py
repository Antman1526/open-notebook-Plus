from __future__ import annotations

import asyncio  # v0.7.92 / v0.7.93 — wait_for + gather for parallel pages + timeouts
import csv
import html
import json
import os
import re
import sys
import zipfile
from io import StringIO
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.routing import APIRoute
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ValidationError

from api.command_service import CommandService
from api.podcast_service import PodcastService
from api.routers.sources import save_uploaded_file
from api.schemas.studio import (
    StudioArtifactCreate,
    StudioArtifactResponse,
    StudioArtifactUpdate,
    StudioWorkflowRunCreate,
    StudioWorkflowRunResponse,
)
from deeper_notebook.ai.models import Model
from deeper_notebook.ai.provision import provision_langchain_model
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import (
    Asset,
    Note,
    Notebook,
    Source,
    StudioArtifact,
    StudioWorkflowRun,
)
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import InvalidInputError, NotFoundError
from deeper_notebook.feature_flags import evidence_studio_enabled
from deeper_notebook.local_models.inventory import enumerate_models
from deeper_notebook.local_models.role_routing import (
    inventory_model_match_keys,
    model_match_key,
    recommend_model_roles,
)
from deeper_notebook.studio import artifact_generation as artifact_generation_service
from deeper_notebook.studio.generation.context import artifact_sources
from deeper_notebook.studio.payloads import (
    build_structured_payload,
    parse_payload_document,
)
from deeper_notebook.studio.renderers import render_artifact_markdown
from deeper_notebook.utils.text_utils import (
    clean_thinking_content,
    extract_text_content,
)


class _LegacyPatchSyncRoute(APIRoute):
    """Apply legacy facade patches before a split endpoint handles a request."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def synced_route_handler(request):
            studio = sys.modules.get("api.routers.studio")
            sync = getattr(studio, "_sync_legacy_patches", None)
            if sync is not None:
                sync()
            return await route_handler(request)

        return synced_route_handler


router = APIRouter(
    prefix="/studio",
    tags=["studio"],
    route_class=_LegacyPatchSyncRoute,
)


def _require_evidence_studio() -> None:
    if not evidence_studio_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence Studio is not enabled",
        )


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _artifact_response(artifact: StudioArtifact) -> StudioArtifactResponse:
    try:
        from deeper_notebook.studio.generation import persistence

        stale_formats = persistence.get_stale_export_formats(artifact)
    except Exception:
        stale_formats = []

    return StudioArtifactResponse(
        id=str(artifact.id),
        notebook_id=str(artifact.notebook_id),
        artifact_type=artifact.artifact_type,
        title=artifact.title,
        status=artifact.status,
        source_ids=[str(source_id) for source_id in artifact.source_ids],
        prompt=artifact.prompt,
        model_id=artifact.model_id,
        provider=artifact.provider,
        output_format=artifact.output_format,
        output_payload=artifact.output_payload,
        citations=artifact.citations,
        export_paths=artifact.export_paths,
        stale_export_formats=stale_formats,
        revision_of_id=(
            str(artifact.revision_of_id)
            if artifact.revision_of_id is not None
            else None
        ),
        created=_iso(getattr(artifact, "created", None)),
        updated=_iso(getattr(artifact, "updated", None)),
    )


def _workflow_run_response(run: StudioWorkflowRun) -> StudioWorkflowRunResponse:
    return StudioWorkflowRunResponse(
        id=str(run.id),
        artifact_id=str(run.artifact_id),
        notebook_id=str(run.notebook_id),
        title=run.title,
        status=run.status,
        source_ids=[str(source_id) for source_id in run.source_ids],
        approval_required=run.approval_required,
        steps=run.steps,
        command_id=str(run.command_id) if run.command_id is not None else None,
        created=_iso(getattr(run, "created", None)),
        updated=_iso(getattr(run, "updated", None)),
    )


def _sync_artifact_generation_service_dependencies() -> None:
    artifact_generation_service.StudioArtifact = StudioArtifact
    artifact_generation_service.StudioWorkflowRun = StudioWorkflowRun
    artifact_generation_service.Notebook = Notebook
    artifact_generation_service.Source = Source
    artifact_generation_service.Model = Model
    artifact_generation_service.provision_langchain_model = provision_langchain_model
    artifact_generation_service.enumerate_models = enumerate_models
    artifact_generation_service.recommend_model_roles = recommend_model_roles


def _workflow_steps_for_artifact(
    artifact: StudioArtifact,
    *,
    approval_required: bool,
) -> list[dict[str, str]]:
    approval_status = "pending" if approval_required else "completed"
    model_status = "blocked" if approval_required else "pending"
    return [
        {"id": "context", "label": "Context built", "status": "completed"},
        {"id": "privacy_gate", "label": "Privacy gate", "status": approval_status},
        {
            "id": "model_route",
            "label": "Model route",
            "status": model_status,
        },
        {
            "id": "artifact_generation",
            "label": _artifact_type_label(artifact.artifact_type),
            "status": model_status,
        },
    ]


def _artifact_type_label(artifact_type: str) -> str:
    if artifact_type in {"course_pack", "training_guide"}:
        return "Course Pack"
    return artifact_type.replace("_", " ").title()


def _set_workflow_step_status(
    run: StudioWorkflowRun,
    step_ids: set[str],
    status_value: str,
) -> None:
    run.steps = [
        {
            **step,
            "status": status_value
            if step.get("id") in step_ids
            else step.get("status", "pending"),
        }
        for step in run.steps
    ]


async def _active_workflow_run_for_artifact(
    artifact_id: str,
) -> StudioWorkflowRun | None:
    try:
        runs = await StudioWorkflowRun.get_for_artifact(artifact_id)
    except Exception:
        logger.debug("Could not load Studio workflow runs for {}", artifact_id)
        return None

    active_statuses = {"queued", "awaiting_approval", "running"}
    return next((run for run in runs if run.status in active_statuses), None)


def _sources_not_ready_exception(
    not_ready_sources: list[dict[str, str | None]],
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "sources_not_ready",
            "message": (
                "One or more selected sources are still processing. "
                "Wait for extraction to finish, then generate again."
            ),
            "not_ready_sources": not_ready_sources,
        },
    )


async def _ensure_artifact_sources_ready(artifact: StudioArtifact) -> None:
    sources = await _artifact_sources(artifact)
    not_ready_sources = _artifact_not_ready_sources(sources)
    if not_ready_sources:
        raise _sources_not_ready_exception(not_ready_sources)


async def _submit_studio_generation_command(
    artifact: StudioArtifact,
    run: StudioWorkflowRun,
) -> None:
    if run.command_id:
        return

    if run.source_ids:
        artifact.source_ids = [str(source_id) for source_id in run.source_ids]
    await _ensure_artifact_sources_ready(artifact)

    try:
        import commands.studio_commands  # noqa: F401

        command_id = await CommandService.submit_command_job(
            "open_notebook",
            "generate_studio_artifact",
            {
                "artifact_id": str(artifact.id),
                "workflow_run_id": str(run.id),
            },
        )
    except ValueError as exc:
        run.status = "failed"
        _set_workflow_step_status(run, {"artifact_generation"}, "failed")
        await run.save()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Studio generation queue is temporarily unavailable. "
                "Please retry in a moment."
            ),
        ) from exc

    run.command_id = command_id
    run.status = "queued"
    run.approval_required = False
    _set_workflow_step_status(run, {"privacy_gate"}, "completed")
    _set_workflow_step_status(run, {"model_route", "artifact_generation"}, "pending")
    artifact.status = "running"
    await artifact.save()
    await run.save()


async def _artifact_sources(artifact: StudioArtifact) -> list[Source]:
    """Load an artifact's sources, or its notebook's when none are selected.

    v0.8.99 — delegates to `studio.generation.context.artifact_sources` rather
    than duplicating it. `Source` / `Notebook` are passed explicitly because
    they are resolved from THIS module's namespace at call time: the Evidence
    Studio API suite patches `studio_mod.Source` (26 sites, mirrored down by
    `_sync_legacy_patches`), and a bare delegation would silently use the
    canonical module's classes, escaping the patch and hitting the live
    database. Behaviour, ordering, and the 404 contract are unchanged.
    """
    return await artifact_sources(artifact, source_cls=Source, notebook_cls=Notebook)


def _artifact_not_ready_sources(sources: list[Source]) -> list[dict[str, str | None]]:
    not_ready: list[dict[str, str | None]] = []
    for source in sources:
        text = (getattr(source, "full_text", None) or "").strip()
        if text:
            continue
        command = getattr(source, "command", None)
        not_ready.append(
            {
                "source_id": str(getattr(source, "id", "")),
                "title": getattr(source, "title", None) or "Untitled source",
                "command_id": str(command) if command is not None else None,
            }
        )
    return not_ready


def _env_int(name: str, default: int) -> int:
    raw = resolve_env(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError:
        import logging

        logging.getLogger(__name__).warning(
            "Invalid %s=%r; using default %d",
            name,
            raw,
            default,
        )
        return default


_MAX_EXTRACT_CHARS_PER_FILE = _env_int("DEEPER_NOTEBOOK_STUDIO_MAX_FILE_CHARS", 15_000)
_MAX_COMBINED_CHARS = _env_int("DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS", 60_000)


async def _notebook_record_exists(notebook_id: str) -> bool:
    rows = await repo_query(
        "SELECT id FROM $notebook_id LIMIT 1",
        {"notebook_id": ensure_record_id(notebook_id)},
    )
    return bool(rows)
