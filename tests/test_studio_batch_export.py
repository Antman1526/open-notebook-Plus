"""v0.8.124 — Tests for POST /studio/notebooks/{notebook_id}/exports/bundle.

Mirrors the fake-artifact TestClient pattern used by
tests/test_studio_export_endpoint.py: `StudioArtifact` (its `get_for_notebook`
classmethod) and the `persistence` regeneration helpers are monkeypatched so
the endpoint's own orchestration (404 / 409 / 403 / 200, warning collection)
is exercised without a real database or real exporters. Export files
referenced by `export_paths` are real temp files so the zip-writing path in
`persistence.write_notebook_artifact_bundle` runs for real.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
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
    by_notebook: dict[str, list[str]] = {}

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

    @classmethod
    async def get_for_notebook(cls, notebook_id: str):
        ids = cls.by_notebook.get(notebook_id, [])
        return [cls.records[i] for i in ids]


def _install_fake_artifacts(monkeypatch):
    _FakeArtifact.records = {}
    _FakeArtifact.saved_ids = []
    _FakeArtifact.by_notebook = {}
    monkeypatch.setattr(studio_mod, "StudioArtifact", _FakeArtifact)
    return _FakeArtifact


def _register(fake_cls, notebook_id: str, artifact: "_FakeArtifact") -> None:
    fake_cls.records[artifact.id] = artifact
    fake_cls.by_notebook.setdefault(notebook_id, []).append(artifact.id)


@pytest.fixture(autouse=True)
def _evidence_studio_enabled(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_EVIDENCE_STUDIO", "1")


def test_bundle_export_happy_path(monkeypatch, tmp_path):
    fake_cls = _install_fake_artifacts(monkeypatch)
    md_path = tmp_path / "report.md"
    md_path.write_text("# Report", encoding="utf-8")
    json_path = tmp_path / "report.json"
    json_path.write_text("{}", encoding="utf-8")

    _register(
        fake_cls,
        "notebook:alpha",
        fake_cls(
            id="studio_artifact:1",
            notebook_id="notebook:alpha",
            artifact_type="report",
            title="Quarterly Report",
            status="completed",
            output_payload={"content": "# Report"},
            export_paths={"markdown": str(md_path), "json": str(json_path)},
        ),
    )
    monkeypatch.setattr(persistence, "get_stale_export_formats", lambda _a: [])

    destination = tmp_path / "out" / "bundle.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)

    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={"destination": str(destination), "regenerate_stale": False},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["destination"] == str(destination)
    assert body["artifact_count"] == 1
    assert body["skipped"] == 0
    assert body["file_count"] == 3  # markdown + json + manifest.json
    assert body["warnings"] == []
    assert destination.is_file()

    with zipfile.ZipFile(destination) as zf:
        names = set(zf.namelist())
        assert "studio_artifact-1/markdown/report.md" in names
        assert "studio_artifact-1/json/report.json" in names
        assert "manifest.json" in names
        manifest = zf.read("manifest.json").decode("utf-8")
        import json as _json

        parsed = _json.loads(manifest)
        assert parsed["notebook_id"] == "notebook:alpha"
        assert parsed["skipped"] == []
        assert len(parsed["artifacts"]) == 1
        assert parsed["artifacts"][0]["id"] == "studio_artifact:1"
        formats = {f["format"]: f["bytes"] for f in parsed["artifacts"][0]["formats"]}
        assert formats["markdown"] == md_path.stat().st_size
        assert formats["json"] == json_path.stat().st_size


def test_bundle_export_skips_non_completed_artifact(monkeypatch, tmp_path):
    fake_cls = _install_fake_artifacts(monkeypatch)
    md_path = tmp_path / "done.md"
    md_path.write_text("# Done", encoding="utf-8")

    _register(
        fake_cls,
        "notebook:alpha",
        fake_cls(
            id="studio_artifact:done",
            notebook_id="notebook:alpha",
            artifact_type="report",
            title="Done Report",
            status="completed",
            output_payload={"content": "# Done"},
            export_paths={"markdown": str(md_path)},
        ),
    )
    _register(
        fake_cls,
        "notebook:alpha",
        fake_cls(
            id="studio_artifact:pending",
            notebook_id="notebook:alpha",
            artifact_type="report",
            title="Pending Report",
            status="pending",
            output_payload={},
            export_paths={},
        ),
    )
    monkeypatch.setattr(persistence, "get_stale_export_formats", lambda _a: [])

    destination = tmp_path / "bundle.zip"
    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={"destination": str(destination), "regenerate_stale": False},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_count"] == 1
    assert body["skipped"] == 1

    with zipfile.ZipFile(destination) as zf:
        names = zf.namelist()
        assert not any(name.startswith("studio_artifact-pending/") for name in names)
        import json as _json

        manifest = _json.loads(zf.read("manifest.json"))
        assert manifest["skipped"] == ["studio_artifact:pending"]


def test_bundle_export_returns_409_when_destination_exists_without_overwrite(
    monkeypatch, tmp_path
):
    fake_cls = _install_fake_artifacts(monkeypatch)
    _register(
        fake_cls,
        "notebook:alpha",
        fake_cls(
            id="studio_artifact:1",
            notebook_id="notebook:alpha",
            artifact_type="report",
            title="Report",
            status="completed",
            output_payload={"content": "# Report"},
            export_paths={},
        ),
    )
    monkeypatch.setattr(persistence, "get_stale_export_formats", lambda _a: [])

    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"existing")

    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={"destination": str(destination), "regenerate_stale": False},
    )

    assert response.status_code == 409
    assert destination.read_bytes() == b"existing"


def test_bundle_export_rejects_destination_outside_allowed_roots(monkeypatch):
    fake_cls = _install_fake_artifacts(monkeypatch)
    _register(
        fake_cls,
        "notebook:alpha",
        fake_cls(
            id="studio_artifact:1",
            notebook_id="notebook:alpha",
            artifact_type="report",
            title="Report",
            status="completed",
            output_payload={"content": "# Report"},
            export_paths={},
        ),
    )
    monkeypatch.setattr(persistence, "get_stale_export_formats", lambda _a: [])

    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={"destination": "/etc/bundle.zip", "regenerate_stale": False},
    )

    assert response.status_code == 403


def test_bundle_export_returns_404_for_empty_notebook(monkeypatch, tmp_path):
    _install_fake_artifacts(monkeypatch)

    response = _client().post(
        "/api/studio/notebooks/notebook:empty/exports/bundle",
        json={"destination": str(tmp_path / "bundle.zip")},
    )

    assert response.status_code == 404


def test_bundle_export_regenerates_stale_and_records_per_format_failure_as_warning(
    monkeypatch, tmp_path
):
    fake_cls = _install_fake_artifacts(monkeypatch)
    md_path = tmp_path / "report.md"
    md_path.write_text("# Report", encoding="utf-8")

    artifact = fake_cls(
        id="studio_artifact:1",
        notebook_id="notebook:alpha",
        artifact_type="report",
        title="Quarterly Report",
        status="completed",
        output_payload={"content": "# Report"},
        export_paths={"markdown": str(md_path), "pdf": str(tmp_path / "report.pdf")},
    )
    _register(fake_cls, "notebook:alpha", artifact)

    monkeypatch.setattr(
        persistence, "get_stale_export_formats", lambda _a: ["markdown", "pdf"]
    )
    monkeypatch.setattr(
        persistence,
        "producible_export_formats",
        lambda _a, include_aliases=True: {"markdown", "pdf"},
    )

    def _fake_persist(art, fmt):
        if fmt == "pdf":
            raise RuntimeError("boom")
        return str(md_path)

    regenerate = MagicMock(side_effect=_fake_persist)
    monkeypatch.setattr(persistence, "persist_single_export", regenerate)

    destination = tmp_path / "bundle.zip"
    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={"destination": str(destination), "regenerate_stale": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert any("pdf" in w and "studio_artifact:1" in w for w in body["warnings"])
    assert regenerate.call_count == 2
    assert fake_cls.saved_ids == ["studio_artifact:1"]
