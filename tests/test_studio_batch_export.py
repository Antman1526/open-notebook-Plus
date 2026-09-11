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


class _FakeEpisode:
    """Minimal PodcastEpisode-shaped stand-in for
    `persistence.write_notebook_artifact_bundle`'s `episodes` param."""

    def __init__(self, id, name, audio_file, transcript_segments=None):
        self.id = id
        self.name = name
        self.audio_file = audio_file
        self.transcript_segments = transcript_segments or []


# v0.8.125 — write_notebook_artifact_bundle's podcast-bundling path, tested
# directly against the function (not the route): PodcastEpisode has no
# notebook_id and no notebook relation exists in this codebase, so the
# route itself always passes `episodes=[]` (see
# api/routers/studio/exports.py::_load_notebook_episodes).
def test_write_bundle_includes_podcast_audio_inside_root(monkeypatch, tmp_path):
    audio_root = tmp_path / "podcasts" / "episodes"
    audio_root.mkdir(parents=True)
    monkeypatch.setattr(persistence, "_PODCAST_AUDIO_ROOT", audio_root)

    episode_dir = audio_root / "ep1"
    episode_dir.mkdir()
    audio_path = episode_dir / "audio.mp3"
    audio_path.write_bytes(b"fake-mp3-bytes")

    destination = tmp_path / "bundle.zip"
    report = persistence.write_notebook_artifact_bundle(
        [],
        destination,
        zipfile.ZIP_DEFLATED,
        episodes=[_FakeEpisode("episode:1", "My Episode", str(audio_path))],
    )

    assert report.media_count == 1
    assert report.media_bytes == audio_path.stat().st_size
    assert report.warnings == []

    with zipfile.ZipFile(destination) as zf:
        names = set(zf.namelist())
        assert "podcasts/episode-1/audio.mp3" in names
        manifest = __import__("json").loads(zf.read("manifest.json"))
        assert manifest["podcasts"] == [
            {
                "id": "episode:1",
                "title": "My Episode",
                "files": [{"filename": "audio.mp3", "bytes": len(b"fake-mp3-bytes")}],
                "bytes": len(b"fake-mp3-bytes"),
            }
        ]


def test_write_bundle_includes_transcript_when_present(monkeypatch, tmp_path):
    audio_root = tmp_path / "podcasts" / "episodes"
    audio_root.mkdir(parents=True)
    monkeypatch.setattr(persistence, "_PODCAST_AUDIO_ROOT", audio_root)

    audio_path = audio_root / "ep2" / "audio.mp3"
    audio_path.parent.mkdir()
    audio_path.write_bytes(b"bytes")

    segment = MagicMock()
    segment.model_dump.return_value = {
        "start_seconds": 0,
        "end_seconds": 1,
        "speaker": "Host",
        "text": "Hello",
        "citation_ids": [],
    }

    destination = tmp_path / "bundle.zip"
    report = persistence.write_notebook_artifact_bundle(
        [],
        destination,
        zipfile.ZIP_DEFLATED,
        episodes=[
            _FakeEpisode(
                "episode:2", "Episode Two", str(audio_path), transcript_segments=[segment]
            )
        ],
    )

    assert report.media_count == 1
    with zipfile.ZipFile(destination) as zf:
        names = set(zf.namelist())
        assert "podcasts/episode-2/audio.mp3" in names
        assert "podcasts/episode-2/transcript.json" in names


def test_write_bundle_skips_episode_audio_outside_root(monkeypatch, tmp_path):
    audio_root = tmp_path / "podcasts" / "episodes"
    audio_root.mkdir(parents=True)
    monkeypatch.setattr(persistence, "_PODCAST_AUDIO_ROOT", audio_root)

    outside_path = tmp_path / "outside" / "audio.mp3"
    outside_path.parent.mkdir()
    outside_path.write_bytes(b"bytes")

    destination = tmp_path / "bundle.zip"
    report = persistence.write_notebook_artifact_bundle(
        [],
        destination,
        zipfile.ZIP_DEFLATED,
        episodes=[_FakeEpisode("episode:3", "Escapee", str(outside_path))],
    )

    assert report.media_count == 0
    assert report.media_bytes == 0
    assert any("episode:3" in w for w in report.warnings)
    with zipfile.ZipFile(destination) as zf:
        names = zf.namelist()
        assert not any(name.startswith("podcasts/") for name in names)


def test_write_bundle_include_media_false_skips_video_overview_files(tmp_path):
    """v0.8.125 — the media flag must have a real effect today: with it off,
    a slide deck's video_mp4 / video_captions export files stay out of the
    zip while its document exports are still bundled."""
    import zipfile

    from deeper_notebook.studio.generation import persistence

    docx = tmp_path / "deck.docx"
    docx.write_bytes(b"docx")
    mp4 = tmp_path / "deck.mp4"
    mp4.write_bytes(b"mp4")
    vtt = tmp_path / "deck.vtt"
    vtt.write_bytes(b"vtt")

    class _Artifact:
        id = "studio_artifact:deck1"
        notebook_id = "notebook:n1"
        status = "completed"
        title = "Deck"
        artifact_type = "slide_deck"
        export_paths = {"docx": str(docx), "video_mp4": str(mp4), "video_captions": str(vtt)}

    target_on = tmp_path / "on.zip"
    persistence.write_notebook_artifact_bundle(
        [_Artifact()], target_on, zipfile.ZIP_DEFLATED, include_media=True
    )
    names_on = set(zipfile.ZipFile(target_on).namelist())
    assert "studio_artifact-deck1/video_mp4/deck.mp4" in names_on
    assert "studio_artifact-deck1/video_captions/deck.vtt" in names_on

    target_off = tmp_path / "off.zip"
    persistence.write_notebook_artifact_bundle(
        [_Artifact()], target_off, zipfile.ZIP_DEFLATED, include_media=False
    )
    names_off = set(zipfile.ZipFile(target_off).namelist())
    assert "studio_artifact-deck1/docx/deck.docx" in names_off
    assert not any("/video_mp4/" in n or "/video_captions/" in n for n in names_off)


def test_bundle_export_include_media_false_bundles_no_podcasts(monkeypatch, tmp_path):
    fake_cls = _install_fake_artifacts(monkeypatch)
    md_path = tmp_path / "report.md"
    md_path.write_text("# Report", encoding="utf-8")
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
            export_paths={"markdown": str(md_path)},
        ),
    )
    monkeypatch.setattr(persistence, "get_stale_export_formats", lambda _a: [])

    destination = tmp_path / "bundle.zip"
    response = _client().post(
        "/api/studio/notebooks/notebook:alpha/exports/bundle",
        json={
            "destination": str(destination),
            "regenerate_stale": False,
            "include_media": False,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_count"] == 0


def test_bundle_export_include_media_true_bundles_zero_episodes_not_notebook_scoped(
    monkeypatch, tmp_path
):
    """PodcastEpisode has no notebook relation, so even with
    include_media=True (the default) the route bundles zero episodes,
    without error."""
    fake_cls = _install_fake_artifacts(monkeypatch)
    md_path = tmp_path / "report.md"
    md_path.write_text("# Report", encoding="utf-8")
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
            export_paths={"markdown": str(md_path)},
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
    assert body["media_count"] == 0
    assert body["warnings"] == []


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
