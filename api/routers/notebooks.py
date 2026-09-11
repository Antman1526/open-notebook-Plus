from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.models import (
    DiscoverResult,
    DiscoverSourcesRequest,
    DiscoverSourcesResponse,
    NotebookCreate,
    NotebookDeletePreview,
    NotebookDeleteResponse,
    NotebookGraphResponse,
    NotebookResponse,
    NotebookUpdate,
)
from api.utils.iso import iso  # v0.7.181 — Safari-safe datetime serialization
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import (
    ExternalNoteReadOnlyError,
    Notebook,
    Source,
)
from deeper_notebook.exceptions import InvalidInputError, NotFoundError

router = APIRouter()


async def _cleanup_checkpoint_threads(session_ids: list[str], *, context: str) -> int:
    """v0.8.48 — best-effort LangGraph checkpoint cleanup for chat
    sessions cascade-deleted by a notebook delete.

    The domain `Notebook.delete()` removes the `chat_session` ROWS but
    deliberately can't touch the LangGraph checkpointer (layering — the
    domain layer must not import the chat graph). The single-session
    delete path already cleans checkpoints (api/routers/chat.py
    v0.7.171); without this, a notebook delete leaked every cascade-
    deleted session's checkpoint thread forever, because
    `prune_old_checkpoints` only trims the oldest snapshots WITHIN a
    thread that exceeds the per-thread retention (50) — an orphaned
    <50-checkpoint thread is never reached.

    Best-effort by design: the SurrealDB rows are already gone, so a
    checkpoint-cleanup failure must NOT fail the delete. Each thread is
    isolated in its own try/except so one failure doesn't abort the
    rest. Returns the count successfully cleaned. Never raises.
    """
    if not session_ids:
        return 0
    try:
        import asyncio

        from deeper_notebook.graphs.chat import chat_graph

        checkpointer = getattr(chat_graph, "checkpointer", None)
        delete_thread = getattr(checkpointer, "delete_thread", None)
    except Exception as exc:  # import / attribute access failure
        logger.warning(
            "Checkpoint cleanup unavailable for {} (non-fatal): {}",
            context,
            exc,
        )
        return 0
    if delete_thread is None:
        return 0
    cleaned = 0
    for sid in session_ids:
        try:
            await asyncio.to_thread(delete_thread, sid)
            cleaned += 1
        except Exception as cleanup_exc:
            logger.warning(
                "Checkpoint cleanup failed for session {} ({}) — "
                "non-fatal, row already deleted: {}",
                sid,
                context,
                cleanup_exc,
            )
    logger.debug(
        "Cleaned up {}/{} checkpoint thread(s) for {}",
        cleaned,
        len(session_ids),
        context,
    )
    return cleaned


@router.get("/notebooks", response_model=list[NotebookResponse])
async def get_notebooks(
    archived: Optional[bool] = Query(None, description="Filter by archived status"),
    order_by: str = Query("updated desc", description="Order by field and direction"),
):
    """Get all notebooks with optional filtering and ordering."""
    try:
        # Validate order_by against allowlist to prevent SurrealQL injection
        allowed_fields = {"name", "created", "updated"}
        allowed_directions = {"asc", "desc"}

        parts = order_by.strip().lower().split()
        if len(parts) == 1:
            if parts[0] not in allowed_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid order_by field: '{order_by}'. Allowed fields: {', '.join(sorted(allowed_fields))}",
                )
            validated_order_by = parts[0]
        elif len(parts) == 2:
            if parts[0] not in allowed_fields or parts[1] not in allowed_directions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid order_by: '{order_by}'. Allowed fields: {', '.join(sorted(allowed_fields))}. Allowed directions: asc, desc",
                )
            validated_order_by = f"{parts[0]} {parts[1]}"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid order_by format: '{order_by}'. Expected 'field' or 'field direction'",
            )

        # v0.7.166 — `archived` filter moved into the WHERE clause.
        # Previously this fetched ALL notebook rows (including the
        # source_count + note_count subqueries fired per row) and
        # then filtered in Python. With many archived notebooks a
        # caller asking for `?archived=false` paid for the full
        # archive scan + post-filtering — wasted DB work + payload.
        # Now SurrealDB skips archived rows server-side. Note: f-string
        # interpolation of `validated_order_by` is safe — it's been
        # checked against the `allowed_fields` allowlist + `allowed_directions`
        # whitelist above; raw user input never reaches the query.
        # `archived` flows in via the `$archived` binding, not f-string.
        where_clause = ""
        params: dict = {}
        if archived is not None:
            where_clause = "WHERE archived = $archived"
            params["archived"] = archived

        query = f"""
            SELECT *,
            count(<-reference.in) as source_count,
            count(<-artifact.in) as note_count
            FROM notebook
            {where_clause}
            ORDER BY {validated_order_by}
        """  # nosec B608

        result = await repo_query(query, params if params else None)

        return [
            NotebookResponse(
                id=str(nb.get("id", "")),
                name=nb.get("name", ""),
                description=nb.get("description", ""),
                archived=nb.get("archived", False),
                created=str(nb.get("created", "")),
                updated=str(nb.get("updated", "")),
                source_count=nb.get("source_count", 0),
                note_count=nb.get("note_count", 0),
            )
            for nb in result
        ]
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(f"Error fetching notebooks: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching notebooks")


@router.post("/notebooks", response_model=NotebookResponse)
async def create_notebook(notebook: NotebookCreate):
    """Create a new notebook."""
    try:
        new_notebook = Notebook(
            name=notebook.name,
            description=notebook.description,
        )
        await new_notebook.save()

        return NotebookResponse(
            id=new_notebook.id or "",
            name=new_notebook.name,
            description=new_notebook.description,
            archived=new_notebook.archived or False,
            # v0.7.181 — iso() for Safari new Date() compat.
            created=iso(new_notebook.created),
            updated=iso(new_notebook.updated),
            source_count=0,  # New notebook has no sources
            note_count=0,  # New notebook has no notes
        )
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except Exception as e:
        logger.error(f"Error creating notebook: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating notebook")


@router.get(
    "/notebooks/{notebook_id}/delete-preview", response_model=NotebookDeletePreview
)
async def get_notebook_delete_preview(notebook_id: str):
    """Get a preview of what will be deleted when this notebook is deleted."""
    try:
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        preview = await notebook.get_delete_preview()

        return NotebookDeletePreview(
            notebook_id=str(notebook.id),
            notebook_name=notebook.name,
            note_count=preview["note_count"],
            exclusive_source_count=preview["exclusive_source_count"],
            shared_source_count=preview["shared_source_count"],
        )
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(f"Error getting delete preview for notebook {notebook_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error fetching notebook deletion preview",
        )


@router.get("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def get_notebook(notebook_id: str):
    """Get a specific notebook by ID."""
    try:
        # Query with counts for single notebook
        query = """
            SELECT *,
            count(<-reference.in) as source_count,
            count(<-artifact.in) as note_count
            FROM $notebook_id
        """
        result = await repo_query(query, {"notebook_id": ensure_record_id(notebook_id)})

        if not result:
            raise HTTPException(status_code=404, detail="Notebook not found")

        nb = result[0]
        return NotebookResponse(
            id=str(nb.get("id", "")),
            name=nb.get("name", ""),
            description=nb.get("description", ""),
            archived=nb.get("archived", False),
            created=str(nb.get("created", "")),
            updated=str(nb.get("updated", "")),
            source_count=nb.get("source_count", 0),
            note_count=nb.get("note_count", 0),
        )
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(f"Error fetching notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching notebook")


# v0.8.74 — Suggested starter questions (improvement roadmap, Batch 1).
# NotebookLM seeds clickable starter questions so the chat never opens to a
# blank box. Generate a few concise, corpus-grounded questions from the
# notebook's source titles + topics via a single bounded LLM call. This is a
# NON-CRITICAL convenience: any failure (no sources, no model configured, LLM
# error/timeout, unparseable output) degrades gracefully to an empty list
# rather than a 500, so it can never block opening a notebook.
_SUGGESTED_QUESTIONS_SYSTEM = (
    "You help a user start exploring a research notebook. Given the notebook and "
    "a list of its sources (titles and topics), propose {n} concise, specific "
    "starter questions the user could ask that THIS corpus can plausibly answer.\n"
    "Rules:\n"
    "- Only propose questions answerable from the listed sources; do not invent "
    "topics that aren't present.\n"
    "- Each question is under ~16 words and ends with '?'. No numbering or bullets.\n"
    "- Output ONLY the questions, exactly one per line."
)


@router.get("/notebooks/{notebook_id}/suggested-questions")
async def get_suggested_questions(notebook_id: str, limit: int = Query(4, ge=1, le=8)):
    """Generate starter questions grounded in the notebook's sources.

    Best-effort: returns ``{"questions": []}`` on any failure (no sources, no
    model configured, LLM error) — starter questions must never block the
    notebook UI. NotFound/InvalidInput still surface as 404/400.
    """
    import asyncio

    from langchain_core.messages import HumanMessage, SystemMessage

    from deeper_notebook.ai.provision import provision_langchain_model
    from deeper_notebook.utils import clean_thinking_content
    from deeper_notebook.utils.text_utils import extract_text_content

    try:
        notebook = await Notebook.get(notebook_id)
    except HTTPException:
        # v0.8.74 — re-raise typed HTTP errors so the generic catch below can't
        # clobber a 4xx/5xx to 500 (enforced by tests/test_v0_7_135_meta.py).
        raise
    except (NotFoundError, InvalidInputError):
        raise
    except Exception as e:
        logger.error(f"suggested-questions: notebook fetch failed {notebook_id}: {e}")
        raise HTTPException(status_code=404, detail="Notebook not found")

    try:
        sources = await notebook.get_sources()
    except Exception as e:
        logger.warning(
            f"suggested-questions: get_sources failed for {notebook_id}: {e}"
        )
        return {"questions": []}
    if not sources:
        return {"questions": []}

    # Build a compact corpus digest (titles + topics); cap so the prompt stays
    # small on large notebooks.
    lines = []
    for s in sources[:40]:
        title = (getattr(s, "title", None) or "Untitled source").strip()
        topics = ", ".join((getattr(s, "topics", None) or [])[:6])
        lines.append(f"- {title}" + (f" — {topics}" if topics else ""))
    corpus = (
        f"Notebook: {notebook.name or 'Untitled'}\n"
        f"Description: {notebook.description or '(none)'}\n\n"
        "Sources:\n" + "\n".join(lines)
    )
    system = _SUGGESTED_QUESTIONS_SYSTEM.format(n=limit)

    try:
        chain = await provision_langchain_model(
            system + "\n" + corpus, None, "transformation", max_tokens=400
        )
        response = await asyncio.wait_for(
            chain.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=corpus)]
            ),
            timeout=30.0,
        )
    except Exception as e:
        # Non-critical: log at info and degrade to no suggestions.
        logger.info(f"suggested-questions: generation skipped for {notebook_id} ({e})")
        return {"questions": []}

    text = clean_thinking_content(extract_text_content(response.content))
    questions: list[str] = []
    for raw in text.splitlines():
        q = raw.strip().lstrip("-*•0123456789.) ").strip().strip('"').strip()
        if len(q) >= 8 and "?" in q and q not in questions:
            questions.append(q)
        if len(questions) >= limit:
            break
    return {"questions": questions}


class ExecutiveSynthesisResponse(BaseModel):
    notebook_id: str
    notebook_name: str
    synthesis: str
    source_count: int
    sources: list[str]


_EXECUTIVE_SYNTHESIS_SYSTEM = (
    "You are an elite research synthesizer and intelligence analyst. Your objective is "
    "to analyze the provided notebook sources and produce an executive cross-source synthesis "
    "that uncovers patterns, points of agreement, critical tensions, and actionable intelligence.\n\n"
    "Structure your response strictly in clear, polished Markdown with the following sections:\n"
    "## 🎯 Executive Summary\n"
    "A tight, high-impact synthesis of the overall corpus.\n\n"
    "## 🌐 Cross-Cutting Themes & Consensus\n"
    "Key insights and common ground shared across the sources.\n\n"
    "## ⚡ Points of Tension & Divergence\n"
    "Disagreements, competing hypotheses, contrasting perspectives, or methodological differences.\n\n"
    "## 🔍 Critical Gaps & Open Questions\n"
    "What the current sources omit, assume without evidence, or leave unresolved.\n\n"
    "## 🚀 Actionable Recommendations & Next Steps\n"
    "Concrete next steps, follow-up inquiry vectors, or practical implications."
)


@router.post("/notebooks/{notebook_id}/synthesis", response_model=ExecutiveSynthesisResponse)
async def generate_notebook_synthesis(notebook_id: str):
    """Generate an executive cross-source synthesis uncovering themes, tensions, and next steps."""
    import asyncio

    from langchain_core.messages import HumanMessage, SystemMessage

    from deeper_notebook.ai.provision import provision_langchain_model
    from deeper_notebook.utils import clean_thinking_content
    from deeper_notebook.utils.text_utils import extract_text_content

    try:
        notebook = await Notebook.get(notebook_id)
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        raise HTTPException(status_code=404, detail="Notebook not found")
    except Exception as e:
        logger.error(f"synthesis: notebook fetch failed {notebook_id}: {e}")
        raise HTTPException(status_code=404, detail="Notebook not found")

    try:
        try:
            sources = await notebook.get_sources(include_full_text=True)
        except TypeError:
            sources = await notebook.get_sources()
    except Exception as e:
        logger.warning(f"synthesis: get_sources failed for {notebook_id}: {e}")
        sources = []

    if not sources:
        raise HTTPException(
            status_code=400,
            detail="Cannot generate synthesis without sources in the notebook.",
        )

    # Build corpus representation with title, topics, and excerpt
    source_lines = []
    source_titles = []
    for i, s in enumerate(sources[:20], 1):
        title = (getattr(s, "title", None) or f"Source {i}").strip()
        source_titles.append(title)
        topics = ", ".join((getattr(s, "topics", None) or [])[:5])
        raw_text = getattr(s, "full_text", None) or ""
        if not raw_text and getattr(s, "id", None):
            try:
                hydrated = await Source.get(s.id)
                if hydrated and getattr(hydrated, "full_text", None):
                    raw_text = hydrated.full_text
            except Exception:
                pass
        excerpt = raw_text[:1200].replace("\n", " ") if raw_text else "(no full text)"
        source_lines.append(f"### Source {i}: {title}\nTopics: {topics}\nExcerpt: {excerpt}\n")

    corpus = (
        f"Notebook Title: {notebook.name or 'Untitled Notebook'}\n"
        f"Description: {notebook.description or '(no description)'}\n\n"
        f"Sources ({len(sources)} total):\n" + "\n".join(source_lines)
    )

    try:
        chain = await provision_langchain_model(
            _EXECUTIVE_SYNTHESIS_SYSTEM + "\n" + corpus, None, "transformation", max_tokens=1500
        )
        response = await asyncio.wait_for(
            chain.ainvoke(
                [
                    SystemMessage(content=_EXECUTIVE_SYNTHESIS_SYSTEM),
                    HumanMessage(content=corpus),
                ]
            ),
            timeout=60.0,
        )
        raw_text = clean_thinking_content(extract_text_content(response.content))
        synthesis_text = raw_text.strip()
    except Exception as e:
        logger.error(f"synthesis: generation failed for {notebook_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate executive synthesis: {e}",
        )

    return ExecutiveSynthesisResponse(
        notebook_id=notebook_id,
        notebook_name=notebook.name or "Untitled Notebook",
        synthesis=synthesis_text,
        source_count=len(sources),
        sources=source_titles,
    )



@router.get("/notebooks/{notebook_id}/graph", response_model=NotebookGraphResponse)
async def get_notebook_graph(notebook_id: str):
    """v0.8.83 — mind-map graph (improvement roadmap, Batch 3).

    Returns the notebook as a hub node plus its sources and notes as connected
    nodes, grounded in the existing reference/artifact edges (no schema change).
    The frontend renders this with React Flow and deep-links node clicks.
    """
    try:
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")
        graph = await notebook.get_graph()
        return NotebookGraphResponse(**graph)
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except Exception as e:
        logger.error(f"Error building notebook graph for {notebook_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to build notebook graph")


@router.post(
    "/notebooks/{notebook_id}/discover-sources",
    response_model=DiscoverSourcesResponse,
)
async def discover_sources(notebook_id: str, request: DiscoverSourcesRequest):
    """v0.8.87 — Discover sources (improvement roadmap, Batch 3).

    Guarded web search over the existing `web_search` tool. SEARCH ONLY —
    returns candidate {title, url, snippet}; the user picks which to add
    (as link sources via the normal POST /sources pipeline). Privacy: this
    reaches the network only when the user runs it. v0.8.82 — the provider
    chain now ends in a keyless Wikipedia tail, so `enabled` is True on a
    fresh install; `DEEPER_NOTEBOOK_WEB_SEARCH_KEYLESS=0` restores the old
    key-only gating, and with that set and no key, `enabled=False` and the UI
    shows a setup hint (HTTP 200, never an error). Best-effort:
    provider/transport errors degrade to empty results rather than failing
    the request.
    """
    from deeper_notebook.tools.web_search import (
        active_provider,
        run_web_search,
        web_search_enabled,
    )

    if not web_search_enabled():
        return DiscoverSourcesResponse(enabled=False, provider=None, results=[])

    query = (request.query or "").strip()
    if not query:
        return DiscoverSourcesResponse(
            enabled=True, provider=active_provider(), results=[]
        )

    try:
        limit = max(1, min(int(request.limit or 6), 20))
        raw = await run_web_search(query, max_results=limit)
    except Exception as e:  # best-effort — never 500 on a search hiccup
        logger.warning(f"discover-sources search failed for {notebook_id}: {e}")
        raw = []

    results = [
        DiscoverResult(
            title=str(r.get("title") or ""),
            url=str(r.get("url") or ""),
            snippet=str(r.get("snippet") or ""),
        )
        for r in raw
        if r.get("url")
    ]
    return DiscoverSourcesResponse(
        enabled=True, provider=active_provider(), results=results
    )


@router.put("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def update_notebook(notebook_id: str, notebook_update: NotebookUpdate):
    """Update a notebook."""
    try:
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Update only provided fields
        if notebook_update.name is not None:
            notebook.name = notebook_update.name
        if notebook_update.description is not None:
            notebook.description = notebook_update.description
        if notebook_update.archived is not None:
            notebook.archived = notebook_update.archived

        await notebook.save()

        # Query with counts after update
        query = """
            SELECT *,
            count(<-reference.in) as source_count,
            count(<-artifact.in) as note_count
            FROM $notebook_id
        """
        result = await repo_query(query, {"notebook_id": ensure_record_id(notebook_id)})

        if result:
            nb = result[0]
            return NotebookResponse(
                id=str(nb.get("id", "")),
                name=nb.get("name", ""),
                description=nb.get("description", ""),
                archived=nb.get("archived", False),
                created=str(nb.get("created", "")),
                updated=str(nb.get("updated", "")),
                source_count=nb.get("source_count", 0),
                note_count=nb.get("note_count", 0),
            )

        # Fallback if query fails
        return NotebookResponse(
            id=notebook.id or "",
            name=notebook.name,
            description=notebook.description,
            archived=notebook.archived or False,
            # v0.7.181 — iso() for Safari new Date() compat.
            created=iso(notebook.created),
            updated=iso(notebook.updated),
            source_count=0,
            note_count=0,
        )
    except HTTPException:
        raise
    except NotFoundError:
        # v0.7.179 — bubble to global handler → 404 (Notebook.get raises
        # NotFoundError instead of returning None; the local `if not
        # notebook: raise HTTPException(404)` guard above is dead code).
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating notebook")


@router.post("/notebooks/{notebook_id}/sources/{source_id}")
async def add_source_to_notebook(notebook_id: str, source_id: str):
    """Add an existing source to a notebook (create the reference)."""
    try:
        # Check if notebook exists
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Check if source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if reference already exists (idempotency).
        # v0.7.60 — query columns were swapped: the `RELATE
        # $source_id->reference->$notebook_id` below produces an edge
        # with `in=source, out=notebook`, so the idempotency check has
        # to match that direction. The previous version checked
        # `out=source, in=notebook` (always empty), so EVERY call
        # created a fresh edge. `source_count` then inflated without
        # bound and the symmetric DELETE on line 301 (which uses the
        # correct direction) only removed a single edge per call,
        # leaving the rest as junk. Matches the delete-query orientation
        # now.
        existing_ref = await repo_query(
            "SELECT * FROM reference WHERE out = $notebook_id AND in = $source_id",
            {
                "notebook_id": ensure_record_id(notebook_id),
                "source_id": ensure_record_id(source_id),
            },
        )

        # If reference doesn't exist, create it
        if not existing_ref:
            await repo_query(
                "RELATE $source_id->reference->$notebook_id",
                {
                    "notebook_id": ensure_record_id(notebook_id),
                    "source_id": ensure_record_id(source_id),
                },
            )

        return {"message": "Source linked to notebook successfully"}
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(
            f"Error linking source {source_id} to notebook {notebook_id}: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Error linking source to notebook")


@router.delete("/notebooks/{notebook_id}/sources/{source_id}")
async def remove_source_from_notebook(notebook_id: str, source_id: str):
    """Remove a source from a notebook (delete the reference)."""
    try:
        # Check if notebook exists
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Delete the reference record linking source to notebook
        await repo_query(
            "DELETE FROM reference WHERE out = $notebook_id AND in = $source_id",
            {
                "notebook_id": ensure_record_id(notebook_id),
                "source_id": ensure_record_id(source_id),
            },
        )

        return {"message": "Source removed from notebook successfully"}
    except HTTPException:
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(
            f"Error removing source {source_id} from notebook {notebook_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Error removing source from notebook"
        )


@router.delete("/notebooks/{notebook_id}", response_model=NotebookDeleteResponse)
async def delete_notebook(
    notebook_id: str,
    delete_exclusive_sources: bool = Query(
        False,
        description="Whether to delete sources that belong only to this notebook",
    ),
):
    """
    Delete a notebook with cascade deletion.

    Always deletes all notes associated with the notebook.
    If delete_exclusive_sources is True, also deletes sources that belong only
    to this notebook (not linked to any other notebooks).
    """
    try:
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        result = await notebook.delete(
            delete_exclusive_sources=delete_exclusive_sources
        )

        # v0.8.48 — clean up the LangGraph checkpoint threads for the chat
        # sessions this delete cascaded away (see _cleanup_checkpoint_threads).
        await _cleanup_checkpoint_threads(
            result.get("deleted_chat_session_ids") or [],
            context=f"notebook {notebook_id}",
        )

        return NotebookDeleteResponse(
            message="Notebook deleted successfully",
            deleted_notes=result["deleted_notes"],
            deleted_sources=result["deleted_sources"],
            unlinked_sources=result["unlinked_sources"],
        )
    except HTTPException:
        raise
    except ExternalNoteReadOnlyError:
        raise HTTPException(status_code=409, detail="external_note_read_only")
    except (NotFoundError, InvalidInputError):
        # v0.7.179 — Let typed exceptions bubble to the global handlers
        # in api/main.py (NotFoundError → 404, InvalidInputError → 400).
        # Without this re-raise, the broad `except Exception` below
        # masks legitimate 404/400 cases as generic 500s.
        raise
    except Exception as e:
        logger.error(f"Error deleting notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting notebook")
