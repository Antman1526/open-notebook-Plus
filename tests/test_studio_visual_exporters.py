from __future__ import annotations

import zipfile
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageStat
from pptx import Presentation

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.exporters import export_infographic, export_slide_deck
from deeper_notebook.studio.generation.persistence import persist_artifact_exports
from deeper_notebook.studio.payloads import build_structured_payload
from deeper_notebook.studio.schemas import (
    InfographicDocument,
    SlideDeckDocument,
    parse_artifact_document,
)


def _slide_deck() -> SlideDeckDocument:
    document = parse_artifact_document(
        "slide_deck",
        {
            "artifact_type": "slide_deck",
            "title": "Local Evidence Studio",
            "audience": "Private researchers",
            "slides": [
                {
                    "title": "Grounded generation",
                    "bullets": [
                        "Artifacts remain tied to selected sources.",
                        "Local models can produce validated documents.",
                    ],
                    "speaker_notes": "Explain why source ownership matters.",
                    "visual_direction": "Use a simple evidence flow.",
                    "citations": ["[S1]", "[S2]"],
                },
                {
                    "title": "Private by default",
                    "bullets": ["No hosted rendering service is required."],
                    "citations": ["[S2]"],
                },
            ],
        },
    )
    assert isinstance(document, SlideDeckDocument)
    return document


def _infographic(orientation: str = "portrait") -> InfographicDocument:
    document = parse_artifact_document(
        "infographic",
        {
            "artifact_type": "infographic",
            "title": "Evidence at a glance",
            "orientation": orientation,
            "panels": [
                {
                    "kind": "metric",
                    "heading": "Source coverage",
                    "value": "95%",
                    "body": "Resolved citation target",
                    "citations": ["[S1]"],
                },
                {
                    "kind": "process",
                    "heading": "Workflow",
                    "body": "Collect, validate, render, and review.",
                    "citations": ["[S2]"],
                },
                {
                    "kind": "comparison",
                    "heading": "Ownership",
                    "body": "Local files and models remain under owner control.",
                },
            ],
        },
    )
    assert isinstance(document, InfographicDocument)
    return document


def _dense_infographic(orientation: str) -> InfographicDocument:
    document = parse_artifact_document(
        "infographic",
        {
            "artifact_type": "infographic",
            "title": "Twenty grounded findings",
            "orientation": orientation,
            "panels": [
                {
                    "kind": "metric" if index % 3 == 0 else "text",
                    "heading": f"Finding {index + 1}",
                    "value": f"{index + 1}%" if index % 3 == 0 else "",
                    "body": "A compact source-grounded finding.",
                    "citations": [f"[S{(index % 4) + 1}]"],
                }
                for index in range(20)
            ],
        },
    )
    assert isinstance(document, InfographicDocument)
    return document


def _assert_nonblank_image(path: Path, expected_size: tuple[int, int]) -> None:
    with Image.open(path) as image:
        assert image.size == expected_size
        assert image.mode in {"RGB", "RGBA"}
        extrema = ImageStat.Stat(image.convert("RGB")).extrema
        assert any(low != high for low, high in extrema)


def _office_application(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        xml = package.read("docProps/app.xml").decode("utf-8")
    start = xml.index("<Application>") + len("<Application>")
    return xml[start : xml.index("</Application>", start)]


def test_slide_deck_exports_editable_pptx_with_notes_and_citations(tmp_path):
    pptx_path = tmp_path / "deck.pptx"
    pdf_path = tmp_path / "deck.pdf"

    export_slide_deck(_slide_deck(), pptx_path, pdf_path)

    presentation = Presentation(pptx_path)
    assert presentation.core_properties.author == "Deeper Notebook"
    assert _office_application(pptx_path) == "Deeper Notebook"
    assert presentation.slide_width == 12192000
    assert presentation.slide_height == 6858000
    assert len(presentation.slides) == 3
    assert "Local Evidence Studio" in " ".join(
        shape.text for shape in presentation.slides[0].shapes if hasattr(shape, "text")
    )
    content_slide = presentation.slides[1]
    slide_text = " ".join(
        shape.text for shape in content_slide.shapes if hasattr(shape, "text")
    )
    assert "Grounded generation" in slide_text
    assert "Artifacts remain tied to selected sources." in slide_text
    assert "[S1] [S2]" in slide_text
    notes = content_slide.notes_slide.notes_text_frame.text
    assert "Explain why source ownership matters." in notes
    assert "Use a simple evidence flow." in notes
    assert "[S1] [S2]" in notes

    pdf = fitz.open(pdf_path)
    assert pdf.metadata["author"] == "Deeper Notebook"
    assert pdf.metadata["creator"] == "Deeper Notebook"
    assert pdf.metadata["producer"] == "Deeper Notebook"
    assert pdf.page_count == 3
    pixmap = pdf[1].get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
    assert len(set(pixmap.samples)) > 8
    pdf.close()


@pytest.mark.parametrize(
    ("orientation", "expected_size"),
    [
        ("portrait", (1200, 1800)),
        ("landscape", (1800, 1200)),
        ("square", (1400, 1400)),
    ],
)
def test_infographic_exports_nonblank_png_and_pdf(
    tmp_path,
    orientation,
    expected_size,
):
    png_path = tmp_path / f"infographic-{orientation}.png"
    pdf_path = tmp_path / f"infographic-{orientation}.pdf"

    export_infographic(_infographic(orientation), png_path, pdf_path)

    _assert_nonblank_image(png_path, expected_size)
    pdf = fitz.open(pdf_path)
    assert pdf.metadata["author"] == "Deeper Notebook"
    assert pdf.metadata["creator"] == "Deeper Notebook"
    assert pdf.metadata["producer"] == "Deeper Notebook"
    assert pdf.page_count == 1
    assert pdf[0].rect.width > 0
    assert pdf[0].rect.height > 0
    pdf.close()


@pytest.mark.parametrize(
    ("orientation", "expected_size"),
    [
        ("portrait", (1200, 1800)),
        ("landscape", (1800, 1200)),
        ("square", (1400, 1400)),
    ],
)
def test_infographic_exports_schema_maximum_panel_count(
    tmp_path,
    orientation,
    expected_size,
):
    png_path = tmp_path / f"dense-{orientation}.png"
    pdf_path = tmp_path / f"dense-{orientation}.pdf"

    export_infographic(_dense_infographic(orientation), png_path, pdf_path)

    _assert_nonblank_image(png_path, expected_size)
    with fitz.open(pdf_path) as pdf:
        assert pdf.page_count == 1


# v0.8.127 — coverage for persist_artifact_exports' visual-export path
# reuse (pptx/png/pdf), mirroring the office-export coverage in
# test_studio_export_staleness.py for the same v0.8.126 pattern.
def _slide_deck_artifact() -> StudioArtifact:
    document = _slide_deck()
    markdown = (
        "# Local Evidence Studio\n\nGrounded generation [S1] [S2].\n\n"
        "Private by default [S2]."
    )
    return StudioArtifact(
        id="studio_artifact:slide-deck-persist",
        notebook_id="notebook:visual-exports",
        artifact_type="slide_deck",
        title="Local Evidence Studio",
        status="completed",
        output_payload=build_structured_payload(document, markdown),
    )


def _infographic_artifact() -> StudioArtifact:
    document = _infographic()
    markdown = "# Evidence at a glance\n\nSource coverage [S1]. Workflow [S2]."
    return StudioArtifact(
        id="studio_artifact:infographic-persist",
        notebook_id="notebook:visual-exports",
        artifact_type="infographic",
        title="Evidence at a glance",
        status="completed",
        output_payload=build_structured_payload(document, markdown),
    )


def test_persist_artifact_exports_slide_deck_twice_reuses_paths_and_leaves_no_orphans(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _slide_deck_artifact()
    content = artifact.output_payload["content"]

    first_paths = persist_artifact_exports(artifact, content)
    assert "pptx" in first_paths and "pdf" in first_paths
    files_after_first = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    second_paths = persist_artifact_exports(artifact, content)
    files_after_second = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    assert second_paths == first_paths
    assert files_after_second == files_after_first
    assert not any("-2." in name for name in files_after_second)
    assert Path(first_paths["pptx"]).is_file()
    assert Path(first_paths["pdf"]).is_file()


def test_persist_artifact_exports_infographic_twice_reuses_paths_and_leaves_no_orphans(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    artifact = _infographic_artifact()
    content = artifact.output_payload["content"]

    first_paths = persist_artifact_exports(artifact, content)
    assert "png" in first_paths and "pdf" in first_paths
    files_after_first = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    second_paths = persist_artifact_exports(artifact, content)
    files_after_second = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    assert second_paths == first_paths
    assert files_after_second == files_after_first
    assert not any("-2." in name for name in files_after_second)
    assert Path(first_paths["png"]).is_file()
    assert Path(first_paths["pdf"]).is_file()


def test_persist_artifact_exports_visual_ignores_recorded_path_outside_export_root(
    tmp_path, monkeypatch
):
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(export_dir))
    artifact = _slide_deck_artifact()
    content = artifact.output_payload["content"]

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_pptx = outside_dir / "escaped.pptx"
    outside_pptx.write_bytes(b"must not be overwritten")
    artifact.export_paths = {"pptx": str(outside_pptx)}

    export_paths = persist_artifact_exports(artifact, content)

    pptx_path = Path(export_paths["pptx"])
    assert pptx_path != outside_pptx
    assert export_dir.resolve() in pptx_path.resolve().parents
    assert outside_pptx.read_bytes() == b"must not be overwritten"


def test_visual_exporters_reject_the_wrong_document(tmp_path):
    with pytest.raises(TypeError, match="SlideDeckDocument"):
        export_slide_deck(
            _infographic(),
            tmp_path / "wrong.pptx",
            tmp_path / "wrong.pdf",
        )

    with pytest.raises(TypeError, match="InfographicDocument"):
        export_infographic(
            _slide_deck(),
            tmp_path / "wrong.png",
            tmp_path / "wrong.pdf",
        )
