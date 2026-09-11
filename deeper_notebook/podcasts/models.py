import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Union

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from surrealdb import RecordID

from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.base import ObjectModel


class PodcastOverviewMode(str, Enum):
    """The complete Audio Overview format set.

    Keep this intentionally closed. Episode profiles choose providers and
    voices; the overview mode defines the durable editorial contract that a
    retry or resumed outline review must preserve.
    """

    DEEP_DIVE = "deep_dive"
    BRIEF = "brief"
    CRITIQUE = "critique"
    DEBATE = "debate"


@dataclass(frozen=True)
class PodcastModeSpec:
    speaker_count: int
    min_segments: int
    max_segments: int
    min_duration_minutes: int
    max_duration_minutes: int
    outline_schema: tuple[str, ...]
    prompt_contract: str


PODCAST_MODE_SPECS: dict[PodcastOverviewMode, PodcastModeSpec] = {
    PodcastOverviewMode.DEEP_DIVE: PodcastModeSpec(
        speaker_count=2,
        min_segments=5,
        max_segments=8,
        min_duration_minutes=10,
        max_duration_minutes=20,
        outline_schema=("framing", "concepts", "evidence", "implications", "recap"),
        prompt_contract=(
            "Create a two-speaker deep dive. Build a five-part or longer outline "
            "that explains concepts, weighs source evidence, and closes with a "
            "grounded recap. Do not introduce claims absent from the source material."
        ),
    ),
    PodcastOverviewMode.BRIEF: PodcastModeSpec(
        speaker_count=1,
        min_segments=3,
        max_segments=4,
        min_duration_minutes=3,
        max_duration_minutes=6,
        outline_schema=("context", "key_findings", "next_steps"),
        prompt_contract=(
            "Create a concise one-speaker brief. Use three or four compact segments "
            "covering context, the highest-value findings, and source-grounded next "
            "steps. Prefer precision and omit conversational filler."
        ),
    ),
    PodcastOverviewMode.CRITIQUE: PodcastModeSpec(
        speaker_count=2,
        min_segments=4,
        max_segments=6,
        min_duration_minutes=8,
        max_duration_minutes=14,
        outline_schema=(
            "thesis",
            "strengths",
            "limitations",
            "evidence_check",
            "verdict",
        ),
        prompt_contract=(
            "Create a two-speaker critique. Separate the source's thesis, strengths, "
            "limitations, evidence quality, and a qualified verdict. Attribute every "
            "assessment to the supplied source material and state uncertainty plainly."
        ),
    ),
    PodcastOverviewMode.DEBATE: PodcastModeSpec(
        speaker_count=2,
        min_segments=4,
        max_segments=6,
        min_duration_minutes=10,
        max_duration_minutes=16,
        outline_schema=(
            "question",
            "case_for",
            "case_against",
            "rebuttals",
            "synthesis",
        ),
        prompt_contract=(
            "Create a two-speaker evidence debate. Present the strongest source-grounded "
            "case for and against the central question, include fair rebuttals, and end "
            "with a synthesis that distinguishes evidence from unresolved judgment."
        ),
    ),
}


def normalize_podcast_mode(
    value: PodcastOverviewMode | str | None,
) -> PodcastOverviewMode:
    """Read legacy missing values as deep_dive and reject invented formats."""
    if value is None or value == "":
        return PodcastOverviewMode.DEEP_DIVE
    if isinstance(value, PodcastOverviewMode):
        return value
    try:
        return PodcastOverviewMode(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported podcast overview mode: {value!r}") from exc


def get_podcast_mode_spec(
    mode: PodcastOverviewMode | str | None,
) -> PodcastModeSpec:
    return PODCAST_MODE_SPECS[normalize_podcast_mode(mode)]


def mode_prompt_contract(mode: PodcastOverviewMode | str | None) -> str:
    """Stable mode instructions appended to the user-selected profile briefing."""
    return get_podcast_mode_spec(mode).prompt_contract


class TranscriptSegment(BaseModel):
    """Timestamp-ready transcript metadata persisted with an episode.

    Existing podcast-creator backends do not always emit clip timings. The
    command therefore records monotonic estimated bounds until a provider
    supplies exact timings; later playback can safely refine them in place.
    """

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bounds(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class PodcastRetrySubmission(BaseModel):
    """Durable reservation linking exactly one replacement to a retry."""

    model_config = ConfigDict(extra="forbid", strict=True)

    state: Literal["reserved", "submitted"]
    operation_id: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")
    job_id: str | None = Field(default=None, min_length=1, max_length=256)
    replacement_command: str | None = Field(default=None, min_length=1, max_length=256)
    generation: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def state_matches_command_fence(self) -> "PodcastRetrySubmission":
        if self.state == "reserved":
            if self.job_id is not None or self.replacement_command is not None:
                raise ValueError("reserved retry submission cannot contain a command")
            return self
        if self.job_id is None or self.replacement_command is None:
            raise ValueError("submitted retry submission requires a command")
        if self.job_id != self.replacement_command:
            raise ValueError("submitted retry command must match its job")
        return self


def normalize_retry_submission(
    value: Any,
) -> PodcastRetrySubmission | Any:
    """Upgrade only the exact two-field fence written by the prior build."""
    if not isinstance(value, dict) or set(value) != {"job_id", "generation"}:
        return value
    job_id = value.get("job_id")
    generation = value.get("generation")
    if (
        not isinstance(job_id, str)
        or not job_id.strip()
        or len(job_id) > 256
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 1 <= generation <= 1_000_000
    ):
        raise ValueError("stored podcast retry fence is invalid")
    operation_id = hashlib.sha256(
        f"legacy-retry:{job_id}:{generation}".encode()
    ).hexdigest()[:32]
    return PodcastRetrySubmission(
        state="submitted",
        operation_id=operation_id,
        job_id=job_id,
        replacement_command=job_id,
        generation=generation,
    )


def transcript_segments_from_payload(
    payload: Any,
    *,
    mode: PodcastOverviewMode | str | None,
) -> list[TranscriptSegment]:
    """Normalize podcast-creator dialogue into durable, typed metadata.

    Providers have used both ``text`` and ``content`` for dialogue and may
    omit timing entirely. We never fabricate citations; unknown citations stay
    empty until a source-aware transcription provider can supply them.
    """
    if isinstance(payload, dict):
        payload = payload.get("segments", payload.get("transcript", []))
    if not isinstance(payload, list):
        return []

    spec = get_podcast_mode_spec(mode)
    usable: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        elif not isinstance(item, dict):
            item = {
                "speaker": getattr(item, "speaker", None),
                "text": getattr(item, "text", getattr(item, "content", None)),
            }
        text = str(
            item.get("text") or item.get("content") or item.get("dialogue") or ""
        ).strip()
        if text:
            usable.append({**item, "text": text})

    if not usable:
        return []

    estimated_duration = spec.min_duration_minutes * 60 / len(usable)
    cursor = 0.0
    segments: list[TranscriptSegment] = []
    for index, item in enumerate(usable, start=1):
        start = float(item.get("start_seconds", item.get("start_time", cursor)) or 0)
        end = float(
            item.get("end_seconds", item.get("end_time", start + estimated_duration))
            or 0
        )
        if end <= start:
            end = start + estimated_duration
        citations = item.get("citation_ids", item.get("citations", []))
        if not isinstance(citations, list):
            citations = []
        segments.append(
            TranscriptSegment(
                start_seconds=start,
                end_seconds=end,
                speaker=str(
                    item.get("speaker")
                    or item.get("speaker_name")
                    or f"Speaker {index}"
                ),
                text=item["text"],
                citation_ids=[str(value) for value in citations if value],
            )
        )
        cursor = max(cursor, end)
    return segments


async def _resolve_model_config(model_id: str) -> tuple[str, str, dict]:
    """Load Model record, resolve credential -> (provider, model_name, config_dict).

    Used by resolve_outline_config, resolve_transcript_config, resolve_tts_config,
    and per-speaker TTS overrides.
    """
    from deeper_notebook.ai.models import Model

    model = await Model.get(model_id)
    config: dict = {}
    if model.credential:
        credential = await model.get_credential_obj()
        if credential:
            config = credential.to_esperanto_config()
    if not config:
        from deeper_notebook.ai.key_provider import provision_provider_keys

        await provision_provider_keys(model.provider)
    return (model.provider, model.name, config)


class EpisodeProfile(ObjectModel):
    """
    Episode Profile - Simplified podcast configuration.
    Replaces complex 15+ field configuration with user-friendly profiles.
    """

    table_name: ClassVar[str] = "episode_profile"
    nullable_fields: ClassVar[set[str]] = {
        "description",
        "outline_provider",
        "outline_model",
        "transcript_provider",
        "transcript_model",
        "outline_llm",
        "transcript_llm",
        "language",
    }

    name: str = Field(..., description="Unique profile name")
    description: Optional[str] = Field(None, description="Profile description")
    speaker_config: str = Field(..., description="Reference to speaker profile name")

    # Legacy fields (kept for migration, app ignores)
    outline_provider: Optional[str] = Field(
        None, description="[Legacy] AI provider for outline generation"
    )
    outline_model: Optional[str] = Field(
        None, description="[Legacy] AI model for outline generation"
    )
    transcript_provider: Optional[str] = Field(
        None, description="[Legacy] AI provider for transcript generation"
    )
    transcript_model: Optional[str] = Field(
        None, description="[Legacy] AI model for transcript generation"
    )

    # New fields: Model registry references
    outline_llm: Optional[str] = Field(
        None, description="Model record ID for outline generation"
    )
    transcript_llm: Optional[str] = Field(
        None, description="Model record ID for transcript generation"
    )
    language: Optional[str] = Field(
        None, description="Podcast language (BCP 47 locale code, e.g. pt-BR, en-US)"
    )

    default_briefing: str = Field(..., description="Default briefing template")
    num_segments: int = Field(default=5, description="Number of podcast segments")

    @field_validator("num_segments")
    @classmethod
    def validate_segments(cls, v):
        if not 3 <= v <= 20:
            raise ValueError("Number of segments must be between 3 and 20")
        return v

    def _prepare_save_data(self) -> dict:
        data = super()._prepare_save_data()
        if data.get("outline_llm"):
            data["outline_llm"] = ensure_record_id(data["outline_llm"])
        if data.get("transcript_llm"):
            data["transcript_llm"] = ensure_record_id(data["transcript_llm"])
        return data

    async def resolve_outline_config(self) -> tuple[str, str, dict]:
        """Resolve outline model -> (provider, model_name, config_dict)"""
        if not self.outline_llm:
            raise ValueError(
                f"Episode profile '{self.name}' has no outline model configured. "
                "Please update the profile to select an outline model."
            )
        return await _resolve_model_config(self.outline_llm)

    async def resolve_transcript_config(self) -> tuple[str, str, dict]:
        """Resolve transcript model -> (provider, model_name, config_dict)"""
        if not self.transcript_llm:
            raise ValueError(
                f"Episode profile '{self.name}' has no transcript model configured. "
                "Please update the profile to select a transcript model."
            )
        return await _resolve_model_config(self.transcript_llm)

    @classmethod
    async def get_by_name(cls, name: str) -> Optional["EpisodeProfile"]:
        """Get episode profile by name"""
        result = await repo_query(
            "SELECT * FROM episode_profile WHERE name = $name", {"name": name}
        )
        if result:
            return cls(**result[0])
        return None


class SpeakerProfile(ObjectModel):
    """
    Speaker Profile - Voice and personality configuration.
    Supports 1-4 speakers for flexible podcast formats.
    """

    table_name: ClassVar[str] = "speaker_profile"
    nullable_fields: ClassVar[set[str]] = {
        "description",
        "tts_provider",
        "tts_model",
        "voice_model",
    }

    name: str = Field(..., description="Unique profile name")
    description: Optional[str] = Field(None, description="Profile description")

    # Legacy fields (kept for migration, app ignores)
    tts_provider: Optional[str] = Field(
        None, description="[Legacy] TTS provider (openai, elevenlabs, etc.)"
    )
    tts_model: Optional[str] = Field(None, description="[Legacy] TTS model name")

    # New field: Model registry reference
    voice_model: Optional[str] = Field(None, description="Model record ID for TTS")

    speakers: list[dict[str, Any]] = Field(
        ..., description="Array of speaker configurations"
    )

    @field_validator("speakers")
    @classmethod
    def validate_speakers(cls, v):
        if not 1 <= len(v) <= 4:
            raise ValueError("Must have between 1 and 4 speakers")

        required_fields = ["name", "voice_id", "backstory", "personality"]
        for speaker in v:
            for field in required_fields:
                if field not in speaker:
                    raise ValueError(f"Speaker missing required field: {field}")
        return v

    def _prepare_save_data(self) -> dict:
        data = super()._prepare_save_data()
        if data.get("voice_model"):
            data["voice_model"] = ensure_record_id(data["voice_model"])
        # Handle per-speaker voice_model overrides
        if data.get("speakers"):
            for speaker in data["speakers"]:
                if speaker.get("voice_model"):
                    speaker["voice_model"] = ensure_record_id(speaker["voice_model"])
        return data

    async def resolve_tts_config(self) -> tuple[str, str, dict]:
        """Resolve TTS model -> (provider, model_name, config_dict)"""
        if not self.voice_model:
            raise ValueError(
                f"Speaker profile '{self.name}' has no voice model configured. "
                "Please update the profile to select a voice model."
            )
        return await _resolve_model_config(self.voice_model)

    @classmethod
    async def get_by_name(cls, name: str) -> Optional["SpeakerProfile"]:
        """Get speaker profile by name"""
        result = await repo_query(
            "SELECT * FROM speaker_profile WHERE name = $name", {"name": name}
        )
        if result:
            return cls(**result[0])
        return None


# v0.8.68 — generation stages, written by the worker as podcast-creator's
# LangGraph nodes complete, read by the episodes UI for per-stage progress.
# Plain strings (not an Enum) so the API layer can reference them without
# importing podcast-creator.
STAGE_OUTLINE = "generating_outline"
STAGE_TRANSCRIPT = "generating_transcript"
STAGE_AUDIO = "generating_audio"
STAGE_COMBINE = "combining_audio"
STAGE_AWAITING_REVIEW = "awaiting_review"
STAGE_CANCELLED = "cancelled"

GENERATION_STAGES = (
    STAGE_OUTLINE,
    STAGE_TRANSCRIPT,
    STAGE_AUDIO,
    STAGE_COMBINE,
    STAGE_AWAITING_REVIEW,
    STAGE_CANCELLED,
)


class PodcastEpisode(ObjectModel):
    """Enhanced PodcastEpisode with job tracking and metadata"""

    table_name: ClassVar[str] = "episode"
    # v0.8.68 — ObjectModel._prepare_save_data drops None values unless the
    # field is listed here, so the workers' `generation_stage = None` on
    # success never reached the DB and the last stage ("combining_audio")
    # stuck on completed episodes forever (caught by the live smoke test).
    nullable_fields: ClassVar[set[str]] = {
        "generation_stage",
        "custom_prompt",
        "selection_summary",
        "selection_fingerprint",
        "editorial_brief",
        "retry_submitted",
    }

    name: str = Field(..., description="Episode name")
    episode_profile: dict[str, Any] = Field(
        ..., description="Episode profile used (stored as object)"
    )
    speaker_profile: dict[str, Any] = Field(
        ..., description="Speaker profile used (stored as object)"
    )
    briefing: str = Field(..., description="Full briefing used for generation")
    # v0.8.68 — the user's per-episode customization, stored SEPARATELY from
    # the combined `briefing` so retry can replay it verbatim. Pre-v0.8.68
    # retries silently regenerated with the base briefing only.
    briefing_suffix: Optional[str] = Field(
        default=None, description="User-provided extra instructions, if any"
    )
    mode: PodcastOverviewMode = Field(
        default=PodcastOverviewMode.DEEP_DIVE,
        description="Closed Audio Overview format; legacy episodes are deep_dive",
    )
    custom_prompt: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="Exact user customization applied to this overview",
    )
    content: str = Field(..., description="Source content")
    audio_file: Optional[str] = Field(
        default=None, description="Path to generated audio file"
    )
    transcript: Optional[dict[str, Any]] = Field(
        default_factory=dict, description="Generated transcript"
    )
    outline: Optional[dict[str, Any]] = Field(
        default_factory=dict, description="Generated outline"
    )
    transcript_segments: list[TranscriptSegment] = Field(
        default_factory=list,
        description="Typed timestamp-ready transcript segment metadata",
    )
    command: Optional[str | RecordID] = Field(
        default=None, description="Link to surreal-commands job"
    )
    # v0.8.68 — per-stage progress + cooperative cancellation + outline review.
    generation_stage: Optional[str] = Field(
        default=None,
        description="Current generation stage (see GENERATION_STAGES); None "
        "when idle/finished",
    )
    cancel_requested: Optional[bool] = Field(
        default=False,
        description="Set by POST /podcasts/episodes/{id}/cancel; the worker "
        "polls it and aborts the in-flight generation",
    )
    # Phase 2 Studio receipts. Legacy rows retain the defaults. Persist only
    # redacted references/counts and planner decisions, never selected bodies.
    selection_summary: Optional[dict[str, Any]] = Field(default=None)
    selection_fingerprint: Optional[str] = Field(default=None, max_length=128)
    editorial_brief: Optional[dict[str, Any]] = Field(default=None)
    model_plan_receipts: list[dict[str, Any]] = Field(default_factory=list)
    retry_submitted: PodcastRetrySubmission | None = Field(default=None)
    # v0.8.126 — episodes were not notebook-scoped: nothing could list "this
    # notebook's episodes", so the v0.8.125 bundle export's podcast branch
    # always bundled zero episodes. None on legacy rows (and on any episode
    # created from raw content rather than a notebook).
    notebook_id: Optional[str] = Field(
        default=None, description="Notebook this episode was generated from, if any"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(
        cls, value: PodcastOverviewMode | str | None
    ) -> PodcastOverviewMode:
        return normalize_podcast_mode(value)

    @field_validator("retry_submitted", mode="before")
    @classmethod
    def normalize_legacy_retry_submission(cls, value: Any) -> Any:
        return normalize_retry_submission(value)

    async def get_job_status(self) -> Optional[str]:
        """Get the status of the associated command"""
        if not self.command:
            return None

        try:
            from surreal_commands import get_command_status

            status = await get_command_status(str(self.command))
            return status.status if status else "unknown"
        except Exception:
            return "unknown"

    async def get_job_detail(self) -> dict:
        """Get status and error_message of the associated command"""
        if not self.command:
            return {"status": None, "error_message": None}

        try:
            from surreal_commands import get_command_status

            status = await get_command_status(str(self.command))
            if not status:
                return {"status": "unknown", "error_message": None}
            return {
                "status": status.status,
                "error_message": getattr(status, "error_message", None),
            }
        except Exception as exc:
            # v0.8.68 — was a bare swallow: a broken job-queue backend or a
            # corrupt command id showed "unknown" status forever with zero
            # diagnostic trail. Still degrade to "unknown" (the UI contract),
            # but leave the operator a breadcrumb.
            logger.warning(
                f"get_job_detail({self.command}): status lookup failed, "
                f"reporting 'unknown' ({type(exc).__name__}: {exc})"
            )
            return {"status": "unknown", "error_message": None}

    @field_validator("command", mode="before")
    @classmethod
    def parse_command(cls, value):
        if isinstance(value, str):
            return ensure_record_id(value)
        return value

    def _prepare_save_data(self) -> dict:
        """Override to ensure command field is always RecordID format for database"""
        data = super()._prepare_save_data()

        # Ensure command field is RecordID format if not None
        if data.get("command") is not None:
            data["command"] = ensure_record_id(data["command"])
        data["mode"] = normalize_podcast_mode(data.get("mode")).value

        return data

    # v0.8.126 — notebook-scoped episode listing (see `notebook_id` above).
    @classmethod
    async def get_for_notebook(cls, notebook_id: str) -> list["PodcastEpisode"]:
        """All episodes generated from the given notebook, newest first."""
        result = await repo_query(
            "SELECT * FROM episode WHERE notebook_id = $notebook_id ORDER BY created DESC",
            {"notebook_id": notebook_id},
        )
        return [cls(**row) for row in (result or [])]
