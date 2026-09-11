"""Tests for the sources API endpoint."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deeper_notebook.config import UPLOADS_FOLDER
from deeper_notebook.domain.notebook import Asset, Source
from deeper_notebook.exceptions import NotFoundError


@pytest.fixture
def client():
    """Create test client after environment variables have been cleared by conftest."""
    from api.main import app

    return TestClient(app)


class TestAsyncSourceAssetPersistence:
    """Tests for #627 - asset is persisted before async processing.

    These tests hit the real create_source endpoint with mocked DB/command
    calls, verifying that the Source saved to the database has the correct
    asset set *before* async processing begins.
    """

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_async_link_source_persists_url_asset(
        self, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        """POST /sources with type=link and async_processing=true persists Asset(url=...)."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        saved_sources = []

        async def capture_save(self_source):
            saved_sources.append(self_source)
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "link",
                    "url": "https://example.com/article",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "true",
                },
            )

        assert response.status_code == 200
        response_body = response.json()
        assert response_body["asset"] == {
            "file_path": None,
            "url": "https://example.com/article",
        }
        assert len(saved_sources) >= 1

        source = saved_sources[0]
        assert source.asset is not None
        assert source.asset.url == "https://example.com/article"
        assert source.asset.file_path is None

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_async_source_persists_labels_and_provenance(
        self, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        saved_sources = []

        async def capture_save(self_source):
            saved_sources.append(self_source)
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "link",
                    "url": "https://academy.example.com/lesson",
                    "notebooks": '["notebook:1", "notebook:2"]',
                    "topics": '["training", "policy", "training"]',
                    "provenance": '{"origin": "training_builder"}',
                    "async_processing": "true",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["topics"] == ["training", "policy"]
        assert body["provenance"]["origin"] == "training_builder"
        assert body["provenance"]["domain"] == "academy.example.com"
        assert body["notebook_count"] == 2
        assert body["is_shared"] is True

        source = saved_sources[0]
        assert source.topics == ["training", "policy"]
        assert source.provenance["origin"] == "training_builder"
        assert source.source_type == "link"

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_async_legacy_notebook_id_links_and_queues_source(
        self, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        """Legacy notebook_id form field must not create orphaned sources."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "link",
                    "url": "https://example.com/article",
                    "notebook_id": "notebook:legacy",
                    "async_processing": "true",
                },
            )

        assert response.status_code == 200
        mock_nb_get.assert_awaited_once_with("notebook:legacy")
        mock_add_nb.assert_awaited_once_with("notebook:legacy")

        command_payload = mock_submit.await_args.args[2]
        assert command_payload["notebook_ids"] == ["notebook:legacy"]

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_async_upload_accepts_frontend_notebook_id_and_notebooks_pair(
        self, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        """Quick uploads send both fields; backend must merge and dedupe them."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with (
            patch.object(Source, "save", autospec=True, side_effect=capture_save),
            patch(
                "api.routers.sources.save_uploaded_file", new_callable=AsyncMock
            ) as mock_upload,
        ):
            mock_upload.return_value = os.path.join(
                os.path.abspath(UPLOADS_FOLDER),
                "quick-upload.pdf",
            )
            response = client.post(
                "/api/sources",
                data={
                    "type": "upload",
                    "notebook_id": "notebook:quick",
                    "notebooks": '["notebook:quick"]',
                    "async_processing": "true",
                },
                files={"file": ("quick-upload.pdf", b"fake pdf", "application/pdf")},
            )

        assert response.status_code == 200
        mock_nb_get.assert_awaited_once_with("notebook:quick")
        mock_add_nb.assert_awaited_once_with("notebook:quick")
        command_payload = mock_submit.await_args.args[2]
        assert command_payload["notebook_ids"] == ["notebook:quick"]

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    @patch("api.routers.sources.save_uploaded_file", new_callable=AsyncMock)
    async def test_async_upload_source_persists_file_asset(
        self, mock_upload, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        """POST /sources with type=upload and async_processing=true persists Asset(file_path=...)."""
        mock_nb_get.return_value = MagicMock()
        mock_upload.return_value = os.path.join(
            os.path.abspath(UPLOADS_FOLDER), "video.mp4"
        )
        mock_submit.return_value = "command:123"

        saved_sources = []

        async def capture_save(self_source):
            saved_sources.append(self_source)
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "upload",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "true",
                },
                files={"file": ("video.mp4", b"fake content", "video/mp4")},
            )

        assert response.status_code == 200
        response_body = response.json()
        assert response_body["asset"] == {
            "file_path": os.path.join(os.path.abspath(UPLOADS_FOLDER), "video.mp4"),
            "url": None,
        }
        assert len(saved_sources) >= 1

        source = saved_sources[0]
        assert source.asset is not None
        assert source.asset.file_path == os.path.join(
            os.path.abspath(UPLOADS_FOLDER), "video.mp4"
        )
        assert source.asset.url is None

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_async_text_source_has_no_asset(
        self, mock_nb_get, mock_add_nb, mock_submit, client
    ):
        """POST /sources with type=text and async_processing=true has asset=None."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        saved_sources = []

        async def capture_save(self_source):
            saved_sources.append(self_source)
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "text",
                    "content": "Some text content",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "true",
                },
            )

        assert response.status_code == 200
        assert len(saved_sources) >= 1

        source = saved_sources[0]
        assert source.asset is None

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.execute_command_sync")
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_source_create_defaults_to_searchable_queued_processing(
        self, mock_nb_get, mock_add_nb, mock_execute_sync, mock_submit, client
    ):
        """Omitted processing flags should match the UI's searchable queued default."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "link",
                    "url": "https://example.com/defaults",
                    "notebooks": '["notebook:1"]',
                },
            )

        assert response.status_code == 200
        mock_execute_sync.assert_not_called()
        mock_submit.assert_awaited_once()
        command_payload = mock_submit.await_args.args[2]
        assert command_payload["embed"] is True


class TestSourceListingErrors:
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    def test_get_sources_for_missing_notebook_returns_404(
        self, mock_nb_get, mock_repo_query, client
    ):
        mock_nb_get.side_effect = NotFoundError(
            "notebook with id notebook:gone not found"
        )

        response = client.get("/api/sources", params={"notebook_id": "notebook:gone"})

        assert response.status_code == 404
        assert "Notebook not found" in response.json()["detail"]
        mock_repo_query.assert_not_called()


class TestSourceListingDerivedStatus:
    """v0.8.127 — a source with extracted text and no command row (in-process
    sync path, or predating command tracking) reports status "completed"
    instead of no status; with no text and no command it stays None."""

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_list_derives_completed_from_extracted_text(self, mock_repo_query, client):
        mock_repo_query.return_value = [
            {
                "id": "source:sync",
                "title": "Sync source",
                "topics": [],
                "asset": None,
                "created": "2026-09-11T10:00:00Z",
                "updated": "2026-09-11T10:00:00Z",
                "command": None,
                "extracted_char_count": 116,
                "insights_count": 0,
                "notebook_count": 1,
                "embedded_chunks": 0,
            },
            {
                "id": "source:pending",
                "title": "Never processed",
                "topics": [],
                "asset": None,
                "created": "2026-09-11T10:00:00Z",
                "updated": "2026-09-11T10:00:00Z",
                "command": None,
                "extracted_char_count": 0,
                "insights_count": 0,
                "notebook_count": 1,
                "embedded_chunks": 0,
            },
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()}
        assert by_id["source:sync"]["status"] == "completed"
        assert by_id["source:pending"]["status"] is None

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_list_explicit_failed_status_overrides_extracted_chars(
        self, mock_repo_query, client
    ):
        """v0.8.128 — a failed source with partial extracted text must report
        failed, not completed, because explicit processing_status takes precedence."""
        mock_repo_query.return_value = [
            {
                "id": "source:failed_partial",
                "title": "Partial failed source",
                "topics": [],
                "provenance": {
                    "processing_status": "failed",
                    "processing_error": "boom",
                },
                "asset": None,
                "created": "2026-09-11T10:00:00Z",
                "updated": "2026-09-11T10:00:00Z",
                "command": None,
                "extracted_char_count": 150,
                "insights_count": 0,
                "notebook_count": 1,
                "embedded_chunks": 0,
            },
            {
                "id": "source:explicit_completed",
                "title": "Explicit completed source",
                "topics": [],
                "provenance": {"processing_status": "completed"},
                "asset": None,
                "created": "2026-09-11T10:00:00Z",
                "updated": "2026-09-11T10:00:00Z",
                "command": None,
                "extracted_char_count": 200,
                "insights_count": 0,
                "notebook_count": 1,
                "embedded_chunks": 0,
            },
        ]

        response = client.get("/api/sources")
        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()}
        assert by_id["source:failed_partial"]["status"] == "failed"
        assert by_id["source:explicit_completed"]["status"] == "completed"


class TestSourceExplicitProcessingStatusDetailAndStatus:
    """v0.8.128 — Tests for explicit processing_status in GET /sources/{id} and
    GET /sources/{id}/status endpoints."""

    @pytest.mark.asyncio
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_get_source_detail_prefers_explicit_processing_status(
        self, mock_source_get, mock_repo_query, client
    ):
        mock_repo_query.return_value = []
        source = MagicMock()
        source.id = "source:failed1"
        source.title = "Failed Source"
        source.topics = []
        source.provenance = {"processing_status": "failed"}
        source.source_type = "text"
        source.asset = None
        source.full_text = "Some partial text that was extracted before failure"
        source.created = "2026-09-11T10:00:00Z"
        source.updated = "2026-09-11T10:00:00Z"
        source.command = None
        source.get_embedded_chunks = AsyncMock(return_value=0)
        mock_source_get.return_value = source

        response = client.get("/api/sources/source:failed1")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_get_source_status_with_explicit_processing_status(
        self, mock_source_get, client
    ):
        source_completed = MagicMock()
        source_completed.command = None
        source_completed.provenance = {"processing_status": "completed"}

        mock_source_get.return_value = source_completed
        res_comp = client.get("/api/sources/source:comp/status")
        assert res_comp.status_code == 200
        assert res_comp.json()["status"] == "completed"
        assert res_comp.json()["message"] == "Source processing completed successfully"

        source_failed = MagicMock()
        source_failed.command = None
        source_failed.provenance = {"processing_status": "failed"}

        mock_source_get.return_value = source_failed
        res_fail = client.get("/api/sources/source:fail/status")
        assert res_fail.status_code == 200
        assert res_fail.json()["status"] == "failed"
        assert res_fail.json()["message"] == "Source processing failed"

        source_legacy = MagicMock()
        source_legacy.command = None
        source_legacy.provenance = {}

        mock_source_get.return_value = source_legacy
        res_leg = client.get("/api/sources/source:leg/status")
        assert res_leg.status_code == 200
        assert res_leg.json()["status"] is None
        assert "Legacy source" in res_leg.json()["message"]


class TestSourceListingNullSafety:
    """v0.8.125 — found live: a source whose processing never ran (worker
    down, command reaped) has full_text = NONE, and string::len(NONE) makes
    SurrealDB reject the whole list query. Both list queries must coalesce."""

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_global_list_query_coalesces_full_text(self, mock_repo_query, client):
        mock_repo_query.return_value = []
        response = client.get("/api/sources")
        assert response.status_code == 200
        query = mock_repo_query.call_args.args[0]
        assert 'string::len(full_text ?? "")' in query
        assert "string::len(full_text)" not in query

    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_notebook_list_query_coalesces_full_text(
        self, mock_repo_query, mock_nb_get, client
    ):
        mock_nb_get.return_value = object()
        mock_repo_query.return_value = []
        response = client.get("/api/sources", params={"notebook_id": "notebook:n1"})
        assert response.status_code == 200
        query = mock_repo_query.call_args.args[0]
        assert 'string::len(full_text ?? "")' in query
        assert "string::len(full_text)" not in query


class TestSourceListingProcessingInfo:
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_sources_includes_fetched_command_progress_and_result(
        self, mock_repo_query, client
    ):
        mock_repo_query.return_value = [
            {
                "id": "source:running",
                "title": "Running import",
                "topics": [],
                "asset": None,
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": False,
                "insights_count": 0,
                "command": {
                    "id": "command:running",
                    "status": "running",
                    "progress": {"processed": 1, "total": 4, "percentage": 25},
                    "error_message": None,
                    "result": {
                        "execution_metadata": {
                            "started_at": "2026-06-23T10:00:30Z",
                            "completed_at": None,
                        },
                        "source_id": "source:running",
                    },
                },
            }
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["status"] == "running"
        assert body[0]["command_id"] == "command:running"
        assert body[0]["processing_info"] == {
            "status": "running",
            "started_at": "2026-06-23T10:00:30Z",
            "completed_at": None,
            "error": None,
            "progress": {"processed": 1, "total": 4, "percentage": 25},
            "result": {
                "execution_metadata": {
                    "started_at": "2026-06-23T10:00:30Z",
                    "completed_at": None,
                },
                "source_id": "source:running",
            },
        }

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_sources_reports_missing_uploaded_file_availability(
        self, mock_repo_query, client, monkeypatch, tmp_path
    ):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(uploads))

        mock_repo_query.return_value = [
            {
                "id": "source:missing-upload",
                "title": "Missing upload",
                "topics": [],
                "asset": {"file_path": str(uploads / "missing.pdf"), "url": None},
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": True,
                "insights_count": 0,
                "command": None,
            }
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["asset"]["file_path"] == str(uploads / "missing.pdf")
        assert body[0]["file_available"] is False

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_sources_reports_extracted_text_metrics(self, mock_repo_query, client):
        mock_repo_query.return_value = [
            {
                "id": "source:thin-extract",
                "title": "Scanned PDF",
                "topics": [],
                "asset": {"file_path": "/uploads/scanned.pdf", "url": None},
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": False,
                "insights_count": 0,
                "command": None,
                "extracted_char_count": 42,
            },
            {
                "id": "source:healthy-extract",
                "title": "Research paper",
                "topics": [],
                "asset": {"file_path": "/uploads/paper.pdf", "url": None},
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": True,
                "insights_count": 0,
                "command": None,
                "extracted_char_count": 4096,
            },
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["extracted_char_count"] == 42
        assert body[0]["extraction_quality"] == "low_text"
        assert body[1]["extracted_char_count"] == 4096
        assert body[1]["extraction_quality"] == "ok"


class TestSourceListingEmbeddingMetrics:
    @patch("api.routers.sources.Source.get_embedded_chunks", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_sources_reports_projected_embedded_chunk_count_without_n_plus_one(
        self, mock_repo_query, mock_embedded_chunks, client
    ):
        mock_repo_query.return_value = [
            {
                "id": "source:all-sources",
                "title": "All sources",
                "topics": [],
                "asset": None,
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": False,
                "embedded_chunks": 7,
                "insights_count": 0,
                "command": None,
            }
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["embedded_chunks"] == 7
        assert body[0]["embedded"] is True
        mock_embedded_chunks.assert_not_called()

    @patch("api.routers.sources.Source.get_embedded_chunks", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_notebook_sources_reports_projected_embedded_chunk_count_without_n_plus_one(
        self, mock_repo_query, mock_nb_get, mock_embedded_chunks, client
    ):
        mock_nb_get.return_value = MagicMock()
        mock_repo_query.return_value = [
            {
                "id": "source:notebook-filter",
                "title": "Notebook source",
                "topics": [],
                "asset": None,
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": False,
                "embedded_chunks": 4,
                "insights_count": 0,
                "notebook_count": 1,
                "command": None,
            }
        ]

        response = client.get("/api/sources", params={"notebook_id": "notebook:1"})

        assert response.status_code == 200
        body = response.json()
        assert body[0]["embedded_chunks"] == 4
        assert body[0]["embedded"] is True
        mock_embedded_chunks.assert_not_called()

    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_sources_treats_missing_embedded_chunk_count_as_zero(
        self, mock_repo_query, client
    ):
        mock_repo_query.return_value = [
            {
                "id": "source:no-embeddings",
                "title": "Not embedded",
                "topics": [],
                "asset": None,
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": True,
                "embedded_chunks": None,
                "insights_count": 0,
                "command": None,
            },
            {
                "id": "source:missing-count",
                "title": "Missing count",
                "topics": [],
                "asset": None,
                "created": "2026-06-23T10:00:00Z",
                "updated": "2026-06-23T10:01:00Z",
                "embedded": True,
                "insights_count": 0,
                "command": None,
            },
        ]

        response = client.get("/api/sources")

        assert response.status_code == 200
        body = response.json()
        assert [row["embedded_chunks"] for row in body] == [0, 0]
        assert [row["embedded"] for row in body] == [False, False]


class TestSourceDetailExtractionMetrics:
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get_embedded_chunks", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    def test_get_source_reports_extracted_text_metrics(
        self, mock_repo_query, mock_embedded_chunks, mock_get, client
    ):
        mock_get.return_value = Source(
            id="source:detail",
            title="Scanned PDF",
            topics=[],
            asset=None,
            full_text="Short OCR text.",
        )
        mock_embedded_chunks.return_value = 0
        mock_repo_query.side_effect = [
            ["notebook:1"],
            [0],
        ]

        response = client.get("/api/sources/source:detail")

        assert response.status_code == 200
        body = response.json()
        assert body["full_text"] == "Short OCR text."
        assert body["extracted_char_count"] == len("Short OCR text.")
        assert body["extraction_quality"] == "low_text"


class TestSourceCreationErrors:
    def test_create_source_rejects_non_array_notebooks_form_field(self, client):
        response = client.post(
            "/api/sources",
            data={
                "type": "link",
                "url": "https://example.com/article",
                "notebooks": '{"id": "notebook:bad"}',
                "async_processing": "true",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "notebooks must be a JSON array of strings"

    def test_create_source_rejects_non_array_transformations_form_field(self, client):
        response = client.post(
            "/api/sources",
            data={
                "type": "link",
                "url": "https://example.com/article",
                "transformations": '"transformation:bad"',
                "async_processing": "true",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "transformations must be a JSON array of strings"
        )

    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    def test_create_source_for_missing_notebook_returns_404(self, mock_nb_get, client):
        mock_nb_get.side_effect = NotFoundError(
            "notebook with id notebook:gone not found"
        )

        response = client.post(
            "/api/sources",
            data={
                "type": "link",
                "url": "https://example.com/article",
                "notebooks": '["notebook:gone"]',
                "async_processing": "true",
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Notebook notebook:gone not found"

    @patch("api.routers.sources.Source.save", new_callable=AsyncMock)
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    def test_oversized_upload_returns_413_and_leaves_no_partial_file(
        self, mock_nb_get, mock_submit, mock_save, client, monkeypatch, tmp_path
    ):
        mock_nb_get.return_value = MagicMock()
        monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(tmp_path))
        monkeypatch.setattr("api.routers.sources._source_upload_max_bytes", lambda: 3)

        response = client.post(
            "/api/sources",
            data={
                "type": "upload",
                "notebooks": '["notebook:1"]',
                "async_processing": "true",
            },
            files={"file": ("too-large.txt", b"abcdef", "text/plain")},
        )

        assert response.status_code == 413
        assert "Upload exceeds size limit" in response.json()["detail"]
        assert list(tmp_path.iterdir()) == []
        mock_save.assert_not_called()
        mock_submit.assert_not_called()


class TestSourceRetryUploadPreflight:
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    def test_retry_upload_source_rejects_missing_original_file(
        self, mock_get, mock_repo_query, mock_submit, client, monkeypatch, tmp_path
    ):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(uploads))
        missing_file = uploads / "missing.pdf"

        mock_get.return_value = Source(
            id="source:missing-file",
            title="Missing file",
            asset=Asset(file_path=str(missing_file)),
        )
        mock_repo_query.return_value = ["notebook:1"]

        response = client.post("/api/sources/source:missing-file/retry")

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Original uploaded file is not available for retry"
        )
        mock_submit.assert_not_called()

    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    def test_retry_upload_source_rejects_file_outside_uploads_folder(
        self, mock_get, mock_repo_query, mock_submit, client, monkeypatch, tmp_path
    ):
        uploads = tmp_path / "uploads"
        outside = tmp_path / "outside"
        uploads.mkdir()
        outside.mkdir()
        outside_file = outside / "private.pdf"
        outside_file.write_bytes(b"private")
        monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(uploads))

        mock_get.return_value = Source(
            id="source:outside-file",
            title="Outside file",
            asset=Asset(file_path=str(outside_file)),
        )
        mock_repo_query.return_value = ["notebook:1"]

        response = client.post("/api/sources/source:outside-file/retry")

        assert response.status_code == 403
        assert response.json()["detail"] == "Access to source file denied"
        mock_submit.assert_not_called()


class TestSourceRetryResponseMetrics:
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.Source.save", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get_embedded_chunks", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    def test_retry_response_marks_extraction_quality_pending(
        self,
        mock_get,
        mock_repo_query,
        mock_embedded_chunks,
        mock_save,
        mock_submit,
        client,
    ):
        mock_get.return_value = Source(
            id="source:thin-extract",
            title="Scanned PDF",
            topics=[],
            asset=Asset(url="https://example.com/scanned"),
            full_text="",
        )
        mock_repo_query.return_value = ["notebook:1"]
        mock_embedded_chunks.return_value = 0
        mock_submit.return_value = "command:retry"

        response = client.post("/api/sources/source:thin-extract/retry")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["command_id"] == "command:retry"
        assert body["extracted_char_count"] == 0
        assert body["extraction_quality"] == "pending"
        mock_save.assert_awaited_once()


class TestRetrySourceProcessing:
    """POST /sources/{id}/retry must find a source's notebooks via the reference
    edge's in/out columns, not a non-existent `source` column (#861)."""

    @pytest.mark.asyncio
    @patch(
        "api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock
    )
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_retry_finds_notebooks_and_requeues(
        self, mock_get, mock_repo_query, mock_submit, client
    ):
        source = MagicMock()
        source.id = "source:1"
        source.command = None
        source.title = "My source"
        source.topics = []
        source.full_text = None
        source.asset = MagicMock(file_path=None, url="https://example.com/post")
        source.save = AsyncMock()
        source.get_embedded_chunks = AsyncMock(return_value=0)
        mock_get.return_value = source

        # The corrected query returns the linked notebook(s)
        mock_repo_query.return_value = ["notebook:1"]
        # submit_command_job returns str(RecordID), which already includes the
        # "command:" table prefix.
        mock_submit.return_value = "command:123"

        response = client.post("/api/sources/source:1/retry")

        assert response.status_code == 200
        # Regression guard: must query the reference edge by its `in` column
        called_query = mock_repo_query.await_args.args[0]
        assert "WHERE in = $source_id" in called_query
        assert "SELECT VALUE out FROM reference" in called_query
        # Regression guard: command_id must not be double-prefixed
        # (`command:command:…`), which previously raised a 500 on save.
        assert "command:command" not in str(source.command)
        assert str(source.command).count("command:") == 1
        assert str(source.command).startswith("command:")

    @pytest.mark.asyncio
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_retry_400_only_when_truly_unlinked(
        self, mock_get, mock_repo_query, client
    ):
        source = MagicMock()
        source.id = "source:1"
        source.command = None
        mock_get.return_value = source
        mock_repo_query.return_value = []  # genuinely no notebooks

        response = client.post("/api/sources/source:1/retry")

        assert response.status_code == 400
        assert "not associated with any notebooks" in response.json()["detail"]


class TestGetSourceNotFound:
    """GET /sources/{id} must return 404 (not 500) for a missing/deleted source.
    `Source.get()` raises NotFoundError rather than returning None, so the handler
    must map it to 404 instead of catching it in its generic `except`."""

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_get_missing_source_returns_404(self, mock_get, client):
        from deeper_notebook.exceptions import NotFoundError

        mock_get.side_effect = NotFoundError("source with id source:gone not found")

        response = client.get("/api/sources/source:gone")

        assert response.status_code == 404


class TestSyncSourceProcessing:
    """v0.8.126 — POST /sources with async_processing=false must process
    in-process (await process_source_command(...) directly) instead of
    queueing through execute_command_sync(), which hangs for the full
    timeout with no surreal-commands-worker running (#A)."""

    @pytest.mark.asyncio
    @patch("api.routers.sources.execute_command_sync")
    @patch("api.routers.sources.process_source_command", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_sync_create_succeeds_without_calling_execute_command_sync(
        self,
        mock_nb_get,
        mock_add_nb,
        mock_source_get,
        mock_process_command,
        mock_execute_sync,
        client,
    ):
        from commands.source_commands import SourceProcessingOutput

        mock_nb_get.return_value = MagicMock()
        # execute_command_sync must never be called on the sync path anymore.
        mock_execute_sync.side_effect = AssertionError(
            "execute_command_sync should not be called by the sync path"
        )

        mock_process_command.return_value = SourceProcessingOutput(
            success=True,
            source_id="source:fake",
            insights_created=0,
            processing_time=0.01,
        )

        processed_source = MagicMock()
        processed_source.id = "source:fake"
        processed_source.title = "Processing..."
        processed_source.topics = []
        processed_source.provenance = {}
        processed_source.source_type = "text"
        processed_source.asset = None
        processed_source.full_text = "hello world"
        processed_source.created = "2026-01-01T00:00:00Z"
        processed_source.updated = "2026-01-01T00:00:00Z"
        processed_source.get_embedded_chunks = AsyncMock(return_value=0)
        mock_source_get.return_value = processed_source

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "text",
                    "content": "hello world",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "false",
                },
            )

        assert response.status_code == 200
        mock_execute_sync.assert_not_called()
        mock_process_command.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("api.routers.sources.execute_command_sync")
    @patch("api.routers.sources.process_source_command", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_sync_create_failure_deletes_source_and_returns_failure_status(
        self,
        mock_nb_get,
        mock_add_nb,
        mock_process_command,
        mock_execute_sync,
        client,
    ):
        mock_nb_get.return_value = MagicMock()
        mock_process_command.side_effect = RuntimeError("boom: transient failure")

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with (
            patch.object(Source, "save", autospec=True, side_effect=capture_save),
            patch.object(Source, "delete", autospec=True) as mock_delete,
        ):
            response = client.post(
                "/api/sources",
                data={
                    "type": "text",
                    "content": "hello world",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "false",
                },
            )

            assert response.status_code == 500
            assert response.json()["detail"] == "Source processing failed"
            mock_delete.assert_awaited_once()
        mock_execute_sync.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.routers.sources.execute_command_sync")
    @patch("api.routers.sources.process_source_command", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_sync_create_timeout_maps_to_failure_path(
        self,
        mock_nb_get,
        mock_add_nb,
        mock_process_command,
        mock_execute_sync,
        client,
        monkeypatch,
    ):
        # Tiny timeout so the test doesn't actually wait 300s.
        monkeypatch.setenv("DEEPER_NOTEBOOK_SOURCE_SYNC_TIMEOUT_SEC", "0.05")

        mock_nb_get.return_value = MagicMock()

        async def never_returns(_input):
            await asyncio.sleep(10)

        mock_process_command.side_effect = never_returns

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with (
            patch.object(Source, "save", autospec=True, side_effect=capture_save),
            patch.object(Source, "delete", autospec=True) as mock_delete,
        ):
            response = client.post(
                "/api/sources",
                data={
                    "type": "text",
                    "content": "hello world",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "false",
                },
            )

            assert response.status_code == 500
            assert response.json()["detail"] == "Source processing failed"
            mock_delete.assert_awaited_once()
        mock_execute_sync.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
