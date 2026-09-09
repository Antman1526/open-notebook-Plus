import asyncio
import json
import os
import re
import traceback
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from api.utils.stream_keepalive import (
    NDJSON_HEARTBEAT_FRAME,
    idle_timeout_message,
    with_keepalive,
)
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.utils.iso import iso  # v0.7.181 — Safari-safe datetime serialization
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import ChatSession, Note, Notebook, Source
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
)

# v0.7.192 — Lazy async-graph getter for ainvoke / astream_events
# call sites. Newer langgraph raises NotImplementedError when those
# internally call aget_tuple() against the sync SqliteSaver. The
# lazy pattern works around aiosqlite capturing the event loop at
# construct time; see deeper_notebook/graphs/chat.py for details.
from deeper_notebook.graphs.chat import get_async_graph
from deeper_notebook.graphs.chat import graph as chat_graph
from deeper_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# v0.7.68 — memory-writer hook. The bundled desktop build registers
# `memory_extract_turn` + `memory_summarize_session` surreal_commands
# handlers (desktop/memory/memory_commands.py, registered through
# desktop/app.py:_phase_register_memory_commands). They've been wired
# up since v0.7.47, but until now NOTHING in the chat path actually
# submitted those jobs after a turn — so the memory feature was
# entirely inert at runtime. Both /chat/execute and /chat/stream now
# fire `deeper_notebook.memory_extract_turn` fire-and-forget after the
# turn's session.save() succeeds.
#
# Best-effort: any failure is logged at debug and swallowed. The
# user's chat experience is unaffected when the memory worker is
# down, the chat LLM is missing, or the launcher is the upstream
# (non-desktop) build that doesn't ship memory commands.
def _memory_extraction_configured() -> bool:
    """True when the memory worker stack is configured (desktop build)."""
    return all(
        os.environ.get(var)
        for var in (
            "MEMORY_SURREAL_URL",
            "MEMORY_EMBED_URL",
            "MEMORY_CHAT_LLM_URL",
        )
    )


def _extract_text(msg: Any) -> str:
    """Coerce a LangChain message-or-dict's content to a plain string."""
    if msg is None:
        return ""
    c = getattr(msg, "content", None)
    if c is None and isinstance(msg, dict):
        c = msg.get("content")
    if c is None:
        return ""
    return c if isinstance(c, str) else str(c)


async def _fire_memory_extract_turn(
    chat_session_id: str,
    user_text: str,
    assistant_text: str,
    model_override: Optional[str] = None,
) -> None:
    """Submit the memory_extract_turn job fire-and-forget.

    Caller does NOT await the actual extraction — surreal_commands.submit_command
    only queues the row. The worker picks it up asynchronously. We still wrap
    submit_command in asyncio.to_thread (matches v0.7.55/57/62 sites) because
    it opens a synchronous SurrealDB WebSocket.

    v0.7.83 — `model_override` is now plumbed through to the worker. The
    memory writer's LLM client previously always used the bundled chat
    model (DEEPER_NOTEBOOK_CHAT_MODEL_NAME env var or "default"). When the user
    explicitly picked a different model for their chat session, the
    memory extractor still ran against the bundled model, producing
    facts that disagreed with the assistant's voice. Passing the
    override here lets the worker fall through to it; absent → falls
    back to the bundled model as before.
    """
    if not _memory_extraction_configured():
        # Upstream (non-desktop) build, or memory env vars not wired —
        # silently skip. The desktop launcher sets all three before
        # spawning the API.
        return
    if not (user_text or assistant_text):
        return
    try:
        from surreal_commands import submit_command

        args = {
            "chat_session_id": chat_session_id,
            "user_text": user_text,
            "assistant_text": assistant_text,
        }
        if model_override:
            args["model_override"] = model_override
        await asyncio.to_thread(
            submit_command,
            "open_notebook",
            "memory_extract_turn",
            args,
        )
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        # Memory is best-effort. A failed submit_command (worker down,
        # SurrealDB blip, command not registered on this build) MUST
        # NOT take the chat response down with it.
        logger.debug(
            "memory_extract_turn submit failed (best-effort, ignored): {}",
            exc,
        )


async def _fire_memory_summarize_session(
    chat_session_id: str,
    model_override: Optional[str] = None,
) -> None:
    """Submit the memory_summarize_session job fire-and-forget.

    v0.7.70 — also wired up alongside the per-turn extractor. Fires
    when a chat session is deleted: the user's explicit "end of
    conversation" signal. We pull the full transcript from the
    LangGraph SQLite checkpoint via chat_graph.get_state, render it
    as a plain-text exchange, and queue the summarizer. The writer's
    16k-char truncation guard kicks in for very long sessions.

    v0.7.83 — `model_override` plumbed through (see _fire_memory_extract_turn
    for the rationale). For the summarizer, the right model is the same
    one the chat session was using, so the episode record's voice
    matches the chat's voice.
    """
    if not _memory_extraction_configured():
        return
    try:
        # Read messages from the graph checkpoint. Sync API; run on a
        # worker thread to keep the FastAPI event loop free.
        current_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": chat_session_id}),
        )
        msgs = current_state.values.get("messages", []) if current_state else []
        if not msgs:
            return
        # Render as "USER: ..." / "ASSISTANT: ..." lines so the
        # summarize prompt sees a clean transcript regardless of how
        # the upstream chat graph stored the messages.
        lines = []
        for m in msgs:
            mtype = getattr(m, "type", None)
            if mtype == "human":
                lines.append(f"USER: {_extract_text(m)}")
            elif mtype == "ai":
                lines.append(f"ASSISTANT: {_extract_text(m)}")
            # System messages and tool messages don't help the summary.
        transcript = "\n\n".join(lines).strip()
        if not transcript:
            return
        from surreal_commands import submit_command

        args = {
            "chat_session_id": chat_session_id,
            "transcript": transcript,
        }
        if model_override:
            args["model_override"] = model_override
        await asyncio.to_thread(
            submit_command,
            "open_notebook",
            "memory_summarize_session",
            args,
        )
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        logger.debug(
            "memory_summarize_session submit failed (best-effort, ignored): {}",
            exc,
        )


# Request/Response models
class CreateSessionRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to create session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    # v0.8.43 — Persistent per-conversation MCP server disable picks.
    # When the user toggles the v0.8.42 MCP tool picker, the new state
    # gets PATCHed here so the picks survive page reloads. null clears
    # all picks ("all servers enabled"); empty list [] means the same
    # in practice but is preserved as-is so the UI can distinguish "I
    # explicitly cleared the list" from "I never set it."
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names disabled for this session",
    )


class ChatMessage(BaseModel):
    id: str = Field(..., description="Message ID")
    type: str = Field(..., description="Message type (human|ai)")
    content: str = Field(..., description="Message content")
    timestamp: Optional[str] = Field(None, description="Message timestamp")


class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    notebook_id: Optional[str] = Field(None, description="Notebook ID")
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    # v0.8.43 — persistent MCP server disable picks (null = none).
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names disabled for this session",
    )


class ChatSessionWithMessagesResponse(ChatSessionResponse):
    messages: list[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )


class ExecuteChatRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User message content")
    context: dict[str, Any] = Field(
        ..., description="Chat context with sources and notes"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )
    # v0.8.42 — per-request MCP server disable list. Frontend sends this
    # so the user can untick "load only what I need" picks for the
    # current chat turn (the XDA Developers / Pi-harness pattern). The
    # chat-graph node passes it into bind_mcp_and_run_tool_loop's
    # exclude_server_names; server-name match is case-insensitive +
    # trimmed (`_resolve_chat_tools` normalises both sides). Empty list
    # or null = all enabled servers visible (the v0.8.0 default).
    disabled_mcp_servers: Optional[list[str]] = Field(
        None,
        description=(
            "MCP server names to skip for this chat turn. Each entry "
            "matches `mcp_server.name` case-insensitively. Omit / null "
            "to expose all enabled servers (default)."
        ),
    )
    # v0.8.63 — explicit user consent to send THIS turn to cloud even though
    # the fail-closed privacy gate flagged it (the "Re-ask allowing cloud"
    # action in the redaction-review sheet). Default False — the gate stays
    # active. Set True ONLY by a deliberate user action; never a default path.
    bypass_privacy_gate: bool = Field(
        False,
        description=(
            "When True, skip the fail-closed privacy gate for THIS turn "
            "(explicit user consent to send flagged content to cloud). "
            "Default False — the gate stays active."
        ),
    )
    # v0.8.97 — Debate mode (idea adopted from PageLM; implementation
    # original). Per-turn, following the disabled_mcp_servers precedent, so
    # the user can enter and leave debate inside one session. "debate" swaps
    # the system prompt for prompts/chat/debate.jinja: the assistant argues
    # the opposing side of the user's position, grounded in the selected
    # sources with the same citation contract as standard chat.
    chat_mode: Literal["standard", "debate"] = Field(
        "standard",
        description=(
            "Conversation mode for this turn. 'debate' makes the assistant "
            "argue the strongest opposing case, grounded in the selected "
            "sources with citations. Default 'standard'."
        ),
    )


class ExecuteChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: list[ChatMessage] = Field(..., description="Updated message list")
    # v0.8.1 — smart-router decision surfaced to clients. "local" when the
    # llama.cpp sidecar served this turn, "cloud" when the cloud provider
    # served it, None when smart routing is off / an explicit
    # model_override was used / no v0.8.0 routing happened. Closes the
    # introspection gap that forced scripts/verify-chat-platform.sh
    # Steps 4+5 into "manual eyeball check" mode.
    selected_provider: Optional[str] = Field(
        None,
        description=(
            "Which side of the smart router served this turn: 'local', "
            "'cloud', or None when smart routing did not run."
        ),
    )
    selected_model_id: Optional[str] = Field(
        None,
        description=(
            "SurrealDB model ID actually used for this turn (None when "
            "smart routing did not run)."
        ),
    )
    # v0.8.68 — set when the offline gate answered this turn with a local
    # model: {"offline_fallback": true, "from_model_id", "to_model_id",
    # "to_model_name", "reason": "offline"|"forced-offline"}. None otherwise.
    offline_fallback: Optional[dict[str, Any]] = Field(
        None, description="Offline local-model fallback info for this turn"
    )
    mcp_tool_calls: Optional[list[dict[str, Any]]] = Field(
        None,
        description=(
            "v0.8.1 Item 3 — MCP tool-call payloads for this turn. Each item: "
            "{index: int (1-based, matches [mcp:N] markers), name: str, "
            "args: dict, text: str (truncated to 4000 chars)}. None when "
            "no MCP tools fired."
        ),
    )
    privacy_gated: Optional[bool] = Field(
        None,
        description=(
            "v0.8.58 — True when the fail-closed privacy gate kept this turn "
            "on the local model because sensitive content was detected "
            "(rather than letting the smart router send it to cloud). None "
            "when the gate didn't act."
        ),
    )
    privacy_categories: Optional[list[str]] = Field(
        None,
        description=(
            "v0.8.58 — category LABELS of the sensitive content the gate "
            "detected (e.g. 'email', 'person_name'). NEVER the matched secret "
            "values. None when the gate didn't act."
        ),
    )
    agent_state: Optional[str] = Field(
        None,
        description=(
            "v0.8.60 — agent-FSM terminal state of the tool loop when "
            "DEEPER_NOTEBOOK_AGENT_FSM is on: 'complete', 'clarify' (the model paused to "
            "ask the user), or 'truncated' (hit the tool-iteration cap). None "
            "when the FSM is off."
        ),
    )


class BuildContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID")
    context_config: dict[str, Any] = Field(..., description="Context configuration")


class BuildContextResponse(BaseModel):
    context: dict[str, Any] = Field(..., description="Built context data")
    token_count: int = Field(..., description="Estimated token count")
    char_count: int = Field(..., description="Character count")


class SuccessResponse(BaseModel):
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")


@router.get("/chat/sessions", response_model=list[ChatSessionResponse])
async def get_sessions(
    notebook_id: str = Query(..., description="Notebook ID"),
    # v0.7.169 — Pagination follow-through. The right-rail Chat list
    # on every notebook page open used to return EVERY session attached
    # to the notebook with no bound, and each one then paid for a
    # LangGraph checkpoint read (v0.7.161 made those concurrent, but
    # the underlying SELECT was still unbounded). Default cap = 100
    # newest sessions; 1000 hard ceiling.
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Max sessions to return (default 100, max 1000).",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Sessions to skip for pagination (default 0).",
    ),
):
    """Get all chat sessions for a notebook."""
    try:
        # Get notebook to verify it exists
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Get sessions for this notebook
        # v0.7.169 — pass through `limit` / `offset` so the inner
        # SELECT is bounded server-side.
        sessions_list = await notebook.get_chat_sessions(
            limit=limit,
            offset=offset,
        )

        # v0.7.161 — N+1 fix: parallelize the per-session LangGraph
        # checkpoint reads. Previously this loop awaited
        # get_session_message_count() sequentially, so a notebook with
        # 50 sessions paid 50 × ~30ms = ~1.5s wall-clock before the
        # right-rail Chat list could render (each call does a
        # sync-in-thread SQLite checkpoint read via the graph_utils
        # helper). With asyncio.gather, those reads run concurrently
        # — wall-clock drops to ~30ms for the single longest read,
        # regardless of session count. The reads themselves are still
        # made; the bigger fix (denormalize total_messages onto the
        # chat_session row at write time) requires a schema migration
        # and a LangGraph checkpoint hook; tracked as deferred.
        msg_counts = await asyncio.gather(
            *[
                get_session_message_count(chat_graph, str(session.id))
                for session in sessions_list
            ]
        )

        results = [
            ChatSessionResponse(
                id=session.id or "",
                title=session.title or "Untitled Session",
                notebook_id=notebook_id,
                # v0.7.181 — iso() for Safari new Date() compat.
                created=iso(session.created),
                updated=iso(session.updated),
                message_count=msg_count,
                model_override=getattr(session, "model_override", None),
                # v0.8.43 — surface the persistent MCP disable picks. Use
                # getattr so pre-migration rows (where the field isn't on
                # the model instance yet) return None safely.
                disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
            )
            for session, msg_count in zip(sessions_list, msg_counts)
        ]

        return results
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        logger.error(f"Error fetching chat sessions: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching chat sessions")


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_session(request: CreateSessionRequest):
    """Create a new chat session."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Create new session
        session = ChatSession(
            title=request.title
            or f"Chat Session {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to notebook
        await session.relate_to_notebook(request.notebook_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=request.notebook_id,
            # v0.7.181 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=0,
            model_override=session.model_override,
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating chat session")


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionWithMessagesResponse
)
async def get_session(session_id: str):
    """Get a specific session with its messages."""
    try:
        # Get session
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        if thread_state and thread_state.values and "messages" in thread_state.values:
            for msg in thread_state.values["messages"]:
                messages.append(
                    ChatMessage(
                        id=getattr(msg, "id", f"msg_{len(messages)}"),
                        type=msg.type if hasattr(msg, "type") else "unknown",
                        content=msg.content if hasattr(msg, "content") else str(msg),
                        timestamp=None,  # LangChain messages don't have timestamps by default
                    )
                )

        # Find notebook_id (we need to query the relationship)
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )

        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )

        notebook_id = notebook_query[0]["out"] if notebook_query else None

        if not notebook_id:
            # This might be an old session created before API migration
            logger.warning(
                f"No notebook relationship found for session {session_id} - may be an orphaned session"
            )

        return ChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            notebook_id=notebook_id,
            # v0.7.181 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=len(messages),
            messages=messages,
            model_override=getattr(session, "model_override", None),
            # v0.8.43 — surface the persistent MCP disable picks. Use
            # getattr so pre-migration rows (where the field isn't on
            # the model instance yet) return None safely.
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        logger.error(f"Error fetching session: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching session")


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(session_id: str, request: UpdateSessionRequest):
    """Update session title."""
    try:
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        update_data = request.model_dump(exclude_unset=True)

        if "title" in update_data:
            session.title = update_data["title"]

        if "model_override" in update_data:
            session.model_override = update_data["model_override"]

        # v0.8.43 — persist the v0.8.42 MCP server disable picks.
        # `exclude_unset=True` above means we ONLY touch the field
        # when the client explicitly sends it (omitted in PATCH ≠
        # clear-to-null), so the v0.7.x "rename session" flow keeps
        # working untouched.
        if "disabled_mcp_servers" in update_data:
            session.disabled_mcp_servers = update_data["disabled_mcp_servers"]

        await session.save()

        # Find notebook_id
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook_id = notebook_query[0]["out"] if notebook_query else None

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(chat_graph, full_session_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=notebook_id,
            # v0.7.181 — iso() for Safari new Date() compat.
            created=iso(session.created),
            updated=iso(session.updated),
            message_count=msg_count,
            model_override=session.model_override,
            disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating session")


@router.delete("/chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_session(session_id: str):
    """Delete a chat session."""
    try:
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # v0.7.70 — fire the session-end summarizer BEFORE we delete the
        # checkpoint. Session deletion is the explicit "end of
        # conversation" signal: distill the transcript into one memory
        # episode record before the underlying state goes away. Fire-
        # and-forget — failure does not block the delete.
        # v0.7.83 — pass the session's model_override so the summarizer
        # uses the same model the chat was using.
        await _fire_memory_summarize_session(
            full_session_id,
            model_override=getattr(session, "model_override", None),
            # v0.8.46d — the v0.8.43 `replace_all` that added
            # `disabled_mcp_servers=getattr(session, ...)` after every
            # `model_override=getattr(session, "model_override", None),`
            # in this file ALSO matched this fire-and-forget call —
            # passing a kwarg `_fire_memory_summarize_session` doesn't
            # accept (its signature is just chat_session_id +
            # model_override). That raised TypeError on EVERY session
            # delete, killing the v0.7.70 session-summary memory write
            # (the delete itself then 500'd before `session.delete()`).
            # The summarizer has no use for the MCP picks — removed.
        )

        await session.delete()

        # v0.7.171 — Clean up the LangGraph checkpoint rows for this
        # thread. Previously `session.delete()` removed the
        # chat_session row from SurrealDB but the LangGraph SQLite
        # checkpoints + writes tables kept the thread's full message
        # history forever, indexed by `thread_id = full_session_id`.
        # Over the life of an install this grows unbounded (every
        # deleted chat leaves its full transcript behind) — meaningful
        # disk usage on chat-heavy users. Worse: if a `chat_session:`
        # ID ever collides with one used in the past (test harness,
        # manual SurrealQL insert), the "new" session inherits the
        # old transcript as its history. Now we explicitly call
        # `delete_thread` on the checkpointer (the SqliteSaver method
        # introduced for exactly this purpose). Wrapped in best-effort
        # try/except so a checkpoint-cleanup failure doesn't block the
        # primary SurrealDB delete — the row IS gone.
        # v0.8.48 — corrected an earlier comment that claimed the
        # `checkpoint_prune` background task (api/main.py v0.7.125) would
        # reclaim the orphan on failure: it WON'T. prune_old_checkpoints
        # uses per-thread retention (keep newest 50 PER thread_id), so it
        # only trims old snapshots WITHIN an over-retention thread — it
        # never deletes an orphaned thread whose session is gone. If
        # delete_thread below fails, this thread's (≤50) checkpoints
        # persist; the leak is bounded to one session and this path is
        # rare, so we accept it rather than add an orphan-sweep pass.
        try:
            checkpointer = getattr(chat_graph, "checkpointer", None)
            delete_thread = getattr(checkpointer, "delete_thread", None)
            if delete_thread is not None:
                await asyncio.to_thread(delete_thread, full_session_id)
                logger.debug(
                    "Cleaned up LangGraph checkpoint thread {}",
                    full_session_id,
                )
        except Exception as cleanup_exc:
            logger.warning(
                "LangGraph checkpoint cleanup failed for session {} "
                "(non-fatal — the row is already deleted; "
                "checkpoint_prune will catch the orphan): {}",
                full_session_id,
                cleanup_exc,
            )

        return SuccessResponse(success=True, message="Session deleted successfully")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers.
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting session")


@router.post("/chat/execute", response_model=ExecuteChatResponse)
async def execute_chat(request: ExecuteChatRequest):
    """Execute a chat request and get AI response."""
    try:
        # Verify session exists
        # Ensure session_id has proper table prefix
        full_session_id = (
            request.session_id
            if request.session_id.startswith("chat_session:")
            else f"chat_session:{request.session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Determine model override (per-request override takes precedence over session-level)
        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        # v0.7.174 — Serialize execution per session_id. Two concurrent
        # requests to the SAME thread_id (two tabs, an SSE reconnect
        # racing a fresh POST, an aggressive client retry) used to each
        # read the checkpoint independently, each append their own
        # HumanMessage in process memory, each invoke. With the
        # add_messages reducer the checkpoint DID append both new
        # messages — but each ainvoke's INPUT state was missing the
        # other's user turn, so the LLM never saw the concurrent
        # question and the saved AIMessage could overwrite the other's
        # response. Net effect: silently lost turns.
        #
        # The lock is per-session (not global) so unrelated notebooks
        # don't serialize. WeakValueDictionary backing means the lock
        # auto-GCs when no caller holds it — no memory growth on a
        # long-running install with many session_ids.
        from api.utils.session_locks import get_session_lock

        session_lock = await get_session_lock(full_session_id)
        async with session_lock:
            # Get current state
            # Use sync get_state() in a thread since SqliteSaver doesn't support async
            current_state = await asyncio.to_thread(
                chat_graph.get_state,
                config=RunnableConfig(configurable={"thread_id": full_session_id}),
            )

            # Prepare state for execution
            state_values = current_state.values if current_state else {}
            state_values["messages"] = state_values.get("messages", [])
            state_values["context"] = request.context
            state_values["model_override"] = model_override
            # v0.8.42 — propagate the per-request MCP-server disable
            # list into LangGraph state. The chat node reads this in
            # `bind_mcp_and_run_tool_loop` so the user's "load only
            # what I need" pick takes effect on this turn only — no
            # persistent state mutation.
            # v0.8.43 — per-request value wins; fall back to the
            # session's persisted picks (set via PATCH /chat/sessions/{id}
            # when the user toggles the v0.8.42 tool picker). Pre-
            # v0.8.43 the only signal was the request body, which
            # didn't survive page reloads.
            state_values["disabled_mcp_servers"] = (
                request.disabled_mcp_servers
                if request.disabled_mcp_servers is not None
                else getattr(session, "disabled_mcp_servers", None)
            )
            # v0.8.63 — per-request privacy-gate bypass (explicit user consent).
            state_values["bypass_privacy_gate"] = bool(request.bypass_privacy_gate)
            # v0.8.97 — per-turn conversation mode ("standard" | "debate").
            # The graph's prompt-assembly node selects the matching template.
            state_values["chat_mode"] = request.chat_mode

            # Add user message to state
            from langchain_core.messages import HumanMessage

            user_message = HumanMessage(content=request.message)
            state_values["messages"].append(user_message)

            # Execute chat graph
            # v0.7.37 — native async. The v0.6.10 asyncio.to_thread wrapper
            # is no longer needed because the chat-graph node itself is
            # now `async def call_model_with_messages`. LangGraph routes
            # ainvoke() directly to the async node without re-bridging
            # through a thread pool.
            # v0.7.99 — wrap in wait_for so a hung local chat model can't
            # block the non-streaming /chat endpoint indefinitely. Default
            # 300s is generous (chat graphs can do memory recall + tool
            # calls + long generations); cloud users can lower, slow-LLM
            # users can raise. The streaming endpoint /chat/stream is
            # naturally bounded by SSE disconnect handling (v0.7.50+) and
            # doesn't need this wrap.
            _chat_timeout = float(
                resolve_env("DEEPER_NOTEBOOK_CHAT_TIMEOUT_SEC", "300").strip() or 300
            )
            try:
                # v0.7.192 — Use the AsyncSqliteSaver-backed twin
                # (lazily initialised). Newer langgraph raises
                # NotImplementedError when ainvoke's internal
                # aget_tuple() hits the sync SqliteSaver. Both savers
                # point at the same on-disk SQLite file → state
                # stays consistent across this async write and the
                # sync `chat_graph.get_state(...)` reads above.
                _chat_graph_async = await get_async_graph()
                result = await asyncio.wait_for(
                    _chat_graph_async.ainvoke(
                        input=state_values,  # type: ignore[arg-type]
                        config=RunnableConfig(
                            configurable={
                                "thread_id": full_session_id,
                                "model_id": model_override,
                            }
                        ),
                    ),
                    timeout=_chat_timeout,
                )
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "Chat /chat: timed out after {}s for session {}",
                    _chat_timeout,
                    full_session_id,
                )
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Chat timed out after {_chat_timeout}s. The model may "
                        "be loading, overloaded, or generating a very long "
                        "response. Raise DEEPER_NOTEBOOK_CHAT_TIMEOUT_SEC, switch to a "
                        "faster model, or try /chat/stream for token-by-token "
                        "responses that surface progress immediately."
                    ),
                ) from exc

        # v0.7.174 — The session lock is released here. Everything below
        # (session.save, response building, memory extractor fire-and-
        # forget) does NOT need to hold the lock — concurrent sessions
        # can race on their own session.save() rows safely (each writes
        # to a different chat_session: id) and the memory extractor is
        # already fire-and-forget per turn.

        # Update session timestamp
        await session.save()

        # v0.7.165 — LangGraph state-shape variance guard. The graph
        # currently returns a TypedDict so `result.get("messages")`
        # works, but the streaming path in this file (lines ~820-836)
        # already applies the dual dict/Pydantic guard because past
        # LangGraph releases have shipped Pydantic-typed states and
        # the CLAUDE.md standing audit calls this out as a recurring
        # footgun (v0.7.52/55/56/75/81/95 are prior fixes for the
        # same pattern). Normalize once at the top so both reads
        # below (response conversion + memory extractor) share the
        # same source-of-truth list.
        result_messages = (
            result.get("messages", [])
            if isinstance(result, dict)
            else (getattr(result, "messages", None) or [])
        )

        # v0.8.1 — read smart-router decision (set by the chat-graph node
        # when DEEPER_NOTEBOOK_AUTO_ROUTE_CHAT is on). Same dual dict /
        # Pydantic guard as messages above so a future LangGraph state
        # shape change doesn't silently drop the field.
        selected_provider = (
            result.get("selected_provider")
            if isinstance(result, dict)
            else getattr(result, "selected_provider", None)
        )
        selected_model_id = (
            result.get("selected_model_id")
            if isinstance(result, dict)
            else getattr(result, "selected_model_id", None)
        )
        # v0.8.68 — offline-fallback info (None when the gate didn't act).
        offline_fallback = (
            result.get("offline_fallback")
            if isinstance(result, dict)
            else getattr(result, "offline_fallback", None)
        )
        # v0.8.1 Item 3 — MCP tool-call payloads captured this turn.
        mcp_tool_calls = (
            result.get("mcp_tool_calls")
            if isinstance(result, dict)
            else getattr(result, "mcp_tool_calls", None)
        )
        # v0.8.58 — privacy-gate decision (None when the gate didn't act).
        privacy_gated = (
            result.get("privacy_gated")
            if isinstance(result, dict)
            else getattr(result, "privacy_gated", None)
        )
        privacy_categories = (
            result.get("privacy_categories")
            if isinstance(result, dict)
            else getattr(result, "privacy_categories", None)
        )
        # v0.8.60 — agent-FSM terminal state (None when the FSM is off).
        agent_state = (
            result.get("agent_state")
            if isinstance(result, dict)
            else getattr(result, "agent_state", None)
        )

        # Convert messages to response format
        messages: list[ChatMessage] = []
        for msg in result_messages:
            messages.append(
                ChatMessage(
                    id=getattr(msg, "id", f"msg_{len(messages)}"),
                    type=msg.type if hasattr(msg, "type") else "unknown",
                    content=msg.content if hasattr(msg, "content") else str(msg),
                    timestamp=None,
                )
            )

        # v0.7.68 — fire the memory extractor for this turn. The user's
        # text is request.message; the assistant's reply is the last
        # AIMessage in the message list. Last-message heuristic is safe
        # here because the chat graph appends exactly one AI response
        # per turn (state["messages"] is a reducer-added list).
        # v0.7.165 — iterate the dual-path-normalized `result_messages`
        # so the Pydantic-state case is covered here too.
        ai_text = ""
        for msg in reversed(result_messages):
            mtype = getattr(msg, "type", None)
            if mtype == "ai":
                ai_text = _extract_text(msg)
                break
        # v0.7.83 — pass model_override (the same one we already
        # resolved at line 348 for the chat graph itself) so the
        # memory worker matches the chat's model.
        await _fire_memory_extract_turn(
            chat_session_id=full_session_id,
            user_text=request.message,
            assistant_text=ai_text,
            model_override=model_override,
        )

        return ExecuteChatResponse(
            session_id=request.session_id,
            messages=messages,
            selected_provider=selected_provider,
            selected_model_id=selected_model_id,
            offline_fallback=offline_fallback,
            mcp_tool_calls=mcp_tool_calls,
            privacy_gated=privacy_gated,
            privacy_categories=privacy_categories,
            agent_state=agent_state,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        # v0.7.108 — Pre-existing bug exposed by the timeout test:
        # `except Exception as e` below was swallowing the v0.7.99
        # HTTPException(504) raise and re-wrapping it as a 500. Now
        # we re-raise typed HTTPExceptions (404, 504, etc.) so the
        # client sees the right status code + actionable detail.
        # Same fix pattern applies anywhere with `except Exception`
        # around an inner `raise HTTPException(...)`.
        raise
    except Exception as e:
        # Log detailed error with context for debugging
        logger.error(
            f"Error executing chat: {str(e)}\n"
            f"  Session ID: {request.session_id}\n"
            f"  Model override: {request.model_override}\n"
            f"  Traceback:\n{traceback.format_exc()}"
        )
        raise HTTPException(status_code=500, detail="Error executing chat")


# ---------------------------------------------------------------------------
# v0.7.38 — Streaming chat endpoint
#
# Streams tokens as the LLM emits them, instead of buffering the full
# response and returning it as one payload. For local-LLM users (5-30
# tok/s), this turns a 15-30s wall of blank screen into a typewriter-
# style flow — dramatically improves perceived latency.
#
# Wire format: newline-delimited JSON (NDJSON), one event per line. Each
# line is a complete JSON object the frontend can parse without buffering
# partial bytes. Five event types:
#
#   {"type":"start", "session_id":"..."}
#       First event. Signals SSE connection is established.
#
#   {"type":"token", "content":"hello"}
#       One chunk of model output. Concatenate `content` values across
#       all `token` events to reconstruct the message.
#
#   {"type":"done", "messages":[...]}
#       Final event on success. `messages` is the same shape as
#       /chat/execute's response — full history including the AI reply,
#       so the frontend can replace its streaming buffer with canonical
#       state.
#
#   {"type":"error", "detail":"..."}
#       Terminal error. The frontend should surface the detail string.
#
# The endpoint is /chat/stream (alongside /chat/execute which is kept
# for the non-streaming path: tests, scripted clients, OpenAPI consumers
# that don't want SSE).
# ---------------------------------------------------------------------------

# v0.8.65h — strip <think> blocks from STREAMED tokens (reasoning models).
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _visible_streamed_text(accum: str) -> str:
    """Return the user-visible (non-thinking) prefix of the accumulated stream.

    Reasoning models (Qwen3, DeepSeek-R1, ...) emit ``<think>…</think>`` before
    their answer. During STREAMING we must HIDE that — the opposite of
    ``clean_thinking_content`` (which is for FINAL content and falls back to
    surfacing reasoning when there's no answer). So:
      * complete ``<think>…</think>`` blocks are removed, and
      * an as-yet-UNCLOSED ``<think>`` (the model is still thinking) suppresses
        everything from it onward.
    No ``strip()`` — we must not mangle incremental whitespace across chunks.
    Non-reasoning models have no think tags → returns ``accum`` unchanged → the
    per-chunk delta is identical to the pre-v0.8.65h behaviour.
    """
    s = _THINK_BLOCK_RE.sub("", accum)
    lowered = s.lower()
    open_idx = lowered.find("<think>")
    if open_idx != -1:  # dangling open tag → still thinking; hide the rest
        return s[:open_idx]
    # Withhold a trailing PARTIAL "<think>" prefix — the open tag may be split
    # across chunks ("<th" then "ink>"), and emitting "<th" now would leak the
    # start of a think tag. It's released on the next chunk once disambiguated;
    # if it's the very end of the answer, the `done` event carries it.
    low = s.lower()
    for k in range(len("<think>") - 1, 0, -1):
        if low.endswith("<think>"[:k]):
            return s[:-k]
    return s


async def _stream_chat_events(
    request: ExecuteChatRequest,
    fastapi_request: Request,
) -> AsyncGenerator[str, None]:
    """Generator yielding NDJSON event lines for the streaming endpoint.

    Errors are surfaced as {"type":"error"} events so a partial-stream
    failure doesn't leave the client stuck waiting on a half-written
    response. Client disconnects are detected between yields and stop
    the stream early — important for local LLMs where cancelling a
    long-running token stream actually saves compute.
    """
    try:
        full_session_id = (
            request.session_id
            if request.session_id.startswith("chat_session:")
            else f"chat_session:{request.session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            yield json.dumps({"type": "error", "detail": "Session not found"}) + "\n"
            return

        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        # v0.7.174 — Per-session serialization for the streaming path.
        # Same race as /chat/execute (two concurrent streams to the
        # same thread_id each read the same checkpoint, each append
        # their HumanMessage, each invoke — losing turns). Manual
        # acquire/finally-release used here (instead of `async with`)
        # because the critical section spans the multi-yield generator
        # body and re-indenting the entire astream_events loop in this
        # edit would be high-risk. When the consumer (FastAPI's
        # StreamingResponse) closes the generator early (e.g. client
        # disconnect), the GeneratorExit cleanup runs the finally so
        # the lock IS released even mid-stream.
        from api.utils.session_locks import get_session_lock

        session_lock = await get_session_lock(full_session_id)
        await session_lock.acquire()
        try:
            # Get current state — same as /chat/execute
            current_state = await asyncio.to_thread(
                chat_graph.get_state,
                config=RunnableConfig(configurable={"thread_id": full_session_id}),
            )
            state_values = current_state.values if current_state else {}
            state_values["messages"] = state_values.get("messages", [])
            state_values["context"] = request.context
            state_values["model_override"] = model_override
            # v0.8.42 — propagate the per-request MCP-server disable
            # list into LangGraph state. The chat node reads this in
            # `bind_mcp_and_run_tool_loop` so the user's "load only
            # what I need" pick takes effect on this turn only — no
            # persistent state mutation.
            # v0.8.43 — per-request value wins; fall back to the
            # session's persisted picks (set via PATCH /chat/sessions/{id}
            # when the user toggles the v0.8.42 tool picker). Pre-
            # v0.8.43 the only signal was the request body, which
            # didn't survive page reloads.
            state_values["disabled_mcp_servers"] = (
                request.disabled_mcp_servers
                if request.disabled_mcp_servers is not None
                else getattr(session, "disabled_mcp_servers", None)
            )
            # v0.8.63 — per-request privacy-gate bypass (explicit user consent).
            state_values["bypass_privacy_gate"] = bool(request.bypass_privacy_gate)
            # v0.8.97 — per-turn conversation mode ("standard" | "debate").
            # The graph's prompt-assembly node selects the matching template.
            state_values["chat_mode"] = request.chat_mode

            from langchain_core.messages import HumanMessage

            user_message = HumanMessage(content=request.message)
            state_values["messages"].append(user_message)

            yield (
                json.dumps(
                    {
                        "type": "start",
                        "session_id": request.session_id,
                    }
                )
                + "\n"
            )

            # Stream events from the LangGraph. astream_events yields a rich
            # event stream — we filter for `on_chat_model_stream` which fires
            # for each token chunk the LLM emits.
            # v0.7.52 — removed dead `last_token_idx` counter (was incremented
            # but never read).
            final_result: Optional[dict[str, Any]] = None
            # v0.8.65h — running buffers to strip <think> blocks from streamed
            # tokens (reasoning models). `_stream_accum` is the raw concatenated
            # stream; `_streamed_visible` is the non-think text already sent.
            _stream_accum = ""
            _streamed_visible = ""
            # v0.7.192 — AsyncSqliteSaver twin (lazily initialised).
            # See ainvoke call site above for the full rationale.
            _chat_graph_async = await get_async_graph()
            async for event in _chat_graph_async.astream_events(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={
                        "thread_id": full_session_id,
                        "model_id": model_override,
                    }
                ),
                version="v2",
            ):
                # Stop the stream if the client disconnected — saves the
                # local LLM from churning out tokens nobody will see.
                if await fastapi_request.is_disconnected():
                    logger.info(
                        "chat stream: client disconnected for session {}; halting",
                        full_session_id,
                    )
                    # v0.8.66 (audit M-B5) — if the turn already COMPLETED
                    # (final_result captured via the outer on_chain_end) but the
                    # client dropped during the done phase, still fire the
                    # fire-and-forget memory extraction so the checkpoint-
                    # committed turn isn't silently left unextracted. We skip
                    # when final_result is None (turn incomplete) so a partial
                    # turn never pollutes memory.
                    if final_result and "messages" in final_result:
                        _ai_text = ""
                        for _m in reversed(final_result["messages"]):
                            if getattr(_m, "type", None) == "ai":
                                _ai_text = _extract_text(_m)
                                break
                        if _ai_text:
                            await _fire_memory_extract_turn(
                                chat_session_id=full_session_id,
                                user_text=request.message,
                                assistant_text=_ai_text,
                                model_override=model_override,
                            )
                    return

                etype = event.get("event")
                if etype == "on_chat_model_start":
                    # v0.8.66 (audit A-1) — a NEW model invocation begins (e.g.
                    # the tool loop's post-tool re-invoke). Reset the
                    # per-invocation streaming accumulators so <think>-stripping
                    # is computed over THIS invocation's tokens only — not the
                    # concatenation of every ainvoke this turn, where an unclosed
                    # <think> from the tool-deciding call could swallow the final
                    # answer's tokens (corrupted stream on chatty/tool-using
                    # models). The `done` event still carries the canonical
                    # cleaned final message.
                    _stream_accum = ""
                    _streamed_visible = ""
                    continue
                if etype == "on_chat_model_stream":
                    # Event shape: {"event": "on_chat_model_stream",
                    #               "data": {"chunk": AIMessageChunk(content="...")}}
                    chunk = event.get("data", {}).get("chunk")
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        # v0.8.65h — suppress <think> reasoning live. Re-derive
                        # the visible (non-think) text from the FULL accumulated
                        # stream each chunk (handles tags spanning chunks), then
                        # emit only the new delta. Pre-v0.8.65h the raw chunk
                        # (incl. <think>…</think>) was streamed and only replaced
                        # by the cleaned answer at `done` — reasoning models
                        # (Qwen3, DeepSeek-R1) flashed their raw reasoning at the
                        # user. Non-reasoning models are unaffected (no tags →
                        # visible grows exactly like the raw stream).
                        _stream_accum += content
                        visible = _visible_streamed_text(_stream_accum)
                        if visible.startswith(_streamed_visible) and len(visible) > len(
                            _streamed_visible
                        ):
                            delta = visible[len(_streamed_visible) :]
                            _streamed_visible = visible
                            yield (
                                json.dumps(
                                    {
                                        "type": "token",
                                        "content": delta,
                                    }
                                )
                                + "\n"
                            )
                        elif visible != _streamed_visible:
                            # A <think> opened AFTER some answer text → visible
                            # shrank; resync silently (the `done` event carries
                            # the canonical cleaned message).
                            _streamed_visible = visible
                elif etype == "on_chain_end":
                    # The outer graph's on_chain_end carries the final state.
                    # We capture it to send the canonical messages list with
                    # the done event.
                    #
                    # v0.7.52 — accept either dict or Pydantic state. LangGraph
                    # graphs that declare a TypedDict yield dicts at chain
                    # boundaries; graphs that declare a Pydantic BaseModel
                    # yield model instances. Our graph happens to use a
                    # TypedDict today, but other graphs invoked through the
                    # same streaming machinery may not — and an upstream
                    # LangGraph release could legitimately change this. The
                    # previous `isinstance(output, dict)` guard silently
                    # dropped the final result for the Pydantic shape and
                    # the stream's `done` event would arrive with messages=[],
                    # causing the frontend to fall back to refetching the
                    # session — slow and racy.
                    data = event.get("data", {})
                    output = data.get("output")
                    msgs = None
                    if isinstance(output, dict):
                        msgs = output.get("messages")
                    else:
                        msgs = getattr(output, "messages", None)
                    if msgs is not None:
                        # Normalize to a dict either way so downstream code
                        # can stay simple.
                        # v0.8.1 Item 3 — also capture mcp_tool_calls from
                        # the final output if available, so we can emit it
                        # as a dedicated NDJSON event before stream-close.
                        mcp_calls_raw = (
                            output.get("mcp_tool_calls")
                            if isinstance(output, dict)
                            else getattr(output, "mcp_tool_calls", None)
                        )
                        # v0.8.1 follow-up — also capture the smart-router
                        # selection so the `done` event can include
                        # selected_provider / selected_model_id (parity
                        # with the non-streaming /chat/execute response).
                        # AUDIT FIX: the previous Pydantic-fallback dict
                        # below was `{"messages": msgs, "mcp_tool_calls":
                        # mcp_calls_raw}` which dropped these two fields
                        # on the Pydantic-state branch — frontends saw
                        # null even when the graph populated them.
                        selected_provider_raw = (
                            output.get("selected_provider")
                            if isinstance(output, dict)
                            else getattr(output, "selected_provider", None)
                        )
                        selected_model_id_raw = (
                            output.get("selected_model_id")
                            if isinstance(output, dict)
                            else getattr(output, "selected_model_id", None)
                        )
                        # v0.8.58 — same dual-path capture for the privacy-gate
                        # decision so the Pydantic-state branch doesn't drop it.
                        privacy_gated_raw = (
                            output.get("privacy_gated")
                            if isinstance(output, dict)
                            else getattr(output, "privacy_gated", None)
                        )
                        privacy_categories_raw = (
                            output.get("privacy_categories")
                            if isinstance(output, dict)
                            else getattr(output, "privacy_categories", None)
                        )
                        # v0.8.60 — agent-FSM terminal state (dual-path).
                        agent_state_raw = (
                            output.get("agent_state")
                            if isinstance(output, dict)
                            else getattr(output, "agent_state", None)
                        )
                        # v0.8.68 — offline-fallback info (dual-path).
                        offline_fallback_raw = (
                            output.get("offline_fallback")
                            if isinstance(output, dict)
                            else getattr(output, "offline_fallback", None)
                        )
                        final_result = (
                            output
                            if isinstance(output, dict)
                            else {
                                "messages": msgs,
                                "mcp_tool_calls": mcp_calls_raw,
                                "selected_provider": selected_provider_raw,
                                "selected_model_id": selected_model_id_raw,
                                "privacy_gated": privacy_gated_raw,
                                "privacy_categories": privacy_categories_raw,
                                "agent_state": agent_state_raw,
                                "offline_fallback": offline_fallback_raw,
                            }
                        )
        finally:
            # v0.7.174 — release the per-session lock. Runs on every exit
            # path: normal completion, early `return` on client disconnect,
            # raised exception, AND GeneratorExit when FastAPI's
            # StreamingResponse closes the generator early.
            try:
                session_lock.release()
            except RuntimeError:
                # Already released (e.g. lock wasn't actually acquired
                # because acquire() raised). Safe to swallow.
                pass

        # Final event with the canonical message list
        messages: list = []
        if final_result and "messages" in final_result:
            for msg in final_result.get("messages", []):
                messages.append(
                    {
                        "id": getattr(msg, "id", f"msg_{len(messages)}"),
                        "type": msg.type if hasattr(msg, "type") else "unknown",
                        "content": msg.content if hasattr(msg, "content") else str(msg),
                        "timestamp": None,
                    }
                )

        # Update session timestamp (same as /chat/execute)
        try:
            await session.save()
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            logger.warning("chat stream: session save failed: {}", exc)

        # v0.7.68 — fire the memory extractor. Same logic as
        # /chat/execute: the user's text is the inbound request.message,
        # the assistant's reply is the last AIMessage in the final
        # canonical state. Fire-and-forget; failure does NOT take the
        # stream down.
        # v0.7.83 — pass model_override (already resolved as
        # `model_override` in this handler) so memory writer matches
        # the chat's model.
        ai_text = ""
        if final_result and "messages" in final_result:
            for msg in reversed(final_result["messages"]):
                mtype = getattr(msg, "type", None)
                if mtype == "ai":
                    ai_text = _extract_text(msg)
                    break
        await _fire_memory_extract_turn(
            chat_session_id=full_session_id,
            user_text=request.message,
            assistant_text=ai_text,
            model_override=model_override,
        )

        # v0.8.1 Item 3 — emit a dedicated mcp_tool_calls event just
        # before stream-close so the frontend can stash the payloads
        # in the TanStack Query cache. Emitted only when MCP calls were
        # actually made this turn; clients that don't handle this event
        # type will receive it as an unknown event and skip it.
        mcp_tool_calls_out = (
            final_result.get("mcp_tool_calls")
            if isinstance(final_result, dict)
            else None
        )
        if mcp_tool_calls_out:
            yield (
                json.dumps(
                    {
                        "type": "mcp_tool_calls",
                        "calls": mcp_tool_calls_out,
                    }
                )
                + "\n"
            )

        # v0.8.1 follow-up — surface smart-router decision in the `done`
        # event so SSE clients can render the local/cloud badge without
        # an extra round trip. Keys are ALWAYS present (null when smart
        # routing didn't run / explicit model_override / no on_chain_end
        # event) to keep the wire shape stable for clients that destructure.
        selected_provider_out = (
            final_result.get("selected_provider")
            if isinstance(final_result, dict)
            else None
        )
        selected_model_id_out = (
            final_result.get("selected_model_id")
            if isinstance(final_result, dict)
            else None
        )
        # v0.8.58 — privacy-gate decision parity with /chat/execute.
        privacy_gated_out = (
            final_result.get("privacy_gated")
            if isinstance(final_result, dict)
            else None
        )
        privacy_categories_out = (
            final_result.get("privacy_categories")
            if isinstance(final_result, dict)
            else None
        )
        # v0.8.60 — agent-FSM terminal state parity with /chat/execute.
        agent_state_out_evt = (
            final_result.get("agent_state") if isinstance(final_result, dict) else None
        )
        # v0.8.68 — offline-fallback parity with /chat/execute so SSE
        # clients can render the "Answered with <model> (offline)" pill.
        offline_fallback_out = (
            final_result.get("offline_fallback")
            if isinstance(final_result, dict)
            else None
        )

        yield (
            json.dumps(
                {
                    "type": "done",
                    "messages": messages,
                    "selected_provider": selected_provider_out,
                    "selected_model_id": selected_model_id_out,
                    "privacy_gated": privacy_gated_out,
                    "privacy_categories": privacy_categories_out,
                    "agent_state": agent_state_out_evt,
                    "offline_fallback": offline_fallback_out,
                }
            )
            + "\n"
        )

    except NotFoundError:
        # Streaming context — the HTTP response has already started
        # by the time we get here, so we CAN'T change the status code
        # to 404. Yield a structured error event instead; the frontend
        # renders it via getApiErrorMessage().
        yield json.dumps({"type": "error", "detail": "Session not found"}) + "\n"
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except InvalidInputError as e:
        # v0.7.184 — Was `except (NotFoundError, InvalidInputError): raise`
        # (the v0.7.183 bulk sweep applied uniformly), which (a) made
        # the NotFoundError leg unreachable (already caught above) and
        # (b) tried to BUBBLE in a context where bubbling is wrong —
        # the response has already begun streaming, so we can't change
        # status. Narrowed to InvalidInputError + yield-as-event,
        # matching the NotFoundError treatment above. Backend audit
        # finding #2.
        yield json.dumps({"type": "error", "detail": str(e)}) + "\n"
    except ConfigurationError as e:
        # v0.8.66 (audit A-2) — surface the ACTIONABLE ConfigurationError
        # message (model-config guidance, or the fail-closed privacy-gate block)
        # instead of the generic "failed unexpectedly". These are user-facing
        # config messages, not secrets — same as the non-streaming 422 path.
        # Flag the privacy-gate case so the client can offer a "re-ask allowing
        # cloud" consent action instead of treating it as an opaque failure.
        detail = str(e)
        privacy_blocked = "privacy gate blocked" in detail.lower()
        logger.info(
            "chat stream: ConfigurationError for session {} (privacy_blocked={})",
            request.session_id,
            privacy_blocked,
        )
        evt: dict = {"type": "error", "detail": detail}
        if privacy_blocked:
            evt["privacy_blocked"] = True
        yield json.dumps(evt) + "\n"
    except (ExternalServiceError, NetworkError) as e:
        # v0.8.67i — surface the ACTIONABLE message produced by
        # error_classifier.classify_error() and the *_node-timeout raises
        # (e.g. "Content too large for the selected model. Try using a
        # smaller selection or a model with a larger context window.",
        # "The local model is still loading…", "Could not reach the AI
        # model server…") instead of the opaque "Chat stream failed
        # unexpectedly." below. These are crafted, user-facing strings —
        # NOT raw provider/driver text — so they are safe to echo, same
        # trust level as the ConfigurationError leg above. Before this, a
        # too-large source selection on a local model with a small context
        # window surfaced only the generic failure toast, giving the user
        # no hint to deselect sources or pick a larger-context model.
        logger.info(
            "chat stream: {} for session {}: {}",
            type(e).__name__,
            request.session_id,
            str(e),
        )
        yield json.dumps({"type": "error", "detail": str(e)}) + "\n"
    except Exception as e:
        # v0.7.184 — Don't echo str(e) into the SSE error event. The
        # underlying exception can carry driver internals (SurrealDB
        # WS frames, RecordIDs, partial paths) — same info-leak class
        # the v0.7.168/177 sweeps already closed for non-streaming
        # routes. logger.error captures the full traceback for ops;
        # the client gets a generic message.
        logger.error(
            "Error in /chat/stream for session {}: {}\n{}",
            request.session_id,
            str(e),
            traceback.format_exc(),
        )
        yield (
            json.dumps(
                {
                    "type": "error",
                    "detail": "Chat stream failed unexpectedly.",
                }
            )
            + "\n"
        )


@router.post("/chat/stream")
async def stream_chat(request: ExecuteChatRequest, fastapi_request: Request):
    """Streaming variant of /chat/execute. NDJSON event stream — see
    _stream_chat_events docstring for wire format."""
    # v0.8.116 — heartbeat + server-side idle limit (see
    # api/utils/stream_keepalive.py). Emits {"type":"heartbeat"} lines
    # during model silence so the client can distinguish "thinking" from
    # "daemon died", and ends the stream with an error frame if the model
    # produces nothing for DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC.
    return StreamingResponse(
        with_keepalive(
            _stream_chat_events(request, fastapi_request),
            heartbeat_frame=NDJSON_HEARTBEAT_FRAME,
            make_timeout_frame=lambda idle: json.dumps(
                {"type": "error", "detail": idle_timeout_message(idle)}
            )
            + "\n",
        ),
        media_type="application/x-ndjson",
        # Disable HTTP/1.1 keep-alive buffering on the proxy side. Some
        # reverse proxies (and the Next.js dev proxy) hold buffered
        # responses until they hit a flush threshold; X-Accel-Buffering
        # tells nginx-family proxies to disable that.
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
    )


@router.post("/chat/context", response_model=BuildContextResponse)
async def build_context(request: BuildContextRequest):
    """Build context for a notebook based on context configuration."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        context_data: dict[str, list[dict[str, str]]] = {"sources": [], "notes": []}
        total_content = ""

        # Process context configuration if provided
        if request.context_config:
            # Process sources
            for source_id, status in request.context_config.get("sources", {}).items():
                if "not in" in status:
                    continue

                try:
                    # Add table prefix if not present
                    full_source_id = (
                        source_id
                        if source_id.startswith("source:")
                        else f"source:{source_id}"
                    )

                    try:
                        source = await Source.get(full_source_id)
                    except HTTPException:
                        # v0.7.108 — re-raise typed HTTPExceptions so the next
                        # `except Exception` doesn't clobber them to 500.
                        raise
                    except Exception:
                        continue

                    if "insights" in status:
                        source_context = await source.get_context(context_size="short")
                        context_data["sources"].append(source_context)
                        total_content += str(source_context)
                    elif "full content" in status:
                        source_context = await source.get_context(context_size="long")
                        context_data["sources"].append(source_context)
                        total_content += str(source_context)
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as e:
                    logger.warning(f"Error processing source {source_id}: {str(e)}")
                    continue

            # Process notes
            for note_id, status in request.context_config.get("notes", {}).items():
                if "not in" in status:
                    continue

                try:
                    # Add table prefix if not present
                    full_note_id = (
                        note_id if note_id.startswith("note:") else f"note:{note_id}"
                    )
                    note = await Note.get(full_note_id)
                    if not note:
                        continue

                    if "full content" in status:
                        note_context = note.get_context(context_size="long")
                        context_data["notes"].append(note_context)
                        total_content += str(note_context)
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as e:
                    logger.warning(f"Error processing note {note_id}: {str(e)}")
                    continue
        else:
            # Default behavior - include all sources and notes with short context
            sources = await notebook.get_sources()
            for source in sources:
                try:
                    source_context = await source.get_context(context_size="short")
                    context_data["sources"].append(source_context)
                    total_content += str(source_context)
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as e:
                    logger.warning(f"Error processing source {source.id}: {str(e)}")
                    continue

            notes = await notebook.get_notes()
            for note in notes:
                try:
                    note_context = note.get_context(context_size="short")
                    context_data["notes"].append(note_context)
                    total_content += str(note_context)
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as e:
                    logger.warning(f"Error processing note {note.id}: {str(e)}")
                    continue

        # Calculate character and token counts
        char_count = len(total_content)
        # Use token count utility if available
        try:
            from deeper_notebook.utils import token_count

            estimated_tokens = token_count(total_content) if total_content else 0
        except ImportError:
            # Fallback to simple estimation
            estimated_tokens = char_count // 4

        return BuildContextResponse(
            context=context_data, token_count=estimated_tokens, char_count=char_count
        )
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.183 — bubble typed exceptions to the global handlers
        # (NotFoundError → 404, InvalidInputError → 400). Continuation
        # of the v0.7.179/181/182 sweep to the final routers.
        raise
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise HTTPException(status_code=500, detail="Error building context")
