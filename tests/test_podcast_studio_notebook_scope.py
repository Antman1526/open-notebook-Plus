"""v0.8.127 — notebook_id threaded through the Podcast Studio submit path.

`POST /podcasts/studio/submit` had no `notebook_id` anywhere in its request
chain (unlike the standard `POST /podcasts/generate` path, which has
persisted it since v0.8.126 — see tests/test_podcast_episode_notebook_scope.py).
Episodes made from the Studio therefore stayed unscoped and never appeared
in a notebook's bundle export.

These tests are hermetic — no SurrealDB, no surreal-commands worker — and
mirror the fixture/monkeypatch style of tests/test_podcast_studio_api.py.
"""

from __future__ import annotations

from time import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deeper_notebook.local_models.contracts import LocalModelRouteCandidate


class _Notebook:
    id = "notebook:research"
    name = "Research notebook"

    async def get_context(self) -> str:
        return "Private app-owned notebook material"


async def _load_notebook(notebook_id: str) -> _Notebook | None:
    assert notebook_id == "notebook:research"
    return _Notebook()


@pytest.fixture()
def app_with_notebook() -> FastAPI:
    from api.routers.podcasts import router

    app = FastAPI()
    app.state.podcast_notebook_loader = _load_notebook
    app.state.local_model_route_candidates = (
        LocalModelRouteCandidate(
            model_id="local-podcast",
            provider="openai_compatible",
            fingerprint="b" * 64,
            modalities=("text",),
            accepted_roles=("podcast_outline", "podcast_script"),
            context_tokens=32_768,
            supports_structured_output=True,
            readiness="ready_verified",
            health_healthy=True,
            accepted_quality=0.9,
            benchmarked_at=time(),
            peak_memory_bytes=1,
            latency_ms=1,
        ),
        LocalModelRouteCandidate(
            model_id="local-voice",
            provider="piper",
            fingerprint="c" * 64,
            modalities=("audio",),
            accepted_roles=("text_to_speech",),
            context_tokens=32_768,
            supports_structured_output=False,
            readiness="ready_verified",
            health_healthy=True,
            accepted_quality=0.9,
            benchmarked_at=time(),
            peak_memory_bytes=1,
            latency_ms=1,
        ),
    )
    app.include_router(router, prefix="/api")
    return app


async def _submit_notebook_selection(
    client: AsyncClient,
    *,
    idempotency_key: str,
    notebook_id: str | None,
    omit_notebook_id: bool = False,
):
    selection = {"kind": "notebook", "notebook_id": "notebook:research"}
    preview = await client.post(
        "/api/podcasts/selection/preview", json={"selections": [selection]}
    )
    assert preview.status_code == 200, preview.text

    payload: dict[str, object] = {
        "selections": [selection],
        "selection_fingerprint": preview.json()["selection_fingerprint"],
        "idempotency_key": idempotency_key,
        "confirmed": True,
        "episode_profile": "Local Episode",
        "speaker_profile": "Local Voice",
        "episode_name": "Research synthesis",
    }
    if not omit_notebook_id:
        payload["notebook_id"] = notebook_id
    return await client.post("/api/podcasts/studio/submit", json=payload)


@pytest.mark.asyncio
async def test_submit_forwards_and_normalizes_a_bare_notebook_id(
    app_with_notebook: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.podcast_service import PodcastService

    calls: list[dict[str, object]] = []

    async def submit_generation_job(**kwargs) -> str:
        calls.append(kwargs)
        return "command:podcast-notebook-scoped"

    monkeypatch.setattr(PodcastService, "submit_generation_job", submit_generation_job)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_notebook), base_url="http://test"
    ) as client:
        response = await _submit_notebook_selection(
            client,
            idempotency_key="podcast-notebook-scope-1",
            notebook_id="research",  # bare id, no `notebook:` prefix
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["notebook_id"] == "notebook:research"


@pytest.mark.asyncio
async def test_submit_passes_an_already_prefixed_notebook_id_through_unchanged(
    app_with_notebook: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.podcast_service import PodcastService

    calls: list[dict[str, object]] = []

    async def submit_generation_job(**kwargs) -> str:
        calls.append(kwargs)
        return "command:podcast-notebook-scoped-2"

    monkeypatch.setattr(PodcastService, "submit_generation_job", submit_generation_job)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_notebook), base_url="http://test"
    ) as client:
        response = await _submit_notebook_selection(
            client,
            idempotency_key="podcast-notebook-scope-2",
            notebook_id="notebook:research",
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["notebook_id"] == "notebook:research"


@pytest.mark.asyncio
async def test_submit_omits_notebook_id_defaults_to_none(
    app_with_notebook: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.podcast_service import PodcastService

    calls: list[dict[str, object]] = []

    async def submit_generation_job(**kwargs) -> str:
        calls.append(kwargs)
        return "command:podcast-unscoped"

    monkeypatch.setattr(PodcastService, "submit_generation_job", submit_generation_job)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_notebook), base_url="http://test"
    ) as client:
        response = await _submit_notebook_selection(
            client,
            idempotency_key="podcast-notebook-scope-3",
            notebook_id=None,
            omit_notebook_id=True,
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["notebook_id"] is None


@pytest.mark.asyncio
async def test_submit_explicit_null_notebook_id_is_none(
    app_with_notebook: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.podcast_service import PodcastService

    calls: list[dict[str, object]] = []

    async def submit_generation_job(**kwargs) -> str:
        calls.append(kwargs)
        return "command:podcast-unscoped-explicit"

    monkeypatch.setattr(PodcastService, "submit_generation_job", submit_generation_job)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_notebook), base_url="http://test"
    ) as client:
        response = await _submit_notebook_selection(
            client,
            idempotency_key="podcast-notebook-scope-4",
            notebook_id=None,
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["notebook_id"] is None


def test_studio_submit_request_schema_defaults_notebook_id_to_none() -> None:
    from api.schemas.podcast_studio import PodcastStudioSubmitRequest

    request = PodcastStudioSubmitRequest(
        selections=[{"kind": "notebook", "notebook_id": "notebook:research"}],
        selection_fingerprint="a" * 64,
        idempotency_key="podcast-schema-default",
        confirmed=True,
        episode_profile="Local Episode",
        speaker_profile="Local Voice",
        episode_name="Research synthesis",
    )
    assert request.notebook_id is None

    with_id = PodcastStudioSubmitRequest(
        selections=[{"kind": "notebook", "notebook_id": "notebook:research"}],
        selection_fingerprint="a" * 64,
        idempotency_key="podcast-schema-with-id",
        confirmed=True,
        episode_profile="Local Episode",
        speaker_profile="Local Voice",
        episode_name="Research synthesis",
        notebook_id="research",
    )
    assert with_id.notebook_id == "research"
