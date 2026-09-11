"""v0.8.126 — investigation of the live "500s while simply viewing a
notebook, with no models configured" report.

The API log the report quotes is `provision_langchain_model`'s own
`logger.error(...)` right before it raises `ConfigurationError` (see
`deeper_notebook/ai/provision.py`) — that log line fires whenever a
default model is missing, REGARDLESS of whether the caller then turns it
into an HTTP failure. It is not, by itself, evidence of a 500.

Investigation (grep candidates named in the report: notebooks.py:355,
search.py:727) found no automatically-triggered (i.e. non-user-initiated)
route in either file that lets a `ConfigurationError` from missing-model
provisioning reach the client as an uncaught 500 today:

* `GET /notebooks/{id}/suggested-questions` — the ONLY "transformation"
  provisioning call that fires automatically on notebook view (via
  ChatColumn.tsx's `useQuery(['suggested-questions', ...])`) — already
  degrades to `200 {"questions": []}` on ANY failure. This is deliberate
  (v0.8.74, see the docstring/comment above `get_suggested_questions` in
  api/routers/notebooks.py) and already covered by
  tests/test_suggested_questions.py::test_degrades_to_empty_on_llm_error.
  `test_suggested_questions_endpoint_degrades_on_configuration_error`
  below re-proves it with the exact exception class/message the live log
  showed, as a regression guard against this file's future changes.

* `POST /notebooks/{id}/synthesis` (notebooks.py:466) DOES turn a missing
  model into a 500 — but it is explicitly user-triggered (the "Synthesis"
  button), so per the task's own instruction ("a user clicking Synthesis
  should still see the actionable error") this was intentionally left
  unchanged.
  `test_synthesis_endpoint_still_surfaces_500_when_user_triggered` below
  locks in that this route is unaffected by this pass.

* `search.py:727` is inside `deep_research_endpoint` (`POST
  /search/deep-research`), itself explicitly user-triggered. Its default
  strategy/synthesis model resolution is already wrapped in a
  try/except that only logs a warning and continues (never raises) —
  confirmed by `test_deep_research_default_model_resolution_does_not_raise`
  below. No other route in search.py fires automatically on notebook view
  (`/search`, `/search/ask`, `/search/ask/simple`, `/search/deep-research`
  are all POST endpoints requiring explicit user action).

**Conclusion: no code change was made to notebooks.py, search.py, or
provision.py for this item** — the two grep candidates are already
correctly handled (one gracefully degrades by design, the other is a
user-triggered action whose default-model resolution is already
non-fatal). See the accompanying report for what to check next if the
live 500s persist (most likely a route outside this task's file scope,
e.g. api/routers/sources.py, which has no "transformation" provisioning
call today — or the browser Network tab should be checked directly to
name the exact failing request URL).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deeper_notebook.exceptions import ConfigurationError


class _FakeNotebook:
    name = "ML Reading List"
    description = "Papers on deep learning"

    def __init__(self, sources):
        self._sources = sources

    async def get_sources(self, include_full_text: bool = False):
        return self._sources


class _FakeSource:
    def __init__(self, title, topics=None):
        self.title = title
        self.topics = topics or []


def _configuration_error():
    """The exact failure class/message provision_langchain_model raises
    for 'no default model configured' (deeper_notebook/ai/provision.py)."""
    return ConfigurationError(
        "No model configured for default for type=transformation. Please "
        "go to Settings → Models and configure a default model for "
        "'transformation'."
    )


@pytest.mark.asyncio
async def test_suggested_questions_endpoint_degrades_on_configuration_error():
    """GET /notebooks/{id}/suggested-questions: auto-fired on notebook
    view, must return 200 with an empty list, never a 500."""
    from api.routers.notebooks import router
    from deeper_notebook.domain.notebook import Notebook

    nb = _FakeNotebook([_FakeSource("Deep Learning", ["ml"])])

    app = FastAPI()
    app.include_router(router)

    with (
        patch.object(Notebook, "get", new=AsyncMock(return_value=nb)),
        patch(
            "deeper_notebook.ai.provision.provision_langchain_model",
            new=AsyncMock(side_effect=_configuration_error()),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/notebooks/notebook:x/suggested-questions")

    assert resp.status_code == 200
    assert resp.json() == {"questions": []}


@pytest.mark.asyncio
async def test_synthesis_endpoint_still_surfaces_500_when_user_triggered():
    """POST /notebooks/{id}/synthesis is an explicit user action (the
    Synthesis button) — it must keep surfacing an actionable error, not
    silently degrade."""
    from api.routers.notebooks import router
    from deeper_notebook.domain.notebook import Notebook

    nb = _FakeNotebook(
        [SimpleNamespace(id="source:1", title="S1", topics=[], full_text="text")]
    )

    app = FastAPI()
    app.include_router(router)

    with (
        patch.object(Notebook, "get", new=AsyncMock(return_value=nb)),
        patch(
            "deeper_notebook.ai.provision.provision_langchain_model",
            new=AsyncMock(side_effect=_configuration_error()),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/notebooks/notebook:x/synthesis")

    assert resp.status_code == 500
    assert "Failed to generate executive synthesis" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deep_research_default_model_resolution_does_not_raise():
    """search.py:727 — POST /search/deep-research (itself an explicit
    user action) must not 500 merely because no default strategy/synthesis
    model is configured: resolution failures are logged and the ids stay
    unresolved (None), and the endpoint proceeds to run_deep_research."""
    from api.routers.search import router

    app = FastAPI()
    app.include_router(router)

    captured_kwargs = {}

    async def fake_run_deep_research(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "plan": None,
            "evidence": [],
            "research_brief": "brief",
            "citations": [],
            "agent_state": "complete",
        }

    with (
        patch(
            "api.routers.search.model_manager.get_default_model",
            new=AsyncMock(side_effect=_configuration_error()),
        ),
        patch(
            "deeper_notebook.graphs.deep_research.run_deep_research",
            new=fake_run_deep_research,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/search/deep-research",
                json={"objective": "What changed?", "max_queries": 2},
            )

    assert resp.status_code == 200, resp.text
    assert captured_kwargs["strategy_model"] is None
    assert captured_kwargs["synthesis_model"] is None
