"""Text-flow PDF export for course-pack Studio artifacts.

v0.8.117 — Course packs already get an editable DOCX (`documents.py`) and a
reflowable EPUB 3 ebook (`epub.py`). This adds a third, print-ready format: a
text-flow PDF built with `reportlab`'s `platypus` layout engine, so a learner
or facilitator can open (or print) the course pack without an e-reader or
Office app.

Why `reportlab` platypus, no images: this is a long, reflowable text
document (title page, module/lesson headings, body copy, lists, code
blocks, an assessment, a citation appendix) — exactly what `platypus`'s
`SimpleDocTemplate` + `Paragraph`/`ListFlowable`/`Preformatted` flowables
are for. Code blocks always use the standard `Courier` base font, so
`Preformatted` stays dependency-light. See the v0.8.119 note below for how
body/heading text gets a font that can render more than WinAnsi.

Markdown handling: lesson/summary/exercise/explanation markdown is tokenized
with the same `markdown_it` (`MarkdownIt("commonmark")`) already used by
`epub.py`, then walked by a small, deterministic token-to-flowable converter
below — heading levels map to `Heading2`/`Heading3`, `strong`/`em` map to
reportlab's `<b>`/`<i>` mini-markup, bullet/ordered lists map to
`ListFlowable`, fenced/indented code maps to `Preformatted`. Every branch is
defensive: an unrecognized token is skipped rather than raised, so malformed
or unusual markdown can never break an export.

v0.8.119 — The standard PDF base fonts (Helvetica/Times/Courier) only cover
WinAnsi, so Cyrillic, Greek, and CJK text used to render as empty boxes.
Rather than bundle a TTF in this repo, `_resolve_fonts()` runs once per
process (cached behind the `_FONTS_RESOLVED` module flag, so repeated
`write_course_pack_pdf` calls never re-probe the filesystem or re-register)
and probes an ordered list of *system* TrueType files for a Latin+Cyrillic+
Greek face: an env override (`DEEPER_NOTEBOOK_PDF_FONT`), then macOS's
Arial Unicode / Arial, Windows's Arial, then Linux's DejaVu Sans —
registering the first file that exists (and parses) as `DNBody`, plus a
matching bold file as `DNBodyBold` when one is listed and exists. A file
that fails to parse is skipped in favor of the next candidate. When no
candidate resolves, body/heading text keeps using Helvetica/Helvetica-Bold
exactly as before. CJK is handled separately and unconditionally: reportlab
ships four `UnicodeCIDFont` definitions (`STSong-Light` for Simplified
Chinese, `MSung-Light` for Traditional Chinese, `HeiseiMin-W3` for
Japanese, `HYSMyeongJo-Medium` for Korean) that need no font *file* at all,
so all four are always registered. Per-paragraph text is then scanned
run-by-run for Hangul, Hiragana/Katakana, and Han script ranges, and each
such run is wrapped in a `<font name="...">` mini-markup tag pointing at
the matching CID font, so mixed Latin/CJK text renders correctly within a
single `Paragraph`; everything else renders in the resolved body font. No
TTF file is bundled in this repo — this export only ever reads font files
that are already installed on the host.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
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

# Body/heading font names. Start as the standard PDF base fonts and are
# swapped for a discovered system TTF by `_resolve_fonts()` (see the
# v0.8.119 docstring note above).
_BODY_FONT = "Helvetica"
_BODY_FONT_BOLD = "Helvetica-Bold"
_FONTS_RESOLVED = False

# CID font names, in the order they are registered. These ship inside
# reportlab itself (no font *file* required) and cover, in order,
# Simplified Chinese, Traditional Chinese, Japanese, and Korean.
_CID_FONT_NAMES = ("STSong-Light", "MSung-Light", "HeiseiMin-W3", "HYSMyeongJo-Medium")

_HAN_FONT = "STSong-Light"
_KANA_FONT = "HeiseiMin-W3"
_HANGUL_FONT = "HYSMyeongJo-Medium"

_HANGUL_RANGES = ((0xAC00, 0xD7AF), (0x1100, 0x11FF))
_KANA_RANGES = ((0x3040, 0x30FF),)
_HAN_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))


def _body_font_candidates() -> list[tuple[str, str | None]]:
    """Ordered (regular-path, bold-path-or-None) system TTF candidates.

    Read directly from `os.environ` (not `deeper_notebook.environment.
    resolve_env`, which would need a registry entry) so the override is a
    plain, zero-config escape hatch. None of these paths are read unless
    they exist on the host, and none are ever copied into this repo.
    """
    candidates: list[tuple[str, str | None]] = []
    env_font = os.environ.get("DEEPER_NOTEBOOK_PDF_FONT")
    if env_font:
        candidates.append((env_font, None))
    candidates.extend(
        [
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),
            ("/Library/Fonts/Arial Unicode.ttf", None),
            (
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            ),
            ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ),
            ("/usr/share/fonts/dejavu/DejaVuSans.ttf", None),
        ]
    )
    return candidates


def _register_body_font() -> None:
    """Register the first working candidate as `DNBody`/`DNBodyBold`.

    A corrupt or unsupported file raises inside `TTFont`/`registerFont`;
    that exception is swallowed here so resolution falls through to the
    next candidate instead of breaking the export.
    """
    global _BODY_FONT, _BODY_FONT_BOLD
    for regular_path, bold_path in _body_font_candidates():
        if not Path(regular_path).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("DNBody", regular_path))
        except Exception:
            continue
        _BODY_FONT = "DNBody"
        # v0.8.119 — when the chosen family ships no bold file (macOS's
        # "Arial Unicode.ttf" is the common case), headings use the REGULAR
        # body font rather than Helvetica-Bold. Losing the bold weight is a
        # cosmetic downgrade; keeping Helvetica-Bold would render every
        # Cyrillic/Greek heading as boxes, which is a correctness one.
        _BODY_FONT_BOLD = "DNBody"
        if bold_path and Path(bold_path).is_file():
            try:
                pdfmetrics.registerFont(TTFont("DNBodyBold", bold_path))
                _BODY_FONT_BOLD = "DNBodyBold"
            except Exception:
                pass
        break


def _register_cid_fonts() -> None:
    """Register the four CJK CID fonts. These need no font file, but each
    registration is still guarded so one failure can't skip the rest."""
    for name in _CID_FONT_NAMES:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(name))
        except Exception:
            continue


def _resolve_fonts() -> None:
    """Resolve and register export fonts once per process.

    Cached behind `_FONTS_RESOLVED`: a second (or later) call is a no-op, so
    repeated `write_course_pack_pdf` calls never re-probe the filesystem or
    re-register fonts with reportlab.
    """
    global _FONTS_RESOLVED
    if _FONTS_RESOLVED:
        return
    _register_body_font()
    _register_cid_fonts()
    for style_name in ("Title", "Heading1", "Heading2", "Heading3"):
        _STYLES[style_name].fontName = _BODY_FONT_BOLD
    _STYLES["BodyText"].fontName = _BODY_FONT
    _CITATION_STYLE.fontName = _BODY_FONT
    _FONTS_RESOLVED = True


def _escape(text: str) -> str:
    """Escape text for reportlab's mini-markup (a small XML-like subset)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _script_font_for_char(char: str) -> str | None:
    """Return the CID font name for `char`'s script, or None for everything
    that should render in the resolved body font."""
    code_point = ord(char)
    for low, high in _HANGUL_RANGES:
        if low <= code_point <= high:
            return _HANGUL_FONT
    for low, high in _KANA_RANGES:
        if low <= code_point <= high:
            return _KANA_FONT
    for low, high in _HAN_RANGES:
        if low <= code_point <= high:
            return _HAN_FONT
    return None


def _wrap_script_runs(text: str) -> str:
    """Escape `text` and wrap Hangul/Kana/Han runs in CID-font mini-markup.

    Groups the string into contiguous same-script runs so a mixed line like
    "Hello 世界" renders both halves correctly in one `Paragraph`: the Latin
    run is left in the paragraph style's own (resolved body) font, and the
    CJK run is wrapped in `<font name="...">` pointing at the matching
    `UnicodeCIDFont` (see `_resolve_fonts`). Each run is escaped only after
    grouping, so `&`/`<`/`>` are always escaped before a `<font>` tag is
    inserted around them.
    """
    if not text:
        return ""
    parts: list[str] = []
    for font_name, run in itertools.groupby(text, key=_script_font_for_char):
        escaped = _escape("".join(run))
        if font_name:
            parts.append(f'<font name="{font_name}">{escaped}</font>')
        else:
            parts.append(escaped)
    return "".join(parts)


def _inline_markup(children: list[Any] | None) -> str:
    """Render markdown-it inline children to reportlab paragraph markup.

    Unknown inline token types (links, images, HTML, …) fall back to their
    plain text content, or are skipped when they carry none — this never
    raises on unexpected input.
    """
    parts: list[str] = []
    for token in children or []:
        if token.type == "text":
            parts.append(_wrap_script_runs(token.content))
        elif token.type == "code_inline":
            parts.append(f'<font face="Courier">{_escape(token.content)}</font>')
        elif token.type in ("softbreak", "hardbreak"):
            parts.append("<br/>")
        elif token.type.endswith("_open") and token.tag in _INLINE_TAG_MAP:
            parts.append(f"<{_INLINE_TAG_MAP[token.tag]}>")
        elif token.type.endswith("_close") and token.tag in _INLINE_TAG_MAP:
            parts.append(f"</{_INLINE_TAG_MAP[token.tag]}>")
        elif token.content:
            parts.append(_wrap_script_runs(token.content))
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
        return [Paragraph(_wrap_script_runs(text), _STYLES["BodyText"])]
    return _convert_tokens(tokens)


def _lesson_flowables(lesson) -> list[Any]:
    flowables: list[Any] = [Paragraph(_wrap_script_runs(lesson.title), _STYLES["Heading2"])]
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
        flowables.append(Paragraph(f"Sources {_wrap_script_runs(markers)}", _CITATION_STYLE))
    flowables.append(Spacer(1, 0.15 * inch))
    return flowables


def _module_flowables(module) -> list[Any]:
    flowables: list[Any] = [Paragraph(_wrap_script_runs(module.title), _STYLES["Heading1"])]
    if module.summary:
        flowables.extend(_render_markdown_flowables(module.summary))
    for lesson in module.lessons:
        flowables.extend(_lesson_flowables(lesson))
    return flowables


def _title_page_flowables(document: CoursePackDocument) -> list[Any]:
    flowables: list[Any] = [
        Paragraph(_wrap_script_runs(document.title), _STYLES["Title"]),
        Spacer(1, 0.2 * inch),
        Paragraph(
            f"<b>Audience:</b> {_wrap_script_runs(document.audience)}", _STYLES["BodyText"]
        ),
    ]
    if document.learning_outcomes:
        flowables.append(Paragraph("Learning outcomes", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_wrap_script_runs(outcome), _STYLES["BodyText"]))
            for outcome in document.learning_outcomes
        ]
        flowables.append(ListFlowable(items, bulletType="bullet"))
    if document.prerequisites:
        flowables.append(Paragraph("Prerequisites", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_wrap_script_runs(item), _STYLES["BodyText"]))
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
        flowables.append(Paragraph(_wrap_script_runs(question.prompt), _STYLES["Heading2"]))
        items = []
        for option in question.options:
            marker = " (correct)" if option.id == question.correct_option_id else ""
            items.append(
                ListItem(
                    Paragraph(f"{_wrap_script_runs(option.text)}{marker}", _STYLES["BodyText"])
                )
            )
        flowables.append(ListFlowable(items, bulletType="bullet"))
        if question.explanation:
            flowables.extend(_render_markdown_flowables(question.explanation))
        if question.citations:
            all_citations.extend(question.citations)
            markers = " ".join(dict.fromkeys(question.citations))
            flowables.append(
                Paragraph(f"Sources {_wrap_script_runs(markers)}", _CITATION_STYLE)
            )
        flowables.append(Spacer(1, 0.15 * inch))
    if all_citations:
        flowables.append(Paragraph("Citation appendix", _STYLES["Heading2"]))
        items = [
            ListItem(Paragraph(_wrap_script_runs(marker), _STYLES["BodyText"]))
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
                f"<b>{_wrap_script_runs(marker)}</b> — Stored source marker; review in the "
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

    _resolve_fonts()

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
