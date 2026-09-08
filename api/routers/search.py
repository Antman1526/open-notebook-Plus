import json
from collections.abc import Mapping
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from api.models import (
    AskRequest,
    AskResponse,
    DeepResearchRequest,
    DeepResearchResponse,
    SearchRequest,
    SearchResponse,
)
from api.source_visual_projection import project_search_source_visuals
from deeper_notebook.ai.models import Model, model_manager
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import text_search, vector_search
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
)
from deeper_notebook.feature_flags import source_visuals_enabled
from deeper_notebook.graphs.ask import graph as ask_graph
from deeper_notebook.search.fusion import reciprocal_rank_fusion
from deeper_notebook.search.reranker import rerank_results

router = APIRouter()

_SOURCE_VISUAL_SEARCH_BATCH_LIMIT = 200


def _exact_results(results: list[dict], query: str) -> list[dict]:
    needle = query.casefold()
    return [
        result
        for result in results
        if str(result.get("title", "")).casefold() == needle
        or any(str(match).casefold() == needle for match in result.get("matches", []))
    ]


def _source_id_from_search_result(result: Mapping[str, Any]) -> str | None:
    """Extract only a direct source id or a source-insight parent id."""

    source_id = str(result.get("id", ""))
    if source_id.startswith("source:"):
        return source_id
    if source_id.startswith("source_insight:"):
        parent_id = str(result.get("parent_id", ""))
        if parent_id.startswith("source:"):
            return parent_id
    return None


async def _authoritative_search_source_rows(
    results: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """Batch-load the current revisions for source-bearing production search rows."""

    source_ids = list(
        dict.fromkeys(
            source_id
            for result in results
            if isinstance(result, Mapping)
            and (source_id := _source_id_from_search_result(result)) is not None
        )
    )[:_SOURCE_VISUAL_SEARCH_BATCH_LIMIT]
    if not source_ids:
        return []
    try:
        rows = await repo_query(
            "SELECT id, updated FROM source WHERE id IN $source_ids LIMIT $limit;",
            {
                "source_ids": [ensure_record_id(source_id) for source_id in source_ids],
                "limit": _SOURCE_VISUAL_SEARCH_BATCH_LIMIT,
            },
        )
    except Exception:
        # Visual projection is additive; a lookup failure must not alter the
        # established search contract or disclose database details.
        return []
    allowed = set(source_ids)
    authoritative: list[dict[str, object]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        source_id = str(row.get("id", ""))
        if source_id in allowed:
            authoritative.append({"id": source_id, "updated": row.get("updated")})
        if len(authoritative) >= _SOURCE_VISUAL_SEARCH_BATCH_LIMIT:
            break
    return authoritative


@router.post("/search", response_model=SearchResponse)
async def search_knowledge_base(search_request: SearchRequest):
    """Search the knowledge base using text or vector search."""
    # v0.7.102 — Per-call timeout. The underlying SurrealDB queries are
    # async but unbounded — a hung pool or a pathological vector index
    # state can pin the request indefinitely. Vector search ALSO calls
    # the embedding model on the query string, so it inherits the
    # provider-latency risk that v0.7.100 wrapped for connection tests.
    # 60s default is generous for any healthy DB + small embedding;
    # raise via env if you've intentionally over-loaded the box.
    import asyncio
    import os

    _search_timeout = float(
        resolve_env("DEEPER_NOTEBOOK_SEARCH_TIMEOUT_SEC", "60").strip() or 60
    )
    if (
        search_request.space_ids
        or search_request.authority_kinds
        or search_request.tags
    ):
        raise HTTPException(
            status_code=422,
            detail="Search filters are not supported by the current search index.",
        )
    effective_type = (
        "vector" if search_request.match_mode == "semantic" else search_request.type
    )
    try:
        if effective_type == "hybrid":
            # v0.8.113 — run both legs and fuse by rank. Keyword and semantic
            # retrieval miss in different directions (text misses paraphrase,
            # vector misses exact identifiers and error strings), so answering
            # with one and discarding the other loses recall on every query.
            #
            # Degrades rather than fails: with no embedding model the vector leg
            # is skipped and this is plain text search, which is strictly better
            # than the 400 the "vector" branch raises. Either leg erroring is
            # likewise absorbed — a hybrid search that dies because one half is
            # unavailable is worse than one that returns the other half.
            legs: list[list] = []
            embedding_ready = bool(await model_manager.get_embedding_model())
            for name, coro in (
                (
                    "vector",
                    vector_search(
                        keyword=search_request.query,
                        results=search_request.limit,
                        source=search_request.search_sources,
                        note=search_request.search_notes,
                        minimum_score=search_request.minimum_score,
                    )
                    if embedding_ready
                    else None,
                ),
                (
                    "text",
                    text_search(
                        keyword=search_request.query,
                        results=search_request.limit,
                        source=search_request.search_sources,
                        note=search_request.search_notes,
                    ),
                ),
            ):
                if coro is None:
                    continue
                try:
                    legs.append(await asyncio.wait_for(coro, timeout=_search_timeout))
                except asyncio.TimeoutError:
                    logger.warning(
                        "hybrid search: {} leg timed out after {}s; continuing "
                        "with the remaining leg(s)",
                        name,
                        _search_timeout,
                    )
                except Exception as leg_error:  # noqa: BLE001 - one leg must not sink the query
                    logger.warning(
                        "hybrid search: {} leg failed ({}); continuing with the "
                        "remaining leg(s)",
                        name,
                        leg_error,
                    )
            if not legs:
                raise HTTPException(
                    status_code=504,
                    detail=(
                        "Both search legs failed or timed out. Raise "
                        "DEEPER_NOTEBOOK_SEARCH_TIMEOUT_SEC, or check the "
                        "database and embedding model."
                    ),
                )
            candidate_limit = max(search_request.limit * 2, 20)
            candidate_results = reciprocal_rank_fusion(legs, limit=candidate_limit)
            results = await rerank_results(
                search_request.query,
                candidate_results,
                top_n=search_request.limit,
            )
        elif effective_type == "vector":
            # Check if embedding model is available for vector search
            if not await model_manager.get_embedding_model():
                raise HTTPException(
                    status_code=400,
                    detail="Vector search requires an embedding model. Please configure one in the Models section.",
                )

            try:
                results = await asyncio.wait_for(
                    vector_search(
                        keyword=search_request.query,
                        results=search_request.limit,
                        source=search_request.search_sources,
                        note=search_request.search_notes,
                        minimum_score=search_request.minimum_score,
                    ),
                    timeout=_search_timeout,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Vector search timed out after {_search_timeout:.0f}s. "
                        "The embedding model may be slow, or the database "
                        "pool is overloaded. Raise DEEPER_NOTEBOOK_SEARCH_TIMEOUT_SEC."
                    ),
                )
        else:
            # Text search
            try:
                results = await asyncio.wait_for(
                    text_search(
                        keyword=search_request.query,
                        results=search_request.limit,
                        source=search_request.search_sources,
                        note=search_request.search_notes,
                    ),
                    timeout=_search_timeout,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Text search timed out after {_search_timeout:.0f}s. "
                        "The database pool may be overloaded. Raise "
                        "DEEPER_NOTEBOOK_SEARCH_TIMEOUT_SEC."
                    ),
                )

        normalized_results = results or []
        if search_request.match_mode == "exact":
            normalized_results = _exact_results(
                normalized_results, search_request.query
            )
        if source_visuals_enabled():
            source_rows = await _authoritative_search_source_rows(normalized_results)
            if source_rows:
                normalized_results = await project_search_source_visuals(
                    normalized_results,
                    source_rows=source_rows,
                )
        else:
            # v0.8.86 — capability sentinel; see api/routers/sources.py.
            from api.schemas.source_visuals import disabled_visual_status

            sentinel = disabled_visual_status().model_dump(mode="json")
            normalized_results = [
                {**row, "visual_status": sentinel} if isinstance(row, dict) else row
                for row in normalized_results
            ]
        is_reranked = any(
            isinstance(row, dict) and "rerank_score" in row
            for row in normalized_results
        )
        return SearchResponse(
            results=normalized_results,
            total_count=len(normalized_results),
            search_type=effective_type,
            reranked=is_reranked,
        )

    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError, DatabaseOperationError):
        # v0.7.200 — bubble ALL typed open-notebook exceptions to the
        # global classifier middleware. Before, this function caught
        # `InvalidInputError` and `DatabaseOperationError` here and
        # collapsed them into HTTPException(400/500, "Search failed") —
        # defeating the v0.7.179-183 typed-exception sweep that taught
        # `main.py` to render user-friendly messages from
        # NotFoundError/InvalidInputError/AuthenticationError/etc.
        # The global handler (api/main.py) classifies
        # DatabaseOperationError into a 500 with a descriptive message,
        # so users see "Database query failed" instead of the
        # opaque "Search failed" placeholder.
        raise
    except Exception as e:
        logger.error(f"Unexpected error during search: {str(e)}")
        raise HTTPException(status_code=500, detail="Search failed")


async def stream_ask_response(
    question: str,
    strategy_model: Model,
    answer_model: Model,
    final_answer_model: Model,
    fastapi_request: Optional[Request] = None,
) -> AsyncGenerator[str, None]:
    """Stream the ask response as Server-Sent Events.

    v0.7.43 — final-answer phase now streams token-by-token. Previously
    `stream_mode="updates"` emitted exactly one event per node
    completion, so `write_final_answer` was a 20-60s wait followed by
    one giant payload. The final synthesis is the longest single LLM
    call in the whole app (consolidates multiple sub-answers) — the
    slowest UX path on the local-deploy build.

    Now using `astream_events(version="v2")`, which yields:
      - on_chain_end events when a node completes (used to emit
        the strategy and per-query answer events as before)
      - on_chat_model_stream events for each token the LLM emits
        (used to emit per-token deltas during the final-answer phase)

    We filter on_chat_model_stream events to ONLY the write_final_answer
    node — the per-query LLM calls inside provide_answer also emit
    token events, but those would clutter the wire (we already deliver
    them in batch as `answer` events on node completion). Filter
    matches on the `metadata.langgraph_node` field LangGraph attaches.

    Event types:
      - `strategy`          — search strategy decided
      - `answer`            — one sub-query's answer (batched per node)
      - `final_answer_delta`— one token of the final synthesis (NEW)
      - `final_answer`      — terminal canonical final-answer text
      - `complete`          — done
      - `error`             — failure
    """
    # v0.7.200 — Proper cancellation propagation to the upstream graph.
    #
    # Before: the function was a simple `async for event in
    # ask_graph.astream_events(...)`. On disconnect we did `return`,
    # which the async-generator translated into the iterator getting
    # GeneratorExit at its NEXT `await` boundary. That boundary
    # happens AFTER the current node finishes — and for the
    # `write_final_answer` node, the "current await" is the full
    # synthesis LLM call. Result: user navigates away, local LLM
    # still spends the next 30-60s tokenising tokens nobody reads.
    # On the desktop build this is real wasted GPU + battery.
    #
    # Fix: drive the iterator manually with `__anext__()` inside an
    # `asyncio.Task`, and cancel that task when we detect disconnect.
    # The cancel propagates into the in-flight LLM call (most modern
    # async LLM clients honour cancellation), stopping mid-token.
    # Mirrors the pattern v0.7.184 applied in chat.py.
    import asyncio

    try:
        final_answer = None
        final_answer_buffer = ""
        event_iter = ask_graph.astream_events(
            input=dict(question=question),  # type: ignore[arg-type]
            config=dict(
                configurable=dict(
                    strategy_model=strategy_model.id,
                    answer_model=answer_model.id,
                    final_answer_model=final_answer_model.id,
                )
            ),
            version="v2",
        ).__aiter__()

        while True:
            next_task = asyncio.ensure_future(event_iter.__anext__())
            try:
                # v0.7.200 — race the iterator against an async sleep
                # that periodically polls is_disconnected(). 200ms poll
                # is generous compared to typical LLM token latency
                # (50-200ms per token) so we don't burn CPU.
                while not next_task.done():
                    if (
                        fastapi_request is not None
                        and await fastapi_request.is_disconnected()
                    ):
                        logger.info(
                            "ask stream: client disconnected mid-stream; "
                            "cancelling in-flight graph + LLM call"
                        )
                        next_task.cancel()
                        try:
                            await next_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        # Best-effort close on the underlying iterator.
                        try:
                            await event_iter.aclose()  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        return
                    await asyncio.sleep(0.2)
                event = next_task.result()
            except StopAsyncIteration:
                break

            etype = event.get("event")
            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")

            # Per-token streaming for write_final_answer ONLY.
            if etype == "on_chat_model_stream" and node_name == "write_final_answer":
                chunk = event.get("data", {}).get("chunk")
                content = getattr(chunk, "content", None)
                if isinstance(content, str) and content:
                    final_answer_buffer += content
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "final_answer_delta",
                                "content": content,
                            }
                        )
                        + "\n\n"
                    )

            # Node-completion events drive the existing strategy/answer
            # events. `on_chain_end` fires with the node's output state.
            elif etype == "on_chain_end":
                output = event.get("data", {}).get("output")
                # v0.7.199 — LangGraph state-shape variance: a node
                # may return either a plain dict OR a Pydantic model
                # (depending on whether the node typed its return).
                # Bare `isinstance(output, dict)` previously dropped
                # all subsequent strategy/answer/final-answer SSE
                # events when a Pydantic model came through — the
                # user saw a blank streaming response. Mirror the
                # pattern v0.7.55 introduced in /search/ask/simple:
                # try dict access first, fall back to getattr.
                if output is None:
                    continue

                def _get(key: str):
                    if isinstance(output, dict):
                        return output.get(key)
                    return getattr(output, key, None)

                if node_name == "agent":
                    strategy = _get("strategy")
                    if strategy is None:
                        continue
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "strategy",
                                "reasoning": strategy.reasoning,
                                "searches": [
                                    {"term": s.term, "instructions": s.instructions}
                                    for s in strategy.searches
                                ],
                            }
                        )
                        + "\n\n"
                    )
                elif node_name == "provide_answer":
                    answers = _get("answers")
                    if not answers:
                        continue
                    for answer in answers:
                        yield (
                            "data: "
                            + json.dumps({"type": "answer", "content": answer})
                            + "\n\n"
                        )
                elif node_name == "write_final_answer":
                    final_answer = _get("final_answer")
                    if final_answer is None:
                        continue
                    # Terminal canonical event — fallback for clients
                    # that ignore deltas, and the final text after any
                    # post-processing (e.g. clean_thinking_content stripped
                    # tokens the streaming consumer saw).
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "final_answer",
                                "content": final_answer,
                            }
                        )
                        + "\n\n"
                    )

        # Send completion signal
        yield (
            "data: "
            + json.dumps({"type": "complete", "final_answer": final_answer})
            + "\n\n"
        )

    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        from deeper_notebook.utils.error_classifier import classify_error

        _, user_message = classify_error(e)
        logger.error(f"Error in ask streaming: {str(e)}")
        error_data = {"type": "error", "message": user_message}
        yield f"data: {json.dumps(error_data)}\n\n"


@router.post("/search/ask")
async def ask_knowledge_base(ask_request: AskRequest, fastapi_request: Request):
    """Ask the knowledge base a question using AI models."""
    try:
        # Validate models exist
        strategy_model = await Model.get(ask_request.strategy_model)
        answer_model = await Model.get(ask_request.answer_model)
        final_answer_model = await Model.get(ask_request.final_answer_model)

        if not strategy_model:
            raise HTTPException(
                status_code=400,
                detail=f"Strategy model {ask_request.strategy_model} not found",
            )
        if not answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Answer model {ask_request.answer_model} not found",
            )
        if not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Final answer model {ask_request.final_answer_model} not found",
            )

        # Check if embedding model is available
        if not await model_manager.get_embedding_model():
            raise HTTPException(
                status_code=400,
                detail="Ask feature requires an embedding model. Please configure one in the Models section.",
            )

        # For streaming response
        # v0.7.43 — proxy-flush headers so each NDJSON line lands
        # client-side immediately (same hint pair as /chat/stream).
        return StreamingResponse(
            stream_ask_response(
                ask_request.question,
                strategy_model,
                answer_model,
                final_answer_model,
                fastapi_request=fastapi_request,
            ),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers
        # (NotFoundError → 404, InvalidInputError → 400). Continuation
        # of the v0.7.179/181/182 sweep to the final routers.
        raise
    except Exception as e:
        logger.error(f"Error in ask endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Ask operation failed")


@router.post("/search/ask/simple", response_model=AskResponse)
async def ask_knowledge_base_simple(ask_request: AskRequest, fastapi_request: Request):
    """Ask the knowledge base a question and return a simple response (non-streaming)."""
    try:
        # Validate models exist
        strategy_model = await Model.get(ask_request.strategy_model)
        answer_model = await Model.get(ask_request.answer_model)
        final_answer_model = await Model.get(ask_request.final_answer_model)

        if not strategy_model:
            raise HTTPException(
                status_code=400,
                detail=f"Strategy model {ask_request.strategy_model} not found",
            )
        if not answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Answer model {ask_request.answer_model} not found",
            )
        if not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Final answer model {ask_request.final_answer_model} not found",
            )

        # Check if embedding model is available
        if not await model_manager.get_embedding_model():
            raise HTTPException(
                status_code=400,
                detail="Ask feature requires an embedding model. Please configure one in the Models section.",
            )

        # Run the ask graph and get final result
        # v0.7.55 — bail out if the client navigated away. The ask graph
        # fans out to multiple LLM calls; without this the local LLM
        # keeps generating answers nobody will see. Also guard the chunk
        # subscript against future Pydantic-shaped states (same root
        # cause as v0.7.52 chat.py fix).
        final_answer = None
        async for chunk in ask_graph.astream(
            input=dict(question=ask_request.question),  # type: ignore[arg-type]
            config=dict(
                configurable=dict(
                    strategy_model=strategy_model.id,
                    answer_model=answer_model.id,
                    final_answer_model=final_answer_model.id,
                )
            ),
            stream_mode="updates",
        ):
            if await fastapi_request.is_disconnected():
                logger.info(
                    "/search/ask/simple: client disconnected mid-stream; halting"
                )
                # v0.7.200 — was 499 (nginx-only) which renders as
                # "Unknown status" in FastAPI logs / Sentry / OpenTelemetry
                # exporters. 503 is the standard "service unavailable —
                # try again" code, which is what a client that already
                # gave up effectively asked for. Detail string is the
                # operative signal.
                raise HTTPException(
                    status_code=503,
                    detail="Client disconnected before answer ready",
                )
            node_output = (
                chunk.get("write_final_answer") if isinstance(chunk, dict) else None
            )
            if node_output is not None:
                if isinstance(node_output, dict):
                    final_answer = node_output.get("final_answer")
                else:
                    final_answer = getattr(node_output, "final_answer", None)

        if not final_answer:
            raise HTTPException(status_code=500, detail="No answer generated")

        return AskResponse(answer=final_answer, question=ask_request.question)

    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers
        # (NotFoundError → 404, InvalidInputError → 400). Continuation
        # of the v0.7.179/181/182 sweep to the final routers.
        raise
    except Exception as e:
        logger.error(f"Error in ask simple endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Ask operation failed")


@router.post("/search/deep-research", response_model=DeepResearchResponse)
async def deep_research_endpoint(
    request: DeepResearchRequest,
    fastapi_request: Request,
):
    """Execute a multi-step deep research investigation with hybrid search and reranking."""
    try:
        from deeper_notebook.graphs.deep_research import run_deep_research

        strategy_model_id = request.strategy_model
        synthesis_model_id = request.synthesis_model

        if strategy_model_id:
            model = await Model.get(strategy_model_id)
            if not model:
                raise HTTPException(
                    status_code=400,
                    detail=f"Strategy model {strategy_model_id} not found",
                )
        else:
            try:
                default_model = (
                    await model_manager.get_default_model("tools")
                    or await model_manager.get_default_model("chat")
                )
                if default_model:
                    strategy_model_id = default_model.id
            except Exception as e:
                logger.warning(f"Could not load default strategy model: {e}")

        if synthesis_model_id:
            model = await Model.get(synthesis_model_id)
            if not model:
                raise HTTPException(
                    status_code=400,
                    detail=f"Synthesis model {synthesis_model_id} not found",
                )
        else:
            try:
                final_model = (
                    await model_manager.get_default_model("reasoning")
                    or await model_manager.get_default_model("transformation")
                    or await model_manager.get_default_model("chat")
                )
                if final_model:
                    synthesis_model_id = final_model.id
            except Exception as e:
                logger.warning(f"Could not load default synthesis model: {e}")

        result = await run_deep_research(
            objective=request.objective,
            notebook_id=request.notebook_id,
            max_queries=request.max_queries,
            strategy_model=strategy_model_id,
            synthesis_model=synthesis_model_id,
        )

        plan = result.get("plan")
        plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else (plan or {})
        evidence = result.get("evidence") or []
        research_brief = result.get("research_brief") or ""
        citations = result.get("citations") or []
        agent_state = result.get("agent_state") or "complete"

        return DeepResearchResponse(
            objective=request.objective,
            plan=plan_dict,
            evidence_count=len(evidence),
            research_brief=research_brief,
            citations=citations,
            agent_state=agent_state,
        )

    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        raise
    except Exception as e:
        logger.error(f"Error in deep_research endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Deep research operation failed")
