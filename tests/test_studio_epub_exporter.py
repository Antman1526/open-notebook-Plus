"""EPUB 3 export contracts for course-pack Studio artifacts."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio.exporters.epub import write_course_pack_epub
from deeper_notebook.studio.generation.persistence import persist_artifact_exports
from deeper_notebook.studio.payloads import build_structured_payload
from deeper_notebook.studio.schemas import CoursePackDocument, parse_artifact_document


def _course_pack(*, second_module_content: str = "Practice writing a follow-up query.") -> CoursePackDocument:
    document = parse_artifact_document(
        "course_pack",
        {
            "artifact_type": "course_pack",
            "title": "Evidence Studio Course Pack",
            "audience": "Private researchers",
            "learning_outcomes": ["Create an editable, source-grounded export."],
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


def _namelist(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _read(path: Path, member: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(member)


def test_mimetype_is_first_entry_stored_and_exact(tmp_path: Path) -> None:
    path = tmp_path / "course-pack.epub"

    write_course_pack_epub(_course_pack(), path)

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"


def test_container_xml_points_at_content_opf(tmp_path: Path) -> None:
    path = tmp_path / "course-pack.epub"

    write_course_pack_epub(_course_pack(), path)

    container = _read(path, "META-INF/container.xml")
    root = ET.fromstring(container)
    rootfile = root.find(
        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
    )
    assert rootfile is not None
    assert rootfile.get("full-path") == "OEBPS/content.opf"


def test_opf_parses_and_spine_has_title_page_plus_one_itemref_per_module(
    tmp_path: Path,
) -> None:
    path = tmp_path / "course-pack.epub"

    write_course_pack_epub(_course_pack(), path)

    opf = _read(path, "OEBPS/content.opf")
    root = ET.fromstring(opf)
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    spine = root.find("opf:spine", ns)
    assert spine is not None
    itemrefs = spine.findall("opf:itemref", ns)
    # title page + 2 modules + assessment
    assert len(itemrefs) == 4
    idrefs = [item.get("idref") for item in itemrefs]
    assert idrefs[0] == "title-page"
    assert idrefs[1] == "module-1"
    assert idrefs[2] == "module-2"
    assert idrefs[3] == "assessment"


def test_every_xhtml_parses_and_module_one_body_has_its_heading(tmp_path: Path) -> None:
    path = tmp_path / "course-pack.epub"

    write_course_pack_epub(_course_pack(), path)

    names = _namelist(path)
    xhtml_members = [name for name in names if name.endswith(".xhtml")]
    assert xhtml_members, "expected at least one .xhtml member"
    for member in xhtml_members:
        ET.fromstring(_read(path, member))

    module_one = _read(path, "OEBPS/module-01.xhtml").decode("utf-8")
    assert "Module 1: Trusted outputs" in module_one


def test_persist_artifact_exports_yields_epub_for_course_pack(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", str(tmp_path))
    document = _course_pack()
    artifact = StudioArtifact(
        id="studio_artifact:epub-course-pack",
        notebook_id="notebook:private",
        artifact_type="course_pack",
        title=document.title,
        status="completed",
        output_payload=build_structured_payload(document, "# Evidence Studio Course Pack"),
    )

    paths = persist_artifact_exports(artifact, "# Evidence Studio Course Pack")

    assert "epub" in paths
    epub_path = Path(paths["epub"])
    assert epub_path.exists()
    assert epub_path.read_bytes()[:2] == b"PK"
    assert artifact.export_paths["epub"] == paths["epub"]


def test_raw_html_in_module_markdown_still_yields_xml_parseable_xhtml(
    tmp_path: Path,
) -> None:
    path = tmp_path / "course-pack.epub"
    document = _course_pack(
        second_module_content="Line one<br>and a raw & ampersand and <img src=x> tag."
    )

    write_course_pack_epub(document, path)

    module_two = _read(path, "OEBPS/module-02.xhtml")
    # Must parse cleanly as XML despite raw, unescaped, unclosed HTML in the
    # source markdown.
    ET.fromstring(module_two)
    text = module_two.decode("utf-8")
    assert "&lt;br&gt;" in text
    assert "&amp;" in text
    assert "&lt;img src=x&gt;" in text
