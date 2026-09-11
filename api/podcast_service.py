import asyncio
from typing import Any, Dict, Literal, Optional

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, field_validator
from surreal_commands import get_command_status, submit_command

from api.utils.iso import iso  # v0.7.183 — Safari-safe datetime serialization
from deeper_notebook.domain.notebook import Notebook
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import (  # v0.8.68 — offline gate + content budget
    ConfigurationError,
    InvalidInputError,
)
from deeper_notebook.podcasts.models import (
    EpisodeProfile,
    PodcastEpisode,
    PodcastOverviewMode,
    SpeakerProfile,
    get_podcast_mode_spec,
    normalize_podcast_mode,
)


class PodcastGenerationRequest(BaseModel):
    """Request model for podcast generation"""

    episode_profile: str
    speaker_profile: str
    episode_name: str
    content: Optional[str] = None
    notebook_id: Optional[str] = None
    briefing_suffix: Optional[str] = None
    # `briefing_suffix` is the pre-format request field. Keep accepting it for
    # queued desktop clients, while persisting the user-facing name below.
    mode: PodcastOverviewMode = PodcastOverviewMode.DEEP_DIVE
    custom_prompt: Optional[str] = None
    # v0.8.86 — per-episode length: "short" | "medium" | "long" (overrides the
    # profile's num_segments for this episode). None → use the profile default.
    episode_length: Optional[str] = None
    # v0.8.68 — outline-review workflow: stop after the outline so the user
    # can edit it before transcript + audio are generated.
    review_outline: bool = False
    execution_policy: Literal["strict_local", "local_preferred", "custom"] = (
        "strict_local"
    )
    compute_profile: Literal["efficient", "balanced", "maximum_quality"] = "balanced"
    include_transcription: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(
        cls, value: PodcastOverviewMode | str | None
    ) -> PodcastOverviewMode:
        return normalize_podcast_mode(value)

    @field_validator("custom_prompt", "briefing_suffix")
    @classmethod
    def normalize_prompt(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def resolved_custom_prompt(self) -> Optional[str]:
        """Use the new request field first; old clients keep their behavior."""
        return self.custom_prompt or self.briefing_suffix


class PodcastGenerationResponse(BaseModel):
    """Response model for podcast generation"""

    job_id: str
    status: str
    message: str
    episode_profile: str
    episode_name: str
    mode: PodcastOverviewMode = PodcastOverviewMode.DEEP_DIVE


class PodcastSubmissionNotCreatedError(Exception):
    """The request failed before the command submitter was invoked."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


class PodcastSubmissionUncertainError(Exception):
    """The command submitter was invoked, but acceptance is not known."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


class PodcastService:
    """Service layer for podcast operations"""

    @staticmethod
    async def _gate_offline_cloud_models(episode_profile, speaker_profile) -> None:
        """v0.8.68 — raise a clear, typed error when the machine is offline
        (real or Offline-mode toggle) and the podcast's profiles reference
        cloud-provider models. Local providers (llama.cpp / ollama / piper via
        openai_compatible) are never blocked. Best-effort: any failure inside
        the gate itself is swallowed so it can't break a submit that would
        have worked before."""
        try:
            from deeper_notebook.ai.offline_gate import LOCAL_PROVIDERS
            from deeper_notebook.health.network import (
                get_network_state_with_settings,
            )

            state = await get_network_state_with_settings()
            if state.status != "offline":
                return

            cloud_models: list[str] = []
            for label, resolver in (
                ("voice (text-to-speech)", speaker_profile.resolve_tts_config),
                ("outline LLM", episode_profile.resolve_outline_config),
                ("transcript LLM", episode_profile.resolve_transcript_config),
            ):
                try:
                    provider, model_name, _cfg = await resolver()
                except Exception:
                    continue  # unresolvable profile keeps its existing error path
                if (provider or "").strip().lower().replace(
                    "-", "_"
                ) not in LOCAL_PROVIDERS:
                    cloud_models.append(f"{label}: {model_name} ({provider})")
        except ConfigurationError:
            raise
        except Exception as exc:
            logger.debug(f"podcast offline gate skipped (non-fatal): {exc}")
            return

        if cloud_models:
            reason = "Offline mode is on" if state.forced_offline else "You're offline"
            raise ConfigurationError(
                f"{reason}, and this podcast profile uses cloud models that "
                f"can't be reached: {'; '.join(cloud_models)}. Reconnect, or "
                f"switch the profile to local models (Settings → Podcasts)."
            )

    @staticmethod
    async def submit_generation_job(
        episode_profile_name: str,
        speaker_profile_name: str,
        episode_name: str,
        notebook_id: Optional[str] = None,
        content: Optional[str] = None,
        briefing_suffix: Optional[str] = None,
        mode: PodcastOverviewMode | str | None = None,
        custom_prompt: Optional[str] = None,
        episode_length: Optional[str] = None,
        review_outline: bool = False,
        execution_policy: Literal[
            "strict_local", "local_preferred", "custom"
        ] = "strict_local",
        compute_profile: Literal[
            "efficient", "balanced", "maximum_quality"
        ] = "balanced",
        include_transcription: bool = False,
        selection_summary: Optional[dict[str, Any]] = None,
        selection_fingerprint: Optional[str] = None,
        editorial_brief: Optional[dict[str, Any]] = None,
        model_plan_receipts: Optional[list[dict[str, Any]]] = None,
        classify_submission_failures: bool = False,
    ) -> str:
        """Submit a podcast generation job for background processing"""
        command_invocation_started = False
        try:
            # Validate episode profile exists
            episode_profile = await EpisodeProfile.get_by_name(episode_profile_name)
            if not episode_profile:
                raise ValueError(f"Episode profile '{episode_profile_name}' not found")

            # Validate speaker profile exists
            speaker_profile = await SpeakerProfile.get_by_name(speaker_profile_name)
            if not speaker_profile:
                raise ValueError(f"Speaker profile '{speaker_profile_name}' not found")

            normalized_mode = normalize_podcast_mode(mode)
            required_speakers = get_podcast_mode_spec(normalized_mode).speaker_count
            if len(speaker_profile.speakers) < required_speakers:
                raise ValueError(
                    f"Audio Overview format '{normalized_mode.value}' requires "
                    f"{required_speakers} speaker{'s' if required_speakers != 1 else ''}, "
                    f"but profile '{speaker_profile_name}' has only "
                    f"{len(speaker_profile.speakers)}."
                )

            # v0.8.68 — offline gate at SUBMIT time (spec §6 follow-up). The
            # podcast worker calls TTS/LLM providers directly (not through
            # provision_langchain_model), so the chat offline gate never sees
            # it: offline + a cloud voice/LLM previously meant a job that hung
            # against an unreachable provider for up to the 1800s timeout
            # before failing. Fail fast HERE with the offending models named,
            # while the user is looking at the submit button. Fail-open on
            # resolution errors: an unresolvable profile keeps its existing
            # downstream error path.
            await PodcastService._gate_offline_cloud_models(
                episode_profile, speaker_profile
            )

            # Get content from notebook if not provided directly
            if not content and notebook_id:
                try:
                    notebook = await Notebook.get(notebook_id)
                    # v0.7.201 — was `str(notebook) if no get_context`
                    # which, on a stale ID (Notebook.get returned None),
                    # silently wrote the literal string "None" as the
                    # podcast's content. Generation then went through
                    # with empty source material and produced a
                    # nonsensical episode. Raise NotFoundError before
                    # touching `notebook` so the user gets a clear
                    # error at submission time.
                    if notebook is None:
                        from deeper_notebook.exceptions import NotFoundError

                        raise NotFoundError(f"Notebook {notebook_id} not found")
                    content = (
                        await notebook.get_context()
                        if hasattr(notebook, "get_context")
                        else str(notebook)
                    )
                except Exception as e:
                    # v0.7.201 — let typed NotFoundError pass through
                    # so the global classifier emits a clean 404 with
                    # the user-facing message above. Other failures
                    # still fall back to the notebook-id-only content
                    # path (kept for backward compat with non-fatal
                    # transient DB hiccups).
                    from deeper_notebook.exceptions import NotFoundError

                    if isinstance(e, NotFoundError):
                        raise
                    logger.warning(
                        f"Failed to get notebook content, using notebook_id as content: {e}"
                    )
                    content = f"Notebook ID: {notebook_id}"

            if not content:
                raise ValueError(
                    "Content is required - provide either content or notebook_id"
                )

            # v0.8.68 — content token budget. The full content goes to the
            # outline LLM untruncated, so a huge notebook selection blew the
            # model's context window MID-JOB with a generic provider error
            # after minutes of waiting. Check at submit instead, while the
            # user is still looking at the dialog. Env-tunable
            # (DEEPER_NOTEBOOK_PODCAST_MAX_CONTENT_TOKENS, 0 disables); the default is
            # generous for cloud models but catches the pathological cases.
            try:
                import os as _os

                from deeper_notebook.utils import token_count

                _max_tokens = int(
                    resolve_env("DEEPER_NOTEBOOK_PODCAST_MAX_CONTENT_TOKENS", "100000")
                    or 100000
                )
            except Exception:
                _max_tokens = 100000
            if _max_tokens > 0:
                try:
                    _content_tokens = token_count(str(content))
                except Exception:
                    _content_tokens = None  # tokenizer hiccup → don't block
                if _content_tokens is not None and _content_tokens > _max_tokens:
                    raise InvalidInputError(
                        f"The selected content is too large for podcast "
                        f"generation (~{_content_tokens:,} tokens, limit "
                        f"{_max_tokens:,}). Select fewer sources, or raise "
                        f"DEEPER_NOTEBOOK_PODCAST_MAX_CONTENT_TOKENS if your models can "
                        f"handle it."
                    )

            resolved_custom_prompt = (
                custom_prompt or briefing_suffix or ""
            ).strip() or None

            # Prepare command arguments. Keep briefing_suffix for commands
            # persisted by pre-0.8.95 desktop clients; new workers persist the
            # same text as custom_prompt so retries can be exact.
            command_args = {
                # v0.8.126 — carried to the worker so the episode is notebook-scoped.
                "notebook_id": notebook_id,
                "episode_profile": episode_profile_name,
                "speaker_profile": speaker_profile_name,
                "episode_name": episode_name,
                "content": str(content),
                "briefing_suffix": briefing_suffix,
                "mode": normalized_mode.value,
                "custom_prompt": resolved_custom_prompt,
                # v0.8.86 — per-episode length override (None → profile default).
                "episode_length": episode_length,
                # v0.8.68 — outline-review workflow flag.
                "review_outline": bool(review_outline),
                "execution_policy": execution_policy,
                "compute_profile": compute_profile,
                "include_transcription": bool(include_transcription),
                "selection_summary": selection_summary,
                "selection_fingerprint": selection_fingerprint,
                "editorial_brief": editorial_brief,
                "model_plan_receipts": model_plan_receipts or [],
            }

            # Ensure command modules are imported before submitting
            # This is needed because submit_command validates against local registry
            try:
                import commands.podcast_commands  # noqa: F401
            except ImportError as import_err:
                logger.error(f"Failed to import podcast commands: {import_err}")
                raise ValueError("Podcast commands not available")

            # v0.7.55 — surreal_commands.submit_command opens a SYNCHRONOUS
            # SurrealDB WS connection (sign-in + use + create), which
            # blocks the FastAPI event loop for the duration of the
            # handshake. Under concurrent podcast submissions or general
            # load this stalls every other in-flight request (chat
            # streams, SSE polls, etc.). Move the blocking call onto a
            # worker thread so the event loop stays responsive.
            # v0.7.115 — also wrap in wait_for so a hung pool can't
            # pin the podcast-generation endpoint. Same env knob as
            # CommandService.submit_command_job for consistency.
            _submit_timeout = float(
                (
                    resolve_env(
                        "DEEPER_NOTEBOOK_SUBMIT_COMMAND_TIMEOUT_SEC",
                        "10",
                    )
                    or "10"
                ).strip()
                or 10
            )
            try:
                command_invocation_started = True
                job_id = await asyncio.wait_for(
                    asyncio.to_thread(
                        submit_command,
                        "open_notebook",
                        "generate_podcast",
                        command_args,
                    ),
                    timeout=_submit_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise ValueError(
                    f"Podcast submission timed out after {_submit_timeout:.0f}s. "
                    "The SurrealDB pool may be saturated. Raise "
                    "DEEPER_NOTEBOOK_SUBMIT_COMMAND_TIMEOUT_SEC or check pool health."
                ) from exc

            # Convert RecordID to string if needed
            if not job_id:
                raise ValueError("Failed to get job_id from submit_command")
            job_id_str = str(job_id)
            logger.info(
                f"Submitted podcast generation job: {job_id_str} for episode '{episode_name}'"
            )
            return job_id_str

        except (InvalidInputError, ConfigurationError) as exc:
            # v0.8.68 — let the typed exceptions raised by the offline gate
            # and the content-budget check bubble to the global handlers in
            # api/main.py (400 / 422 with their actionable messages). The
            # broad `except Exception` below otherwise converted them into a
            # generic 500 "Server error", hiding exactly the guidance those
            # errors exist to deliver.
            if classify_submission_failures and not command_invocation_started:
                raise PodcastSubmissionNotCreatedError(exc) from exc
            if classify_submission_failures:
                raise PodcastSubmissionUncertainError(exc) from exc
            raise
        except ValueError as e:
            if classify_submission_failures:
                error_type = (
                    PodcastSubmissionUncertainError
                    if command_invocation_started
                    else PodcastSubmissionNotCreatedError
                )
                raise error_type(e) from e
            logger.warning(f"Podcast submission rejected: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            if classify_submission_failures:
                error_type = (
                    PodcastSubmissionUncertainError
                    if command_invocation_started
                    else PodcastSubmissionNotCreatedError
                )
                raise error_type(e) from e
            logger.error(f"Failed to submit podcast generation job: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to submit podcast generation job",
            )

    @staticmethod
    async def submit_outline_approval(episode_id: str) -> str:
        """v0.8.68 — phase 2 of the outline-review workflow: submit the
        resume_podcast command for an episode awaiting review. The offline
        gate runs here (transcript LLM + TTS are about to be used)."""
        try:
            from deeper_notebook.podcasts.models import (
                STAGE_AWAITING_REVIEW,
                PodcastEpisode,
            )

            episode = await PodcastEpisode.get(episode_id)
            if not episode:
                raise ValueError(f"Episode '{episode_id}' not found")
            if episode.generation_stage != STAGE_AWAITING_REVIEW:
                raise ValueError(
                    f"Episode is not awaiting outline review "
                    f"(stage: {episode.generation_stage})"
                )

            ep_name = (episode.episode_profile or {}).get("name")
            sp_name = (episode.speaker_profile or {}).get("name")
            episode_profile = (
                await EpisodeProfile.get_by_name(ep_name) if ep_name else None
            )
            speaker_profile = (
                await SpeakerProfile.get_by_name(sp_name) if sp_name else None
            )
            if not episode_profile or not speaker_profile:
                raise ValueError(
                    "The episode/speaker profile used for this outline no "
                    "longer exists — restore it and approve again."
                )
            await PodcastService._gate_offline_cloud_models(
                episode_profile, speaker_profile
            )

            try:
                import commands.podcast_commands  # noqa: F401
            except ImportError as import_err:
                logger.error(f"Failed to import podcast commands: {import_err}")
                raise ValueError("Podcast commands not available")

            _submit_timeout = float(
                (
                    resolve_env(
                        "DEEPER_NOTEBOOK_SUBMIT_COMMAND_TIMEOUT_SEC",
                        "10",
                    )
                    or "10"
                ).strip()
                or 10
            )
            try:
                job_id = await asyncio.wait_for(
                    asyncio.to_thread(
                        submit_command,
                        "open_notebook",
                        "resume_podcast",
                        {"episode_id": str(episode.id)},
                    ),
                    timeout=_submit_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise ValueError(
                    f"Approval submission timed out after "
                    f"{_submit_timeout:.0f}s. The SurrealDB pool may be "
                    f"saturated."
                ) from exc
            if not job_id:
                raise ValueError("Failed to get job_id from submit_command")
            logger.info(
                f"Submitted outline approval (resume) job {job_id} for "
                f"episode {episode_id}"
            )
            return str(job_id)
        except (InvalidInputError, ConfigurationError):
            raise  # typed errors keep their status codes (offline gate etc.)
        except ValueError as e:
            # v0.7.58 — distinguish user-input errors (missing profile,
            # missing content, "commands not available") from genuine
            # 500s. Previously the broad `except Exception` mapped every
            # one of these to HTTP 500 "Server error", which is wrong:
            # they're caller mistakes, the user should see a 400 with
            # the actual reason ("Episode profile 'X' not found").
            logger.warning(f"Podcast submission rejected: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            # v0.7.177 — Don't echo str(e) back to the client. The
            # underlying exception can carry driver internals (SurrealDB
            # WS frames, connection-pool diagnostics, partial RecordIDs)
            # which are sensitive operationally and useless to the
            # caller. logger.error captures the full picture for ops;
            # the client gets a generic message. Same pattern as the
            # v0.7.168 router sweep that this service file missed.
            logger.error(f"Failed to submit podcast generation job: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to submit podcast generation job",
            )

    @staticmethod
    async def get_job_status(job_id: str) -> dict[str, Any]:
        """Get status of a podcast generation job"""
        try:
            status = await get_command_status(job_id)
            return {
                "job_id": job_id,
                "status": status.status if status else "unknown",
                "result": status.result if status else None,
                "error_message": getattr(status, "error_message", None)
                if status
                else None,
                "created": iso(status.created)
                if status and hasattr(status, "created") and status.created
                else None,
                "updated": iso(status.updated)
                if status and hasattr(status, "updated") and status.updated
                else None,
                "progress": getattr(status, "progress", None) if status else None,
            }
        except Exception as e:
            # v0.7.177 — Sanitize 500 detail; logger.error keeps the
            # full exception for ops, the client gets a generic message.
            logger.error(f"Failed to get podcast job status: {e}")
            raise HTTPException(status_code=500, detail="Failed to get job status")

    @staticmethod
    async def list_episodes(notebook_id: Optional[str] = None) -> list:
        """List podcast episodes.

        v0.8.126 — optional `notebook_id` scopes the list to episodes
        generated from that notebook (`PodcastEpisode.get_for_notebook`).
        Omitted/None keeps the previous unfiltered, all-episodes behavior.
        """
        try:
            if notebook_id:
                return await PodcastEpisode.get_for_notebook(notebook_id)
            episodes = await PodcastEpisode.get_all(order_by="created desc")
            return episodes
        except Exception as e:
            # v0.7.177 — Sanitize 500 detail (see above).
            logger.error(f"Failed to list podcast episodes: {e}")
            raise HTTPException(status_code=500, detail="Failed to list episodes")

    @staticmethod
    async def get_episode(episode_id: str) -> PodcastEpisode:
        """Get a specific podcast episode"""
        # v0.7.204 — was a bare `try/except Exception` that turned
        # EVERY failure (DB connection drop, mid-query timeout,
        # decryption error, etc.) into 404 "Episode not found". An
        # operator looking at logs saw a real backend issue but
        # the API client got the same 404 it would for a stale ID
        # — debugging took 10× longer because the symptom was
        # misclassified. Now: a None return from
        # `PodcastEpisode.get` (the actual "not found" path) raises
        # NotFoundError; everything else propagates as its real
        # type and hits the global classifier with the right
        # HTTP code (500 for DB, 502 for upstream, etc.).
        from deeper_notebook.exceptions import NotFoundError

        episode = await PodcastEpisode.get(episode_id)
        if episode is None:
            raise NotFoundError(f"Episode {episode_id} not found")
        return episode


class DefaultProfiles:
    """Utility class for creating default profiles (if needed beyond migration data)"""

    @staticmethod
    async def create_default_episode_profiles():
        """Create default episode profiles if they don't exist"""
        try:
            # Check if profiles already exist
            existing = await EpisodeProfile.get_all()
            if existing:
                logger.info(f"Episode profiles already exist: {len(existing)} found")
                return existing

            # This would create profiles, but since we have migration data,
            # this is mainly for future extensibility
            logger.info(
                "Default episode profiles should be created via database migration"
            )
            return []

        except Exception as e:
            logger.error(f"Failed to create default episode profiles: {e}")
            raise

    @staticmethod
    async def create_default_speaker_profiles():
        """Create default speaker profiles if they don't exist"""
        try:
            # Check if profiles already exist
            existing = await SpeakerProfile.get_all()
            if existing:
                logger.info(f"Speaker profiles already exist: {len(existing)} found")
                return existing

            # This would create profiles, but since we have migration data,
            # this is mainly for future extensibility
            logger.info(
                "Default speaker profiles should be created via database migration"
            )
            return []

        except Exception as e:
            logger.error(f"Failed to create default speaker profiles: {e}")
            raise
