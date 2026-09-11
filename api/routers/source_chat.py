import asyncio
import json
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.utils.iso import iso  # v0.7.182 — Safari-safe datetime serialization
from api.utils.stream_keepalive import (
    SSE_HEARTBEAT_FRAME,
    idle_timeout_message,
    with_keepalive,
)
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import ChatSession, Source
from deeper_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
)

# v0.7.192 — Lazy async-graph getter. See deeper_notebook/graphs/chat.py
# for the full rationale on the lazy/aiosqlite pattern.
from deeper_notebook.graphs.source_chat import get_async_source_chat_graph
from deeper_notebook.graphs.source_chat import source_chat_graph as source_chat_graph
from deeper_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# Request/Response models
class CreateSourceChatSessionRequest(BaseModel):
    source_id: str = Field(..., description="Source ID to create chat session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSourceChatSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    # v0.8.44b — persistent per-conversation MCP server disable picks.
    # Parity with notebook chat's v0.8.43 `UpdateSessionRequest`. When
    # the user toggles the source-chat MCP picker, the new state gets
    # PATCHed here so picks survive page reloads. exclude_unset
    # semantics in the handler mean omitting the field does NOT clear
    # the persisted value (so the existing rename/model-override flow
    # is untouched). null explicitly clears; [] is "explicitly none".
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names disabled for this session",
    )


class ChatMessage(BaseModel):
    id: str = Field(..., description="Message ID")
    type: str = Field(..., description="Message type (human|ai)")
    content: str = Field(..., description="Message content")
    timestamp: Optional[str] = Field(None, description="Message timestamp")


class ContextIndicator(BaseModel):
    sources: list[str] = Field(
        default_factory=list, description="Source IDs used in context"
    )
    insights: list[str] = Field(
        default_factory=list, description="Insight IDs used in context"
    )
    notes: list[str] = Field(
        default_factory=list, description="Note IDs used in context"
    )


class SourceChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    source_id: str = Field(..., description="Source ID")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    # v0.8.44b — persistent MCP server disable picks (null = none).
    # Source-chat sessions share the `chat_session` table with
    # notebook chat, so migration 20 (v0.8.43) already provisions the
    # column — no new migration needed. The frontend hydrates the
    # source-chat picker from this on session load.
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names disabled for this session",
    )


class SourceChatSessionWithMessagesResponse(SourceChatSessionResponse):
    messages: list[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )
    context_indicators: Optional[ContextIndicator] = Field(
        None, description="Context indicators from last response"
    )


class SendMessageRequest(BaseModel):
    message: str = Field(..., description="User message content")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )
    # v0.8.44 — per-request MCP server disable list (parity with
    # notebook chat's v0.8.42 `ExecuteChatRequest.disabled_mcp_servers`).
    # Names match `mcp_server.name` case-insensitively in
    # `_resolve_chat_tools`. Null = all enabled servers visible (the
    # pre-v0.8.44 default — no behavior change for existing clients).
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description=(
            "MCP server names to skip for this source-chat turn. "
            "Names match `mcp_server.name` case-insensitively."
        ),
    )


class SuccessResponse(BaseModel):
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")


@router.post(
    "/sources/{source_id}/chat/sessions", response_model=SourceChatSessionResponse
)
async def create_source_chat_session(
    request: CreateSourceChatSessionRequest,
    source_id: str = Path(..., description="Source ID"),
):
    """Create a new chat session for a source."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Create new session with model_override support
        session = ChatSession(
            title=request.title or f"Source Chat {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to source using "refers_to" relation
        await session.relate("refers_to", full_source_id)

        return SourceChatSessionResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=session.model_override,
            # v0.7.182 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=0,
            # v0.8.44b — newly-created session has no picks yet.
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError:
        # v0.7.183 — bubble InvalidInputError to the global handler
        # (→ 400). NotFoundError is already caught explicitly above
        # with a clean "Source not found" message; the v0.7.182 bulk
        # sweep also inserted it here, but that clause was unreachable.
        # Narrowed to InvalidInputError only.
        raise
    except Exception as e:
        logger.error(f"Error creating source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error creating source chat session"
        )


@router.get(
    "/sources/{source_id}/chat/sessions", response_model=list[SourceChatSessionResponse]
)
async def get_source_chat_sessions(source_id: str = Path(..., description="Source ID")):
    """Get all chat sessions for a source."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Get sessions that refer to this source - first get relations, then sessions
        relations = await repo_query(
            "SELECT in FROM refers_to WHERE out = $source_id",
            {"source_id": ensure_record_id(full_source_id)},
        )

        # v0.7.161 — N+1 fix: previously this loop did TWO sequential
        # round-trips per session (a row fetch + a LangGraph checkpoint
        # read). A source with 30 chat sessions paid 60 sequential
        # network/disk hits. Now we issue both fan-outs concurrently:
        #   1. asyncio.gather all session-row fetches in parallel
        #   2. then asyncio.gather all message-count reads in parallel
        # Each phase has its wall-clock bounded by the slowest single
        # call instead of the sum.
        session_ids: list[str] = [str(r["in"]) for r in relations if r.get("in")]
        session_rows = await asyncio.gather(
            *[
                repo_query("SELECT * FROM $id", {"id": ensure_record_id(sid)})
                for sid in session_ids
            ]
        )
        msg_counts = await asyncio.gather(
            *[get_session_message_count(source_chat_graph, sid) for sid in session_ids]
        )

        sessions: list[SourceChatSessionResponse] = []
        for sid, session_result, msg_count in zip(
            session_ids, session_rows, msg_counts
        ):
            if not session_result or len(session_result) == 0:
                # Session record was deleted between the relations
                # query and the fan-out fetch; skip cleanly.
                continue
            session_data = session_result[0]
            sessions.append(
                SourceChatSessionResponse(
                    id=session_data.get("id") or "",
                    title=session_data.get("title") or "Untitled Session",
                    source_id=source_id,
                    model_override=session_data.get("model_override"),
                    created=str(session_data.get("created")),
                    updated=str(session_data.get("updated")),
                    message_count=msg_count,
                    # v0.8.44b — SELECT * includes the migration-20 field.
                    disabled_mcp_servers=session_data.get("disabled_mcp_servers"),
                )
            )

        # Sort sessions by created date (newest first)
        sessions.sort(key=lambda x: x.created, reverse=True)
        return sessions
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError:
        # v0.7.183 — bubble InvalidInputError to the global handler
        # (→ 400). NotFoundError is already caught explicitly above
        # with a clean "Source not found" message; the v0.7.182 bulk
        # sweep also inserted it here, but that clause was unreachable.
        # Narrowed to InvalidInputError only.
        raise
    except Exception as e:
        logger.error(f"Error fetching source chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error fetching source chat sessions"
        )


@router.get(
    "/sources/{source_id}/chat/sessions/{session_id}",
    response_model=SourceChatSessionWithMessagesResponse,
)
async def get_source_chat_session(
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Get a specific source chat session with its messages."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Get session
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Verify session is related to this source
        relation_query = await repo_query(
            "SELECT * FROM refers_to WHERE in = $session_id AND out = $source_id",
            {
                "session_id": ensure_record_id(full_session_id),
                "source_id": ensure_record_id(full_source_id),
            },
        )

        if not relation_query:
            raise HTTPException(
                status_code=404, detail="Session not found for this source"
            )

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            source_chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        context_indicators = None

        if thread_state and thread_state.values:
            # Extract messages
            if "messages" in thread_state.values:
                for msg in thread_state.values["messages"]:
                    messages.append(
                        ChatMessage(
                            id=getattr(msg, "id", f"msg_{len(messages)}"),
                            type=msg.type if hasattr(msg, "type") else "unknown",
                            content=msg.content
                            if hasattr(msg, "content")
                            else str(msg),
                            timestamp=None,  # LangChain messages don't have timestamps by default
                        )
                    )

            # Extract context indicators from the last state
            if "context_indicators" in thread_state.values:
                context_data = thread_state.values["context_indicators"]
                context_indicators = ContextIndicator(
                    sources=context_data.get("sources", []),
                    insights=context_data.get("insights", []),
                    notes=context_data.get("notes", []),
                )

        return SourceChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=getattr(session, "model_override", None),
            # v0.7.182 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=len(messages),
            messages=messages,
            context_indicators=context_indicators,
            # v0.8.44b — hydrate the source-chat picker from persisted picks.
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError:
        # v0.7.183 — bubble InvalidInputError to the global handler
        # (→ 400). NotFoundError is already caught explicitly above
        # with a clean "Source not found" message; the v0.7.182 bulk
        # sweep also inserted it here, but that clause was unreachable.
        # Narrowed to InvalidInputError only.
        raise
    except Exception as e:
        logger.error(f"Error fetching source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error fetching source chat session"
        )


@router.put(
    "/sources/{source_id}/chat/sessions/{session_id}",
    response_model=SourceChatSessionResponse,
)
async def update_source_chat_session(
    request: UpdateSourceChatSessionRequest,
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Update source chat session title and/or model override."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Get session
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Verify session is related to this source
        relation_query = await repo_query(
            "SELECT * FROM refers_to WHERE in = $session_id AND out = $source_id",
            {
                "session_id": ensure_record_id(full_session_id),
                "source_id": ensure_record_id(full_source_id),
            },
        )

        if not relation_query:
            raise HTTPException(
                status_code=404, detail="Session not found for this source"
            )

        # Update session fields
        # v0.8.44b — use exclude_unset so an omitted field is NOT a
        # clear-to-null. Mirrors the notebook-chat v0.8.43 update
        # handler so the rename / model-override flows are untouched
        # when the picker PATCH only carries disabled_mcp_servers.
        update_fields = request.model_dump(exclude_unset=True)
        if "title" in update_fields:
            session.title = request.title
        if "model_override" in update_fields:
            session.model_override = request.model_override
        if "disabled_mcp_servers" in update_fields:
            session.disabled_mcp_servers = request.disabled_mcp_servers

        await session.save()

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(source_chat_graph, full_session_id)

        return SourceChatSessionResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=getattr(session, "model_override", None),
            # v0.7.182 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=msg_count,
            # v0.8.44b — echo back the persisted picks so the client
            # stays in sync after the PATCH.
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError:
        # v0.7.183 — bubble InvalidInputError to the global handler
        # (→ 400). NotFoundError is already caught explicitly above
        # with a clean "Source not found" message; the v0.7.182 bulk
        # sweep also inserted it here, but that clause was unreachable.
        # Narrowed to InvalidInputError only.
        raise
    except Exception as e:
        logger.error(f"Error updating source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error updating source chat session"
        )


@router.delete(
    "/sources/{source_id}/chat/sessions/{session_id}", response_model=SuccessResponse
)
async def delete_source_chat_session(
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Delete a source chat session."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Get session
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Verify session is related to this source
        relation_query = await repo_query(
            "SELECT * FROM refers_to WHERE in = $session_id AND out = $source_id",
            {
                "session_id": ensure_record_id(full_session_id),
                "source_id": ensure_record_id(full_source_id),
            },
        )

        if not relation_query:
            raise HTTPException(
                status_code=404, detail="Session not found for this source"
            )

        await session.delete()

        # v0.7.171 — Clean up the source-chat LangGraph checkpoint
        # rows for this thread. Same rationale as the chat.py fix:
        # without this, every deleted source-chat leaves its full
        # transcript behind in the LangGraph SQLite (indexed by
        # thread_id = full_session_id), growing unbounded over the
        # life of an install. Best-effort try/except: a checkpoint-
        # cleanup failure doesn't block the primary SurrealDB delete,
        # and the existing v0.7.125 prune-loop will catch any orphan.
        try:
            checkpointer = getattr(source_chat_graph, "checkpointer", None)
            delete_thread = getattr(checkpointer, "delete_thread", None)
            if delete_thread is not None:
                await asyncio.to_thread(delete_thread, full_session_id)
                logger.debug(
                    "Cleaned up source-chat checkpoint thread {}",
                    full_session_id,
                )
        except Exception as cleanup_exc:
            logger.warning(
                "Source-chat checkpoint cleanup failed for session {} (non-fatal): {}",
                full_session_id,
                cleanup_exc,
            )

        return SuccessResponse(
            success=True, message="Source chat session deleted successfully"
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError:
        # v0.7.183 — bubble InvalidInputError to the global handler
        # (→ 400). NotFoundError is already caught explicitly above
        # with a clean "Source not found" message; the v0.7.182 bulk
        # sweep also inserted it here, but that clause was unreachable.
        # Narrowed to InvalidInputError only.
        raise
    except Exception as e:
        logger.error(f"Error deleting source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error deleting source chat session"
        )


async def stream_source_chat_response(
    session_id: str,
    source_id: str,
    message: str,
    model_override: Optional[str] = None,
    fastapi_request: Optional["Request"] = None,
    # v0.8.44 — per-request MCP server disable list (parity with
    # notebook chat's v0.8.42). Threaded into ThreadState below; the
    # source-chat node reads it in `bind_mcp_and_run_tool_loop`.
    disabled_mcp_servers: Optional[list[str]] = None,
) -> AsyncGenerator[str, None]:
    """Stream the source chat response as Server-Sent Events.

    v0.7.42 — REAL token streaming. The previous implementation called
    `await source_chat_graph.ainvoke(...)` which blocked until the
    entire AI message was built, then yielded one
    `{"type":"ai_message", "content": <full>}` event. For local 5-30
    tok/s LLMs a 400-token answer hung the source-chat UI 13-80s with
    no incremental output. Mirrors the notebook-chat streaming wired
    in v0.7.38.

    Event types emitted:
      - `user_message`         — confirms the user message landed
      - `ai_message_delta`     — one LLM token chunk (concat to build)
      - `ai_message`           — terminal full text (canonical final
                                  value once streaming finishes; also
                                  acts as a fallback for clients that
                                  ignore deltas)
      - `context_indicators`   — final source/insight references
      - `complete`             — done
      - `error`                — terminal failure
    """
    try:
        # v0.7.174 — Per-session serialization (mirrors the chat.py
        # stream fix). Two concurrent calls to the same `session_id`
        # used to each read the same checkpoint, each append their
        # HumanMessage in process memory, each ainvoke — silently
        # losing turns when both writes hit the checkpoint. Manual
        # acquire/finally-release because the critical section spans
        # a multi-yield generator body; using `async with` would
        # require indenting the entire astream_events loop. The
        # generator's GeneratorExit cleanup (FastAPI's StreamingResponse
        # closing the generator on client disconnect) reaches the
        # finally clause and releases.
        from api.utils.session_locks import get_session_lock

        session_lock = await get_session_lock(session_id)
        await session_lock.acquire()
        try:
            # Get current state — SqliteSaver.get_state is still sync.
            current_state = await asyncio.to_thread(
                source_chat_graph.get_state,
                config=RunnableConfig(configurable={"thread_id": session_id}),
            )

            # Prepare state for execution
            state_values = current_state.values if current_state else {}
            state_values["messages"] = state_values.get("messages", [])
            state_values["source_id"] = source_id
            state_values["model_override"] = model_override
            # v0.8.44 — propagate the per-request MCP server disable
            # list into LangGraph state (parity with notebook chat's
            # v0.8.42). The source-chat node reads this in
            # `bind_mcp_and_run_tool_loop` so the user's "load only
            # what I need" pick takes effect on this turn. Per-turn
            # only — source-chat sessions don't yet have a persistent
            # picks field (parallel deferred work to v0.8.43 for
            # notebook chat); a future v0.8.44b could add migration
            # 21 with `disabled_mcp_servers` on `source_chat_session`.
            state_values["disabled_mcp_servers"] = disabled_mcp_servers or None

            # Send user message event
            user_event = {"type": "user_message", "content": message, "timestamp": None}
            yield f"data: {json.dumps(user_event)}\n\n"

            # v0.7.42 — token streaming via LangGraph astream_events. The
            # source_chat_graph node is `async def` (v0.7.37) so this
            # routes natively. We collect:
            #   - on_chat_model_stream events → ai_message_delta events
            #   - on_chain_end terminal event with final state → context_indicators
            accumulated_content = ""
            final_state: Optional[dict] = None
            # v0.7.192 — AsyncSqliteSaver twin (lazily initialised).
            # Newer langgraph raises NotImplementedError when
            # astream_events' internal aget_tuple() hits the sync
            # SqliteSaver. State is shared via the underlying SQLite
            # file so the sync `source_chat_graph.get_state()` reads
            # above still see this write's checkpoint.
            _source_chat_graph_async = await get_async_source_chat_graph()
            async for event in _source_chat_graph_async.astream_events(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={"thread_id": session_id, "model_id": model_override}
                ),
                version="v2",
            ):
                # Early-cancel parity with /chat/stream — stop generation
                # the moment the client gives up on the response.
                if (
                    fastapi_request is not None
                    and await fastapi_request.is_disconnected()
                ):
                    logger.info(
                        "source chat stream: client disconnected for "
                        "session {}; halting",
                        session_id,
                    )
                    return

                etype = event.get("event")
                if etype == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        accumulated_content += content
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "ai_message_delta",
                                    "content": content,
                                }
                            )
                            + "\n\n"
                        )
                elif etype == "on_chain_end":
                    # Capture the outer chain's final state — has
                    # context_indicators + canonical messages.
                    # v0.7.56 — accept both dict and Pydantic state shapes
                    # (same root cause as v0.7.52 chat.py fix). If LangGraph
                    # ever yields a model instance the SSE consumer would
                    # otherwise never see the terminal context_indicators
                    # event.
                    # v0.8.17 — also capture mcp_tool_calls so source-chat
                    # gets the same citation-pill popover pipeline as
                    # notebook chat. Pre-v0.8.17 v0.8.16 wired the chat
                    # graph to surface MCP captures in state but the SSE
                    # stream never relayed them — source-chat pills
                    # always showed the v0.8.10 placeholder fallback
                    # even though the backend executed the tools and
                    # populated captures correctly.
                    output = event.get("data", {}).get("output")
                    if isinstance(output, dict):
                        final_state = output
                    elif output is not None and hasattr(output, "messages"):
                        final_state = {
                            "messages": getattr(output, "messages", None),
                            "context_indicators": getattr(
                                output, "context_indicators", None
                            ),
                            "mcp_tool_calls": getattr(output, "mcp_tool_calls", None),
                            # v0.8.68 — offline-fallback info (dual-path,
                            # same Pydantic-state guard as the fields above).
                            "offline_fallback": getattr(
                                output, "offline_fallback", None
                            ),
                            # v0.8.68 — smart-router decision (dual-path).
                            "selected_provider": getattr(
                                output, "selected_provider", None
                            ),
                            "selected_model_id": getattr(
                                output, "selected_model_id", None
                            ),
                        }

            # Emit the terminal ai_message event so clients that ignore
            # the deltas still see a single canonical "full message" event
            # (back-compat with anything written for the v0.6.x SSE
            # contract before v0.7.42).
            if accumulated_content:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "ai_message",
                            "content": accumulated_content,
                            "timestamp": None,
                        }
                    )
                    + "\n\n"
                )

            # Stream context indicators
            if final_state and "context_indicators" in final_state:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "context_indicators",
                            "data": final_state["context_indicators"],
                        }
                    )
                    + "\n\n"
                )

            # v0.8.17 — emit mcp_tool_calls event so the frontend can
            # stash the payloads in the TanStack Query cache keyed by
            # the canonical AI message ID (same v0.8.1 Item 3
            # pipeline as notebook chat). Pre-v0.8.17 the chat graph
            # (v0.8.16) captured calls into state but the SSE never
            # relayed them, so source-chat pill popovers always
            # showed the v0.8.10 placeholder fallback even when the
            # backend had real payloads. Only emit when there's
            # actually something to show.
            if final_state and final_state.get("mcp_tool_calls"):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "mcp_tool_calls",
                            "calls": final_state["mcp_tool_calls"],
                        }
                    )
                    + "\n\n"
                )

            # v0.8.68 — emit the smart-router decision so the source-chat
            # local/cloud badge (already rendered by ChatPanel) finally gets
            # data. Only emitted when smart routing actually ran this turn.
            if final_state and final_state.get("selected_provider") is not None:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "selected_provider",
                            "selected_provider": final_state["selected_provider"],
                            "selected_model_id": final_state.get("selected_model_id"),
                        }
                    )
                    + "\n\n"
                )

            # v0.8.68 — emit the offline-fallback info so useSourceChat can
            # stash it for ChatMessageProviderBadge's amber pill (same
            # pipeline notebook chat uses via its done event). Only emitted
            # when the gate actually substituted a model this turn.
            if final_state and final_state.get("offline_fallback"):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "offline_fallback",
                            "data": final_state["offline_fallback"],
                        }
                    )
                    + "\n\n"
                )

            # Send completion signal
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        finally:
            # v0.7.174 — release the per-session lock. Runs on every exit
            # path: normal completion, early `return` on disconnect,
            # raised exception (re-raised to outer except), and
            # GeneratorExit when FastAPI closes the stream.
            try:
                session_lock.release()
            except RuntimeError:
                # Lock wasn't actually held (acquire() raised). Safe.
                pass

    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — keep the tuple form here. The v0.7.182 sweep
        # inserted this on every endpoint; the v0.7.183 cleanup
        # narrowed the 5 endpoints that had an upstream explicit
        # `except NotFoundError: raise HTTPException(404, ...)` to
        # `except InvalidInputError:` only (the NotFoundError clause
        # was unreachable there). This streaming endpoint does NOT
        # have an upstream NotFoundError handler, so keep the tuple.
        raise
    except Exception as e:
        from deeper_notebook.utils.error_classifier import classify_error

        _, user_friendly_message = classify_error(e)
        logger.error(f"Error in source chat streaming: {str(e)}")
        error_event = {"type": "error", "message": user_friendly_message}
        yield f"data: {json.dumps(error_event)}\n\n"


@router.post("/sources/{source_id}/chat/sessions/{session_id}/messages")
async def send_message_to_source_chat(
    request: SendMessageRequest,
    fastapi_request: Request,
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Send a message to source chat session with SSE streaming response."""
    try:
        # Verify source exists
        full_source_id = (
            source_id if source_id.startswith("source:") else f"source:{source_id}"
        )
        source = await Source.get(full_source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Verify session exists and is related to source
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Verify session is related to this source
        relation_query = await repo_query(
            "SELECT * FROM refers_to WHERE in = $session_id AND out = $source_id",
            {
                "session_id": ensure_record_id(full_session_id),
                "source_id": ensure_record_id(full_source_id),
            },
        )

        if not relation_query:
            raise HTTPException(
                status_code=404, detail="Session not found for this source"
            )

        if not request.message:
            raise HTTPException(status_code=400, detail="Message content is required")

        # Determine model override (request override takes precedence over session override)
        model_override = request.model_override or getattr(
            session, "model_override", None
        )

        # v0.8.44b — resolve the effective MCP disable list with the
        # same precedence as notebook chat's v0.8.43: the per-request
        # body wins; if omitted (null), fall back to the session's
        # persisted picks. Resolved here (not in the generator) because
        # the `session` object is already loaded and the generator
        # only receives `session_id`. An explicit `[]` from the client
        # is "use no disables this turn" and is preserved (it's
        # `is not None`).
        effective_disabled_mcp = (
            request.disabled_mcp_servers
            if request.disabled_mcp_servers is not None
            else getattr(session, "disabled_mcp_servers", None)
        )

        # Update session timestamp
        await session.save()

        # Return streaming response
        # v0.8.116 — heartbeat comment lines during model silence +
        # server-side idle limit (see api/utils/stream_keepalive.py).
        return StreamingResponse(
            with_keepalive(
                stream_source_chat_response(
                    session_id=full_session_id,
                    source_id=full_source_id,
                    message=request.message,
                    model_override=model_override,
                    fastapi_request=fastapi_request,
                    # v0.8.44 / v0.8.44b — forward the EFFECTIVE per-turn
                    # disable list (request body, or session fallback).
                    disabled_mcp_servers=effective_disabled_mcp,
                ),
                heartbeat_frame=SSE_HEARTBEAT_FRAME,
                make_timeout_frame=lambda idle: (
                    "data: "
                    + json.dumps({"type": "error", "message": idle_timeout_message(idle)})
                    + "\n\n"
                ),
            ),
            media_type="text/plain",
            headers={
                # v0.7.42 — same proxy-flush hints v0.7.38 uses on
                # /chat/stream so each SSE event lands client-side
                # immediately, not buffered.
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "Content-Type": "text/plain; charset=utf-8",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — keep tuple form here (no upstream NotFoundError
        # handler in this function; see line 672 region for the
        # rationale).
        raise
    except Exception as e:
        logger.error(f"Error sending message to source chat: {str(e)}")
        raise HTTPException(status_code=500, detail="Error sending message")
