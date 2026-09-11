"""Trust-boundary coverage for Studio SVG charts and research ZIP bundles."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.exporters.charts import ChartDocument, render_svg_chart
from deeper_notebook.studio.exporters.research_bundle import (
    build_research_bundle,
    normalize_bundle_path,
    verify_research_bundle,
)
from deeper_notebook.studio.generation.persistence import persist_artifact_exports
from deeper_notebook.studio.payloads import build_structured_payload
from deeper_notebook.studio.schemas import GenericDocument, parse_artifact_document


def _chart_payload() -> dict[str, object]:
    return {
        "chart_type": "line",
        "title": "Benchmark quality",
        "x_label": "Run",
        "y_label": "Score",
        "series": [
            {
                "name": "Local model",
                "points": [{"x": 1, "y": 82}, {"x": 2, "y": 91}],
            }
        ],
    }


def _artifact() -> StudioArtifact:
    document = parse_artifact_document(
        "report",
        {
            "artifact_type": "report",
            "title": "Trusted research bundle",
            "summary": "A validated source-grounded report.",
            "sections": [
                {
                    "heading": "Findings",
                    "body": "The private workflow keeps claims inspectable.",
                    "citations": ["[S1]"],
                }
            ],
        },
    )
    assert isinstance(document, GenericDocument)
    return StudioArtifact(
        id="studio_artifact:trusted-bundle",
        notebook_id="notebook:private-research",
        artifact_type="report",
        title="Trusted research bundle",
        status="completed",
        source_ids=["source:one"],
        output_payload=build_structured_payload(
            document,
            "# Findings\n\nGrounded claim [S1].",
            extras={
                "chart": _chart_payload(),
                "evaluation_report": {"counts": {"supported": 1}},
            },
        ),
        citations=[
            {
                "source_id": "source:one",
                "title": "Local source",
                "marker": "[S1]",
                "preview": "A short selected quote.",
            }
        ],
    )


def test_svg_renderer_uses_only_internal_primitives_and_escapes_text() -> None:
    chart = ChartDocument.model_validate(_chart_payload())
    svg = render_svg_chart(chart)

    assert "<script" not in svg.lower()
    assert "foreignobject" not in svg.lower()
    assert "href=" not in svg.lower()
    assert "<polyline" in svg
    assert "<circle" in svg


@pytest.mark.parametrize(
    "payload",
    [
        {**_chart_payload(), "title": "<script>alert(1)</script>"},
        {**_chart_payload(), "x_label": "javascript:alert(1)"},
        {
            **_chart_payload(),
            "series": [{"name": "onload=boom", "points": [{"x": 1, "y": 1}]}],
        },
    ],
)
def test_chart_schema_rejects_hostile_content(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="forbidden SVG content"):
        ChartDocument.model_validate(payload)


@pytest.mark.parametrize(
    "path", ["../escape.txt", "/absolute.txt", "bad\\path.txt", "./dot.txt"]
)
def test_bundle_paths_reject_traversal_and_nonportable_inputs(path: str) -> None:
    with pytest.raises(ValueError):
        normalize_bundle_path(path)


def test_bundle_verifies_hashes_and_detects_tampering(tmp_path: Path) -> None:
    generated = tmp_path / "report.docx"
    generated.write_bytes(b"trusted document")
    bundle = tmp_path / "research.zip"
    build_research_bundle(
        bundle,
        artifact={"id": "studio_artifact:one"},
        markdown="# Report\n",
        citations=[],
        source_metadata=[],
        evaluation_report={"counts": {}},
        generated_files={"generated/docx/report.docx": generated},
    )

    manifest = verify_research_bundle(bundle)
    assert {entry["path"] for entry in manifest["entries"]} == {
        "artifact.json",
        "artifact.md",
        "citations.json",
        "sources.json",
        "evaluation.json",
        "generated/docx/report.docx",
    }
    with zipfile.ZipFile(bundle) as archive:
        original_entries = {name: archive.read(name) for name in archive.namelist()}
    original_entries["artifact.md"] = b"changed"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in original_entries.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="invalid entry set|integrity"):
        verify_research_bundle(bundle)


def test_persistence_creates_immutable_research_bundle_with_svg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _artifact()
    paths = persist_artifact_exports(artifact, "# Findings\n\nGrounded claim [S1].")

    assert Path(paths["svg_chart"]).is_file()
    bundle_path = Path(paths["research_bundle"])
    manifest = verify_research_bundle(bundle_path)
    names = {entry["path"] for entry in manifest["entries"]}
    assert {
        "artifact.json",
        "artifact.md",
        "citations.json",
        "sources.json",
        "evaluation.json",
    } <= names
    assert any(name.startswith("generated/docx/") for name in names)
    assert any(name.startswith("generated/svg_chart/") for name in names)
    with zipfile.ZipFile(bundle_path) as archive:
        artifact_payload = json.loads(archive.read("artifact.json"))
    assert artifact_payload["revision_of_id"] is None
    assert artifact_payload["export_paths"]["research_bundle"] == str(bundle_path)


# v0.8.127 — coverage for persist_artifact_exports' svg_chart path reuse,
# mirroring the research_bundle coverage above for the same v0.8.126 pattern.
def test_persist_artifact_exports_svg_chart_twice_reuses_path_and_leaves_no_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _artifact()
    content = "# Findings\n\nGrounded claim [S1]."

    first_paths = persist_artifact_exports(artifact, content)
    svg_files_after_first = sorted(tmp_path.glob("*-chart.svg"))

    second_paths = persist_artifact_exports(artifact, content)
    svg_files_after_second = sorted(tmp_path.glob("*-chart.svg"))

    assert second_paths["svg_chart"] == first_paths["svg_chart"]
    assert svg_files_after_second == svg_files_after_first
    assert len(svg_files_after_second) == 1
    assert Path(first_paths["svg_chart"]).is_file()


def test_persist_artifact_exports_svg_chart_ignores_recorded_path_outside_export_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(export_dir))
    artifact = _artifact()
    content = "# Findings\n\nGrounded claim [S1]."

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_svg = outside_dir / "escaped-chart.svg"
    outside_svg.write_text("must not be overwritten", encoding="utf-8")
    artifact.export_paths = {"svg_chart": str(outside_svg)}

    export_paths = persist_artifact_exports(artifact, content)

    svg_path = Path(export_paths["svg_chart"])
    assert svg_path != outside_svg
    assert export_dir.resolve() in svg_path.resolve().parents
    assert outside_svg.read_text(encoding="utf-8") == "must not be overwritten"
