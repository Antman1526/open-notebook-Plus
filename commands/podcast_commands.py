import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field
from surreal_commands import CommandInput, CommandOutput, command

from deeper_notebook.config import DATA_FOLDER
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.environment import resolve_env
from deeper_notebook.podcasts.models import (
    STAGE_AWAITING_REVIEW,
    STAGE_CANCELLED,
    STAGE_OUTLINE,
    STAGE_TRANSCRIPT,
    EpisodeProfile,
    PodcastEpisode,
    PodcastOverviewMode,
    SpeakerProfile,
    _resolve_model_config,
    mode_prompt_contract,
    normalize_podcast_mode,
    transcript_segments_from_payload,
)

try:
    from podcast_creator import configure
except ImportError as e:
    logger.error(f"Failed to import podcast_creator: {e}")
    raise ValueError("podcast_creator library not available")

# v0.8.68 — staged runner (streams podcast-creator's exported graph for
# per-stage progress, cancellation, and the outline-review workflow).
from commands.podcast_staged import (
    CancelledByUser,
    build_state_and_config,
    generate_outline_only,
    get_full_graph,
    get_resume_graph,
    run_graph_with_stages,
)


def build_episode_output_dir(data_folder: str) -> tuple[str, Path]:
    """Build a filesystem-safe output directory path for a podcast episode.

    Uses a UUID as the directory name so the path is safe regardless of
    what the user typed as episode name (spaces, special chars, etc.).

    Returns:
        A tuple of (episode_dir_name, output_dir_path).
    """
    episode_dir_name = str(uuid.uuid4())
    output_dir = Path(f"{data_folder}/podcasts/episodes/{episode_dir_name}")
    return episode_dir_name, output_dir


def full_model_dump(model):
    if isinstance(model, BaseModel):
        return model.model_dump()
    elif isinstance(model, dict):
        return {k: full_model_dump(v) for k, v in model.items()}
    elif isinstance(model, list):
        return [full_model_dump(item) for item in model]
    else:
        return model


class PodcastGenerationInput(CommandInput):
    episode_profile: str
    # v0.8.126 — persisted onto the episode so a notebook can list and bundle
    # its podcasts. Optional: profile-only generations have no notebook.
    notebook_id: Optional[str] = None
    speaker_profile: str
    episode_name: str
    content: str
    briefing_suffix: Optional[str] = None
    mode: PodcastOverviewMode = PodcastOverviewMode.DEEP_DIVE
    custom_prompt: Optional[str] = None
    # v0.8.86 — per-episode length: "short" | "medium" | "long" (overrides the
    # profile's num_segments for this episode). None → use the profile default.
    episode_length: Optional[str] = None
    # v0.8.68 — outline-review workflow: when True, generation stops after
    # the outline stage; the user reviews/edits it in the UI and approval
    # submits a resume_podcast command for the remaining stages.
    review_outline: bool = False
    execution_policy: Literal["strict_local", "local_preferred", "custom"] = (
        "strict_local"
    )
    compute_profile: Literal["efficient", "balanced", "maximum_quality"] = "balanced"
    include_transcription: bool = False
    # Phase 2 Studio receipts. These are redacted decisions and counts only;
    # source bodies remain in `content` and never appear in these fields.
    selection_summary: Optional[dict[str, Any]] = None
    selection_fingerprint: Optional[str] = None
    editorial_brief: Optional[dict[str, Any]] = None
    model_plan_receipts: list[dict[str, Any]] = Field(default_factory=list)


class PodcastResumeInput(CommandInput):
    """v0.8.68 — phase 2 of the outline-review workflow."""

    episode_id: str


async def _load_and_configure_all_profiles(episode_profile, speaker_profile) -> None:
    """v0.8.68 — extracted from generate_podcast_command so the resume
    command shares it. Loads every profile, resolves model-registry
    references to provider/model/config triples, drops UNRELATED profiles
    that fail resolution (podcast-creator validates the whole config), and
    fail-fasts if the SELECTED profiles didn't survive. Calls
    podcast_creator.configure() — the precondition for
    load_episode_config/load_speaker_config."""
    # v0.7.169 — bounded LIMIT 1000 (see CHANGELOG for rationale).
    episode_profiles = await repo_query("SELECT * FROM episode_profile LIMIT 1000")
    speaker_profiles = await repo_query("SELECT * FROM speaker_profile LIMIT 1000")
    if len(episode_profiles) >= 1000 or len(speaker_profiles) >= 1000:
        logger.warning(
            "Hit LIMIT 1000 on podcast profile load — extending the "
            "cap is safe but suggests the profile tables grew "
            "unexpectedly large (episode={}, speaker={}).",
            len(episode_profiles),
            len(speaker_profiles),
        )

    episode_profiles_dict = {p["name"]: p for p in episode_profiles}
    speaker_profiles_dict = {p["name"]: p for p in speaker_profiles}

    # Resolve ALL episode profiles (podcast-creator validates all).
    # Remove profiles that fail resolution to prevent validation errors.
    for ep_name in list(episode_profiles_dict.keys()):
        ep_dict = episode_profiles_dict[ep_name]
        try:
            if ep_dict.get("outline_llm"):
                prov, model, conf = await _resolve_model_config(
                    str(ep_dict["outline_llm"])
                )
                ep_dict["outline_provider"] = prov
                ep_dict["outline_model"] = model
                ep_dict["outline_config"] = conf
            if ep_dict.get("transcript_llm"):
                prov, model, conf = await _resolve_model_config(
                    str(ep_dict["transcript_llm"])
                )
                ep_dict["transcript_provider"] = prov
                ep_dict["transcript_model"] = model
                ep_dict["transcript_config"] = conf
        except Exception as e:
            logger.warning(
                f"Failed to resolve models for episode profile '{ep_name}', "
                f"removing from config to prevent validation errors: {e}"
            )
            del episode_profiles_dict[ep_name]

    # Resolve TTS for ALL speaker profiles; same removal policy.
    for sp_name in list(speaker_profiles_dict.keys()):
        sp_dict = speaker_profiles_dict[sp_name]
        if sp_dict.get("voice_model"):
            try:
                prov, model, conf = await _resolve_model_config(
                    str(sp_dict["voice_model"])
                )
                sp_dict["tts_provider"] = prov
                sp_dict["tts_model"] = model
                sp_dict["tts_config"] = conf
            except Exception as e:
                logger.warning(
                    f"Failed to resolve TTS for speaker profile '{sp_name}', "
                    f"removing from config to prevent validation errors: {e}"
                )
                del speaker_profiles_dict[sp_name]
                continue

        # Per-speaker TTS overrides
        for speaker in sp_dict.get("speakers", []):
            if speaker.get("voice_model"):
                try:
                    prov, model, conf = await _resolve_model_config(
                        str(speaker["voice_model"])
                    )
                    speaker["tts_provider"] = prov
                    speaker["tts_model"] = model
                    speaker["tts_config"] = conf
                except Exception as e:
                    logger.warning(
                        f"Failed to resolve per-speaker TTS for '{speaker.get('name')}': {e}"
                    )

    # v0.8.68 — defensive guard: the SELECTED profiles must have survived.
    if episode_profile.name not in episode_profiles_dict:
        raise ValueError(
            f"Episode profile '{episode_profile.name}' references models "
            f"that could not be resolved (deleted model or missing "
            f"credential). Fix the profile in Settings → Podcasts and retry."
        )
    if speaker_profile.name not in speaker_profiles_dict:
        raise ValueError(
            f"Speaker profile '{speaker_profile.name}' references a voice "
            f"model that could not be resolved (deleted model or missing "
            f"credential). Fix the profile in Settings → Podcasts and retry."
        )

    configure("speakers_config", {"profiles": speaker_profiles_dict})
    configure("episode_config", {"profiles": episode_profiles_dict})
    logger.info("Configured podcast-creator with episode and speaker profiles")


class PodcastGenerationOutput(CommandOutput):
    success: bool
    episode_id: Optional[str] = None
    audio_file_path: Optional[str] = None
    transcript: Optional[dict] = None
    outline: Optional[dict] = None
    processing_time: float
    error_message: Optional[str] = None


@command("generate_podcast", app="open_notebook", retry={"max_attempts": 1})
async def generate_podcast_command(
    input_data: PodcastGenerationInput,
) -> PodcastGenerationOutput:
    """
    Real podcast generation using podcast-creator library with Episode Profiles
    """
    start_time = time.time()

    try:
        logger.info(
            f"Starting podcast generation for episode: {input_data.episode_name}"
        )
        logger.info(f"Using episode profile: {input_data.episode_profile}")

        # 1. Load Episode and Speaker profiles from SurrealDB
        episode_profile = await EpisodeProfile.get_by_name(input_data.episode_profile)
        if not episode_profile:
            raise ValueError(
                f"Episode profile '{input_data.episode_profile}' not found"
            )

        speaker_profile = await SpeakerProfile.get_by_name(
            episode_profile.speaker_config
        )
        if not speaker_profile:
            raise ValueError(
                f"Speaker profile '{episode_profile.speaker_config}' not found"
            )

        logger.info(f"Loaded episode profile: {episode_profile.name}")
        logger.info(f"Loaded speaker profile: {speaker_profile.name}")

        # 2. Validate that model registry fields are populated
        if not episode_profile.outline_llm:
            raise ValueError(
                f"Episode profile '{episode_profile.name}' has no outline model configured. "
                "Please update the profile to select an outline model."
            )
        if not episode_profile.transcript_llm:
            raise ValueError(
                f"Episode profile '{episode_profile.name}' has no transcript model configured. "
                "Please update the profile to select a transcript model."
            )
        if not speaker_profile.voice_model:
            raise ValueError(
                f"Speaker profile '{speaker_profile.name}' has no voice model configured. "
                "Please update the profile to select a voice model."
            )

        # 3. Resolve model configs with credentials
        (
            outline_provider,
            outline_model_name,
            outline_config,
        ) = await episode_profile.resolve_outline_config()
        (
            transcript_provider,
            transcript_model_name,
            transcript_config,
        ) = await episode_profile.resolve_transcript_config()
        (
            tts_provider,
            tts_model_name,
            tts_config,
        ) = await speaker_profile.resolve_tts_config()

        logger.info(
            f"Resolved models - outline: {outline_provider}/{outline_model_name}, "
            f"transcript: {transcript_provider}/{transcript_model_name}, "
            f"tts: {tts_provider}/{tts_model_name}"
        )

        # 4+5. Load all profiles, resolve model registry references, and
        # configure podcast-creator (extracted v0.8.68 — shared with
        # resume_podcast_command; includes the selected-profile guard).
        await _load_and_configure_all_profiles(episode_profile, speaker_profile)

        # 6. Generate briefing. The closed overview format supplies the
        # non-negotiable editorial contract; the custom prompt is preserved
        # separately and is applied after it so retries reproduce the request.
        mode = normalize_podcast_mode(input_data.mode)
        custom_prompt = (
            input_data.custom_prompt or input_data.briefing_suffix or ""
        ).strip() or None
        briefing = episode_profile.default_briefing
        briefing += (
            f"\n\nAudio Overview format ({mode.value}): {mode_prompt_contract(mode)}"
        )
        if custom_prompt:
            briefing += f"\n\nAdditional instructions: {custom_prompt}"
        # v0.8.68 — pass the profile's language through to generation. The
        # EpisodeProfile.language field (BCP 47) existed since the model
        # registry rework and create_podcast() accepts language=, but the two
        # were never connected — episodes always came out in English.
        _episode_language = (episode_profile.language or "").strip() or None

        # Create the record for the episode and associate with the ongoing command
        episode = PodcastEpisode(
            name=input_data.episode_name,
            notebook_id=input_data.notebook_id,
            episode_profile=full_model_dump(episode_profile.model_dump()),
            speaker_profile=full_model_dump(speaker_profile.model_dump()),
            command=ensure_record_id(input_data.execution_context.command_id)
            if input_data.execution_context
            else None,
            briefing=briefing,
            # v0.8.68 — stored separately so retry can replay it verbatim.
            briefing_suffix=input_data.briefing_suffix,
            mode=mode,
            custom_prompt=custom_prompt,
            content=input_data.content,
            audio_file=None,
            transcript=None,
            outline=None,
            transcript_segments=[],
            selection_summary=input_data.selection_summary,
            selection_fingerprint=input_data.selection_fingerprint,
            editorial_brief=input_data.editorial_brief,
            model_plan_receipts=input_data.model_plan_receipts,
        )
        await episode.save()

        logger.info(f"Generated briefing (length: {len(briefing)} chars)")

        # 7. Create output directory using UUID for filesystem-safe paths
        episode_dir_name, output_dir = build_episode_output_dir(DATA_FOLDER)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Created output directory: {output_dir}")

        # v0.8.68 — build the graph state ourselves (mirrors create_podcast's
        # setup; see commands/podcast_staged.py) so we can stream stages,
        # honor the cancel flag, and support the outline-review workflow.
        state, graph_config = build_state_and_config(
            content=input_data.content,
            briefing=briefing,
            episode_profile_name=episode_profile.name,
            speaker_profile_name=speaker_profile.name,
            language=_episode_language,
            output_dir=str(output_dir),
            episode_name=episode_dir_name,
            # v0.8.86 — per-episode length override (None → profile default).
            episode_length=input_data.episode_length,
            mode=mode,
            custom_prompt=custom_prompt,
        )

        # Outline-review phase 1: outline only, then stop for user review.
        if input_data.review_outline:
            episode.generation_stage = STAGE_OUTLINE
            await episode.save()
            outline_out = await generate_outline_only(state, graph_config)
            episode.outline = (
                full_model_dump(outline_out.get("outline"))
                if outline_out and outline_out.get("outline") is not None
                else None
            )
            if not episode.outline:
                raise RuntimeError(
                    "Outline generation returned no outline — check the "
                    "outline model in the episode profile."
                )
            episode.generation_stage = STAGE_AWAITING_REVIEW
            await episode.save()
            # The empty output dir is left behind intentionally? No — the
            # resume command creates its own dir; sweep this one.
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except Exception:
                pass
            processing_time = time.time() - start_time
            logger.info(
                f"Outline ready for review on episode {episode.id} "
                f"({processing_time:.2f}s)"
            )
            return PodcastGenerationOutput(
                success=True,
                episode_id=str(episode.id),
                outline=episode.outline,
                processing_time=processing_time,
            )

        # 8. Generate podcast using podcast-creator.
        # v0.7.3 — wrap in try/except so we can clean up empty output_dir
        # if generation fails. Without this, every failed generation
        # leaves an empty UUID directory under data/podcasts/episodes/
        # that accumulates over time (disk-fill). We only remove the dir
        # if it's empty post-failure — never delete partial output that
        # might still be useful for diagnostics.
        logger.info("Starting podcast generation with podcast-creator...")

        # v0.7.138 — Bound the whole podcast-creator call with a
        # generous timeout. Real podcast generation has many phases
        # (outline LLM → transcript LLM × N segments → TTS × N speakers)
        # and can legitimately take 5-30 minutes on a local-deploy
        # install. Default 1800s (30 minutes) is large but bounded;
        # without this, a hung TTS provider or wedged local model
        # could pin a worker slot indefinitely (the @command
        # `max_attempts: 1` means there's no retry — a hang is forever
        # unless we cap it).
        #
        # Tunable via DEEPER_NOTEBOOK_PODCAST_GENERATION_TIMEOUT_SEC. A timeout
        # propagates as a regular exception → @command framework
        # marks the episode as failed → episode.delete() cleanup
        # path below fires, including the empty-output-dir sweep.
        _podcast_timeout = float(
            resolve_env(
                "DEEPER_NOTEBOOK_PODCAST_GENERATION_TIMEOUT_SEC", "1800"
            ).strip()
            or 1800
        )
        episode.generation_stage = STAGE_OUTLINE
        await episode.save()
        try:
            # v0.8.68 — stream the library's own graph instead of the
            # create_podcast() black box: per-stage progress lands on the
            # episode record, the cancel flag is honored within ~5s, and a
            # timeout names the stage that hung.
            result = await run_graph_with_stages(
                get_full_graph(),
                state,
                graph_config,
                episode=episode,
                deadline=time.monotonic() + _podcast_timeout,
            )
        except CancelledByUser:
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except Exception:
                pass
            episode.generation_stage = STAGE_CANCELLED
            await episode.save()
            raise RuntimeError(
                f"Generation cancelled by user during stage "
                f"{episode.generation_stage or 'startup'} for episode "
                f"{input_data.episode_name!r}."
            )
        except asyncio.TimeoutError as exc:
            # Treat the timeout as a generation failure for output-dir
            # cleanup purposes: fall through to the existing
            # empty-dir-cleanup logic and re-raise as a clear
            # message. Don't raise as ValueError (which surreal_commands
            # treats as permanent) — a timeout is operationally
            # transient even if @command retries are disabled.
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
                    logger.info(
                        "Cleaned up empty output dir after timeout: {}",
                        output_dir,
                    )
            except Exception:
                pass
            raise RuntimeError(
                f"Podcast generation timed out after {_podcast_timeout}s for "
                f"episode {input_data.episode_name!r} while in stage "
                f"'{episode.generation_stage or 'startup'}'. The provider "
                f"for that stage may be hung or significantly slower than "
                f"expected. Raise DEEPER_NOTEBOOK_PODCAST_GENERATION_TIMEOUT_SEC if it "
                f"legitimately needs more time, or check provider health."
            ) from exc
        except Exception:
            # Leave non-empty dirs alone — those have partial output
            # (transcript file, intermediate WAVs) that the user can
            # inspect to understand the failure.
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
                    logger.info(
                        "Cleaned up empty output dir after failure: {}",
                        output_dir,
                    )
            except Exception as cleanup_exc:
                logger.warning(
                    "Could not clean up output dir {} after failure: {}",
                    output_dir,
                    cleanup_exc,
                )
            raise
        episode.generation_stage = None

        # v0.7.3 — defensive: result may be None (early-return) or a
        # partial dict missing keys (future podcast-creator versions, edge
        # cases where audio succeeded but transcript/outline failed). The
        # PodcastGenerationOutput block below already uses .get() for the
        # same fields — this block was inconsistent and would KeyError on
        # the indexed access, masking a partial success as a worker crash.
        episode.audio_file = (
            str(result.get("final_output_file_path")) if result else None
        )
        episode.transcript = {
            "transcript": full_model_dump(result.get("transcript"))
            if result and result.get("transcript") is not None
            else None
        }
        episode.transcript_segments = transcript_segments_from_payload(
            result.get("transcript") if result else None,
            mode=episode.mode,
        )
        episode.outline = (
            full_model_dump(result.get("outline"))
            if result and result.get("outline") is not None
            else None
        )
        await episode.save()

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully generated podcast episode: {episode.id} in {processing_time:.2f}s"
        )

        # v0.7.69 — harden against `result is None` and partial-result
        # shapes. The earlier `episode.audio_file = ...` block (lines
        # 286-298) was fixed in v0.7.3 to handle this, but THIS return
        # block was missed — `result["transcript"]` / `result["outline"]`
        # subscript access would AttributeError on a None result, masking
        # what is otherwise a successful (but transcript/outline-less)
        # generation as a worker crash that the user can't retry from
        # (max_attempts=1). The earlier block uses the same `.get()`
        # pattern below, applied uniformly.
        transcript_payload = None
        outline_payload = None
        audio_path = None
        if result:
            audio_path = (
                str(result.get("final_output_file_path"))
                if result.get("final_output_file_path") is not None
                else None
            )
            t = result.get("transcript")
            if t is not None:
                transcript_payload = {"transcript": full_model_dump(t)}
            o = result.get("outline")
            if o is not None:
                outline_payload = full_model_dump(o)

        return PodcastGenerationOutput(
            success=True,
            episode_id=str(episode.id),
            audio_file_path=audio_path,
            transcript=transcript_payload,
            outline=outline_payload,
            processing_time=processing_time,
        )

    except ValueError:
        raise

    except Exception as e:
        logger.error(f"Podcast generation failed: {e}")
        logger.exception(e)

        error_msg = str(e)
        if "Invalid json output" in error_msg or "Expecting value" in error_msg:
            error_msg += (
                "\n\nNOTE: This error commonly occurs with GPT-5 models that use extended thinking. "
                "The model may be putting all output inside <think> tags, leaving nothing to parse. "
                "Try using gpt-4o, gpt-4o-mini, or gpt-4-turbo instead in your episode profile."
            )

        raise RuntimeError(error_msg) from e


@command("resume_podcast", app="open_notebook", retry={"max_attempts": 1})
async def resume_podcast_command(
    input_data: PodcastResumeInput,
) -> PodcastGenerationOutput:
    """v0.8.68 — phase 2 of the outline-review workflow: the user approved
    (and possibly edited) the outline; generate transcript + audio from it.
    Runs podcast-creator's own node functions via the resume graph (starts
    at the transcript node), with the same staged progress, cancellation,
    and timeout semantics as the full generation command."""
    start_time = time.time()

    try:
        episode = await PodcastEpisode.get(input_data.episode_id)
        if not episode:
            raise ValueError(f"Episode '{input_data.episode_id}' not found")
        if episode.generation_stage != STAGE_AWAITING_REVIEW:
            raise ValueError(
                f"Episode '{episode.name}' is not awaiting outline review "
                f"(stage: {episode.generation_stage})"
            )
        if not episode.outline or not episode.outline.get("segments"):
            raise ValueError(f"Episode '{episode.name}' has no outline to resume from")

        ep_name = (episode.episode_profile or {}).get("name")
        sp_name = (episode.speaker_profile or {}).get("name")
        episode_profile = await EpisodeProfile.get_by_name(ep_name) if ep_name else None
        if not episode_profile:
            raise ValueError(
                f"Episode profile '{ep_name}' no longer exists — restore it "
                f"(or recreate it with the same name) and approve again."
            )
        speaker_profile = await SpeakerProfile.get_by_name(sp_name) if sp_name else None
        if not speaker_profile:
            raise ValueError(
                f"Speaker profile '{sp_name}' no longer exists — restore it "
                f"(or recreate it with the same name) and approve again."
            )

        await _load_and_configure_all_profiles(episode_profile, speaker_profile)

        # Link this episode to the resume command and reset the cancel flag.
        episode.command = (
            ensure_record_id(input_data.execution_context.command_id)
            if input_data.execution_context
            else episode.command
        )
        episode.cancel_requested = False
        episode.generation_stage = STAGE_TRANSCRIPT
        await episode.save()

        episode_dir_name, output_dir = build_episode_output_dir(DATA_FOLDER)
        output_dir.mkdir(parents=True, exist_ok=True)

        _language = (episode_profile.language or "").strip() or None
        state, graph_config = build_state_and_config(
            content=episode.content,
            briefing=episode.briefing,
            episode_profile_name=episode_profile.name,
            speaker_profile_name=speaker_profile.name,
            language=_language,
            output_dir=str(output_dir),
            episode_name=episode_dir_name,
            outline=episode.outline,  # the user-reviewed outline drives TTS
            mode=getattr(episode, "mode", PodcastOverviewMode.DEEP_DIVE),
            custom_prompt=(
                getattr(episode, "custom_prompt", None)
                or getattr(episode, "briefing_suffix", None)
            ),
        )

        _podcast_timeout = float(
            resolve_env(
                "DEEPER_NOTEBOOK_PODCAST_GENERATION_TIMEOUT_SEC", "1800"
            ).strip()
            or 1800
        )
        try:
            result = await run_graph_with_stages(
                get_resume_graph(),
                state,
                graph_config,
                episode=episode,
                deadline=time.monotonic() + _podcast_timeout,
            )
        except CancelledByUser:
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except Exception:
                pass
            episode.generation_stage = STAGE_CANCELLED
            await episode.save()
            raise RuntimeError(
                f"Generation cancelled by user for episode {episode.name!r}."
            )
        except asyncio.TimeoutError as exc:
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except Exception:
                pass
            raise RuntimeError(
                f"Podcast generation timed out after {_podcast_timeout}s for "
                f"episode {episode.name!r} while in stage "
                f"'{episode.generation_stage or 'startup'}'."
            ) from exc
        except Exception:
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except Exception:
                pass
            raise

        episode.generation_stage = None
        episode.audio_file = (
            str(result.get("final_output_file_path"))
            if result.get("final_output_file_path") is not None
            else None
        )
        t = result.get("transcript")
        episode.transcript = (
            {"transcript": full_model_dump(t)} if t is not None else None
        )
        episode.transcript_segments = transcript_segments_from_payload(
            t, mode=getattr(episode, "mode", PodcastOverviewMode.DEEP_DIVE)
        )
        await episode.save()

        processing_time = time.time() - start_time
        logger.info(
            f"Resumed podcast episode {episode.id} completed in {processing_time:.2f}s"
        )
        return PodcastGenerationOutput(
            success=True,
            episode_id=str(episode.id),
            audio_file_path=episode.audio_file,
            transcript=episode.transcript,
            outline=episode.outline,
            processing_time=processing_time,
        )

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Podcast resume failed: {e}")
        logger.exception(e)
        raise RuntimeError(str(e)) from e
