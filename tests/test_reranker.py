"""Unit tests for the cross-encoder reranker module."""

import pytest
import httpx
from deeper_notebook.search.reranker import extract_document_text, rerank_results


def test_extract_document_text():
    assert extract_document_text({"content": "hello world"}) == "hello world"
    assert extract_document_text({"text": "fallback text"}) == "fallback text"
    assert extract_document_text({"title": "Doc Title", "content": "Doc Body"}) == "Doc Title\n\nDoc Body"
    assert extract_document_text({"title": "Only Title"}) == "Only Title"
    assert extract_document_text("plain string") == "plain string"


@pytest.mark.asyncio
async def test_rerank_results_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_RERANKER_URL", raising=False)
    results = [{"id": "1", "content": "first"}, {"id": "2", "content": "second"}]
    reranked = await rerank_results("test query", results)
    assert reranked == results


@pytest.mark.asyncio
async def test_rerank_results_empty():
    assert await rerank_results("", []) == []
    assert await rerank_results("query", []) == []
    assert await rerank_results("  ", [{"id": "1"}]) == [{"id": "1"}]


@pytest.mark.asyncio
async def test_rerank_results_successful(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_RERANKER_URL", "http://127.0.0.1:8012/v1/rerank")

    results = [
        {"id": "doc1", "content": "introductory text"},
        {"id": "doc2", "content": "highly relevant text for query"},
        {"id": "doc3", "content": "irrelevant text"},
    ]

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        data = {
            "results": [
                {"index": 1, "relevance_score": 0.98},
                {"index": 0, "relevance_score": 0.45},
                {"index": 2, "relevance_score": 0.12},
            ]
        }
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(mock_handler)
    # Monkeypatch httpx.AsyncClient to use mock transport
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    reranked = await rerank_results("relevant query", results, top_n=2)
    assert len(reranked) == 2
    assert reranked[0]["id"] == "doc2"
    assert reranked[0]["rerank_score"] == 0.98
    assert reranked[1]["id"] == "doc1"
    assert reranked[1]["rerank_score"] == 0.45


@pytest.mark.asyncio
async def test_rerank_results_fail_soft_on_error(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_RERANKER_URL", "http://127.0.0.1:8012/v1/rerank")

    results = [{"id": "doc1", "content": "text1"}, {"id": "doc2", "content": "text2"}]

    async def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(error_handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    reranked = await rerank_results("query", results)
    # Fails soft: original order preserved
    assert [r["id"] for r in reranked] == ["doc1", "doc2"]
