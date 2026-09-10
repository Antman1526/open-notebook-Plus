"""Text-flow PDF export contracts for course-pack Studio artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import reportlab.rl_config as rl_config
from reportlab.pdfbase import pdfmetrics as rl_pdfmetrics

import deeper_notebook.studio.exporters.pdf as pdf_module
from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.exporters.pdf import (
    _wrap_script_runs,
    write_course_pack_pdf,
)
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


def test_cyrillic_and_greek_text_renders_without_raising(tmp_path: Path) -> None:
    document = _course_pack(
        second_module_content=(
            "Кириллица: Привет, мир! И немного Ελληνικά: Καλημέρα κόσμε."
        )
    )
    path = tmp_path / "cyrillic-greek.pdf"

    write_course_pack_pdf(document, path)  # must not raise

    assert path.read_bytes()[:5] == b"%PDF-"

    pdf_module._resolve_fonts()
    if pdf_module._BODY_FONT == "Helvetica":
        pytest.skip("no system Unicode TTF candidate found on this host")

    original_compression = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        uncompressed_path = tmp_path / "cyrillic-greek-uncompressed.pdf"
        write_course_pack_pdf(document, uncompressed_path)
        uncompressed_bytes = uncompressed_path.read_bytes()
    finally:
        rl_config.pageCompression = original_compression

    # reportlab's TTFont subsetting renames `/BaseFont` to the font's own
    # internal PostScript name (e.g. "ArialUnicodeMS"), never to the name it
    # was registered under ("DNBody") — so the registered name itself is not
    # a reliable string to search for. `/FontFile2` is: it only appears when
    # a real TrueType font (not a base-14 font) was embedded, which is
    # exactly what a resolved body font candidate causes.
    assert b"/FontFile2" in uncompressed_bytes


def test_cjk_text_renders_and_uses_cid_fonts(tmp_path: Path) -> None:
    document = _course_pack(
        second_module_content=(
            "Chinese: 你好，世界。\n\nJapanese: こんにちは、世界。\n\nKorean: 안녕하세요, 세계."
        )
    )
    path = tmp_path / "cjk.pdf"

    write_course_pack_pdf(document, path)  # must not raise

    assert path.read_bytes()[:5] == b"%PDF-"

    # CID fonts need no font file (they are always available), so this
    # assertion never needs to skip.
    original_compression = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        uncompressed_path = tmp_path / "cjk-uncompressed.pdf"
        write_course_pack_pdf(document, uncompressed_path)
        uncompressed_bytes = uncompressed_path.read_bytes()
    finally:
        rl_config.pageCompression = original_compression

    assert b"STSong-Light" in uncompressed_bytes
    assert b"HeiseiMin-W3" in uncompressed_bytes
    assert b"HYSMyeongJo-Medium" in uncompressed_bytes


def test_wrap_script_runs_wraps_only_the_cjk_portion() -> None:
    result = _wrap_script_runs("Hello 世界")

    assert result == 'Hello <font name="STSong-Light">世界</font>'


def test_wrap_script_runs_escapes_before_wrapping() -> None:
    # `&` must be escaped inside a run that goes on to get wrapped in a
    # `<font>` tag, and it must be escaped *before* the tag is inserted
    # around it (raw `&` inside a `<font name="...">` run would corrupt the
    # markup reportlab parses).
    result = _wrap_script_runs("A & 世界")

    assert result == 'A &amp; <font name="STSong-Light">世界</font>'


def test_pdf_font_env_override_missing_file_falls_through(
    tmp_path: Path, monkeypatch
) -> None:
    missing_font = tmp_path / "does-not-exist.ttf"
    assert not missing_font.exists()

    monkeypatch.setenv("DEEPER_NOTEBOOK_PDF_FONT", str(missing_font))
    monkeypatch.setattr(pdf_module, "_FONTS_RESOLVED", False)
    monkeypatch.setattr(pdf_module, "_BODY_FONT", "Helvetica")
    monkeypatch.setattr(pdf_module, "_BODY_FONT_BOLD", "Helvetica-Bold")

    pdf_module._resolve_fonts()  # must not raise despite the missing override

    path = tmp_path / "env-override-missing.pdf"
    write_course_pack_pdf(_course_pack(), path)  # must not raise either

    assert path.read_bytes()[:5] == b"%PDF-"


def test_bold_falls_back_to_the_unicode_body_font_not_helvetica(monkeypatch, tmp_path):
    """v0.8.119 — a family with no bold file must NOT leave headings on
    Helvetica-Bold: that renders Cyrillic/Greek headings as boxes. The
    regular Unicode face is used instead (bold weight is sacrificed)."""
    import reportlab

    import deeper_notebook.studio.exporters.pdf as pdf_module

    # A real TTF with no bold sibling: reportlab's own bundled Vera.ttf.
    vera = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    assert vera.is_file()

    monkeypatch.setattr(pdf_module, "_FONTS_RESOLVED", False)
    monkeypatch.setattr(pdf_module, "_BODY_FONT", "Helvetica")
    monkeypatch.setattr(pdf_module, "_BODY_FONT_BOLD", "Helvetica-Bold")
    monkeypatch.setattr(
        pdf_module, "_body_font_candidates", lambda: [(str(vera), None)]
    )

    pdf_module._resolve_fonts()

    assert pdf_module._BODY_FONT == "DNBody"
    assert pdf_module._BODY_FONT_BOLD == "DNBody"
    assert pdf_module._STYLES["Heading1"].fontName == "DNBody"


def test_font_resolution_runs_once_per_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_module, "_FONTS_RESOLVED", False)
    monkeypatch.setattr(pdf_module, "_BODY_FONT", "Helvetica")
    monkeypatch.setattr(pdf_module, "_BODY_FONT_BOLD", "Helvetica-Bold")

    calls: list[object] = []
    original_register_font = rl_pdfmetrics.registerFont

    def _spy(font):
        calls.append(font)
        return original_register_font(font)

    monkeypatch.setattr(rl_pdfmetrics, "registerFont", _spy)

    write_course_pack_pdf(_course_pack(), tmp_path / "first.pdf")
    calls_after_first_run = len(calls)
    assert calls_after_first_run > 0

    write_course_pack_pdf(_course_pack(), tmp_path / "second.pdf")

    assert len(calls) == calls_after_first_run


def test_bold_paired_candidate_registers_dnbodybold(monkeypatch) -> None:
    """When a candidate specifies a bold file that exists, DNBodyBold is registered
    and used for headings."""
    import reportlab

    vera = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    vera_bd = Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf"
    assert vera.is_file() and vera_bd.is_file()

    monkeypatch.setattr(pdf_module, "_FONTS_RESOLVED", False)
    monkeypatch.setattr(pdf_module, "_BODY_FONT", "Helvetica")
    monkeypatch.setattr(pdf_module, "_BODY_FONT_BOLD", "Helvetica-Bold")
    monkeypatch.setattr(
        pdf_module, "_body_font_candidates", lambda: [(str(vera), str(vera_bd))]
    )

    pdf_module._resolve_fonts()

    assert pdf_module._BODY_FONT == "DNBody"
    assert pdf_module._BODY_FONT_BOLD == "DNBodyBold"
    assert pdf_module._STYLES["Heading1"].fontName == "DNBodyBold"
