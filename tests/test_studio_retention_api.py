"""v0.8.125 — Tests for GET /studio/retention/status and
POST /studio/retention/dry-run.

Mirrors the TestClient pattern used by tests/test_studio_batch_export.py:
mount the real Studio router and monkeypatch the single seam that would
otherwise touch a real database (`prune_studio_retention`). The status
endpoint reads only env vars via `get_retention_status`, so it needs no
monkeypatching beyond `monkeypatch.delenv` / `setenv`.

`prune_studio_retention` is monkeypatched on `deeper_notebook.studio.retention`
(the source module), not on `api.routers.studio.retention` (the router's
qualified import of it) — the router module intentionally avoids importing
the bare function name (see its top-of-file comment) so the Studio legacy
facade's per-request `_sync_legacy_patches` can't silently revert a
monkeypatch set on the router module itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.studio import retention as retention_router
from deeper_notebook.studio import retention as retention_service
from deeper_notebook.studio.retention import RetentionReport


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(retention_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _evidence_studio_enabled(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_EVIDENCE_STUDIO", "1")


@pytest.fixture(autouse=True)
def _clear_retention_env(monkeypatch):
    for name in (
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS",
        "DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT",
        "DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS",
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN",
    ):
        monkeypatch.delenv(name, raising=False)


class TestRetentionStatus:
    def test_status_shape_and_disabled_by_default(self):
        client = _client()
        response = client.get("/api/studio/retention/status")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "enabled": False,
            "interval_hours": 0.0,
            "revision_keep_per_artifact": 10,
            "stale_export_max_age_days": 30,
            "dry_run_default": False,
            "last_run_at": None,
            "last_report": None,
        }

    def test_status_enabled_reflects_interval_env(self, monkeypatch):
        monkeypatch.setenv("DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS", "6")
        client = _client()

        body = client.get("/api/studio/retention/status").json()

        assert body["enabled"] is True
        assert body["interval_hours"] == 6.0


class TestRetentionDryRun:
    def _known_report(self) -> RetentionReport:
        return RetentionReport(
            revisions_examined=12,
            revisions_deleted=2,
            exports_examined=5,
            exports_removed=3,
            bytes_reclaimed=4096,
            dry_run=True,
        )

    def test_dry_run_returns_report_with_env_default_knobs(self, monkeypatch):
        fake_prune = AsyncMock(return_value=self._known_report())
        monkeypatch.setattr(retention_service, "prune_studio_retention", fake_prune)

        client = _client()
        response = client.post("/api/studio/retention/dry-run", json={})

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "revisions_examined": 12,
            "revisions_deleted": 2,
            "exports_examined": 5,
            "exports_removed": 3,
            "bytes_reclaimed": 4096,
            "dry_run": True,
            "revision_keep_per_artifact": 10,
            "stale_export_max_age_days": 30,
        }
        fake_prune.assert_awaited_once_with(
            revision_keep_per_artifact=10,
            stale_export_max_age_days=30,
            dry_run=True,
        )

    def test_dry_run_body_overrides_are_forwarded(self, monkeypatch):
        fake_prune = AsyncMock(return_value=self._known_report())
        monkeypatch.setattr(retention_service, "prune_studio_retention", fake_prune)

        client = _client()
        response = client.post(
            "/api/studio/retention/dry-run",
            json={"revision_keep_per_artifact": 3, "stale_export_max_age_days": 7},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["revision_keep_per_artifact"] == 3
        assert body["stale_export_max_age_days"] == 7
        fake_prune.assert_awaited_once_with(
            revision_keep_per_artifact=3,
            stale_export_max_age_days=7,
            dry_run=True,
        )

    def test_dry_run_never_runs_a_real_prune(self, monkeypatch):
        fake_prune = AsyncMock(return_value=self._known_report())
        monkeypatch.setattr(retention_service, "prune_studio_retention", fake_prune)

        client = _client()
        client.post("/api/studio/retention/dry-run", json={})

        assert fake_prune.await_args.kwargs["dry_run"] is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"revision_keep_per_artifact": -1},
            {"stale_export_max_age_days": -1},
        ],
    )
    def test_dry_run_rejects_negative_overrides(self, monkeypatch, payload):
        fake_prune = AsyncMock(return_value=self._known_report())
        monkeypatch.setattr(retention_service, "prune_studio_retention", fake_prune)

        client = _client()
        response = client.post("/api/studio/retention/dry-run", json=payload)

        assert response.status_code == 422
        fake_prune.assert_not_awaited()
