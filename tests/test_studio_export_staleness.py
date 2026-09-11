"""Tests for multi-file export regeneration, aliases, and staleness tracking."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.generation.persistence import (
    canonical_export_format,
    get_stale_export_formats,
    is_export_stale,
    persist_artifact_exports,
    persist_single_export,
    producible_export_formats,
)
from deeper_notebook.studio.payloads import build_structured_payload
from deeper_notebook.studio.schemas import parse_artifact_document


def _sample_course_pack_artifact() -> StudioArtifact:
    content = """# Data Science Masterclass

## Module 1: Foundations
Core concepts in data pipelines.
- Learner Handout: Read documentation.
- Hands-on Exercise: Run query.
- Knowledge Check: What is SQL?

## Module 2: Analysis
Advanced analytical patterns.
"""
    doc = parse_artifact_document(
        "course_pack",
        {
            "artifact_type": "course_pack",
            "title": "Data Science Masterclass",
            "audience": "Engineers and Analysts",
            "learning_outcomes": ["Understand SQL"],
            "prerequisites": ["Python basics"],
            "modules": [
                {
                    "title": "Foundations",
                    "summary": "Core concepts in data pipelines.",
                    "lessons": [
                        {
                            "title": "SQL 101",
                            "content": "SELECT * FROM data",
                            "duration_minutes": 15,
                        }
                    ],
                },
                {
                    "title": "Analysis",
                    "summary": "Advanced analytical patterns.",
                    "lessons": [
                        {
                            "title": "Data aggregation",
                            "content": "GROUP BY and window functions",
                            "duration_minutes": 20,
                        }
                    ],
                },
            ],
            "final_assessment": [
                {
                    "prompt": "What is SQL?",
                    "options": [
                        {"id": "a", "text": "Structured Query Language"},
                        {"id": "b", "text": "None"},
                    ],
                    "correct_option_id": "a",
                }
            ],
        },
    )
    payload = build_structured_payload(doc, content)
    return StudioArtifact(
        id="studio_artifact:cs101",
        notebook_id="notebook:alpha",
        artifact_type="course_pack",
        title="Data Science Masterclass",
        status="completed",
        output_payload=payload,
    )


def test_persist_artifact_exports_records_export_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    content = artifact.output_payload["content"]

    export_paths = persist_artifact_exports(artifact, content)

    assert "markdown" in export_paths
    assert "scorm_package" in export_paths
    assert "xapi_package" in export_paths
    assert "instructor_guide" in export_paths
    assert "learner_handout" in export_paths

    hashes = artifact.output_payload.get("export_hashes")
    assert isinstance(hashes, dict)
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert hashes["markdown"] == expected_hash
    assert hashes["scorm_package"] == expected_hash
    assert hashes["xapi_package"] == expected_hash
    assert hashes["pdf"] == expected_hash

    # Verify freshness checks return False (not stale)
    assert not is_export_stale(artifact, "markdown")
    assert not is_export_stale(artifact, "scorm_package")
    assert not is_export_stale(artifact, "scorm")  # alias check
    assert not is_export_stale(artifact, "xapi")  # alias check
    assert not is_export_stale(artifact, "pdf")
    assert get_stale_export_formats(artifact) == []


def test_is_export_stale_detects_content_drift(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    content = artifact.output_payload["content"]
    persist_artifact_exports(artifact, content)

    # All fresh initially
    assert not is_export_stale(artifact, "markdown")

    # Content drift occurs (user edited artifact)
    artifact.output_payload["content"] = "# Updated Masterclass Content"

    assert is_export_stale(artifact, "markdown")
    assert is_export_stale(artifact, "scorm_package")
    assert is_export_stale(artifact, "scorm")

    stale_formats = get_stale_export_formats(artifact)
    assert "markdown" in stale_formats
    assert "scorm_package" in stale_formats


def test_is_export_stale_detects_missing_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    persist_artifact_exports(artifact, artifact.output_payload["content"])

    md_path = Path(artifact.export_paths["markdown"])
    assert md_path.is_file()
    md_path.unlink()

    assert is_export_stale(artifact, "markdown")


def test_persist_single_export_regenerates_scorm_and_refreshes_hash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    initial_content = artifact.output_payload["content"]
    persist_artifact_exports(artifact, initial_content)

    # Edit content
    new_content = initial_content + "\n## Module 3: Advanced Optimization\nDetails here."
    artifact.output_payload["content"] = new_content
    assert is_export_stale(artifact, "scorm_package")

    # Regenerate only SCORM using alias "scorm"
    path = persist_single_export(artifact, "scorm")
    assert path is not None
    assert Path(path).is_file()

    # Verify SCORM zip structure
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        assert "imsmanifest.xml" in names
        assert "index.html" in names
        assert "instructor-guide.md" in names

    # Hash should be refreshed for scorm_package
    new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
    assert artifact.output_payload["export_hashes"]["scorm_package"] == new_hash
    assert not is_export_stale(artifact, "scorm_package")
    assert not is_export_stale(artifact, "scorm")


def test_persist_single_export_regenerates_xapi_and_refreshes_hash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    initial_content = artifact.output_payload["content"]
    persist_artifact_exports(artifact, initial_content)

    new_content = initial_content + "\n## Module 3: Distributed Computing\n"
    artifact.output_payload["content"] = new_content
    assert is_export_stale(artifact, "xapi_package")

    path = persist_single_export(artifact, "xapi")
    assert path is not None
    assert Path(path).is_file()

    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        assert "tincan.xml" in names
        assert "xapi-statements.json" in names
        assert "index.html" in names

    assert not is_export_stale(artifact, "xapi_package")


def test_persist_single_export_regenerates_course_pack_components(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    persist_artifact_exports(artifact, artifact.output_payload["content"])

    # Regenerate instructor guide, learner handout, module checklist, assessment
    for fmt in ["instructor_guide", "learner_handout", "module_checklist", "assessment"]:
        out = persist_single_export(artifact, fmt)
        assert out is not None
        assert Path(out).is_file()
        assert not is_export_stale(artifact, fmt)


def test_persist_single_export_regenerates_research_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    persist_artifact_exports(artifact, artifact.output_payload["content"])

    bundle_path = persist_single_export(artifact, "bundle")
    assert bundle_path is not None
    assert Path(bundle_path).is_file()
    assert not is_export_stale(artifact, "research_bundle")


def test_producible_export_formats_and_aliases() -> None:
    artifact = _sample_course_pack_artifact()
    canonical_formats = producible_export_formats(artifact, include_aliases=False)
    assert "scorm_package" in canonical_formats
    assert "xapi_package" in canonical_formats
    assert "research_bundle" in canonical_formats
    assert "scorm" not in canonical_formats

    all_formats = producible_export_formats(artifact, include_aliases=True)
    assert "scorm" in all_formats
    assert "xapi" in all_formats
    assert "bundle" in all_formats
    assert "checklist" in all_formats

    assert canonical_export_format("scorm") == "scorm_package"
    assert canonical_export_format("xapi") == "xapi_package"
    assert canonical_export_format("bundle") == "research_bundle"
    assert canonical_export_format("unknown_format") == "unknown_format"


@pytest.mark.asyncio
async def test_studio_artifact_delete_cleans_up_export_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _sample_course_pack_artifact()
    artifact.id = "studio_artifact:test_delete"
    persist_artifact_exports(artifact, artifact.output_payload["content"])

    # Verify export files exist on disk
    assert len(artifact.export_paths) > 0
    for path_str in artifact.export_paths.values():
        assert Path(path_str).is_file()

    # External file outside export dir should not be touched
    outside_file = tmp_path.parent / "safe_file_outside.txt"
    outside_file.write_text("should not be deleted")
    artifact.export_paths["outside"] = str(outside_file)

    # Mock DB delete
    monkeypatch.setattr(
        "deeper_notebook.domain.base.repo_delete", AsyncMock(return_value=True)
    )

    deleted = await artifact.delete()
    assert deleted is True

    # Internal files were unlinked
    for k, path_str in artifact.export_paths.items():
        if k == "outside":
            assert Path(path_str).is_file()
        else:
            assert not Path(path_str).exists()

    outside_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_studio_artifact_delete_cleans_up_video_overviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    video_root = tmp_path / "video-overviews"
    video_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("deeper_notebook.config.DATA_FOLDER", str(tmp_path))

    artifact = _sample_course_pack_artifact()
    artifact.id = "studio_artifact:vid123"
    slug = "studio_artifact-vid123"
    artifact_dir = video_root / slug
    artifact_dir.mkdir(parents=True, exist_ok=True)

    mp4_file = artifact_dir / "overview.mp4"
    vtt_file = artifact_dir / "captions.vtt"
    mp4_file.write_bytes(b"dummy mp4 data")
    vtt_file.write_text("WEBVTT\n00:00.000 --> 00:01.000\nHello")

    artifact.export_paths = {
        "video_mp4": str(mp4_file),
        "video_captions": str(vtt_file),
    }

    monkeypatch.setattr(
        "deeper_notebook.domain.base.repo_delete", AsyncMock(return_value=True)
    )

    deleted = await artifact.delete()
    assert deleted is True

    assert not mp4_file.exists()
    assert not vtt_file.exists()
    assert not artifact_dir.exists()
    assert video_root.exists()


