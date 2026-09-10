"""Text-flow PDF export for course-pack Studio artifacts.

v0.8.117 — Course packs already get an editable DOCX (`documents.py`) and a
reflowable EPUB 3 ebook (`epub.py`). This adds a third, print-ready format: a
text-flow PDF built with `reportlab`'s `platypus` layout engine, so a learner
or facilitator can open (or print) the course pack without an e-reader or
Office app.

Why `reportlab` platypus, no images, no custom fonts: this is a long,
reflowable text document (title page, module/lesson headings, body copy,
lists, code blocks, an assessment, a citation appendix) — exactly what
`platypus`'s `SimpleDocTemplate` + `Paragraph`/`ListFlowable`/`Preformatted`
flowables are for. Using only the standard PDF base fonts (Helvetica/
Times/Courier, reportlab's defaults) avoids registering TTF files, keeping
this export as dependency-light as `epub.py` and `documents.py`.

Markdown handling: lesson/summary/exercise/explanation markdown is tokenized
with the same `markdown_it` (`MarkdownIt("commonmark")`) already used by
`epub.py`, then walked by a small, deterministic token-to-flowable converter
below — heading levels map to `Heading2`/`Heading3`, `strong`/`em` map to
reportlab's `<b>`/`<i>` mini-markup, bullet/ordered lists map to
`ListFlowable`, fenced/indented code maps to `Preformatted`. Every branch is
defensive: an unrecognized token is skipped rather than raised, so malformed
or unusual markdown can never break an export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

from deeper_notebook.identity import PRODUCT_NAME
from deeper_notebook.studio.schemas import CoursePackDocument

_MD = MarkdownIt("commonmark")

_STYLES = getSampleStyleSheet()
_CITATION_STYLE = ParagraphStyle(
    "EvidenceCitation",
    parent=_STYLES["BodyText"],
    fontSize=8,
    textColor=colors.HexColor("#555555"),
)

_INLINE_TAG_MAP = {"strong": "b", "em": "i"}


def _escape(text: str) -> str:
    """Escape text for reportlab's mini-markup (a small XML-like subset)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_markup(children: list[Any] | None) -> str:
    """Render markdown-it inline children to reportlab paragraph markup.

    Unknown inline token types (links, images, HTML, …) fall back to their
    plain text content, or are skipped when they carry none — this never
    raises on unexpected input.
    """
    parts: list[str] = []
    for token in children or []:
        if token.type == "text":
            parts.append(_escape(token.content))
        elif token.type == "code_inline":
            parts.append(f'<font face="Courier">{_escape(token.content)}</font>')
        elif token.type in ("softbreak", "hardbreak"):
            parts.append("<br/>")
        elif token.type.endswith("_open") and token.tag in _INLINE_TAG_MAP:
            parts.append(f"<{_INLINE_TAG_MAP[token.tag]}>")
        elif token.type.endswith("_close") and token.tag in _INLINE_TAG_MAP:
            parts.append(f"</{_INLINE_TAG_MAP[token.tag]}>")
        elif token.content:
            parts.append(_escape(token.content))
    return "".join(parts)


def _heading_level(tag: str) -> int:
    try:
        return int(tag[1:])
    except (ValueError, IndexError):
        return 2


def _skip_to_close(tokens: list[Any], index: int, close_type: str) -> int:
    """Return the index just past the token closing `tokens[index]`.

    Matches on the same nesting `level` rather than a fixed offset, so it
    stays correct regardless of what (or how much) sits between an open and
    close token. Falls back to the end of the list if no close is found,
    which guarantees the caller's loop always makes progress.
    """
    level = tokens[index].level
    position = index + 1
    total = len(tokens)
    while position < total and not (
        tokens[position].type == close_type and tokens[position].level == level
    ):
        position += 1
    return min(position + 1, total)


def _next_inline_markup(tokens: list[Any], index: int) -> str:
    if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
        return _inline_markup(tokens[index + 1].children)
    return ""


def _convert_list_items(tokens: list[Any]) -> list[ListItem]:
    items: list[ListItem] = []
    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        if token.type == "list_item_open":
            end = _skip_to_close(tokens, index, "list_item_close")
            item_flowables = _convert_tokens(tokens[index + 1 : end - 1])
            if not item_flowables:
                item_flowables = [Paragraph("", _STYLES["BodyText"])]
            items.append(ListItem(item_flowables))
            index = end
        else:
            index += 1
    return items


def _convert_tokens(tokens: list[Any]) -> list[Any]:
    """Walk a flat markdown-it token stream into reportlab flowables.

    Every branch is wrapped so a single malformed token can never abort the
    whole conversion — it is skipped and the walk continues.
    """
    flowables: list[Any] = []
    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        try:
            if token.type == "heading_open":
                level = _heading_level(token.tag)
                style = _STYLES["Heading2"] if level <= 2 else _STYLES["Heading3"]
                text = _next_inline_markup(tokens, index)
                if text:
                    flowables.append(Paragraph(text, style))
                index = _skip_to_close(tokens, index, "heading_close")
            elif token.type == "paragraph_open":
                text = _next_inline_markup(tokens, index)
                if text:
                    flowables.append(Paragraph(text, _STYLES["BodyText"]))
                index = _skip_to_close(tokens, index, "paragraph_close")
            elif token.type in ("bullet_list_open", "ordered_list_open"):
                ordered = token.type == "ordered_list_open"
                close_type = "ordered_list_close" if ordered else "bullet_list_close"
                end = _skip_to_close(tokens, index, close_type)
                items = _convert_list_items(tokens[index + 1 : end - 1])
                if items:
                    flowables.append(
                        ListFlowable(items, bulletType="1" if ordered else "bullet")
                    )
                index = end
            elif token.type in ("fence", "code_block"):
                code_text = (token.content or "").rstrip("\n")
                flowables.append(Preformatted(code_text, _STYLES["Code"]))
                index += 1
            elif token.type == "blockquote_open":
                end = _skip_to_close(tokens, index, "blockquote_close")
                flowables.extend(_convert_tokens(tokens[index + 1 : end - 1]))
                index = end
            elif token.type == "hr":
                flowables.append(Spacer(1, 0.15 * inch))
                index += 1
            else:
                # Any other token (html_block, stray inline, table
                # fragments, …) is not a Studio-authored shape we expect;
                # skip it rather than guess a rendering.
                index += 1
        except Exception:
            index += 1
    return flowables


def _render_markdown_flowables(text: str) -> list[Any]:
    if not (text or "").strip():
        return []
    try:
        tokens = _MD.parse(text)
    except Exception:
        return [Paragraph(_escape(text), _STYLES["BodyText"])]
    return _convert_tokens(tokens)


def _lesson_flowables(lesson) -> list[Any]:
    flowables: list[Any] = [Paragraph(_escape(lesson.title), _STYLES["Heading2"])]
    if lesson.duration_minutes:
        flowables.append(
            Paragraph(
                f"<i>Estimated duration: {lesson.duration_minutes} minutes</i>",
                _STYLES["BodyText"],
            )
        )
    flowables.extend(_render_markdown_flowables(lesson.content))
    if lesson.exercise:
        flowables.append(Paragraph("Exercise", _STYLES["Heading3"]))
        flowables.extend(_render_markdown_flowables(lesson.exercise))
    if lesson.facilitator_notes:
        flowables.append(Paragraph("Facilitator notes", _STYLES["Heading3"]))
        flowables.extend(_render_markdown_flowables(lesson.facilitator_notes))
    if lesson.citations:
        markers = " ".join(dict.fromkeys(lesson.citations))
        flowables.append(Paragraph(f"Sources {_escape(markers)}", _CITATION_STYLE))
    flowables.append(Spacer(1, 0.15 * inch))
    return flowables


def _module_flowables(module) -> list[Any]:
    flowables: list[Any] = [Paragraph(_escape(module.title), _STYLES["Heading1"])]
    if module.summary:
        flowables.extend(_render_markdown_flowables(module.summary))
    for lesson in module.lessons:
        flowables.extend(_lesson_flowables(lesson))
    return flowables


def _title_page_flowables(document: CoursePackDocument) -> list[Any]:
    flowables: list[Any] = [
        Paragraph(_escape(document.title), _STYLES["Title"]),
        Spacer(1, 0.2 * inch),
        Paragraph(f"<b>Audience:</b> {_escape(document.audience)}", _STYLES["BodyText"]),
    ]
    if document.learning_outcomes:
        flowables.append(Paragraph("Learning outcomes", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_escape(outcome), _STYLES["BodyText"]))
            for outcome in document.learning_outcomes
        ]
        flowables.append(ListFlowable(items, bulletType="bullet"))
    if document.prerequisites:
        flowables.append(Paragraph("Prerequisites", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_escape(item), _STYLES["BodyText"]))
            for item in document.prerequisites
        ]
        flowables.append(ListFlowable(items, bulletType="bullet"))
    flowables.append(Spacer(1, 0.3 * inch))
    flowables.append(
        Paragraph(f"<i>{_escape(PRODUCT_NAME)} / Evidence Studio</i>", _STYLES["BodyText"])
    )
    return flowables


def _assessment_flowables(document: CoursePackDocument) -> list[Any] | None:
    if not document.final_assessment:
        return None
    flowables: list[Any] = [Paragraph("Assessment", _STYLES["Heading1"])]
    all_citations: list[str] = []
    for question in document.final_assessment:
        flowables.append(Paragraph(_escape(question.prompt), _STYLES["Heading2"]))
        items = []
        for option in question.options:
            marker = " (correct)" if option.id == question.correct_option_id else ""
            items.append(
                ListItem(Paragraph(f"{_escape(option.text)}{marker}", _STYLES["BodyText"]))
            )
        flowables.append(ListFlowable(items, bulletType="bullet"))
        if question.explanation:
            flowables.extend(_render_markdown_flowables(question.explanation))
        if question.citations:
            all_citations.extend(question.citations)
            markers = " ".join(dict.fromkeys(question.citations))
            flowables.append(Paragraph(f"Sources {_escape(markers)}", _CITATION_STYLE))
        flowables.append(Spacer(1, 0.15 * inch))
    if all_citations:
        flowables.append(Paragraph("Citation appendix", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_escape(marker), _STYLES["BodyText"]))
            for marker in dict.fromkeys(all_citations)
        ]
        flowables.append(ListFlowable(items, bulletType="bullet"))
    return flowables


def _sources_appendix_flowables(citations: list[str]) -> list[Any] | None:
    unique_markers = list(dict.fromkeys(citations))
    if not unique_markers:
        return None
    flowables: list[Any] = [Paragraph("Sources", _STYLES["Heading1"])]
    items = [
        ListItem(
            Paragraph(
                f"<b>{_escape(marker)}</b> — Stored source marker; review in the "
                "notebook for source context.",
                _STYLES["BodyText"],
            )
        )
        for marker in unique_markers
    ]
    flowables.append(ListFlowable(items, bulletType="bullet"))
    return flowables


def write_course_pack_pdf(document: CoursePackDocument, path: Path) -> Path:
    """Write a text-flow PDF for a course-pack artifact via reportlab platypus.

    Structure: a title page, one section per module (module heading, then
    each lesson's heading + body), an optional assessment section, and an
    optional "Sources" appendix listing every citation marker used across
    the document. Long content paginates automatically through platypus.
    """
    if path.suffix.lower() != ".pdf":
        raise ValueError("PDF export path must end in .pdf")

    path.parent.mkdir(parents=True, exist_ok=True)

    story: list[Any] = list(_title_page_flowables(document))
    story.append(PageBreak())

    all_citations: list[str] = []
    for index, module in enumerate(document.modules):
        if index > 0:
            story.append(PageBreak())
        story.extend(_module_flowables(module))
        for lesson in module.lessons:
            all_citations.extend(lesson.citations)

    assessment_flowables = _assessment_flowables(document)
    if assessment_flowables:
        story.append(PageBreak())
        story.extend(assessment_flowables)
        for question in document.final_assessment:
            all_citations.extend(question.citations)

    sources_flowables = _sources_appendix_flowables(all_citations)
    if sources_flowables:
        story.append(PageBreak())
        story.extend(sources_flowables)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        title=document.title,
        author=PRODUCT_NAME,
    )
    doc.build(story)
    return path


__all__ = ["write_course_pack_pdf"]
