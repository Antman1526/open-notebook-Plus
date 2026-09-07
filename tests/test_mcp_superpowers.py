"""Unit tests for Deeper Notebook MCP stdio client and FastMCP server tools."""

import pytest
from unittest.mock import AsyncMock, patch
from deeper_notebook.mcp.client import MCPClient
from deeper_notebook.mcp import server


def test_mcp_client_stdio_dataclass_fields():
    client = MCPClient(
        url="stdio://cat",
        transport="stdio",
        command="cat",
        args=["-n"],
        env={"TEST_KEY": "val"},
    )
    assert client.transport == "stdio"
    assert client.command == "cat"
    assert client.args == ["-n"]
    assert client.env == {"TEST_KEY": "val"}


@pytest.mark.asyncio
async def test_fastmcp_list_notebooks_mocked():
    fake_rows = [{"id": "notebook:1", "name": "Deep Research", "description": "AI papers"}]
    with patch("deeper_notebook.mcp.server.repo_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = fake_rows
        result = await server.list_notebooks()
        assert len(result) == 1
        assert result[0]["id"] == "notebook:1"
        assert result[0]["name"] == "Deep Research"


@pytest.mark.asyncio
async def test_fastmcp_get_note_mocked():
    fake_rows = [{"id": "note:abc", "title": "My Note", "content": "# Hello World"}]
    with patch("deeper_notebook.mcp.server.repo_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = fake_rows
        result = await server.get_note("note:abc")
        assert result["id"] == "note:abc"
        assert result["title"] == "My Note"
        assert result["content"] == "# Hello World"


@pytest.mark.asyncio
async def test_fastmcp_read_source_mocked():
    fake_rows = [{"id": "source:xyz", "title": "Paper", "full_text": "Content of paper", "url": "https://example.com"}]
    with patch("deeper_notebook.mcp.server.repo_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = fake_rows
        result = await server.read_source("source:xyz")
        assert result["id"] == "source:xyz"
        assert result["text"] == "Content of paper"
        assert result["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_fastmcp_search_knowledge_mocked():
    note_rows = [{"id": "note:1", "title": "Note 1", "content": "Matching search snippet"}]
    with patch("deeper_notebook.mcp.server.repo_query", new_callable=AsyncMock) as mock_query, \
         patch("deeper_notebook.search.reranker.rerank_results", new_callable=AsyncMock) as mock_rerank:
        mock_query.return_value = note_rows
        mock_rerank.return_value = note_rows
        result = await server.search_knowledge("search query", limit=3)
        assert len(result) == 1
        assert result[0]["id"] == "note:1"
        assert "Matching" in result[0]["snippet"]
