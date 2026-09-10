"""v0.8.119 — Tests for POST /studio/artifacts/{artifact_id}/exports/{format}.

Mirrors the fake-artifact TestClient pattern used by
tests/test_evidence_studio_artifact_api.py: the artifact loader
(`StudioArtifact`) and `persistence.persist_single_export` are monkeypatched
so the endpoint's own logic (404 / 409 / 400 / 200) is exercised without a
real database or real exporters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import studio as studio_mod
from deeper_notebook.studio.generation import persistence


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(studio_mod.router, prefix="/api")
    return TestClient(app)


class _FakeArtifact:
    records: dict[str, "_FakeArtifact"] = {}
    saved_ids: list[str] = []

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        self.created = kwargs.pop("created", None)
        self.updated = kwargs.pop("updated", None)
        self.notebook_id = kwargs.pop("notebook_id")
        self.artifact_type = kwargs.pop("artifact_type")
        self.title = kwargs.pop("title")
        self.status = kwargs.pop("status", "pending")
        self.source_ids = kwargs.pop("source_ids", [])
        self.prompt = kwargs.pop("prompt", None)
        self.model_id = kwargs.pop("model_id", None)
        self.provider = kwargs.pop("provider", None)
        self.output_format = kwargs.pop("output_format", None)
        self.output_payload = kwargs.pop("output_payload", {})
        self.citations = kwargs.pop("citations", [])
        self.export_paths = kwargs.pop("export_paths", {})
        self.revision_of_id = kwargs.pop("revision_of_id", None)

    async def save(self):
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        self.created = self.created or now
        self.updated = now
        self.records[self.id] = self
        self.saved_ids.append(self.id)

    @classmethod
    async def get(cls, artifact_id: str):
        return cls.records[artifact_id]


def _install_fake_artifacts(monkeypatch):
    _FakeArtifact.records = {}
    _FakeArtifact.saved_ids = []
    monkeypatch.setattr(studio_mod, "StudioArtifact", _FakeArtifact)
    return _FakeArtifact


@pytest.fixture(autouse=True)
def _evidence_studio_enabled(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_EVIDENCE_STUDIO", "1")


def test_regenerate_export_returns_updated_export_paths(monkeypatch):
    fake_cls = _install_fake_artifacts(monkeypatch)
    fake_cls.records["studio_artifact:1"] = fake_cls(
        id="studio_artifact:1",
        notebook_id="notebook:alpha",
        artifact_type="course_pack",
        title="Onboarding Course Pack",
        status="completed",
        output_payload={"content": "# Onboarding Course Pack"},
        export_paths={"markdown": "/exports/onboarding.md", "docx": "/exports/onboarding.docx"},
    )
    regenerate = MagicMock(return_value="/exports/onboarding.pdf")
    monkeypatch.setattr(persistence, "persist_single_export", regenerate)

    response = _client().post("/api/studio/artifacts/studio_artifact:1/exports/pdf")

    assert response.status_code == 200
    body = response.json()
    assert body["export_paths"]["pdf"] == "/exports/onboarding.pdf"
    assert body["export_paths"]["docx"] == "/exports/onboarding.docx"
    regenerate.assert_called_once()
    assert regenerate.call_args.args[1] == "pdf"
    assert fake_cls.saved_ids == ["studio_artifact:1"]
    assert fake_cls.records["studio_artifact:1"].export_paths["pdf"] == "/exports/onboarding.pdf"


def test_regenerate_export_returns_400_for_unsupported_format(monkeypatch):
    fake_cls = _install_fake_artifacts(monkeypatch)
    fake_cls.records["studio_artifact:1"] = fake_cls(
        id="studio_artifact:1",
        notebook_id="notebook:alpha",
        artifact_type="report",
        title="Quarterly Report",
        status="completed",
        output_payload={"content": "# Quarterly Report"},
    )
    monkeypatch.setattr(persistence, "persist_single_export", MagicMock(return_value=None))

    response = _client().post("/api/studio/artifacts/studio_artifact:1/exports/epub")

    assert response.status_code == 400


def test_regenerate_export_returns_404_for_missing_artifact(monkeypatch):
    _install_fake_artifacts(monkeypatch)
    regenerate = MagicMock(return_value="/exports/onboarding.pdf")
    monkeypatch.setattr(persistence, "persist_single_export", regenerate)

    response = _client().post("/api/studio/artifacts/studio_artifact:missing/exports/pdf")

    assert response.status_code == 404
    regenerate.assert_not_called()


def test_regenerate_export_returns_409_when_no_generated_content_yet(monkeypatch):
    fake_cls = _install_fake_artifacts(monkeypatch)
    fake_cls.records["studio_artifact:1"] = fake_cls(
        id="studio_artifact:1",
        notebook_id="notebook:alpha",
        artifact_type="course_pack",
        title="Onboarding Course Pack",
        status="pending",
        output_payload={},
    )
    regenerate = MagicMock(return_value="/exports/onboarding.pdf")
    monkeypatch.setattr(persistence, "persist_single_export", regenerate)

    response = _client().post("/api/studio/artifacts/studio_artifact:1/exports/pdf")

    assert response.status_code == 409
    regenerate.assert_not_called()
