"""Evidence Studio artifact request and response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StudioArtifactType = Literal[
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

StudioArtifactStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
]

StudioWorkflowRunStatus = Literal[
    "queued",
    "awaiting_approval",
    "running",
    "completed",
    "failed",
    "cancelled",
]


class StudioArtifactCreate(BaseModel):
    notebook_id: str = Field(..., description="Notebook that owns the artifact")
    artifact_type: StudioArtifactType
    title: str
    source_ids: list[str] = Field(default_factory=list)
    prompt: str | None = None
    model_id: str | None = None
    provider: str | None = None
    output_format: str | None = None
    revision_of_id: str | None = None


class StudioArtifactUpdate(BaseModel):
    title: str | None = None
    status: StudioArtifactStatus | None = None
    source_ids: list[str] | None = None
    prompt: str | None = None
    model_id: str | None = None
    provider: str | None = None
    output_format: str | None = None
    output_payload: dict[str, Any] | None = None
    citations: list[dict[str, Any]] | None = None
    export_paths: dict[str, str] | None = None
    revision_of_id: str | None = None


class StudioArtifactResponse(BaseModel):
    id: str
    notebook_id: str
    artifact_type: StudioArtifactType
    title: str
    status: StudioArtifactStatus
    source_ids: list[str] = Field(default_factory=list)
    prompt: str | None = None
    model_id: str | None = None
    provider: str | None = None
    output_format: str | None = None
    output_payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    export_paths: dict[str, str] = Field(default_factory=dict)
    stale_export_formats: list[str] = Field(default_factory=list)
    revision_of_id: str | None = None
    created: str | None = None
    updated: str | None = None


class StudioWorkflowRunCreate(BaseModel):
    title: str
    source_ids: list[str] = Field(default_factory=list)
    approval_required: bool = True


class StudioWorkflowRunResponse(BaseModel):
    id: str
    artifact_id: str
    notebook_id: str
    title: str
    status: StudioWorkflowRunStatus
    source_ids: list[str] = Field(default_factory=list)
    approval_required: bool = False
    steps: list[dict[str, Any]] = Field(default_factory=list)
    command_id: str | None = None
    created: str | None = None
    updated: str | None = None
