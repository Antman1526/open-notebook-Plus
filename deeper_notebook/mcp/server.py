"""Deeper Notebook Model Context Protocol (MCP) Server.

Exposes Deeper Notebook's personal knowledge base, notes, and sources as standard
MCP tools so external clients (Claude Desktop, Cursor, Zed, Windsurf) can query
and cite your private research workspace.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from deeper_notebook.database.repository import ensure_record_id, repo_query

mcp = FastMCP("DeeperNotebook")


@mcp.tool()
async def list_notebooks() -> list[dict[str, Any]]:
    """List all available notebooks in Deeper Notebook with their ids and names."""
    rows = await repo_query("SELECT id, name, description, created FROM notebook ORDER BY updated DESC")
    return [
        {
            "id": str(r.get("id", "")),
            "name": r.get("name", "Untitled"),
            "description": r.get("description", ""),
        }
        for r in (rows or [])
    ]


@mcp.tool()
async def list_sources(notebook_id: Optional[str] = None) -> list[dict[str, Any]]:
    """List reference sources in Deeper Notebook, optionally filtered by notebook id."""
    if notebook_id:
        try:
            nb_rid = ensure_record_id(notebook_id)
            rows = await repo_query(
                "SELECT id, title, url, created FROM source WHERE notebook = $nb ORDER BY created DESC",
                {"nb": nb_rid},
            )
        except Exception:
            rows = []
    else:
        rows = await repo_query("SELECT id, title, url, created FROM source ORDER BY created DESC LIMIT 50")

    return [
        {
            "id": str(r.get("id", "")),
            "title": r.get("title", "Untitled Source"),
            "url": r.get("url"),
        }
        for r in (rows or [])
    ]


@mcp.tool()
async def get_note(note_id: str) -> dict[str, Any]:
    """Retrieve the full markdown content and title of a note by note id."""
    try:
        rid = ensure_record_id(note_id)
        rows = await repo_query("SELECT id, title, content, updated FROM note WHERE id = $id LIMIT 1", {"id": rid})
        if not rows:
            return {"error": f"Note {note_id} not found"}
        row = rows[0]
        return {
            "id": str(row.get("id", "")),
            "title": row.get("title", "Untitled"),
            "content": row.get("content", ""),
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def read_source(source_id: str) -> dict[str, Any]:
    """Retrieve the extracted full text of an ingested source document or web page."""
    try:
        rid = ensure_record_id(source_id)
        rows = await repo_query(
            "SELECT id, title, full_text, url FROM source WHERE id = $id LIMIT 1",
            {"id": rid},
        )
        if not rows:
            return {"error": f"Source {source_id} not found"}
        row = rows[0]
        return {
            "id": str(row.get("id", "")),
            "title": row.get("title", "Untitled"),
            "text": (row.get("full_text") or "")[:20000],
            "url": row.get("url"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Deeper Notebook notes and sources for relevant passages matching a query."""
    from deeper_notebook.search.reranker import rerank_results

    try:
        # Search notes and sources matching keyword query
        rows = await repo_query(
            "SELECT id, title, content FROM note WHERE title ~ $q OR content ~ $q LIMIT 20",
            {"q": query},
        )
        source_rows = await repo_query(
            "SELECT id, title, full_text as content FROM source WHERE title ~ $q OR full_text ~ $q LIMIT 20",
            {"q": query},
        )
        candidates = list(rows or []) + list(source_rows or [])
        if not candidates:
            return []

        reranked = await rerank_results(query, candidates, top_n=limit)
        return [
            {
                "id": str(r.get("id", "")),
                "title": r.get("title", "Untitled"),
                "snippet": (r.get("content") or "")[:500],
            }
            for r in reranked
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


def run_stdio():
    """Run the Deeper Notebook MCP server over stdio for CLI / agent integration."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_stdio()
