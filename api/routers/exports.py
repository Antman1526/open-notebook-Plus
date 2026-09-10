"""ONP v0.7.90 — Notebook + note export to the host filesystem.

The Studio v0.7.89 feature now produces multi-page notebooks (overview
+ N per-topic pages, each with inline AI suggestions). Users want to
save that content out of the app and onto disk where they can version
it, share it, attach it to other tools (paperless-gpt, Logseq, etc.).

This module exposes two POST endpoints:

  * /notebooks/{id}/export — write the notebook to a folder (one .md
    per note) or a single .zip file
  * /notes/{id}/export — write one note to a single .md file

Both rely on the v0.7.90 filesystem router for path-safety validation
(`_resolve_and_validate`). The user picks the destination via the
filesystem-listing endpoints exposed at /fs/*.

Design choices:

  * Markdown is the universal export format. Every note in this app is
    Markdown-native; HTML/PDF rendering is out of scope here.
  * Folder layout (when format="folder"):
        {destination}/
            00-overview.md            # if a "📋 00 · …" overview note exists
            01-{slug}.md              # one per page note, in title-order
            02-{slug}.md
            …
            sources/                  # only if include_sources=True
                {source-id}.md        # source.full_text dumped as markdown
            manifest.json             # notebook + per-note metadata
  * Zip layout (when format="zip"):
        Same as folder but rolled into a single .zip file.
  * Filenames are slugified — the title `📄 01 · Architecture` becomes
    `01-architecture.md`. The slug logic is the SAME you'll find in
    Logseq / Obsidian / paperless: lowercase, ASCII, hyphenated.
  * The endpoint is idempotent under overwrite=True — re-running just
    rewrites the files. With overwrite=False the call 409s if any
    target file already exists.
  * Per-file writes are O(N); for a notebook with hundreds of notes,
    we stream the write rather than building the whole payload in
    memory.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.routers.filesystem import _resolve_and_validate
from api.utils.iso import iso  # v0.7.182 — Safari-safe datetime serialization
from deeper_notebook.domain.notebook import Note, Notebook, Source
from deeper_notebook.exceptions import InvalidInputError, NotFoundError

router = APIRouter(tags=["exports"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NotebookExportRequest(BaseModel):
    destination: str = Field(
        ...,
        description=(
            "Absolute path: a directory (for format='folder' / 'html_folder') "
            "or a file path (for format='zip' / 'html_zip'). User's home is "
            "auto-expanded."
        ),
    )
    # v0.7.97 — html_folder / html_zip render each note's markdown to HTML
    # via markdown-it-py (already a transitive dep). Useful for sharing
    # notebooks with non-markdown-aware tools (email, browsers, Drive).
    # v0.7.111 — combined_md / combined_html concatenate every page into
    # a single file. Better for read-only sharing (email attachment,
    # Drive upload, print-to-PDF) where the user wants one artifact
    # rather than a folder.
    format: Literal[
        "folder",
        "zip",
        "html_folder",
        "html_zip",
        "combined_md",
        "combined_html",
        "obsidian_folder",
        "obsidian_zip",
    ] = "folder"
    include_sources: bool = Field(
        False,
        description="Include each Source's full_text as sources/{source-id}.md",
    )
    overwrite: bool = Field(
        False,
        description="Overwrite existing files / re-create existing folders.",
    )
    # v0.7.98 — Zip compression algorithm. "deflated" is the safe default
    # (gzip-equivalent, good ratio, ~all readers support it). "stored" =
    # no compression (fastest, biggest file — useful when the zip will
    # itself be compressed downstream). "bzip2" and "lzma" trade speed
    # for smaller archives. Ignored when format isn't a zip variant.
    compression: Literal["deflated", "stored", "bzip2", "lzma"] = Field(
        "deflated",
        description=(
            "Zip compression algorithm. Only meaningful when format=zip "
            "or format=html_zip."
        ),
    )


class NoteExportRequest(BaseModel):
    destination: str = Field(
        ...,
        description=(
            "Absolute path of the .md file to write. Parent directory must "
            "already exist (use /api/fs/mkdir first if needed)."
        ),
    )
    overwrite: bool = False


class ExportFileEntry(BaseModel):
    relative_path: str
    bytes: int


class ExportResponse(BaseModel):
    destination: str  # final absolute path written
    format: str  # "folder" | "zip" | "single"
    file_count: int  # number of files written (1 for note)
    total_bytes: int
    files: list[ExportFileEntry]  # per-file breakdown (capped at 100)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Slug regex: keep ASCII alphanumeric + hyphens; everything else → hyphen.
_NON_SLUG = re.compile(r"[^a-z0-9]+")
# v0.7.90 — Strip leading numeric prefixes from titles before slugifying so
# v0.7.89 page titles like "📄 01 · Architecture" don't yield "01-architecture"
# and then get re-prefixed to "01-01-architecture.md". We catch:
#   - "NN " or "NN-" or "NN·" optionally followed by separators
#   - Up to 3 digits to allow page 100+ on huge notebooks
_LEADING_INDEX = re.compile(r"^[0-9]{1,3}[-\s·.]+")


def _slugify(text: str, *, fallback: str = "untitled") -> str:
    """Filesystem-safe slug. Strips emoji + non-ASCII via NFKD + ASCII
    encode. Matches the slug style used by Logseq / Obsidian so users
    can drop the exported folder into either without renaming.

    v0.7.90 — Strips leading numeric prefixes (`"01-"`, `"02 "`, etc.) so
    that pre-numbered titles don't end up double-prefixed when we wrap
    them in our own page-index. Without this, a v0.7.89 page titled
    `📄 01 · Architecture` would become `01-01-architecture.md`.
    """
    if not text:
        return fallback
    # Normalize unicode + drop non-ASCII (emoji, accents).
    normalized = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = _NON_SLUG.sub("-", normalized.lower()).strip("-")
    # Repeatedly strip leading numeric prefixes (handles "01-01-arch" cases).
    while True:
        stripped = _LEADING_INDEX.sub("", slug).lstrip("-")
        if stripped == slug:
            break
        slug = stripped
    if not slug:
        return fallback
    # Cap to a reasonable filename length on macOS/Linux (255 bytes hard cap;
    # leave room for prefix + .md suffix).
    return slug[:80]


def _notebook_record_id_part(notebook_id: str) -> str:
    """Strip the 'notebook:' prefix from a SurrealDB record ID so the
    suffix is safe to use in filenames."""
    return notebook_id.split(":", 1)[1] if ":" in notebook_id else notebook_id


def _build_overview_path(note: Note) -> Optional[str]:
    """If this note looks like a v0.7.89 Overview note, return the
    canonical filename for it. Otherwise None and the caller will
    treat it as a regular page."""
    if not note.title:
        return None
    # v0.7.89 prefixes overview notes with "📋 00 · …"
    if note.title.startswith("📋 00") or "Overview" in note.title:
        return "00-overview.md"
    return None


def _note_filename(note: Note, *, index: int) -> str:
    """Generate a slugified filename for a non-overview note. Prefixed
    with a 2-digit index so the folder sorts in render order."""
    title = note.title or f"note-{index:02d}"
    return f"{index:02d}-{_slugify(title)}.md"


def _render_note_content(note: Note) -> str:
    """Build the full markdown for a single note: a frontmatter-style
    header with timestamps + note-type, then the body."""
    header_lines = [
        "---",
        f"title: {note.title or '(untitled)'}",
        f"type: {note.note_type or 'human'}",
    ]
    if getattr(note, "created", None):
        header_lines.append(f"created: {note.created}")
    if getattr(note, "updated", None):
        header_lines.append(f"updated: {note.updated}")
    if getattr(note, "id", None):
        header_lines.append(f"id: {note.id}")
    header_lines.append("---")
    header_lines.append("")
    body = note.content or "(no content)"
    return "\n".join(header_lines) + "\n" + body


def _render_source_content(source: Source) -> str:
    """Build markdown for a single Source — its title + full_text, with
    a metadata header pointing at the original asset."""
    asset_path = (
        source.asset.file_path if source.asset and source.asset.file_path else None
    )
    asset_url = source.asset.url if source.asset and source.asset.url else None
    header_lines = [
        "---",
        f"title: {source.title or '(untitled source)'}",
        f"source_id: {source.id}",
    ]
    if asset_path:
        header_lines.append(f"original_file: {asset_path}")
    if asset_url:
        header_lines.append(f"original_url: {asset_url}")
    header_lines.append("---")
    header_lines.append("")
    body = source.full_text or "(no extracted text)"
    return "\n".join(header_lines) + "\n" + body


def _render_obsidian_note_content(note: Note) -> str:
    """Build markdown for a single note formatted with Obsidian YAML frontmatter and wikilinks."""
    title = (note.title or "Untitled").strip()
    header_lines = [
        "---",
        f'title: "{title}"',
        f"type: {note.note_type or 'human'}",
        "tags:",
        "  - deeper-notebook",
        "  - note",
        "aliases:",
        f'  - "{title}"',
    ]
    if getattr(note, "created", None):
        header_lines.append(f"created: {note.created}")
    if getattr(note, "updated", None):
        header_lines.append(f"updated: {note.updated}")
    if getattr(note, "id", None):
        header_lines.append(f'id: "{note.id}"')
    header_lines.append("---")
    header_lines.append("")

    raw_body = note.content or "(no content)"
    body = re.sub(
        r"\[source:([a-zA-Z0-9_\-:]+)\]",
        r"[[sources/\1|\1]]",
        raw_body,
    )
    return "\n".join(header_lines) + "\n" + body


def _render_obsidian_source_content(source: Source) -> str:
    """Build markdown for a Source formatted with Obsidian YAML frontmatter."""
    title = (source.title or "Untitled Source").strip()
    sid = _notebook_record_id_part(str(source.id))
    header_lines = [
        "---",
        f'title: "{title}"',
        f'source_id: "{sid}"',
        "tags:",
        "  - deeper-notebook",
        "  - source",
        "aliases:",
        f'  - "{title}"',
    ]
    asset_path = (
        source.asset.file_path if source.asset and source.asset.file_path else None
    )
    asset_url = source.asset.url if source.asset and source.asset.url else None
    if asset_path:
        header_lines.append(f'original_file: "{asset_path}"')
    if asset_url:
        header_lines.append(f'original_url: "{asset_url}"')
    header_lines.append("---")
    header_lines.append("")
    body = source.full_text or "(no extracted text)"
    return "\n".join(header_lines) + "\n" + body


def _render_obsidian_index(
    notebook: Notebook,
    plan: list[tuple[str, Note]],
    sources: list[Source],
) -> str:
    """Build the Obsidian Map of Content (MOC) index note."""
    nb_title = (notebook.name or "Untitled Notebook").strip()
    desc = notebook.description or ""
    lines = [
        "---",
        f'title: "{nb_title}"',
        "tags:",
        "  - deeper-notebook",
        "  - moc",
        "  - index",
        "aliases:",
        f'  - "{nb_title}"',
        "---",
        "",
        f"# 📚 {nb_title}",
        "",
    ]
    if desc:
        lines.extend([f"> {desc}", ""])
    lines.append("## 📝 Notes\n")
    for filename, note in plan:
        stem = Path(filename).stem
        title = note.title or stem
        lines.append(f"- [[{stem}|{title}]]")
    lines.append("")
    if sources:
        lines.append("## 📁 Sources\n")
        for s in sources:
            sid = _notebook_record_id_part(str(s.id))
            stitle = s.title or sid
            lines.append(f"- [[sources/{sid}|{stitle}]]")
        lines.append("")
    return "\n".join(lines)



# v0.7.97 — Markdown → HTML conversion via markdown-it-py (already a
# transitive dep through langchain_core). Lazy-import so non-html exports
# don't pay the import cost. Renders GFM-flavored markdown (tables,
# strikethrough, autolinks) which matches what the chat LLM produces.
def _markdown_to_html(md_text: str) -> str:
    """Render a markdown string to HTML. No external network calls.
    Returns the raw HTML <body>-fragment, not a full document.

    v0.7.97 — Uses the "commonmark" preset + table/strikethrough enabled.
    We avoid the "gfm-like" preset because it auto-enables linkify which
    needs the linkify-it-py package as a runtime dep; commonmark is in
    markdown-it-py's stdlib so this works with zero new dependencies.

    v0.7.117 — 🔒 XSS hardening. `html=False` (the constructor option,
    NOT the .disable("html_block") rule — those are separate) makes
    markdown-it ESCAPE raw HTML tags inside markdown source. Without
    this, `<script>alert(1)</script>` in a note's content would be
    passed through verbatim. That's an XSS vector when the combined_html
    export is shared by email / Drive / link — the recipient opens the
    file and executes the author's script. Self-hosted single-user
    deployments are mostly safe (the author IS the user) but multi-
    user or "share with a colleague" workflows are exposed.
    We also `.disable("html_inline")` to catch inline `<…>` patterns
    that escape-on-block-render would miss. Net result: `<script>`,
    `<img onerror=…>`, etc. are rendered as literal text, not HTML.

    v0.7.118 — 🔒 Add `rel="noopener noreferrer"` to all external
    `<a>` tags via a custom token renderer. Two reasons:
      1. Defense-in-depth tabnabbing protection if the user (or their
         email client) opens the export with `target="_blank"`-like
         behavior.
      2. `noreferrer` prevents the recipient's browser leaking the
         `Referer:` header on click — important when the export is a
         local file://, since the referer would expose the local
         filesystem path.
    Internal anchor links (`#fragment`) keep their default rendering.
    """
    from markdown_it import MarkdownIt

    md = (
        MarkdownIt("commonmark", {"html": False})
        .enable("table")
        .enable("strikethrough")
        .disable("html_inline")
        .disable("html_block")
    )

    # Custom token renderer for link_open: append rel="noopener
    # noreferrer" to any link whose href looks external.
    default_link_open = md.renderer.rules.get(
        "link_open",
        lambda tokens, idx, opts, env: md.renderer.renderToken(tokens, idx, opts, env),
    )

    def _link_open_with_rel(tokens, idx, opts, env):
        token = tokens[idx]
        href = token.attrGet("href") or ""
        # Only add rel for external-looking links. Skip pure
        # fragment-internal links (#section) and same-page relative
        # paths without a scheme.
        is_external = (
            href.startswith("http://")
            or href.startswith("https://")
            or href.startswith("mailto:")
            or href.startswith("ftp://")
        )
        if is_external:
            existing_rel = token.attrGet("rel") or ""
            # Avoid duplicating rel values if already set
            rel_tokens = set(existing_rel.split())
            rel_tokens.update(["noopener", "noreferrer"])
            token.attrSet("rel", " ".join(sorted(rel_tokens)))
        return default_link_open(tokens, idx, opts, env)

    md.renderer.rules["link_open"] = _link_open_with_rel
    return md.render(md_text)


# v0.7.97 — Wrap rendered HTML in a minimal HTML5 document with a clean
# default stylesheet so the user can double-click the file and read it
# without a build step. CSS scoped to common readability defaults; no
# external resources so the file works offline.
_HTML_PAGE_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --fg: #1a1a1a;
      --bg: #fdfdfd;
      --muted: #6b7280;
      --accent: #2563eb;
      --code-bg: #f3f4f6;
      --border: #e5e7eb;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --fg: #e5e7eb; --bg: #0f172a; --muted: #9ca3af;
        --accent: #60a5fa; --code-bg: #1e293b; --border: #334155;
      }}
    }}
    body {{
      max-width: 760px; margin: 2rem auto; padding: 0 1.5rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
                   "Helvetica Neue", Arial, sans-serif;
      line-height: 1.65; color: var(--fg); background: var(--bg);
    }}
    h1, h2, h3 {{ line-height: 1.25; margin-top: 2rem; }}
    h1 {{ border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }}
    a {{ color: var(--accent); }}
    code {{
      background: var(--code-bg); padding: 0.15em 0.4em; border-radius: 3px;
      font-size: 0.92em;
    }}
    pre {{
      background: var(--code-bg); padding: 1rem; border-radius: 6px;
      overflow-x: auto;
    }}
    pre code {{ background: transparent; padding: 0; }}
    blockquote {{
      border-left: 4px solid var(--border); margin: 1rem 0;
      padding: 0.4rem 1rem; color: var(--muted);
    }}
    table {{ border-collapse: collapse; margin: 1rem 0; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem 0.8rem; }}
    th {{ background: var(--code-bg); }}
    .onp-frontmatter {{
      font-size: 0.85rem; color: var(--muted);
      border: 1px solid var(--border); border-radius: 4px;
      padding: 0.6rem 1rem; margin-bottom: 1.5rem;
    }}
    .onp-frontmatter dt {{ font-weight: 600; display: inline; }}
    .onp-frontmatter dd {{ display: inline; margin-left: 0.3rem; }}
    .onp-frontmatter dl {{ margin: 0; }}
    .onp-frontmatter dl > * {{ display: block; }}
  </style>
</head>
<body>
{frontmatter_block}
{body_html}
</body>
</html>
"""


def _render_note_as_html(note: Note) -> str:
    """Build a full HTML5 document for a single note. Frontmatter is
    rendered as a styled metadata block at the top so the user sees the
    same info as the .md export, just in browser-readable form."""
    title = note.title or "(untitled)"
    meta_pairs: list[tuple[str, str]] = []
    meta_pairs.append(("type", note.note_type or "human"))
    # v0.7.182 — iso() for Safari new Date() compat in the exported
    # HTML (`<dd>{date}</dd>` is purely visual but a copy-pasted ISO
    # string into a downstream JS pipeline would re-trip the original
    # bug). The `or ""` guards against iso(None) when the attribute
    # is somehow present but falsy.
    if getattr(note, "created", None):
        meta_pairs.append(("created", iso(note.created) or ""))
    if getattr(note, "updated", None):
        meta_pairs.append(("updated", iso(note.updated) or ""))
    if getattr(note, "id", None):
        meta_pairs.append(("id", str(note.id)))
    fm_html = '<div class="onp-frontmatter"><dl>'
    for k, v in meta_pairs:
        fm_html += f"<dt>{k}:</dt><dd>{_html_escape(v)}</dd>"
    fm_html += "</dl></div>"
    body_html = _markdown_to_html(note.content or "(no content)")
    return _HTML_PAGE_TEMPLATE.format(
        title=_html_escape(title),
        frontmatter_block=fm_html,
        body_html=body_html,
    )


def _render_source_as_html(source: Source) -> str:
    title = source.title or "(untitled source)"
    asset_path = (
        source.asset.file_path if source.asset and source.asset.file_path else None
    )
    asset_url = source.asset.url if source.asset and source.asset.url else None
    meta_pairs: list[tuple[str, str]] = [("source_id", str(source.id))]
    if asset_path:
        meta_pairs.append(("original_file", asset_path))
    if asset_url:
        meta_pairs.append(("original_url", asset_url))
    fm_html = '<div class="onp-frontmatter"><dl>'
    for k, v in meta_pairs:
        fm_html += f"<dt>{k}:</dt><dd>{_html_escape(v)}</dd>"
    fm_html += "</dl></div>"
    body_html = _markdown_to_html(source.full_text or "(no extracted text)")
    return _HTML_PAGE_TEMPLATE.format(
        title=_html_escape(title),
        frontmatter_block=fm_html,
        body_html=body_html,
    )


# Minimal HTML-escape for the few attribute/text positions we emit
# directly. We don't escape body content because markdown-it does that.
def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# v0.7.98 — Zip compression algorithm mapping. Validated by Pydantic
# at request time, so this dict only needs the four allowed names.
_COMPRESSION_BY_NAME: dict[str, int] = {
    "deflated": zipfile.ZIP_DEFLATED,
    "stored": zipfile.ZIP_STORED,
    "bzip2": zipfile.ZIP_BZIP2,
    "lzma": zipfile.ZIP_LZMA,
}


# v0.7.111 — HTML wrapper for combined exports. Same stylesheet as
# the per-page wrapper so the combined file is consistent, plus a
# <hr class="onp-page-break"> separator + print CSS that forces each
# note onto its own page when printing-to-PDF.
_COMBINED_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --fg: #1a1a1a;
      --bg: #fdfdfd;
      --muted: #6b7280;
      --accent: #2563eb;
      --code-bg: #f3f4f6;
      --border: #e5e7eb;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --fg: #e5e7eb; --bg: #0f172a; --muted: #9ca3af;
        --accent: #60a5fa; --code-bg: #1e293b; --border: #334155;
      }}
    }}
    body {{
      max-width: 820px; margin: 2rem auto; padding: 0 1.5rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
                   "Helvetica Neue", Arial, sans-serif;
      line-height: 1.65; color: var(--fg); background: var(--bg);
    }}
    h1, h2, h3 {{ line-height: 1.25; margin-top: 2rem; }}
    h1 {{ border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }}
    a {{ color: var(--accent); }}
    code {{
      background: var(--code-bg); padding: 0.15em 0.4em; border-radius: 3px;
      font-size: 0.92em;
    }}
    pre {{
      background: var(--code-bg); padding: 1rem; border-radius: 6px;
      overflow-x: auto;
    }}
    pre code {{ background: transparent; padding: 0; }}
    blockquote {{
      border-left: 4px solid var(--border); margin: 1rem 0;
      padding: 0.4rem 1rem; color: var(--muted);
    }}
    table {{ border-collapse: collapse; margin: 1rem 0; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem 0.8rem; }}
    th {{ background: var(--code-bg); }}
    .onp-cover {{
      text-align: center; margin: 3rem auto 4rem;
      padding-bottom: 1rem; border-bottom: 2px solid var(--border);
    }}
    .onp-cover h1 {{ border: none; font-size: 2.2rem; margin: 0.5rem 0; }}
    .onp-cover .onp-headline {{ color: var(--muted); font-size: 1.1rem; }}
    .onp-page-break {{
      border: none; border-top: 1px dashed var(--border);
      margin: 3rem 0 2rem;
    }}
    .onp-toc {{ background: var(--code-bg); border-radius: 6px;
                padding: 1rem 1.5rem; margin: 1.5rem 0 2.5rem; }}
    .onp-toc ol {{ margin: 0.5rem 0 0 1.2rem; }}
    @media print {{
      .onp-page-break {{ page-break-after: always; border: none; }}
      body {{ max-width: none; margin: 0; padding: 1rem; }}
    }}
  </style>
</head>
<body>
{body_html}
</body>
</html>
"""


async def _write_combined_export(
    *,
    req: NotebookExportRequest,
    notebook: Notebook,
    notes_sorted: list[Note],
    plan: list[tuple[str, Note]],
    sources: list[Source],
    manifest: dict,
    warnings: list[str],
    is_html: bool,
) -> "ExportResponse":
    """v0.7.111 — Single-file combined export. Concatenates every note
    (in plan order, so Overview comes first) into one .md or .html.
    Page breaks rendered as horizontal rules so print-to-PDF naturally
    paginates each note onto its own page (via the print CSS in the
    HTML wrapper)."""
    target = _resolve_and_validate(req.destination, must_exist=False)
    if target.exists() and target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Destination is a directory; pass a file path for "
                f"format={req.format}: {target}"
            ),
        )
    _check_overwrite(target, overwrite=req.overwrite)
    if not target.parent.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Parent directory does not exist: {target.parent}. "
                "Create it via POST /api/fs/mkdir first."
            ),
        )

    # Force the right extension if the caller forgot one.
    desired_ext = ".html" if is_html else ".md"
    if target.suffix.lower() != desired_ext:
        target = target.with_suffix(desired_ext)
        _check_overwrite(target, overwrite=req.overwrite)

    notebook_title = notebook.name or "Untitled Notebook"

    if not is_html:
        # Combined Markdown — cover page + TOC + each note separated by
        # a horizontal rule (renderers turn `---` into <hr>).
        sections: list[str] = []
        sections.append(f"# 📚 {notebook_title}\n")
        if notebook.description:
            sections.append(f"> {notebook.description}\n")
        sections.append(
            f"_Generated by Deeper Notebook · "
            f"{datetime.now(timezone.utc).isoformat()}_\n"
        )
        # Table of contents
        toc_lines = ["## Contents\n"]
        for i, (_filename, note) in enumerate(plan, start=1):
            toc_lines.append(f"{i}. {note.title or '(untitled)'}")
        sections.append("\n".join(toc_lines))
        # Each note rendered as a markdown section
        for _filename, note in plan:
            sections.append("\n---\n")
            sections.append(_render_note_content(note))
        # Optionally include sources
        if req.include_sources and sources:
            sections.append("\n---\n")
            sections.append("# 📁 Sources\n")
            for s in sources:
                sections.append("\n---\n")
                sections.append(_render_source_content(s))
        combined = "\n\n".join(sections)
        payload = combined.encode("utf-8")
    else:
        # Combined HTML — same structure rendered as HTML5.
        body_parts: list[str] = []
        body_parts.append(
            f'<div class="onp-cover"><h1>{_html_escape(notebook_title)}</h1>'
        )
        if notebook.description:
            body_parts.append(
                f'<p class="onp-headline">{_html_escape(notebook.description)}</p>'
            )
        body_parts.append(
            f'<p class="onp-headline">Generated by Deeper Notebook · '
            f"{datetime.now(timezone.utc).isoformat()}</p></div>"
        )
        toc = ['<div class="onp-toc"><strong>Contents</strong><ol>']
        for _filename, note in plan:
            toc.append(f"<li>{_html_escape(note.title or '(untitled)')}</li>")
        toc.append("</ol></div>")
        body_parts.append("".join(toc))
        for _filename, note in plan:
            body_parts.append('<hr class="onp-page-break">')
            # Reuse the per-note html renderer but strip its outer
            # <html>/<head>/<body> so we have just the article fragment.
            full_html = _render_note_as_html(note)
            body_only = full_html
            if "<body>" in body_only and "</body>" in body_only:
                body_only = body_only.split("<body>", 1)[1]
                body_only = body_only.rsplit("</body>", 1)[0]
            body_parts.append(body_only)
        if req.include_sources and sources:
            body_parts.append('<hr class="onp-page-break">')
            body_parts.append("<h1>📁 Sources</h1>")
            for s in sources:
                body_parts.append('<hr class="onp-page-break">')
                full = _render_source_as_html(s)
                body_only = full
                if "<body>" in body_only and "</body>" in body_only:
                    body_only = body_only.split("<body>", 1)[1]
                    body_only = body_only.rsplit("</body>", 1)[0]
                body_parts.append(body_only)
        combined = _COMBINED_HTML_TEMPLATE.format(
            title=_html_escape(notebook_title),
            body_html="\n".join(body_parts),
        )
        payload = combined.encode("utf-8")

    try:
        target.write_bytes(payload)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write {target}: {exc}",
        )

    logger.info(
        "Notebook export (combined): format={} notebook={} bytes={} → {}",
        req.format,
        notebook.id,
        len(payload),
        str(target),
    )
    return ExportResponse(
        destination=str(target),
        format=req.format,
        file_count=1,
        total_bytes=len(payload),
        files=[
            ExportFileEntry(
                relative_path=os.path.basename(str(target)),
                bytes=len(payload),
            )
        ],
        warnings=warnings,
    )


def _build_manifest(
    notebook: Notebook,
    notes: list[Note],
    sources: list[Source],
) -> dict:
    """Manifest describes the export for downstream tools (Logseq importers,
    paperless ingestion, future "re-import into ONP" workflows)."""
    return {
        "open_notebook_plus_version": "0.7.90",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "notebook": {
            "id": str(notebook.id),
            "name": notebook.name,
            "description": notebook.description,
            "created": getattr(notebook, "created", None),
            "updated": getattr(notebook, "updated", None),
        },
        "notes": [
            {
                "id": str(n.id),
                "title": n.title,
                "type": n.note_type,
                "created": getattr(n, "created", None),
                "updated": getattr(n, "updated", None),
            }
            for n in notes
        ],
        "sources": [
            {
                "id": str(s.id),
                "title": s.title,
                "asset_path": s.asset.file_path if s.asset else None,
                "asset_url": s.asset.url if s.asset else None,
            }
            for s in sources
        ],
    }


def _check_overwrite(target: Path, *, overwrite: bool) -> None:
    """Refuse to overwrite without explicit consent. Raises 409 if the
    target exists and overwrite=False."""
    if target.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Target already exists: {target}. Pass overwrite=true to "
                "replace, or pick a different destination."
            ),
        )


def _plan_filenames(notes: list[Note]) -> list[tuple[str, Note]]:
    """Pick a filename for each note. The first overview-shaped note (if
    any) wins the canonical `00-overview.md`; everything else is indexed
    starting at 01.

    Returns a list of (filename, note) pairs in write order — overview
    first so it sorts to the top of any directory listing.
    """
    plan: list[tuple[str, Note]] = []
    overview_idx: Optional[int] = None
    for i, n in enumerate(notes):
        ov = _build_overview_path(n)
        if ov and overview_idx is None:
            plan.append((ov, n))
            overview_idx = i
    page_idx = 1
    for i, n in enumerate(notes):
        if i == overview_idx:
            continue
        plan.append((_note_filename(n, index=page_idx), n))
        page_idx += 1
    return plan


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/notebooks/{notebook_id}/export", response_model=ExportResponse)
async def export_notebook(
    notebook_id: str,
    req: NotebookExportRequest,
) -> ExportResponse:
    """Export a notebook's overview + all pages as markdown files.

    See module docstring for the on-disk layout. The endpoint is safe
    to re-run with overwrite=true; it produces the same output for the
    same notebook state.
    """
    notebook = await Notebook.get(notebook_id)
    if not notebook:
        raise HTTPException(
            status_code=404, detail=f"Notebook {notebook_id!r} not found"
        )

    try:
        notes = await notebook.get_notes(include_content=True)
    except TypeError:
        notes = await notebook.get_notes()

    for idx, n in enumerate(notes):
        if getattr(n, "content", None) is None and getattr(n, "id", None):
            try:
                hydrated = await Note.get(n.id)
                if hydrated and getattr(hydrated, "content", None) is not None:
                    notes[idx] = hydrated
            except Exception:
                pass

    if not notes and not req.include_sources:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Notebook {notebook.name!r} has no notes to export. Either add "
                "content first, or pass include_sources=true to export just the "
                "uploaded source documents."
            ),
        )

    sources: list[Source] = []
    if req.include_sources:
        try:
            sources = await notebook.get_sources(include_full_text=True)
        except TypeError:
            sources = await notebook.get_sources()

        for idx, s in enumerate(sources):
            if getattr(s, "full_text", None) is None and getattr(s, "id", None):
                try:
                    hydrated = await Source.get(s.id)
                    if hydrated and getattr(hydrated, "full_text", None) is not None:
                        sources[idx] = hydrated
                except Exception:
                    pass

    notes_sorted = sorted(notes, key=lambda n: (n.created or "", n.title or ""))
    plan = _plan_filenames(notes_sorted)
    manifest = _build_manifest(notebook, notes_sorted, sources)
    warnings: list[str] = []

    # v0.7.97 — Per-format renderer + extension selection. Markdown formats
    # use the existing _render_*_content; HTML formats use the v0.7.97
    # _render_*_as_html. file_ext determines the suffix on every emitted
    # file (manifest.json stays .json regardless).
    is_obsidian = req.format.startswith("obsidian_")
    is_html = req.format.startswith("html_") or req.format == "combined_html"
    is_folder = req.format.endswith("folder")
    is_combined = req.format.startswith("combined_")
    if is_html:
        note_renderer = _render_note_as_html
        source_renderer = _render_source_as_html
        file_ext = ".html"
    elif is_obsidian:
        note_renderer = _render_obsidian_note_content
        source_renderer = _render_obsidian_source_content
        file_ext = ".md"
    else:
        note_renderer = _render_note_content
        source_renderer = _render_source_content
        file_ext = ".md"

    # v0.7.111 — Combined single-file export. Concatenate every note (and
    # optionally every source) into one Markdown or HTML file. Useful
    # for share-by-email, print-to-PDF, or import into a single
    # paperless-gpt entry. Bails out of the multi-file dispatch below.
    if is_combined:
        return await _write_combined_export(
            req=req,
            notebook=notebook,
            notes_sorted=notes_sorted,
            plan=plan,
            sources=sources,
            manifest=manifest,
            warnings=warnings,
            is_html=is_html,
        )

    # Translate plan filenames from .md → .html when emitting HTML.
    # _plan_filenames hardcodes .md; this re-suffixes without losing the
    # 00-overview / 01-{slug} ordering.
    def _retype(name: str) -> str:
        if file_ext == ".md":
            return name
        if name.endswith(".md"):
            return name[: -len(".md")] + file_ext
        return name

    if is_folder:
        target_dir = _resolve_and_validate(req.destination, must_exist=False)
        if target_dir.exists() and not target_dir.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Destination exists but is not a directory: {target_dir}",
            )
        # Create the target folder if missing.
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied creating {target_dir}: {exc}",
            )

        # Pre-flight overwrite check across all planned files. Better to
        # 409 BEFORE writing half the notebook than half-way through.
        if not req.overwrite:
            for filename, _ in plan:
                _check_overwrite(target_dir / _retype(filename), overwrite=False)
            if sources:
                for s in sources:
                    sid = _notebook_record_id_part(str(s.id))
                    _check_overwrite(
                        target_dir / "sources" / f"{sid}{file_ext}",
                        overwrite=False,
                    )

        files_written: list[ExportFileEntry] = []
        total_bytes = 0

        for filename, note in plan:
            filename = _retype(filename)
            content = note_renderer(note)
            payload = content.encode("utf-8")
            target = target_dir / filename
            try:
                target.write_bytes(payload)
            except OSError as exc:
                warnings.append(f"Could not write {filename}: {exc}")
                continue
            files_written.append(
                ExportFileEntry(relative_path=filename, bytes=len(payload))
            )
            total_bytes += len(payload)

        if sources:
            sources_dir = target_dir / "sources"
            sources_dir.mkdir(exist_ok=True)
            for s in sources:
                sid = _notebook_record_id_part(str(s.id))
                rel = f"sources/{sid}{file_ext}"
                content = source_renderer(s)
                payload = content.encode("utf-8")
                try:
                    (sources_dir / f"{sid}{file_ext}").write_bytes(payload)
                except OSError as exc:
                    warnings.append(f"Could not write {rel}: {exc}")
                    continue
                files_written.append(
                    ExportFileEntry(relative_path=rel, bytes=len(payload))
                )
                total_bytes += len(payload)

        if is_obsidian:
            try:
                obsidian_dir = target_dir / ".obsidian"
                obsidian_dir.mkdir(exist_ok=True)
                app_json_bytes = json.dumps({"legacyEditor": False, "livePreview": True}, indent=2).encode("utf-8")
                (obsidian_dir / "app.json").write_bytes(app_json_bytes)
                files_written.append(ExportFileEntry(relative_path=".obsidian/app.json", bytes=len(app_json_bytes)))
                total_bytes += len(app_json_bytes)

                index_bytes = _render_obsidian_index(notebook, plan, sources).encode("utf-8")
                (target_dir / "Index.md").write_bytes(index_bytes)
                files_written.append(ExportFileEntry(relative_path="Index.md", bytes=len(index_bytes)))
                total_bytes += len(index_bytes)
            except OSError as exc:
                warnings.append(f"Could not write Obsidian metadata: {exc}")

        # Always write manifest last so partial exports are still readable.
        manifest_payload = json.dumps(manifest, indent=2).encode("utf-8")
        try:
            (target_dir / "manifest.json").write_bytes(manifest_payload)
            files_written.append(
                ExportFileEntry(
                    relative_path="manifest.json", bytes=len(manifest_payload)
                )
            )
            total_bytes += len(manifest_payload)
        except OSError as exc:
            warnings.append(f"Could not write manifest.json: {exc}")

        logger.info(
            "Notebook export: format={} notebook={} files={} bytes={} → {}",
            req.format,
            notebook_id,
            len(files_written),
            total_bytes,
            str(target_dir),
        )
        return ExportResponse(
            destination=str(target_dir),
            format=req.format,
            file_count=len(files_written),
            total_bytes=total_bytes,
            files=files_written[:100],
            warnings=warnings,
        )

    # format ∈ ("zip", "html_zip")
    target_zip = _resolve_and_validate(req.destination, must_exist=False)
    if target_zip.exists() and target_zip.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Destination is a directory; pass a .zip file path for "
                f"format={req.format}: {target_zip}"
            ),
        )
    _check_overwrite(target_zip, overwrite=req.overwrite)
    # Parent directory must exist; we don't auto-create the parent for
    # zip exports because that's surprising — caller can /fs/mkdir first.
    if not target_zip.parent.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Parent directory does not exist: {target_zip.parent}. "
                "Create it via POST /api/fs/mkdir first."
            ),
        )

    files_written = []
    total_bytes = 0
    # v0.7.98 — Pick the compression algorithm from the request. Defaults
    # to DEFLATED to match prior v0.7.90 behavior. ZIP_BZIP2 / ZIP_LZMA
    # require the host's zlib/bz2/lzma to be available (stdlib on macOS
    # + Windows + Linux), which they always are on the desktop bundle.
    zip_compression = _COMPRESSION_BY_NAME[req.compression]
    try:
        with zipfile.ZipFile(target_zip, "w", compression=zip_compression) as zf:
            for filename, note in plan:
                filename = _retype(filename)
                content = note_renderer(note).encode("utf-8")
                zf.writestr(filename, content)
                files_written.append(
                    ExportFileEntry(relative_path=filename, bytes=len(content))
                )
                total_bytes += len(content)
            if sources:
                for s in sources:
                    sid = _notebook_record_id_part(str(s.id))
                    rel = f"sources/{sid}{file_ext}"
                    payload = source_renderer(s).encode("utf-8")
                    zf.writestr(rel, payload)
                    files_written.append(
                        ExportFileEntry(relative_path=rel, bytes=len(payload))
                    )
                    total_bytes += len(payload)
            if is_obsidian:
                app_json_bytes = json.dumps({"legacyEditor": False, "livePreview": True}, indent=2).encode("utf-8")
                zf.writestr(".obsidian/app.json", app_json_bytes)
                files_written.append(ExportFileEntry(relative_path=".obsidian/app.json", bytes=len(app_json_bytes)))
                total_bytes += len(app_json_bytes)

                index_bytes = _render_obsidian_index(notebook, plan, sources).encode("utf-8")
                zf.writestr("Index.md", index_bytes)
                files_written.append(ExportFileEntry(relative_path="Index.md", bytes=len(index_bytes)))
                total_bytes += len(index_bytes)

            manifest_payload = json.dumps(manifest, indent=2).encode("utf-8")
            zf.writestr("manifest.json", manifest_payload)
            files_written.append(
                ExportFileEntry(
                    relative_path="manifest.json", bytes=len(manifest_payload)
                )
            )
            total_bytes += len(manifest_payload)
    except OSError as exc:
        # Clean up half-written zip so the user doesn't see a corrupted
        # archive on disk.
        try:
            if target_zip.exists():
                target_zip.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write zip {target_zip}: {exc}",
        )

    logger.info(
        "Notebook export (zip): format={} compression={} notebook={} files={} bytes={} → {}",
        req.format,
        req.compression,
        notebook_id,
        len(files_written),
        total_bytes,
        str(target_zip),
    )
    return ExportResponse(
        destination=str(target_zip),
        format=req.format,
        file_count=len(files_written),
        total_bytes=total_bytes,
        files=files_written[:100],
        warnings=warnings,
    )


@router.post("/notes/{note_id}/export", response_model=ExportResponse)
async def export_note(note_id: str, req: NoteExportRequest) -> ExportResponse:
    """Export a single note as a .md file."""
    note = await Note.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id!r} not found")

    target = _resolve_and_validate(req.destination, must_exist=False)
    if target.exists() and target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Destination is a directory; pass a file path: {target}. "
                "Use /api/notebooks/{id}/export to export a whole notebook."
            ),
        )
    _check_overwrite(target, overwrite=req.overwrite)
    if not target.parent.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Parent directory does not exist: {target.parent}. "
                "Create it via POST /api/fs/mkdir first."
            ),
        )

    payload = _render_note_content(note).encode("utf-8")
    # Force .md extension if the user didn't include one — single-note
    # exports are always markdown.
    if target.suffix.lower() != ".md":
        target = target.with_suffix(".md")
        _check_overwrite(target, overwrite=req.overwrite)
    try:
        target.write_bytes(payload)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write {target}: {exc}",
        )

    logger.info(
        "Note export: note={} bytes={} → {}",
        note_id,
        len(payload),
        str(target),
    )
    return ExportResponse(
        destination=str(target),
        format="single",
        file_count=1,
        total_bytes=len(payload),
        files=[
            ExportFileEntry(
                relative_path=os.path.basename(str(target)), bytes=len(payload)
            )
        ],
        warnings=[],
    )


# ---------------------------------------------------------------------------
# v0.7.94 — Notebook IMPORT (reverse of export)
# ---------------------------------------------------------------------------
# Closes the loop on v0.7.90 export: a folder of .md files (with optional
# manifest.json) or a .zip archive can be read back into the running ONP
# instance as a Notebook + Notes + Sources. Enables:
#   - Backup/restore workflows
#   - Cross-machine transfer (export from machine A, import on machine B)
#   - Manual editing of exported markdown in any text editor, then
#     re-importing to keep ONP in sync
#   - Bulk import of preexisting markdown libraries (Obsidian vaults,
#     Logseq exports) into ONP for chat-with-sources


# Cap to prevent malicious / accidental DoS via huge import targets.
# A 50 MB zip is plenty for a deep notebook with hundreds of pages;
# legitimate use exceeding this is the user importing their entire
# Obsidian vault, which is out of scope.
_MAX_IMPORT_BYTES = 50 * 1024 * 1024
# Per-file cap inside an import — same rationale as the Studio upload cap.
_MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024
# Cap entries in an imported folder/zip so a malicious archive with
# millions of empty files can't pin the API.
_MAX_IMPORT_ENTRIES = 500


class NotebookImportRequest(BaseModel):
    source_path: str = Field(
        ...,
        description=(
            "Absolute path to either a directory containing .md files "
            "(optionally with a manifest.json) or a .zip archive. "
            "Single .md files are also accepted and become a one-note "
            "notebook. ~/-expansion supported."
        ),
    )
    mode: Literal["new", "into_existing"] = Field(
        "new",
        description=(
            "'new' creates a fresh Notebook; 'into_existing' appends notes "
            "to an existing one (target_notebook_id required)."
        ),
    )
    target_notebook_id: Optional[str] = Field(
        None,
        description="Required when mode='into_existing'.",
    )
    new_name: Optional[str] = Field(
        None,
        description=(
            "Notebook name when mode='new'. Falls back to manifest.json's "
            "notebook.name, then the source folder/zip stem."
        ),
    )
    import_sources: bool = Field(
        True,
        description=(
            "If the import contains a sources/ subfolder (from a prior "
            "include_sources=true export), recreate those as Source "
            "records too."
        ),
    )


class ImportedItemEntry(BaseModel):
    kind: Literal["note", "source"]
    id: str
    title: str
    bytes: int


class NotebookImportResponse(BaseModel):
    notebook_id: str
    notebook_name: str
    mode: str  # "new" | "into_existing"
    note_ids: list[str]
    source_ids: list[str]
    file_count: int  # total .md files processed
    items: list[ImportedItemEntry]  # capped at 100
    warnings: list[str] = []


# Frontmatter parser: capture the YAML-ish block at the top of a .md file
# (between leading "---" and trailing "---") so we can recover title,
# note_type, created/updated, etc. Falls back to filename if absent.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<yaml>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse a YAML-ish frontmatter block off the top of a markdown file.
    Returns (metadata, body). If no frontmatter, returns ({}, content).
    We use a hand-rolled parser rather than PyYAML to avoid the dep —
    we only need flat key:value lines, which is what _render_note_content
    writes."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    yaml_block = m.group("yaml")
    body = m.group("body")
    meta: dict[str, str] = {}
    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip("'\"")
    return meta, body


async def _import_note_from_text(
    *,
    content: str,
    fallback_title: str,
    notebook_id: str,
) -> tuple[Note, int]:
    """Build + save a Note from a markdown string. Returns (note, bytes_read)."""
    meta, body = _parse_frontmatter(content)
    title = meta.get("title") or fallback_title
    note_type = meta.get("type", "ai")
    if note_type not in ("ai", "human"):
        note_type = "ai"
    # Ensure non-empty content — Note.content_must_not_be_empty rejects
    # whitespace-only. Preserve original body even if frontmatter is all
    # there is.
    if not body.strip():
        body = "(no content)"
    note = Note(title=title[:200], content=body, note_type=note_type)  # type: ignore[arg-type]
    await note.save()
    await note.add_to_notebook(notebook_id)
    return note, len(content.encode("utf-8"))


def _validate_archive_member(name: str) -> Optional[str]:
    """Reject zip entries that would escape the extraction directory.
    Returns None if safe, else an error string."""
    # Drop absolute paths and any traversal segment after normalization.
    norm = os.path.normpath(name)
    if name.startswith("/") or name.startswith("\\"):
        return f"absolute path member: {name!r}"
    if ".." in norm.split(os.sep):
        return f"traversal member: {name!r}"
    return None


def _is_regular_file_entry(info: "zipfile.ZipInfo") -> bool:
    """v0.7.117 — Reject non-regular-file zip entries (symlinks, devices,
    FIFOs, sockets). A malicious zip with a symlink "passwords.md" →
    "/etc/passwd" would otherwise have its 'content' read as the link
    target string. Not directly exploitable (we don't extract to disk;
    we just decode bytes to UTF-8 and store as a note), but it would
    silently import nonsense and obscure debugging.

    Unix file-mode bits live in the top 16 bits of external_attr.
    Convention varies across zip tools:
      * macOS Archive Utility / `zip` CLI  → `0o100644 << 16`
        (S_IFREG | rw-r--r--) — full mode
      * Python's `zipfile.writestr(name, ...)` → `0o600 << 16` —
        permissions only, no S_IF* bits
      * DOS-only zips → `external_attr = 0` (no Unix mode at all)

    We REJECT only when an S_IF* bit-pattern is present AND it
    indicates a non-regular file (symlink/FIFO/device/socket). When
    no file-type bits are set we accept (permission-only or
    DOS-only zip — the common case).
    """
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode == 0:
        # No Unix mode info — treat as regular file (DOS attr only).
        return True
    file_type = unix_mode & 0o170000
    if file_type == 0:
        # Permission bits only (no S_IF*); writestr default. Regular.
        return True
    # Explicit S_IF* set — accept only regular file or directory.
    return file_type in (0o100000, 0o040000)


def _read_import_entries(source: Path) -> list[tuple[str, str]]:
    """Read all importable entries from a folder OR zip. Returns a list of
    (relative_path, content) pairs, sorted by relative_path. Cap-enforced.

    Raises HTTPException on:
      - source too large
      - too many entries
      - per-file too large
      - zip traversal members
    """
    entries: list[tuple[str, str]] = []
    if source.is_file() and source.suffix.lower() == ".zip":
        if source.stat().st_size > _MAX_IMPORT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Zip is {source.stat().st_size} bytes; cap is "
                    f"{_MAX_IMPORT_BYTES} bytes (~{_MAX_IMPORT_BYTES // 1024 // 1024} MB)."
                ),
            )
        try:
            with zipfile.ZipFile(source, "r") as zf:
                members = zf.namelist()
                if len(members) > _MAX_IMPORT_ENTRIES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Zip contains {len(members)} entries; cap is "
                            f"{_MAX_IMPORT_ENTRIES}."
                        ),
                    )
                for name in sorted(members):
                    if not name.lower().endswith((".md", ".markdown", ".json")):
                        continue
                    err = _validate_archive_member(name)
                    if err:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Unsafe zip entry: {err}",
                        )
                    info = zf.getinfo(name)
                    # v0.7.117 — Reject symlinks/devices/FIFOs/sockets. They
                    # don't break us today (we read bytes, don't extract
                    # to disk) but they'd silently import garbage.
                    if not _is_regular_file_entry(info):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Unsafe zip entry: {name!r} is not a regular "
                                "file (symlink / device / FIFO). Re-create the "
                                "archive with regular file entries only."
                            ),
                        )
                    if info.file_size > _MAX_IMPORT_FILE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Zip member {name!r} is {info.file_size} bytes; "
                                f"per-file cap is {_MAX_IMPORT_FILE_BYTES}."
                            ),
                        )
                    try:
                        data = zf.read(name)
                    except zipfile.BadZipFile as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Corrupt zip member {name!r}: {exc}",
                        )
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        # Skip binary content silently — not importable.
                        continue
                    entries.append((name, text))
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Not a valid zip: {exc}",
            )
        return entries

    if source.is_file() and source.suffix.lower() in (".md", ".markdown"):
        # Single .md file becomes one note.
        if source.stat().st_size > _MAX_IMPORT_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File is {source.stat().st_size} bytes; per-file cap is "
                    f"{_MAX_IMPORT_FILE_BYTES}."
                ),
            )
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"File is not UTF-8: {exc}",
            )
        return [(source.name, text)]

    if source.is_dir():
        total_bytes = 0
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            if path.suffix.lower() not in (".md", ".markdown", ".json"):
                continue
            if len(entries) >= _MAX_IMPORT_ENTRIES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Folder contains more than {_MAX_IMPORT_ENTRIES} "
                        ".md/.json entries; aborting import."
                    ),
                )
            size = path.stat().st_size
            if size > _MAX_IMPORT_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File {path} is {size} bytes; per-file cap is "
                        f"{_MAX_IMPORT_FILE_BYTES}."
                    ),
                )
            total_bytes += size
            if total_bytes > _MAX_IMPORT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(f"Folder total exceeds cap of {_MAX_IMPORT_BYTES} bytes."),
                )
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # skip non-UTF-8 silently
            rel = str(path.relative_to(source))
            entries.append((rel, text))
        return entries

    raise HTTPException(
        status_code=400,
        detail=(f"Source path is neither a directory nor a .md/.zip file: {source}"),
    )


@router.post("/notebooks/import", response_model=NotebookImportResponse)
async def import_notebook(req: NotebookImportRequest) -> NotebookImportResponse:
    """Import a folder or .zip of markdown files (as produced by
    POST /notebooks/{id}/export) into a fresh or existing Notebook.

    See module docstring + NotebookImportRequest for option semantics.
    """
    src = _resolve_and_validate(req.source_path, must_exist=True)
    if req.mode == "into_existing" and not req.target_notebook_id:
        raise HTTPException(
            status_code=400,
            detail="mode='into_existing' requires target_notebook_id",
        )

    # Read all importable entries up-front so caps are enforced before
    # we touch the database.
    entries = _read_import_entries(src)
    # Manifest (optional) gives us the original notebook name/description.
    manifest: dict = {}
    md_entries: list[tuple[str, str]] = []
    sources_entries: list[tuple[str, str]] = []
    for rel, text in entries:
        if rel.endswith("manifest.json"):
            try:
                manifest = json.loads(text)
            except json.JSONDecodeError as exc:
                # Bad manifest is non-fatal — we'll fall back to filename
                # and import the .md files anyway.
                logger.warning(
                    "Notebook import: ignoring corrupt manifest.json: {}",
                    exc,
                )
            continue
        if rel.startswith("sources/") or rel.startswith("sources" + os.sep):
            sources_entries.append((rel, text))
        else:
            md_entries.append((rel, text))

    if not md_entries:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No .md files found in {src}. Make sure the import "
                "target contains exported markdown."
            ),
        )

    # Resolve or create the destination notebook.
    warnings: list[str] = []
    notebook: Optional[Notebook]
    if req.mode == "into_existing":
        notebook = await Notebook.get(req.target_notebook_id)  # type: ignore[arg-type]
        if not notebook:
            raise HTTPException(
                status_code=404,
                detail=f"Target notebook {req.target_notebook_id!r} not found",
            )
    else:
        name = (
            req.new_name
            or (manifest.get("notebook") or {}).get("name")
            or src.stem
            or "Imported Notebook"
        )
        description = (manifest.get("notebook") or {}).get(
            "description"
        ) or f"Imported from {src.name}"
        try:
            notebook = Notebook(name=name[:200], description=description)
            await notebook.save()
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            logger.exception("Notebook import: failed to create notebook")
            raise HTTPException(
                status_code=500,
                detail=f"Could not create notebook: {exc}",
            )

    notebook_id = str(notebook.id)
    note_ids: list[str] = []
    source_ids: list[str] = []
    items: list[ImportedItemEntry] = []

    # Import notes first; sort by relative path so 00-overview lands first.
    for rel, text in sorted(md_entries):
        fallback_title = Path(rel).stem.replace("-", " ").replace("_", " ").title()
        try:
            note, n_bytes = await _import_note_from_text(
                content=text,
                fallback_title=fallback_title,
                notebook_id=notebook_id,
            )
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            logger.warning(
                "Notebook import: could not import note {!r}: {}",
                rel,
                _brief(exc),
            )
            warnings.append(f"Could not import {rel!r}: {_brief(exc)}")
            continue
        note_ids.append(str(note.id))
        items.append(
            ImportedItemEntry(
                kind="note",
                id=str(note.id),
                title=note.title or rel,
                bytes=n_bytes,
            )
        )

    # Import sources only if requested and the import bundle has them.
    if req.import_sources:
        for rel, text in sources_entries:
            meta, body = _parse_frontmatter(text)
            title = meta.get("title") or Path(rel).stem
            try:
                # No on-disk file from the import — sources/*.md is a
                # text dump, not the original binary. We store the
                # text-only Source so chat-with-sources can use it.
                source = Source(title=title[:200])  # type: ignore[arg-type]
                source.full_text = body
                await source.save()
                await source.add_to_notebook(notebook_id)
                # v0.7.104 — Real bug fix: Source.save() does NOT auto-embed
                # (per deeper_notebook/domain/CLAUDE.md). Without this
                # vectorize() call, imported sources were saved but never
                # got embeddings, which meant vector_search() couldn't find
                # them — breaking the "import then chat-with-sources"
                # promise of v0.7.94. Note.save() DOES auto-embed, which is
                # why imported notes worked; Source is the inconsistent
                # one. Fire-and-forget (returns command_id we don't need).
                try:
                    await source.vectorize()
                except HTTPException:
                    # v0.7.108 — re-raise typed HTTPExceptions so the next
                    # `except Exception` doesn't clobber them to 500.
                    raise
                except Exception as exc:
                    # Vector backend might be unavailable; the source is
                    # still saved + text-searchable. Warn but don't fail
                    # the import.
                    logger.warning(
                        "Import: source vectorize failed for {!r} (non-fatal): {}",
                        rel,
                        _brief(exc),
                    )
                    warnings.append(
                        f"Source {rel!r} imported but embedding queue failed: "
                        f"{_brief(exc)}. Text search still works; rebuild "
                        "embeddings from Settings → Embeddings to enable "
                        "vector search."
                    )
            except HTTPException:
                # v0.7.108 — re-raise typed HTTPExceptions so the next
                # `except Exception` doesn't clobber them to 500.
                raise
            except Exception as exc:
                logger.warning(
                    "Notebook import: could not import source {!r}: {}",
                    rel,
                    _brief(exc),
                )
                warnings.append(f"Could not import source {rel!r}: {_brief(exc)}")
                continue
            source_ids.append(str(source.id))
            items.append(
                ImportedItemEntry(
                    kind="source",
                    id=str(source.id),
                    title=source.title or rel,
                    bytes=len(text.encode("utf-8")),
                )
            )

    logger.info(
        "Notebook import: notebook={} notes={} sources={} → from {}",
        notebook_id,
        len(note_ids),
        len(source_ids),
        str(src),
    )
    return NotebookImportResponse(
        notebook_id=notebook_id,
        notebook_name=notebook.name,
        mode=req.mode,
        note_ids=note_ids,
        source_ids=source_ids,
        file_count=len(md_entries),
        items=items[:100],
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# v0.7.96 — Import preview (dry-run)
# ---------------------------------------------------------------------------
# Returns what WOULD be imported by v0.7.94 import_notebook, without
# committing anything to the database. Lets the frontend show the user
# the parsed structure ("we'll create 5 notes, 2 sources, the notebook
# will be named 'X'") before they confirm. Cheap — same _read_import_entries
# call, same frontmatter parser, no domain layer touched.


class NotebookImportPreviewRequest(BaseModel):
    source_path: str = Field(
        ...,
        description="Absolute path: directory, .zip, or single .md.",
    )


class NotebookImportPreviewItem(BaseModel):
    relative_path: str
    title: str
    bytes: int
    is_overview: bool = False  # detected v0.7.89 overview shape


class NotebookImportPreviewResponse(BaseModel):
    source_path: str
    detected_kind: Literal["folder", "zip", "single_md"]
    notebook_name_hint: Optional[str] = None
    description_hint: Optional[str] = None
    notes: list[NotebookImportPreviewItem]
    sources: list[NotebookImportPreviewItem]
    has_manifest: bool
    total_bytes: int
    warnings: list[str] = []


def _detect_import_kind(src: Path) -> Literal["folder", "zip", "single_md"]:
    if src.is_dir():
        return "folder"
    if src.is_file() and src.suffix.lower() == ".zip":
        return "zip"
    return "single_md"


@router.post(
    "/notebooks/import/preview",
    response_model=NotebookImportPreviewResponse,
)
def preview_import(req: NotebookImportPreviewRequest) -> NotebookImportPreviewResponse:
    """Dry-run an import. Reads the source bundle, parses frontmatter,
    and returns the planned import structure WITHOUT creating any
    Notebook / Note / Source records. Same caps as the real import."""
    src = _resolve_and_validate(req.source_path, must_exist=True)
    detected = _detect_import_kind(src)
    entries = _read_import_entries(src)

    manifest: dict = {}
    has_manifest = False
    md_entries: list[tuple[str, str]] = []
    sources_entries: list[tuple[str, str]] = []
    warnings: list[str] = []

    for rel, text in entries:
        if rel.endswith("manifest.json"):
            has_manifest = True
            try:
                manifest = json.loads(text)
            except json.JSONDecodeError as exc:
                warnings.append(f"manifest.json present but invalid: {exc}")
            continue
        if rel.startswith("sources/") or rel.startswith("sources" + os.sep):
            sources_entries.append((rel, text))
        else:
            md_entries.append((rel, text))

    notebook_meta = (
        (manifest.get("notebook") or {}) if isinstance(manifest, dict) else {}
    )
    name_hint = notebook_meta.get("name") or src.stem or None
    desc_hint = notebook_meta.get("description") or None

    def _preview_note(rel: str, text: str) -> NotebookImportPreviewItem:
        meta, _body = _parse_frontmatter(text)
        title = (
            meta.get("title")
            or Path(rel).stem.replace("-", " ").replace("_", " ").title()
        )
        is_overview = (
            rel.endswith("00-overview.md")
            or "overview" in (meta.get("title") or "").lower()
        )
        return NotebookImportPreviewItem(
            relative_path=rel,
            title=title,
            bytes=len(text.encode("utf-8")),
            is_overview=is_overview,
        )

    notes_preview = [_preview_note(rel, text) for rel, text in sorted(md_entries)]
    sources_preview = [
        _preview_note(rel, text) for rel, text in sorted(sources_entries)
    ]
    total_bytes = sum(n.bytes for n in notes_preview) + sum(
        s.bytes for s in sources_preview
    )

    if not notes_preview and not sources_preview:
        warnings.append(
            "No notes or sources detected. Make sure the path points at "
            "exported markdown (folder, zip, or single .md)."
        )

    return NotebookImportPreviewResponse(
        source_path=str(src),
        detected_kind=detected,
        notebook_name_hint=name_hint,
        description_hint=desc_hint,
        notes=notes_preview,
        sources=sources_preview,
        has_manifest=has_manifest,
        total_bytes=total_bytes,
        warnings=warnings,
    )


# v0.7.94 — Tiny helper that mirrors studio._brief without forcing an
# import (circular-import-safe even if studio's contents change).
def _brief(exc: BaseException, *, max_len: int = 200) -> str:
    s = str(exc)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
