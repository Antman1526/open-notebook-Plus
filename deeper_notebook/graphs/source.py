import hashlib
import operator
import os
import tempfile
from typing import Any, Dict, List

from content_core import extract_content
from content_core.common import ProcessSourceState
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from loguru import logger
from typing_extensions import Annotated, TypedDict

from deeper_notebook.ai.models import Model, ModelManager
from deeper_notebook.domain.content_settings import ContentSettings
from deeper_notebook.domain.notebook import Asset, Source
from deeper_notebook.domain.transformation import Transformation
from deeper_notebook.graphs.transformation import graph as transform_graph
from deeper_notebook.research.safe_fetch import SafeFetchResponse, fetch_public_url


def _text_from_safe_response(response: SafeFetchResponse) -> tuple[str, str]:
    """Extract text locally after the network boundary has accepted a page."""
    text = response.text
    if response.content_type != "text/html":
        return "Imported Web Source", text
    try:
        from bs4 import BeautifulSoup

        document = BeautifulSoup(text, "lxml")
        for element in document(["script", "style", "noscript"]):
            element.decompose()
        title = (
            document.title.get_text(strip=True)
            if document.title
            else "Imported Web Source"
        )
        return title or "Imported Web Source", document.get_text(" ", strip=True)
    except Exception:
        # A malformed but public response is still safe to ingest as plain text.
        return "Imported Web Source", text


async def _extract_checked_url(content_state: dict[str, Any]):
    """Fetch a URL once, then pass only local data to the extraction library."""
    response = await fetch_public_url(content_state["url"])
    if response.content_type.startswith("text/") or response.content_type in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
    }:
        title, content = _text_from_safe_response(response)
        from content_core.common.state import ProcessSourceOutput

        return ProcessSourceOutput(
            title=content_state.get("title") or title,
            content=content,
            url=response.url,
            source_type="url",
            identified_type="text",
        )

    suffix = os.path.splitext(response.url.split("?", 1)[0])[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
        temporary_file.write(response.body)
        temporary_path = temporary_file.name
    try:
        processed = await extract_content(
            {
                "file_path": temporary_path,
                "document_engine": content_state.get("document_engine"),
                "output_format": content_state.get("output_format"),
            }
        )
        processed.url = response.url
        processed.file_path = None
        return processed
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


class SourceState(TypedDict):
    content_state: ProcessSourceState
    apply_transformations: list[Transformation]
    source_id: str
    notebook_ids: list[str]
    source: Source
    transformation: Annotated[list, operator.add]
    embed: bool


class TransformationState(TypedDict):
    source: Source
    transformation: Transformation


async def content_process(state: SourceState) -> dict:
    # v0.7.209 — HIGH: previously this node constructed a FRESH
    # `ContentSettings(...)` with hardcoded literals every time,
    # silently overriding the user's persisted preferences. The
    # Settings page in the UI writes to the persisted singleton
    # (see `api/routers/settings.py`); toggling
    # `default_content_processing_engine_doc` / `_url`,
    # `auto_delete_files`, or `youtube_preferred_languages` then
    # had ZERO effect on the actual ingest pipeline because this
    # node ignored the DB record. Now load the singleton via the
    # RecordModel base class.
    #
    # Defensive: if the DB load fails for any reason (cold cache,
    # transient pool error, fresh install with no record yet),
    # fall back to the same hardcoded defaults so a startup hiccup
    # doesn't block source ingestion.
    try:
        content_settings = await ContentSettings.get_instance()
    except Exception as exc:
        logger.warning(
            "content_process: failed to load ContentSettings "
            "singleton (%s); using safe defaults",
            exc,
        )
        content_settings = ContentSettings(
            default_content_processing_engine_doc="auto",
            default_content_processing_engine_url="auto",
            default_embedding_option="ask",
            auto_delete_files="yes",
            youtube_preferred_languages=[
                "en",
                "pt",
                "es",
                "de",
                "nl",
                "en-GB",
                "fr",
                "hi",
                "ja",
            ],
        )
    content_state: dict[str, Any] = state["content_state"]  # type: ignore[assignment]

    content_state["url_engine"] = (
        content_settings.default_content_processing_engine_url or "auto"
    )
    content_state["document_engine"] = (
        content_settings.default_content_processing_engine_doc or "auto"
    )
    content_state["output_format"] = "markdown"

    # Add speech-to-text model configuration from Default Models
    try:
        model_manager = ModelManager()
        defaults = await model_manager.get_defaults()
        if defaults.default_speech_to_text_model:
            stt_model = await Model.get(defaults.default_speech_to_text_model)
            if stt_model:
                content_state["audio_provider"] = stt_model.provider
                content_state["audio_model"] = stt_model.name
                logger.debug(
                    f"Using speech-to-text model: {stt_model.provider}/{stt_model.name}"
                )
    except Exception as e:
        logger.warning(f"Failed to retrieve speech-to-text model configuration: {e}")
        # Continue without custom audio model (content-core will use its default)

    processed_state = None
    url = content_state.get("url")
    if content_state.get("url_engine") == "crawl4ai" and url:
        # v0.8.67u — Integrated crawl4ai scraping with standard content_core fallback.
        from content_core.common.state import ProcessSourceOutput

        from deeper_notebook.research.safe_fetch import fetch_public_url
        from deeper_notebook.utils.crawler import extract_url_with_crawl4ai

        # Do not allow the optional renderer to own network access. It receives
        # a response already checked at connection time by the URL policy.
        checked_response = await fetch_public_url(url)
        content = await extract_url_with_crawl4ai(url, prefetched=checked_response)
        if content:
            processed_state = ProcessSourceOutput(
                title=content_state.get("title") or "Imported Web Source (crawl4ai)",
                content=content,
                url=url,
                source_type="url",
                identified_type="text",
            )

    if processed_state is None:
        if url:
            processed_state = await _extract_checked_url(content_state)
        else:
            processed_state = await extract_content(content_state)

    # content-core signals a soft extraction failure (e.g. an unreachable or
    # invalid URL) by returning title="Error" and content prefixed with
    # "Failed to extract content:" instead of raising. Detect that sentinel and
    # raise so the job is marked failed and the source becomes retryable, rather
    # than being saved as a "completed" source whose body is the error string.
    if processed_state.title == "Error" and (processed_state.content or "").startswith(
        "Failed to extract content:"
    ):
        raise ValueError(
            "Could not extract content from this source. "
            "The URL or file may be unreachable, invalid, or in an unsupported format."
        )

    if not processed_state.content or not processed_state.content.strip():
        url = processed_state.url or ""
        if url and ("youtube.com" in url or "youtu.be" in url):
            raise ValueError(
                "Could not extract content from this YouTube video. "
                "No transcript or subtitles are available. "
                "Try configuring a Speech-to-Text model in Settings "
                "to transcribe the audio instead."
            )
        raise ValueError(
            "Could not extract any text content from this source. "
            "The content may be empty, inaccessible, or in an unsupported format."
        )

    return {"content_state": processed_state}


async def save_source(state: SourceState) -> dict:
    content_state = state["content_state"]

    # Get existing source using the provided source_id
    source = await Source.get(state["source_id"])
    if not source:
        raise ValueError(f"Source with ID {state['source_id']} not found")

    # Update the source with processed content
    source.asset = Asset(url=content_state.url, file_path=content_state.file_path)
    source.full_text = content_state.content
    extraction_provenance = {
        key: value
        for key, value in {
            "content_source_type": getattr(content_state, "source_type", None),
            "identified_type": getattr(content_state, "identified_type", None),
            "extractor": "content_core",
            "url": getattr(content_state, "url", None),
            "file_path": getattr(content_state, "file_path", None),
        }.items()
        if value is not None
    }
    content_metadata = getattr(content_state, "metadata", None)
    if isinstance(content_metadata, dict):
        extraction_provenance["content_metadata"] = content_metadata
    source.provenance = {
        **(getattr(source, "provenance", None) or {}),
        "extraction": extraction_provenance,
    }
    # Study source readiness and artifact generation require an evidence
    # fingerprint.  Derive it from the actual extracted UTF-8 text at the
    # publication boundary; never trust a client-supplied provenance value.
    source.provenance["content_fingerprint"] = hashlib.sha256(
        source.full_text.encode("utf-8")
    ).hexdigest()

    # Preserve user-set title; only overwrite placeholder or empty titles
    if content_state.title and (not source.title or source.title == "Processing..."):
        source.title = content_state.title

    await source.save()

    # NOTE: Notebook associations are created by the API immediately for UI responsiveness
    # No need to create them here to avoid duplicate edges

    if state["embed"]:
        if source.full_text and source.full_text.strip():
            logger.debug("Embedding content for vector search")
            await source.vectorize()
        else:
            logger.warning(
                f"Source {source.id} has no text content to embed, skipping vectorization"
            )

    return {"source": source}


def trigger_transformations(state: SourceState, config: RunnableConfig) -> list[Send]:
    if len(state["apply_transformations"]) == 0:
        return []

    to_apply = state["apply_transformations"]
    logger.debug(f"Applying transformations {to_apply}")

    return [
        Send(
            "transform_content",
            {
                "source": state["source"],
                "transformation": t,
            },
        )
        for t in to_apply
    ]


async def transform_content(state: TransformationState) -> dict:
    source = state["source"]
    content = source.full_text
    if not content:
        # v0.7.61 — must return a state-shaped dict, not None. SourceState
        # declares `transformation: Annotated[list, operator.add]` so
        # LangGraph applies `current + returned` at merge time. With
        # None, that became `[] + None` → TypeError, which killed the
        # whole graph run mid-fan-out and left the source half-saved
        # (asset + full_text persisted, transformations never applied,
        # only a generic 500 surfaced to the user). Returning an empty
        # transformations list cleanly no-ops this branch.
        return {"transformation": []}
    transformation: Transformation = state["transformation"]

    logger.debug(f"Applying transformation {transformation.name}")
    try:
        result = await transform_graph.ainvoke(
            dict(input_text=content, transformation=transformation)  # type: ignore[arg-type]
        )
        # v0.7.165 — LangGraph state-shape dual-path guard.
        # `transform_graph` happens to return a TypedDict today, so
        # `result["output"]` works — but CLAUDE.md's standing audit rule
        # flags subscript / `.get()` against ainvoke output as a state-
        # shape blind spot (the same pattern that produced the v0.7.52,
        # 55, 56, 75, 81, 95 series of fixes). Normalize once so a future
        # LangGraph release that returns a Pydantic state can't crash
        # source ingestion with KeyError / AttributeError mid-transform.
        output_text = (
            result["output"]
            if isinstance(result, dict)
            else (getattr(result, "output", "") or "")
        )
        await source.add_insight(transformation.title, output_text)
        return {
            "transformation": [
                {
                    "output": output_text,
                    "transformation_name": transformation.name,
                }
            ]
        }
    except Exception as exc:
        # Non-fatal transformation guard: provider outages, missing models, or timeouts
        # during transformation must not fail the source ingestion pipeline.
        logger.warning(
            f"Transformation '{transformation.name}' failed on source {source.id} (non-fatal): {exc}"
        )
        return {"transformation": []}


# Create and compile the workflow
workflow = StateGraph(SourceState)

# Add nodes
workflow.add_node("content_process", content_process)
workflow.add_node("save_source", save_source)
workflow.add_node("transform_content", transform_content)
# Define the graph edges
workflow.add_edge(START, "content_process")
workflow.add_edge("content_process", "save_source")
workflow.add_conditional_edges(
    "save_source", trigger_transformations, ["transform_content"]
)
workflow.add_edge("transform_content", END)

# Compile the graph
source_graph = workflow.compile()
