"""Text-flow PDF export contracts for course-pack Studio artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import reportlab.rl_config as rl_config

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.exporters.pdf import write_course_pack_pdf
from deeper_notebook.studio.generation import persistence as persistence_module
from deeper_notebook.studio.generation.persistence import persist_artifact_exports
from deeper_notebook.studio.payloads import build_structured_payload
from deeper_notebook.studio.schemas import CoursePackDocument, parse_artifact_document

_PAGE_OBJECT_PATTERN = re.compile(rb"/Type\s*/Page(?!s)")


def _count_pdf_pages(data: bytes) -> int:
    """Count real `/Page` objects (not the `/Pages` tree root) in a PDF."""
    return len(_PAGE_OBJECT_PATTERN.findall(data))


def _course_pack(*, second_module_content: str = "Practice writing a follow-up query.") -> CoursePackDocument:
    document = parse_artifact_document(
        "course_pack",
        {
            "artifact_type": "course_pack",
            "title": "Evidence Studio Course Pack",
            "audience": "Private researchers",
            "learning_outcomes": ["Create a print-ready, source-grounded export."],
            "prerequisites": ["A selected source set."],
            "modules": [
                {
                    "title": "Module 1: Trusted outputs",
                    "summary": "Build documents that remain editable and auditable.",
                    "lessons": [
                        {
                            "title": "Export review",
                            "content": "Open the file and validate citations before sharing.",
                            "duration_minutes": 20,
                            "exercise": "Inspect the citation appendix.",
                            "facilitator_notes": "Demonstrate the local-only workflow.",
                            "citations": ["[S1]"],
                        }
                    ],
                },
                {
                    "title": "Module 2: Follow-up practice",
                    "summary": "Apply the workflow to a second source set.",
                    "lessons": [
                        {
                            "title": "Practice lesson",
                            "content": second_module_content,
                            "citations": ["[S2]"],
                        }
                    ],
                },
            ],
            "final_assessment": [
                {
                    "prompt": "Which output remains editable?",
                    "options": [
                        {"id": "a", "text": "DOCX"},
                        {"id": "b", "text": "A screenshot"},
                    ],
                    "correct_option_id": "a",
                    "explanation": "DOCX is an editable Office document.",
                    "citations": ["[S1]"],
                }
            ],
        },
    )
    assert isinstance(document, CoursePackDocument)
    return document


def _long_paragraphs(prefix: str, count: int) -> str:
    return "\n\n".join(
        f"{prefix} paragraph {index}: this sentence exists only to occupy real "
        "vertical space on a printed page so that pagination is genuinely "
        "exercised by the export, not merely asserted by construction."
        for index in range(count)
    )


def _long_course_pack() -> CoursePackDocument:
    """A 2-module course pack with ~200 total body paragraphs."""
    document = parse_artifact_document(
        "course_pack",
        {
            "artifact_type": "course_pack",
            "title": "Evidence Studio Long Course Pack",
            "audience": "Private researchers",
            "learning_outcomes": ["Read a long, paginated, source-grounded export."],
            "prerequisites": [],
            "modules": [
                {
                    "title": "Module Alpha: Long-form reading",
                    "summary": "A module with substantial lesson content.",
                    "lessons": [
                        {
                            "title": "Alpha lesson one",
                            "content": _long_paragraphs("Alpha", 100),
                            "citations": ["[S1]"],
                        }
                    ],
                },
                {
                    "title": "Module Beta: More long-form reading",
                    "summary": "A second module with substantial lesson content.",
                    "lessons": [
                        {
                            "title": "Beta lesson one",
                            "content": _long_paragraphs("Beta", 100),
                            "citations": ["[S2]"],
                        }
                    ],
                },
            ],
            "final_assessment": [],
        },
    )
    assert isinstance(document, CoursePackDocument)
    return document


def test_pdf_starts_with_pdf_header(tmp_path: Path) -> None:
    path = tmp_path / "course-pack.pdf"

    write_course_pack_pdf(_course_pack(), path)

    assert path.read_bytes()[:5] == b"%PDF-"


def test_long_course_pack_produces_a_multi_page_pdf(tmp_path: Path) -> None:
    path = tmp_path / "long-course-pack.pdf"

    write_course_pack_pdf(_long_course_pack(), path)

    data = path.read_bytes()
    assert _count_pdf_pages(data) > 1


def test_module_titles_appear_in_the_rendered_pdf(tmp_path: Path) -> None:
    document = _course_pack()
    path = tmp_path / "course-pack.pdf"
    write_course_pack_pdf(document, path)

    # `/Title` document metadata is always present, compression or not.
    assert b"/Title" in path.read_bytes()

    # reportlab compresses content streams by default, so the literal module
    # titles are not searchable in the compressed bytes above. Render a
    # second, uncompressed copy (module-local page-compression toggle) and
    # assert the module titles are present in the raw content streams.
    original_compression = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        uncompressed_path = tmp_path / "course-pack-uncompressed.pdf"
        write_course_pack_pdf(document, uncompressed_path)
        uncompressed_bytes = uncompressed_path.read_bytes()
    finally:
        rl_config.pageCompression = original_compression

    for module in document.modules:
        assert module.title.encode("latin-1") in uncompressed_bytes


def test_markdown_with_special_and_raw_markup_characters_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "course-pack.pdf"
    document = _course_pack(
        second_module_content=(
            "Line one with a raw & ampersand and a <b>fake bold tag</b> and "
            "some *emphasis* plus **bold** text, a `code span`, and a list:\n\n"
            "- first <item>\n- second & item\n\n"
            "1. step one\n2. step two\n\n"
            "```\ncode block with & < > chars\n```"
        )
    )

    result = write_course_pack_pdf(document, path)

    assert result == path
    assert path.exists()
    assert path.read_bytes()[:5] == b"%PDF-"


def test_persist_artifact_exports_yields_epub_and_pdf_for_course_pack(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    document = _course_pack()
    artifact = StudioArtifact(
        id="studio_artifact:pdf-course-pack",
        notebook_id="notebook:private",
        artifact_type="course_pack",
        title=document.title,
        status="completed",
        output_payload=build_structured_payload(document, "# Evidence Studio Course Pack"),
    )

    paths = persist_artifact_exports(artifact, "# Evidence Studio Course Pack")

    assert "epub" in paths
    assert "pdf" in paths
    epub_path = Path(paths["epub"])
    pdf_path = Path(paths["pdf"])
    assert epub_path.exists()
    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:5] == b"%PDF-"
    assert artifact.export_paths["epub"] == paths["epub"]
    assert artifact.export_paths["pdf"] == paths["pdf"]


def test_pdf_export_failure_still_leaves_docx_and_epub(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(persistence_module, "write_course_pack_pdf", _raise)

    document = _course_pack()
    artifact = StudioArtifact(
        id="studio_artifact:pdf-failure-course-pack",
        notebook_id="notebook:private",
        artifact_type="course_pack",
        title=document.title,
        status="completed",
        output_payload=build_structured_payload(document, "# Evidence Studio Course Pack"),
    )

    paths = persist_artifact_exports(artifact, "# Evidence Studio Course Pack")

    assert "pdf" not in paths
    assert "docx" in paths
    assert "epub" in paths
    assert Path(paths["docx"]).exists()
    assert Path(paths["epub"]).exists()
    assert "pdf" not in artifact.export_paths
