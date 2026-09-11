"""v0.8.126 — notebook-scoped podcast episodes.

`PodcastEpisode` gained a `notebook_id` field so a notebook's episodes can
be listed (for `GET /podcasts/episodes?notebook_id=`) and bundled into the
Studio export (`api/routers/studio/exports.py::_load_notebook_episodes`).

These tests are hermetic — no SurrealDB, no surreal-commands worker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from deeper_notebook.podcasts.models import PodcastEpisode

_REQUIRED_FIELDS = dict(
    name="Episode",
    episode_profile={"name": "default-pod"},
    speaker_profile={"name": "default-speaker"},
    briefing="A briefing",
    content="Some content",
)


class TestPodcastEpisodeNotebookIdField:
    """The model accepts and round-trips notebook_id."""

    def test_defaults_to_none(self):
        episode = PodcastEpisode(**_REQUIRED_FIELDS)
        assert episode.notebook_id is None

    def test_accepts_and_round_trips_notebook_id(self):
        episode = PodcastEpisode(notebook_id="notebook:abc123", **_REQUIRED_FIELDS)
        assert episode.notebook_id == "notebook:abc123"

        dumped = episode.model_dump()
        assert dumped["notebook_id"] == "notebook:abc123"

        reloaded = PodcastEpisode(**dumped)
        assert reloaded.notebook_id == "notebook:abc123"

    def test_prepare_save_data_includes_notebook_id_when_set(self):
        episode = PodcastEpisode(notebook_id="notebook:abc123", **_REQUIRED_FIELDS)
        data = episode._prepare_save_data()
        assert data["notebook_id"] == "notebook:abc123"

    def test_prepare_save_data_omits_notebook_id_when_unset(self):
        # v0.8.68 — ObjectModel._prepare_save_data drops None values unless
        # the field is in nullable_fields; notebook_id is only ever set,
        # never explicitly cleared, so it's intentionally NOT in
        # nullable_fields (matches `content`, `briefing`, etc.).
        episode = PodcastEpisode(**_REQUIRED_FIELDS)
        data = episode._prepare_save_data()
        assert "notebook_id" not in data


class TestGetForNotebook:
    """get_for_notebook issues a query bound to the notebook id."""

    @pytest.mark.asyncio
    async def test_queries_episode_table_bound_to_notebook_id(self):
        fake_row = {
            **_REQUIRED_FIELDS,
            "id": "episode:1",
            "notebook_id": "notebook:xyz",
        }
        with patch(
            "deeper_notebook.podcasts.models.repo_query",
            new=AsyncMock(return_value=[fake_row]),
        ) as mock_query:
            episodes = await PodcastEpisode.get_for_notebook("notebook:xyz")

        assert mock_query.await_count == 1
        query_str, params = mock_query.await_args.args
        assert "episode" in query_str
        assert "notebook_id" in query_str
        assert params == {"notebook_id": "notebook:xyz"}

        assert len(episodes) == 1
        assert isinstance(episodes[0], PodcastEpisode)
        assert episodes[0].notebook_id == "notebook:xyz"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self):
        with patch(
            "deeper_notebook.podcasts.models.repo_query",
            new=AsyncMock(return_value=[]),
        ):
            episodes = await PodcastEpisode.get_for_notebook("notebook:none")
        assert episodes == []


class _FakeEpisode:
    """Minimal PodcastEpisode-shaped stand-in for the route test — mirrors
    the pattern in tests/test_v0_7_130.py::_FakeEpisode."""

    def __init__(self, idx: int):
        self.id = f"episode:fake{idx}"
        self.name = f"Episode {idx}"
        self.episode_profile = {"name": "default-pod"}
        self.speaker_profile = {"name": "default-speaker"}
        self.briefing = ""
        self.audio_file = None
        self.transcript = None
        self.outline = None
        self.created = "2026-05-19T03:00:00Z"
        # Must have a command or audio_file, or the route's "in-flight
        # failure" filter drops the episode entirely.
        self.command = f"command:fake{idx}"
        self.notebook_id = "notebook:scoped"
        self.selection_summary = None
        self.selection_fingerprint = None
        self.editorial_brief = None
        self.model_plan_receipts = []

    async def get_job_detail(self):
        return {"status": "completed", "error_message": None}


class TestEpisodesRouteNotebookFilter:
    """GET /podcasts/episodes filters when notebook_id is given, and is
    unchanged when it's omitted."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.routers.podcasts import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_notebook_id_query_param_is_forwarded_to_service(self):
        client = self._client()
        with patch(
            "api.routers.podcasts.PodcastService.list_episodes",
            new=AsyncMock(return_value=[_FakeEpisode(1)]),
        ) as mock_list:
            resp = client.get("/podcasts/episodes?notebook_id=notebook:scoped")

        assert resp.status_code == 200
        mock_list.assert_awaited_once_with(notebook_id="notebook:scoped")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == "episode:fake1"

    def test_unfiltered_listing_unchanged_when_notebook_id_omitted(self):
        client = self._client()
        with patch(
            "api.routers.podcasts.PodcastService.list_episodes",
            new=AsyncMock(return_value=[_FakeEpisode(1), _FakeEpisode(2)]),
        ) as mock_list:
            resp = client.get("/podcasts/episodes")

        assert resp.status_code == 200
        mock_list.assert_awaited_once_with(notebook_id=None)
        assert len(resp.json()) == 2


class TestServiceListEpisodesNotebookScope:
    """api.podcast_service.PodcastService.list_episodes(notebook_id=...)."""

    @pytest.mark.asyncio
    async def test_delegates_to_get_for_notebook_when_given(self):
        from api.podcast_service import PodcastService

        with patch(
            "api.podcast_service.PodcastEpisode.get_for_notebook",
            new=AsyncMock(return_value=["sentinel"]),
        ) as mock_get_for_notebook:
            result = await PodcastService.list_episodes(notebook_id="notebook:abc")

        mock_get_for_notebook.assert_awaited_once_with("notebook:abc")
        assert result == ["sentinel"]

    @pytest.mark.asyncio
    async def test_falls_back_to_get_all_when_omitted(self):
        from api.podcast_service import PodcastService

        with (
            patch(
                "api.podcast_service.PodcastEpisode.get_for_notebook",
                new=AsyncMock(),
            ) as mock_get_for_notebook,
            patch(
                "api.podcast_service.PodcastEpisode.get_all",
                new=AsyncMock(return_value=["all-episodes"]),
            ) as mock_get_all,
        ):
            result = await PodcastService.list_episodes()

        mock_get_for_notebook.assert_not_awaited()
        mock_get_all.assert_awaited_once_with(order_by="created desc")
        assert result == ["all-episodes"]
