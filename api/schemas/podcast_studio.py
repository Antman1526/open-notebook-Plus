"""Strict, source-body-free wire contracts for Podcast Intelligence Studio."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deeper_notebook.knowledge_engine.capabilities import AuthorityKind
from deeper_notebook.podcasts.selection_contracts import PodcastSelection

_ABSOLUTE_MODEL_ID = re.compile(r"^(?:/|\\\\|//|[A-Za-z]:[\\/]|file://)", re.IGNORECASE)
_EMBEDDED_FILE_URL = re.compile(r"\bfile://[^\s,;\)\]}>]*", re.IGNORECASE)
_EMBEDDED_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s,;\)\]}>]*")
_EMBEDDED_UNC_PATH = re.compile(r"(?:^|(?<=[\s(\"'=]))(?:\\\\|//)[^\s,;\)\]}>]*")
_EMBEDDED_POSIX_PATH = re.compile(r"(?:^|(?<=[\s(\"'=]))/(?!/)[^\s,;\)\]}>]*")


def _looks_like_absolute_model_id(value: str) -> bool:
    return bool(_ABSOLUTE_MODEL_ID.match(value))


def _contains_absolute_filesystem_path(value: str) -> bool:
    return bool(
        _EMBEDDED_FILE_URL.search(value)
        or _EMBEDDED_WINDOWS_PATH.search(value)
        or _EMBEDDED_UNC_PATH.search(value)
        or _EMBEDDED_POSIX_PATH.search(value)
    )


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PodcastSelectionPreviewRequest(_Strict):
    """Stable selection references only; canonical source text is server-side."""

    selections: list[PodcastSelection] = Field(min_length=1, max_length=128)


class PodcastSelectionPreviewEntryResponse(_Strict):
    stable_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    authority_kind: AuthorityKind
    relative_locator: str | None = Field(default=None, max_length=1024)
    revision_id: str | None = Field(default=None, max_length=128)
    fingerprint: str | None = Field(default=None, max_length=128)
    state: Literal[
        "included",
        "duplicate",
        "unavailable",
        "changed",
        "empty",
        "failed_parse",
        "oversize",
    ]
    reason: str = Field(min_length=1, max_length=128)
    estimated_characters: int = Field(ge=0)


class PodcastSelectionPreviewResponse(_Strict):
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    entries: list[PodcastSelectionPreviewEntryResponse] = Field(max_length=10_000)
    included_characters: int = Field(ge=0)
    requires_batch_engine: bool
    current_worker_eligible: bool
    blocked_reasons: list[str] = Field(default_factory=list, max_length=128)


class PodcastReadinessRequest(PodcastSelectionPreviewRequest):
    execution_policy: Literal["strict_local", "local_preferred", "custom"] = (
        "strict_local"
    )
    compute_profile: Literal["efficient", "balanced", "maximum_quality"] = "balanced"
    include_transcription: bool = False
    production_overrides: dict[
        Literal[
            "podcast_outline", "podcast_script", "text_to_speech", "speech_to_text"
        ],
        str,
    ] = Field(default_factory=dict, max_length=4)

    @field_validator("production_overrides")
    @classmethod
    def validate_production_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for role, model_id in value.items():
            model = " ".join(model_id.split())
            if not model or len(model) > 512 or cls._looks_like_filesystem_path(model):
                raise ValueError("production override model IDs must be bounded labels")
            normalized[role] = model
        return normalized

    @staticmethod
    def _looks_like_filesystem_path(value: str) -> bool:
        return _looks_like_absolute_model_id(value)


class PodcastStageModelPlanResponse(_Strict):
    role: Literal[
        "podcast_outline", "podcast_script", "text_to_speech", "speech_to_text"
    ]
    outcome: Literal["ready", "blocked", "approval_required"]
    model_id: str | None = Field(default=None, max_length=512)
    provider: str | None = Field(default=None, max_length=128)
    resource_tier: Literal["light", "standard", "heavyweight"] | None = None
    selection_source: (
        Literal["automatic", "role_override", "production_override"] | None
    ) = None
    reason: str = Field(min_length=1, max_length=1024)
    blocked_reason: str | None = Field(default=None, max_length=1024)
    override_choices: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("override_choices")
    @classmethod
    def validate_override_choices(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        if any(
            not item
            or len(item) > 512
            or PodcastReadinessRequest._looks_like_filesystem_path(item)
            for item in normalized
        ):
            raise ValueError("override choices must be bounded labels")
        return list(dict.fromkeys(normalized))


class PodcastModelPlanReceipt(_Strict):
    """Exact persisted planner receipt with no provider/model/source text."""

    version: Literal[1] = 1
    role: Literal[
        "podcast_outline", "podcast_script", "text_to_speech", "speech_to_text"
    ]
    outcome: Literal["ready", "blocked", "approval_required"]
    resource_tier: Literal["light", "standard", "heavyweight"] | None
    selection_source: (
        Literal["automatic", "role_override", "production_override"] | None
    )
    reason: Literal["route_ready", "route_blocked", "route_approval_required"]


class PodcastReadinessResponse(_Strict):
    preview: PodcastSelectionPreviewResponse
    stage_plans: list[PodcastStageModelPlanResponse] = Field(min_length=3, max_length=4)
    ready: bool
    blocked_reasons: list[str] = Field(default_factory=list, max_length=128)


class PodcastEditorialBrief(_Strict):
    """Small, source-body-free editorial intent saved with an episode."""

    central_question: str | None = Field(default=None, max_length=1_000)
    audience: str | None = Field(default=None, max_length=256)
    purpose: str | None = Field(default=None, max_length=32)
    format: str | None = Field(default=None, max_length=32)
    target_minutes: int | None = Field(default=None, ge=1, le=180)
    required_takeaway: str | None = Field(default=None, max_length=1_000)
    include_unanswered_questions: bool | None = None
    evidence_policy: str | None = Field(default=None, max_length=32)
    episode_profile_name: str | None = Field(default=None, max_length=256)
    speaker_profile_name: str | None = Field(default=None, max_length=256)
    outline: list[str] = Field(default_factory=list, max_length=32)

    @field_validator(
        "central_question",
        "audience",
        "required_takeaway",
        "episode_profile_name",
        "speaker_profile_name",
        "purpose",
        "format",
        "evidence_policy",
    )
    @classmethod
    def normalize_optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if cls._looks_like_filesystem_path(normalized):
            raise ValueError("editorial brief labels must not be filesystem paths")
        return normalized or None

    @field_validator("outline")
    @classmethod
    def normalize_outline(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 512 for item in normalized):
            raise ValueError(
                "outline entries must be non-empty labels up to 512 characters"
            )
        if any(cls._looks_like_filesystem_path(item) for item in normalized):
            raise ValueError("outline entries must not be filesystem paths")
        return normalized

    @staticmethod
    def _looks_like_filesystem_path(value: str) -> bool:
        return _contains_absolute_filesystem_path(value)

    @model_validator(mode="after")
    def validate_full_intent_enums(self) -> "PodcastEditorialBrief":
        full_fields = {
            "purpose",
            "format",
            "target_minutes",
            "required_takeaway",
            "include_unanswered_questions",
            "evidence_policy",
            "episode_profile_name",
            "speaker_profile_name",
        }
        if any(getattr(self, field) is not None for field in full_fields):
            if self.audience not in {"foundation", "practitioner", "expert"}:
                raise ValueError("audience must be foundation, practitioner, or expert")
            if self.purpose not in {
                "explain",
                "analyze",
                "challenge",
                "compare",
                "teach",
            }:
                raise ValueError("purpose is not supported")
            if self.format not in {"brief", "deep_dive", "critique", "debate"}:
                raise ValueError("format is not supported")
            if self.evidence_policy not in {"strict", "interpretation"}:
                raise ValueError("evidence_policy is not supported")
        return self


class PodcastStudioSubmitRequest(PodcastReadinessRequest):
    selection_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$",
    )
    confirmed: Literal[True]
    episode_profile: str = Field(min_length=1, max_length=256)
    speaker_profile: str = Field(min_length=1, max_length=256)
    episode_name: str = Field(min_length=1, max_length=512)
    mode: Literal["deep_dive", "brief", "critique", "debate"] = "deep_dive"
    custom_prompt: str | None = Field(default=None, max_length=10_000)
    episode_length: Literal["short", "medium", "long"] | None = None
    review_outline: bool = True
    editorial_brief: PodcastEditorialBrief | None = None
    # v0.8.127 — the Studio path had no notebook_id anywhere in its request
    # chain, so episodes it created stayed unscoped and never appeared in a
    # notebook's bundle export (unlike the standard generation path, which
    # has persisted this since v0.8.126). Optional: a bare id is normalized
    # to the `notebook:` table prefix in the router before use.
    notebook_id: Optional[str] = Field(default=None, max_length=256)

    @field_validator("episode_profile", "speaker_profile", "episode_name")
    @classmethod
    def label_is_not_path(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or PodcastEditorialBrief._looks_like_filesystem_path(
            normalized
        ):
            raise ValueError("label must not be a filesystem path")
        return normalized

    @model_validator(mode="after")
    def editorial_values_agree_with_submission(self) -> "PodcastStudioSubmitRequest":
        brief = self.editorial_brief
        if brief is None:
            return self
        if brief.format is not None and brief.format != self.mode:
            raise ValueError("editorial format must match submission mode")
        if (
            brief.episode_profile_name is not None
            and brief.episode_profile_name != self.episode_profile
        ):
            raise ValueError("editorial episode profile must match submission profile")
        if (
            brief.speaker_profile_name is not None
            and brief.speaker_profile_name != self.speaker_profile
        ):
            raise ValueError("editorial speaker profile must match submission profile")
        return self


class PodcastStudioSubmitResponse(_Strict):
    job_id: str = Field(min_length=1, max_length=256)
    status: Literal["submitted"]
    message: str = Field(min_length=1, max_length=256)
    episode_profile: str = Field(min_length=1, max_length=256)
    episode_name: str = Field(min_length=1, max_length=512)
    mode: Literal["deep_dive", "brief", "critique", "debate"]


class PodcastRetryPreviewRequiredResponse(_Strict):
    """Typed, non-destructive retry result for stale Studio selections.

    The references are the strict wire-safe union, never resolved source text
    or a filesystem locator.  An empty list is allowed for tampered metadata:
    the caller must fail closed and ask the user to choose fresh references.
    """

    status: Literal["preview_required"]
    code: Literal[
        "podcast_selection_changed",
        "podcast_selection_unavailable",
        "podcast_selection_tampered",
    ]
    message: str = Field(min_length=1, max_length=512)
    episode_id: str = Field(min_length=1, max_length=256)
    selections: list[PodcastSelection] = Field(max_length=128)
    selection_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    preview: PodcastSelectionPreviewResponse | None = None


__all__ = [
    "PodcastReadinessRequest",
    "PodcastReadinessResponse",
    "PodcastEditorialBrief",
    "PodcastSelectionPreviewEntryResponse",
    "PodcastSelectionPreviewRequest",
    "PodcastSelectionPreviewResponse",
    "PodcastStageModelPlanResponse",
    "PodcastModelPlanReceipt",
    "PodcastStudioSubmitRequest",
    "PodcastStudioSubmitResponse",
    "PodcastRetryPreviewRequiredResponse",
]
