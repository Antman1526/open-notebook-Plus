"""v0.8.117 — HTTP-level coverage for the v0.8.116 keepalive wrapper.

`tests/test_stream_keepalive.py` exercises `with_keepalive` directly. These
tests go through FastAPI's TestClient for each wrapped endpoint, stub the
inner generator to go silent past a tiny heartbeat interval, and assert the
heartbeat frame (and, for chat, the idle-timeout error frame) is actually on
the wire in the endpoint's own format.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import chat as chat_router
from api.routers import search as search_router
from api.routers import source_chat as source_chat_router


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _FakeChunk:
    def __init__(self, content: str):
        self.content = content


class _FakeSession:
    def __init__(self, id: str = "chat_session:test", model_override=None):
        self.id = id
        self.model_override = model_override
        self.disabled_mcp_servers = None

    async def save(self):
        return None


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC", "0.05")
    monkeypatch.setenv("DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC", "5")


def _ndjson(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def _sse_data(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# /chat/stream (NDJSON)
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_app(monkeypatch):
    from deeper_notebook.domain import notebook as nb_mod

    class _FakeGraph:
        events: list[dict] = []
        delay: float = 0.0

        def get_state(self, config):
            class _S:
                values = {"messages": []}

            return _S()

        async def astream_events(self, *, input, config, version):
            first = True
            for e in self.events:
                if not first and self.delay:
                    await asyncio.sleep(self.delay)
                first = False
                yield e

    fake = _FakeGraph()
    monkeypatch.setattr(chat_router, "chat_graph", fake)

    async def _fake_get_async_graph():
        return fake

    monkeypatch.setattr(chat_router, "get_async_graph", _fake_get_async_graph)

    async def fake_get(session_id: str):
        return _FakeSession() if session_id == "chat_session:test" else None

    monkeypatch.setattr(nb_mod.ChatSession, "get", staticmethod(fake_get))

    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api")
    return app, fake


def test_chat_stream_emits_heartbeat_frames_during_model_silence(fast_heartbeat, chat_app):
    app, fake = chat_app
    fake.delay = 0.25
    fake.events = [
        {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("Hi")}},
        {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("!")}},
    ]

    with TestClient(app) as client:
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "chat_session:test", "message": "Hi", "context": {}},
        )

    assert resp.status_code == 200
    events = _ndjson(resp.text)
    types = [e["type"] for e in events]
    assert types.count("heartbeat") >= 2, types
    # Data frames are preserved, in order, around the heartbeats.
    assert [e["content"] for e in events if e["type"] == "token"] == ["Hi", "!"]
    assert types[0] == "start"


def test_chat_stream_idle_timeout_ends_with_error_frame(monkeypatch, chat_app):
    monkeypatch.setenv("DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC", "0.05")
    monkeypatch.setenv("DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC", "0.2")
    app, fake = chat_app
    fake.delay = 30  # would hang the response without the idle limit
    fake.events = [
        {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("partial")}},
        {"event": "on_chat_model_stream", "data": {"chunk": _FakeChunk("never")}},
    ]

    with TestClient(app) as client:
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "chat_session:test", "message": "Hi", "context": {}},
        )

    events = _ndjson(resp.text)
    assert events[-1]["type"] == "error"
    assert "produced no output" in events[-1]["detail"]
    assert "never" not in resp.text
    assert any(e["type"] == "heartbeat" for e in events)


# ---------------------------------------------------------------------------
# /search/ask (SSE-style `data:` lines)
# ---------------------------------------------------------------------------


def test_search_ask_emits_sse_heartbeat_comments(monkeypatch, fast_heartbeat):
    class _FakeModel:
        def __init__(self, id):
            self.id = id

    async def fake_model_get(model_id: str):
        return _FakeModel(model_id)

    monkeypatch.setattr(search_router.Model, "get", staticmethod(fake_model_get))

    async def fake_embedding_model():
        return object()

    monkeypatch.setattr(
        search_router.model_manager, "get_embedding_model", fake_embedding_model
    )

    async def fake_stream(question, strategy_model, answer_model, final_answer_model, *, fastapi_request):
        yield f"data: {json.dumps({'type': 'strategy', 'reasoning': 'r', 'searches': []})}\n\n"
        await asyncio.sleep(0.25)
        yield f"data: {json.dumps({'type': 'final_answer', 'content': 'done'})}\n\n"
        yield f"data: {json.dumps({'type': 'complete'})}\n\n"

    monkeypatch.setattr(search_router, "stream_ask_response", fake_stream)

    app = FastAPI()
    app.include_router(search_router.router, prefix="/api")
    with TestClient(app) as client:
        resp = client.post(
            "/api/search/ask",
            json={
                "question": "why?",
                "strategy_model": "m1",
                "answer_model": "m2",
                "final_answer_model": "m3",
            },
        )

    assert resp.status_code == 200
    assert resp.text.count(": heartbeat\n\n") >= 2, resp.text
    assert [e["type"] for e in _sse_data(resp.text)] == ["strategy", "final_answer", "complete"]


# ---------------------------------------------------------------------------
# /sources/{id}/chat/sessions/{sid}/messages (SSE-style `data:` lines)
# ---------------------------------------------------------------------------


def test_source_chat_emits_sse_heartbeat_comments(monkeypatch, fast_heartbeat):
    class _FakeSource:
        id = "source:s1"

    async def fake_source_get(source_id: str):
        return _FakeSource()

    monkeypatch.setattr(source_chat_router.Source, "get", staticmethod(fake_source_get))

    session = _FakeSession(id="chat_session:c1")

    async def fake_session_get(session_id: str):
        return session

    monkeypatch.setattr(
        source_chat_router.ChatSession, "get", staticmethod(fake_session_get)
    )

    async def fake_repo_query(query, params=None):
        return [{"in": "chat_session:c1", "out": "source:s1"}]

    monkeypatch.setattr(source_chat_router, "repo_query", fake_repo_query)

    async def fake_stream(**kwargs):
        yield f"data: {json.dumps({'type': 'ai_message_delta', 'content': 'a'})}\n\n"
        await asyncio.sleep(0.25)
        yield f"data: {json.dumps({'type': 'ai_message', 'content': 'ab'})}\n\n"
        yield f"data: {json.dumps({'type': 'complete'})}\n\n"

    monkeypatch.setattr(source_chat_router, "stream_source_chat_response", fake_stream)

    app = FastAPI()
    app.include_router(source_chat_router.router, prefix="/api")
    with TestClient(app) as client:
        resp = client.post(
            "/api/sources/s1/chat/sessions/c1/messages",
            json={"message": "hello"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.text.count(": heartbeat\n\n") >= 2, resp.text
    assert [e["type"] for e in _sse_data(resp.text)] == ["ai_message_delta", "ai_message", "complete"]
