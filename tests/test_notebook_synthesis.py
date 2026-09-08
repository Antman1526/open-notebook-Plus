"""Tests for executive cross-source synthesis endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from api.routers.notebooks import (
    ExecutiveSynthesisResponse,
    _EXECUTIVE_SYNTHESIS_SYSTEM,
    router,
)
from deeper_notebook.domain.notebook import Notebook
from deeper_notebook.exceptions import NotFoundError


@pytest.fixture()
def app():
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_synthesis_returns_structured_markdown(app, monkeypatch):
    nb = Notebook(name="Quantum AI", description="Research notes")
    nb.id = "notebook:123"

    sources = [
        SimpleNamespace(
            id="source:1",
            title="Qubit Stability",
            topics=["physics", "quantum"],
            full_text="Superconducting qubits exhibit decoherence under thermal noise.",
        ),
        SimpleNamespace(
            id="source:2",
            title="Error Correction",
            topics=["algorithms", "fault-tolerance"],
            full_text="Surface codes achieve threshold error rates with high fidelity.",
        ),
    ]

    async def mock_get(cls, nid):
        if nid == "notebook:123":
            return nb
        raise NotFoundError("Notebook not found")

    async def mock_sources(self):
        return sources

    monkeypatch.setattr(Notebook, "get", classmethod(mock_get))
    monkeypatch.setattr(Notebook, "get_sources", mock_sources)

    # Mock provision_langchain_model
    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = SimpleNamespace(
        content="## 🎯 Executive Summary\nSynthesis content across quantum research."
    )

    import deeper_notebook.ai.provision as provision

    monkeypatch.setattr(
        provision,
        "provision_langchain_model",
        AsyncMock(return_value=mock_chain),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/notebooks/notebook:123/synthesis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["notebook_id"] == "notebook:123"
        assert data["notebook_name"] == "Quantum AI"
        assert "## 🎯 Executive Summary" in data["synthesis"]
        assert data["source_count"] == 2
        assert "Qubit Stability" in data["sources"]


@pytest.mark.asyncio
async def test_synthesis_400_when_no_sources(app, monkeypatch):
    nb = Notebook(name="Empty Notebook", description="")
    nb.id = "notebook:empty"

    async def mock_get(cls, nid):
        return nb

    async def mock_sources(self):
        return []

    monkeypatch.setattr(Notebook, "get", classmethod(mock_get))
    monkeypatch.setattr(Notebook, "get_sources", mock_sources)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/notebooks/notebook:empty/synthesis")
        assert resp.status_code == 400
        assert "Cannot generate synthesis without sources" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_synthesis_404_when_notebook_missing(app, monkeypatch):
    async def mock_get(cls, nid):
        raise NotFoundError("Notebook not found")

    monkeypatch.setattr(Notebook, "get", classmethod(mock_get))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/notebooks/notebook:missing/synthesis")
        assert resp.status_code == 404
