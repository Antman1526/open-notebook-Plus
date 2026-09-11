import inspect
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel
from surreal_commands import CommandInput, CommandOutput, command

from deeper_notebook.database.repository import ensure_record_id
from deeper_notebook.domain.content_settings import ContentSettings
from deeper_notebook.domain.notebook import Source
from deeper_notebook.domain.transformation import (
    KEY_TOPICS_TRANSFORMATION_TITLE,
    Transformation,
    get_or_create_key_topics_transformation,
    get_or_create_summarize_transformation,
    parse_topics,
)
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import ConfigurationError
from deeper_notebook.feature_flags import source_visuals_enabled
from deeper_notebook.source_visuals.authority import compute_source_visual_authority
from deeper_notebook.source_visuals.queue import submit_source_visual

try:
    from deeper_notebook.graphs.source import source_graph
    from deeper_notebook.graphs.transformation import graph as transform_graph
except ImportError as e:
    logger.error(f"Failed to import graphs: {e}")
    raise ValueError("graphs not available")


def full_model_dump(model):
    if isinstance(model, BaseModel):
        return model.model_dump()
    elif isinstance(model, dict):
        return {k: full_model_dump(v) for k, v in model.items()}
    elif isinstance(model, list):
        return [full_model_dump(item) for item in model]
    else:
        return model


async def _await_if_needed(value):
    """Keep the existing production awaits while supporting synchronous test seams."""

    if inspect.isawaitable(value):
        return await value
    return value


def _visual_handoff_error_code(error: object) -> str:
    raw = str(getattr(error, "code", "handoff_failed")).strip().lower()
    safe = "".join(character if character.isalnum() else "_" for character in raw)
    return safe.strip("_")[:64] or "handoff_failed"


class SourceProcessingInput(CommandInput):
    source_id: str
    content_state: dict[str, Any]
    notebook_ids: list[str]
    transformations: list[str]
    embed: bool


class SourceProcessingOutput(CommandOutput):
    success: bool
    source_id: str
    embedded_chunks: int = 0
    insights_created: int = 0
    processing_time: float
    error_message: Optional[str] = None


@command(
    "process_source",
    app="open_notebook",
    retry={
        "max_attempts": 15,  # Handle deep queues (workaround for SurrealDB v2 transaction conflicts)
        "wait_strategy": "exponential_jitter",
        "wait_min": 1,
        "wait_max": 120,  # Allow queue to drain
        "stop_on": [
            ValueError,
            ConfigurationError,
        ],  # Don't retry validation/config errors
        "retry_log_level": "debug",  # Avoid log noise during transaction conflicts
    },
)
async def process_source_command(
    input_data: SourceProcessingInput,
) -> SourceProcessingOutput:
    """
    Process source content using the source_graph workflow
    """
    start_time = time.time()

    try:
        logger.info(f"Starting source processing for source: {input_data.source_id}")
        logger.info(f"Notebook IDs: {input_data.notebook_ids}")
        logger.info(f"Transformations: {input_data.transformations}")
        logger.info(f"Embed: {input_data.embed}")

        # 1. Load transformation objects from IDs
        transformations = []
        for trans_id in input_data.transformations:
            logger.info(f"Loading transformation: {trans_id}")
            transformation = await Transformation.get(trans_id)
            if not transformation:
                raise ValueError(f"Transformation '{trans_id}' not found")
            transformations.append(transformation)

        logger.info(f"Loaded {len(transformations)} transformations")

        # v0.8.88 / v0.8.91 — opt-in auto-summary + key-topics on ingest. When
        # enabled in ContentSettings, append the built-in "summarize" / "key
        # topics" transformations so the existing transform node produces a
        # Summary / Key Topics insight on ingest. Best-effort: never let this
        # fail ingest, and don't double-add if the user already requested one.
        extract_topics = False
        try:
            content_settings = await ContentSettings.get_instance()
            if getattr(content_settings, "auto_summarize_on_ingest", False):
                summarize = await get_or_create_summarize_transformation()
                if not any(str(t.id) == str(summarize.id) for t in transformations):
                    transformations.append(summarize)
                    logger.info(
                        "Auto-summary enabled — added 'Summary' transformation to ingest."
                    )
            if getattr(content_settings, "auto_extract_topics_on_ingest", False):
                key_topics = await get_or_create_key_topics_transformation()
                if not any(str(t.id) == str(key_topics.id) for t in transformations):
                    transformations.append(key_topics)
                    logger.info(
                        "Auto key-topics enabled — added 'Key Topics' transformation to ingest."
                    )
                extract_topics = True
        except Exception as e:  # non-fatal
            logger.warning(f"Auto-summary/topics setup skipped (non-fatal): {e}")

        # 2. Get existing source record to update its command field
        source = await _await_if_needed(Source.get(input_data.source_id))
        if not source:
            raise ValueError(f"Source '{input_data.source_id}' not found")

        # Update source with command reference
        source.command = (
            ensure_record_id(input_data.execution_context.command_id)
            if input_data.execution_context
            else None
        )
        await source.save()

        logger.info(f"Updated source {source.id} with command reference")

        # 3. Process source with all notebooks
        logger.info(f"Processing source with {len(input_data.notebook_ids)} notebooks")

        # Execute source_graph with all notebooks
        result = await _await_if_needed(
            source_graph.ainvoke(
                {  # type: ignore[arg-type]
                    "content_state": input_data.content_state,
                    "notebook_ids": input_data.notebook_ids,  # Use notebook_ids (plural) as expected by SourceState
                    "apply_transformations": transformations,
                    "embed": input_data.embed,
                    "source_id": input_data.source_id,  # Add the source_id to the state
                }
            )
        )

        processed_source = result["source"]

        # 4. Gather processing results (notebook associations handled by source_graph)
        # Note: embedding is fire-and-forget (async job), so we can't query the
        # count here — it hasn't completed yet. The embed_source_command logs
        # the actual count when it finishes.
        insights_list = await processed_source.get_insights()
        insights_created = len(insights_list)

        # v0.8.91 — populate the source's `topics` from the Key Topics insight so
        # the card's topic badges light up. Best-effort: never fail processing.
        if extract_topics:
            try:
                topic_insight = next(
                    (
                        i
                        for i in insights_list
                        if getattr(i, "insight_type", None)
                        == KEY_TOPICS_TRANSFORMATION_TITLE
                    ),
                    None,
                )
                topics = parse_topics(topic_insight.content) if topic_insight else []
                if topics:
                    processed_source.topics = topics
                    await processed_source.save()
                    logger.info(
                        f"Key-topics: set {len(topics)} topics on {processed_source.id}"
                    )
            except Exception as e:  # non-fatal
                logger.warning(f"Key-topics population skipped (non-fatal): {e}")

        processing_time = time.time() - start_time
        embed_status = "submitted" if input_data.embed else "skipped"
        logger.info(
            f"Successfully processed source: {processed_source.id} in {processing_time:.2f}s"
        )
        logger.info(f"Created {insights_created} insights, embedding {embed_status}")

        if source_visuals_enabled():
            try:
                visual_authority = await _await_if_needed(
                    compute_source_visual_authority(processed_source)
                )
                await _await_if_needed(
                    submit_source_visual(
                        str(processed_source.id),
                        f"ingest:{visual_authority.content_sha256}",
                        explicit=False,
                    )
                )
            except Exception as visual_error:
                logger.warning(
                    "Source visual handoff failed with bounded code {}",
                    _visual_handoff_error_code(visual_error),
                )

        return SourceProcessingOutput(
            success=True,
            source_id=str(processed_source.id),
            embedded_chunks=0,
            insights_created=insights_created,
            processing_time=processing_time,
        )

    except ValueError as e:
        # Validation errors are permanent failures - don't retry
        processing_time = time.time() - start_time
        logger.error(f"Source processing failed: {e}")
        # v0.7.209 — Orphan-row cleanup. The API created a
        # placeholder source row with `title="Processing..."`
        # BEFORE submitting this command (sources.py:509-514 /
        # :601-605). On a permanent ValueError (extract failure
        # on a corrupted PDF, unreadable file, etc.) the source
        # row is left orphaned in the DB forever — the user sees
        # a phantom "Processing..." entry that never updates and
        # can only be removed via manual delete (which itself
        # can fail because there's no asset / chunks to clean up).
        #
        # Delete the placeholder ONLY when its title still reads
        # "Processing..." (means the user hadn't renamed it
        # mid-flight) AND `full_text` is still empty (extraction
        # never wrote anything). Both conditions together
        # guarantee we're cleaning up an unsalvageable orphan,
        # not a partially-processed source the user might want to
        # retry manually.
        try:
            orphan = await Source.get(input_data.source_id)
            if (
                orphan
                and (orphan.title or "") == "Processing..."
                and not (orphan.full_text or "").strip()
            ):
                await orphan.delete()
                logger.info(
                    "v0.7.209 orphan-cleanup: deleted placeholder "
                    "source %s after permanent extract failure",
                    input_data.source_id,
                )
            elif orphan:
                # v0.8.128 — persist explicit failed status so partial text
                # does not cause derived status to report "completed".
                orphan.provenance = {
                    **(getattr(orphan, "provenance", None) or {}),
                    "processing_status": "failed",
                    "processing_error": str(e),
                }
                await orphan.save()
        except Exception as cleanup_exc:
            logger.warning(
                "v0.7.209 orphan-cleanup: failed to delete/update "
                "placeholder source %s after extract failure "
                "(leaving in place): %s",
                input_data.source_id,
                cleanup_exc,
            )
        return SourceProcessingOutput(
            success=False,
            source_id=input_data.source_id,
            processing_time=processing_time,
            error_message=str(e),
        )
    except Exception as e:
        # Transient failure - will be retried (surreal-commands logs final failure)
        logger.debug(f"Transient error processing source {input_data.source_id}: {e}")
        raise


# =============================================================================
# RUN TRANSFORMATION COMMAND
# =============================================================================


class RunTransformationInput(CommandInput):
    """Input for running a transformation on an existing source."""

    source_id: str
    transformation_id: str


class RunTransformationOutput(CommandOutput):
    """Output from transformation command."""

    success: bool
    source_id: str
    transformation_id: str
    processing_time: float
    error_message: Optional[str] = None


@command(
    "run_transformation",
    app="open_notebook",
    retry={
        "max_attempts": 5,
        "wait_strategy": "exponential_jitter",
        "wait_min": 1,
        "wait_max": 60,
        "stop_on": [
            ValueError,
            ConfigurationError,
        ],  # Don't retry validation/config errors
        "retry_log_level": "debug",
    },
)
async def run_transformation_command(
    input_data: RunTransformationInput,
) -> RunTransformationOutput:
    """
    Run a transformation on an existing source to generate an insight.

    This command runs the transformation graph which:
    1. Loads the source and transformation
    2. Calls the LLM to generate insight content
    3. Creates the insight via create_insight command (fire-and-forget)

    Use this command for UI-triggered insight generation to avoid blocking
    the HTTP request while the LLM processes.

    Retry Strategy:
    - Retries up to 5 times for transient failures (network, timeout, etc.)
    - Uses exponential-jitter backoff (1-60s)
    - Does NOT retry permanent failures (ValueError for validation errors)
    """
    start_time = time.time()

    try:
        logger.info(
            f"Running transformation {input_data.transformation_id} "
            f"on source {input_data.source_id}"
        )

        # Load source
        source = await Source.get(input_data.source_id)
        if not source:
            raise ValueError(f"Source '{input_data.source_id}' not found")

        # Load transformation
        transformation = await Transformation.get(input_data.transformation_id)
        if not transformation:
            raise ValueError(
                f"Transformation '{input_data.transformation_id}' not found"
            )

        # Run transformation graph (includes LLM call + insight creation).
        #
        # v0.7.138 — bounded by DEEPER_NOTEBOOK_TRANSFORMATION_TIMEOUT_SEC (default
        # 180s, same env var as the HTTP-side /transformations/execute
        # endpoint). Without this, a hung chat model pinned the worker
        # slot indefinitely; surreal_commands retry would eventually
        # mark the command failed, but the loop time was unbounded.
        #
        # A TimeoutError here propagates through the retry-eligible
        # exception path: surreal_commands sees a non-ValueError /
        # ConfigurationError exception and applies its exponential-
        # jitter retry. After max_attempts retries (5) the command
        # surfaces as failed with the user-facing message.
        import asyncio
        import os as _os

        _xform_timeout = float(
            resolve_env("DEEPER_NOTEBOOK_TRANSFORMATION_TIMEOUT_SEC", "180").strip()
            or 180
        )
        try:
            await asyncio.wait_for(
                transform_graph.ainvoke(
                    input=dict(source=source, transformation=transformation)
                ),
                timeout=_xform_timeout,
            )
        except asyncio.TimeoutError as exc:
            # Re-raise as a regular exception (not ValueError) so the
            # surreal_commands retry kicks in — a transient hang on
            # one attempt shouldn't mark the whole transformation as
            # permanently failed.
            raise RuntimeError(
                f"Transformation graph timed out after {_xform_timeout}s "
                f"for source {input_data.source_id} / transformation "
                f"{input_data.transformation_id}. Worker will retry; "
                f"raise DEEPER_NOTEBOOK_TRANSFORMATION_TIMEOUT_SEC if your model "
                f"legitimately needs more time."
            ) from exc

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully ran transformation {input_data.transformation_id} "
            f"on source {input_data.source_id} in {processing_time:.2f}s"
        )

        return RunTransformationOutput(
            success=True,
            source_id=input_data.source_id,
            transformation_id=input_data.transformation_id,
            processing_time=processing_time,
        )

    except ValueError as e:
        # Validation errors are permanent failures - don't retry
        processing_time = time.time() - start_time
        logger.error(
            f"Failed to run transformation {input_data.transformation_id} "
            f"on source {input_data.source_id}: {e}"
        )
        return RunTransformationOutput(
            success=False,
            source_id=input_data.source_id,
            transformation_id=input_data.transformation_id,
            processing_time=processing_time,
            error_message=str(e),
        )
    except Exception as e:
        # Transient failure - will be retried (surreal-commands logs final failure)
        logger.debug(
            f"Transient error running transformation {input_data.transformation_id} "
            f"on source {input_data.source_id}: {e}"
        )
        raise
