"""EPUB 3 export for course-pack Studio artifacts.

v0.8.117 — Course packs already get a DOCX export (documents.py) and, for
`{course_pack, training_guide}`, a SCORM/xAPI LMS zip
(generation/persistence.py `_write_course_pack_lms_packages`). This adds a
third, reader-facing format: a plain EPUB 3 ebook so a learner can open the
course pack in any e-reader without an LMS.

Why stdlib + markdown-it-py only, no `ebooklib`: the desktop bootstrap
provisions Python dependencies from the project's lockfile at build time
(see `dev-init.sh` / the desktop packaging scripts), and this task is
explicitly scoped to add zero new dependencies. An EPUB 3 is just a
specifically-shaped zip of XML/XHTML files — `zipfile` (stdlib) writes the
archive, `xml.etree.ElementTree` (stdlib, used only in tests here to prove
well-formedness) can parse it back, and `markdown_it` (already a project
dependency, used elsewhere for markdown -> HTML) renders lesson content.
No third-party EPUB library is needed for a spec this narrow.

Where PDF stands: course packs do **not** get a PDF export. The only
image-based PDFs Evidence Studio produces are for slide decks and
infographics (`exporters/slides.py`, `exporters/infographic.py`), which
render fixed-layout pages from images. A course pack is reflowable text,
which is exactly what EPUB is for — a text PDF would be redundant with the
DOCX (editable) and EPUB (reflowable reader) exports this module and
`documents.py` already provide.
"""

from __future__ import annotations

import uuid
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path

from markdown_it import MarkdownIt

from deeper_notebook.identity import PRODUCT_NAME
from deeper_notebook.studio.schemas import CoursePackDocument

_MIMETYPE = "application/epub+zip"

_MD = MarkdownIt("commonmark", {"xhtmlOut": True, "html": False})

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_STYLE_CSS = """body { font-family: serif; line-height: 1.4; margin: 1em; }
h1, h2, h3 { font-family: sans-serif; }
.citation { color: #555; font-size: 0.85em; }
"""


def _escape(text: str) -> str:
    return saxutils.escape(text or "")


def _xhtml_document(title: str, body_html: str) -> str:
    """Wrap rendered markdown HTML in a well-formed XHTML document.

    `markdown_it` is configured with `xhtmlOut=True` (void elements from its
    own renderer, e.g. `<br />`/`<hr />`/`<img ... />`, are self-closed) and
    `html=False` (any raw HTML typed into the source markdown, e.g. a stray
    `<br>` or `<img src=x>`, is escaped to text rather than passed through
    unescaped) so the result always parses as XML.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head>\n"
        f"<title>{_escape(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        "<body>\n"
        f"{body_html}"
        "</body>\n"
        "</html>\n"
    )


def _render_markdown(text: str) -> str:
    return _MD.render(text or "")


def _lesson_html(lesson) -> str:
    parts = [f"<h2>{_escape(lesson.title)}</h2>"]
    if lesson.duration_minutes:
        parts.append(f"<p><em>Estimated duration: {lesson.duration_minutes} minutes</em></p>")
    parts.append(_render_markdown(lesson.content))
    if lesson.exercise:
        parts.append("<h3>Exercise</h3>")
        parts.append(_render_markdown(lesson.exercise))
    if lesson.facilitator_notes:
        parts.append("<h3>Facilitator notes</h3>")
        parts.append(_render_markdown(lesson.facilitator_notes))
    if lesson.citations:
        markers = " ".join(dict.fromkeys(lesson.citations))
        parts.append(f'<p class="citation">Sources {_escape(markers)}</p>')
    return "\n".join(parts)


def _module_xhtml(module, index: int) -> str:
    body = [f"<h1>{_escape(module.title)}</h1>"]
    if module.summary:
        body.append(_render_markdown(module.summary))
    for lesson in module.lessons:
        body.append(_lesson_html(lesson))
    return _xhtml_document(module.title, "\n".join(body))


def _title_page_xhtml(document: CoursePackDocument) -> str:
    body = [
        f"<h1>{_escape(document.title)}</h1>",
        f"<p><strong>Audience:</strong> {_escape(document.audience)}</p>",
    ]
    if document.learning_outcomes:
        body.append("<h2>Learning outcomes</h2><ul>")
        body.extend(f"<li>{_escape(outcome)}</li>" for outcome in document.learning_outcomes)
        body.append("</ul>")
    if document.prerequisites:
        body.append("<h2>Prerequisites</h2><ul>")
        body.extend(f"<li>{_escape(item)}</li>" for item in document.prerequisites)
        body.append("</ul>")
    body.append(f"<p><em>{_escape(PRODUCT_NAME)} / Evidence Studio</em></p>")
    return _xhtml_document(document.title, "\n".join(body))


def _assessment_xhtml(document: CoursePackDocument) -> str | None:
    if not document.final_assessment:
        return None
    body = ["<h1>Assessment</h1>"]
    all_citations: list[str] = []
    for question in document.final_assessment:
        body.append(f"<h2>{_escape(question.prompt)}</h2><ul>")
        for option in question.options:
            marker = " (correct)" if option.id == question.correct_option_id else ""
            body.append(f"<li>{_escape(option.text)}{marker}</li>")
        body.append("</ul>")
        if question.explanation:
            body.append(_render_markdown(question.explanation))
        if question.citations:
            all_citations.extend(question.citations)
            markers = " ".join(dict.fromkeys(question.citations))
            body.append(f'<p class="citation">Sources {_escape(markers)}</p>')
    if all_citations:
        body.append("<h2>Citation appendix</h2><ul>")
        for marker in dict.fromkeys(all_citations):
            body.append(f"<li>{_escape(marker)}</li>")
        body.append("</ul>")
    return _xhtml_document("Assessment", "\n".join(body))


def _opf(
    document: CoursePackDocument,
    *,
    book_id: str,
    module_files: list[str],
    has_assessment: bool,
) -> str:
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="style" href="style.css" media-type="text/css"/>',
        '<item id="title-page" href="title-page.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine_items = [
        '<itemref idref="title-page"/>',
    ]
    for index, _filename in enumerate(module_files, start=1):
        item_id = f"module-{index}"
        manifest_items.append(
            f'<item id="{item_id}" href="module-{index:02d}.xhtml" '
            'media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{item_id}"/>')
    if has_assessment:
        manifest_items.append(
            '<item id="assessment" href="assessment.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine_items.append('<itemref idref="assessment"/>')

    manifest = "\n    ".join(manifest_items)
    spine = "\n    ".join(spine_items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{_escape(document.title)}</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>{_escape(PRODUCT_NAME)}</dc:creator>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine>
    {spine}
  </spine>
</package>
"""


def _nav_xhtml(document: CoursePackDocument, module_titles: list[str], has_assessment: bool) -> str:
    items = ['<li><a href="title-page.xhtml">Title page</a></li>']
    for index, title in enumerate(module_titles, start=1):
        items.append(f'<li><a href="module-{index:02d}.xhtml">{_escape(title)}</a></li>')
    if has_assessment:
        items.append('<li><a href="assessment.xhtml">Assessment</a></li>')
    toc = "\n      ".join(items)
    body = (
        '<nav epub:type="toc" id="toc">\n'
        f"<h1>{_escape(document.title)}</h1>\n"
        "<ol>\n"
        f"      {toc}\n"
        "</ol>\n"
        "</nav>\n"
    )
    return _xhtml_document("Table of contents", body)


def write_course_pack_epub(document: CoursePackDocument, path: Path) -> Path:
    """Write a valid EPUB 3 archive for a course-pack artifact.

    Structure: `mimetype` (first entry, stored uncompressed), a standard
    `META-INF/container.xml`, and an `OEBPS/` package with a title page, one
    XHTML page per module, an optional assessment page, `nav.xhtml`
    (EPUB 3 navigation document), `content.opf` (package document), and a
    minimal `style.css`.
    """
    if path.suffix.lower() != ".epub":
        raise ValueError("EPUB export path must end in .epub")

    path.parent.mkdir(parents=True, exist_ok=True)
    book_id = str(uuid.uuid4())

    module_titles = [module.title for module in document.modules]
    module_files = [f"module-{index:02d}.xhtml" for index in range(1, len(module_titles) + 1)]
    assessment_xhtml = _assessment_xhtml(document)

    with zipfile.ZipFile(path, "w") as archive:
        # The mimetype entry MUST be first and stored uncompressed per the
        # EPUB 3 OCF spec, so readers can sniff the format without inflating.
        archive.writestr(zipfile.ZipInfo("mimetype"), _MIMETYPE, zipfile.ZIP_STORED)

        archive.writestr("META-INF/container.xml", _CONTAINER_XML)
        archive.writestr("OEBPS/style.css", _STYLE_CSS)
        archive.writestr("OEBPS/title-page.xhtml", _title_page_xhtml(document))

        for index, module in enumerate(document.modules, start=1):
            archive.writestr(f"OEBPS/module-{index:02d}.xhtml", _module_xhtml(module, index))

        if assessment_xhtml is not None:
            archive.writestr("OEBPS/assessment.xhtml", assessment_xhtml)

        archive.writestr(
            "OEBPS/nav.xhtml",
            _nav_xhtml(document, module_titles, assessment_xhtml is not None),
        )
        archive.writestr(
            "OEBPS/content.opf",
            _opf(
                document,
                book_id=book_id,
                module_files=module_files,
                has_assessment=assessment_xhtml is not None,
            ),
        )

    return path


__all__ = ["write_course_pack_epub"]
