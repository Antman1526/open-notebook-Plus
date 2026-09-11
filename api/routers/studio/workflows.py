from __future__ import annotations

import asyncio  # v0.7.92 / v0.7.93 — wait_for + gather for parallel pages + timeouts
import csv
import html
import json
import os
import re
import zipfile
from io import StringIO
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ValidationError

from api.command_service import CommandService
from api.podcast_service import PodcastService
from api.routers.sources import save_uploaded_file
from api.schemas.studio import (
    StudioArtifactCreate,
    StudioArtifactResponse,
    StudioArtifactUpdate,
    StudioWorkflowRunCreate,
    StudioWorkflowRunResponse,
)
from deeper_notebook.ai.models import Model
from deeper_notebook.ai.provision import provision_langchain_model
from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.notebook import (
    Asset,
    Note,
    Notebook,
    Source,
    StudioArtifact,
    StudioWorkflowRun,
)
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import InvalidInputError, NotFoundError
from deeper_notebook.feature_flags import evidence_studio_enabled
from deeper_notebook.local_models.inventory import enumerate_models
from deeper_notebook.local_models.role_routing import (
    inventory_model_match_keys,
    model_match_key,
    recommend_model_roles,
)
from deeper_notebook.studio import artifact_generation as artifact_generation_service
from deeper_notebook.studio.payloads import (
    build_structured_payload,
    parse_payload_document,
)
from deeper_notebook.studio.renderers import render_artifact_markdown
from deeper_notebook.utils.text_utils import (
    clean_thinking_content,
    extract_text_content,
)

from .artifacts import (
    _ALLOWED_EXTENSIONS,
    _MAX_FILE_BYTES,
    _MAX_STUDIO_LINKS,
)
from .common import (
    _ensure_artifact_sources_ready,
    _require_evidence_studio,
    _set_workflow_step_status,
    _submit_studio_generation_command,
    _workflow_run_response,
    _workflow_steps_for_artifact,
    normalize_artifact_id,
    router,
)


def _normalize_studio_links(raw_links: list[str] | None) -> list[str]:
    if not raw_links:
        return []

    expanded: list[str] = []
    for raw in raw_links:
        value = (raw or "").strip()
        if not value:
            continue
        if value.startswith("["):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                expanded.extend(str(item).strip() for item in decoded)
                continue
        expanded.extend(part.strip() for part in re.split(r"[\n,]+", value))

    deduped: list[str] = []
    seen: set[str] = set()
    for link in expanded:
        if not link or link in seen:
            continue
        parsed = urlparse(link)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid Studio link {link!r}; use a full http(s) URL.",
            )
        seen.add(link)
        deduped.append(link)

    if len(deduped) > _MAX_STUDIO_LINKS:
        raise HTTPException(
            status_code=400,
            detail=f"Studio supports up to {_MAX_STUDIO_LINKS} links per generation.",
        )
    return deduped


def _studio_link_title(link: str) -> str:
    parsed = urlparse(link)
    path = parsed.path.rstrip("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return tail or parsed.netloc or link


# v0.7.4 — Per-source / combined caps tuned for LOCAL MODEL deployments.
#
# ONP is documented as "privacy-focused, self-hosted alternative to Notebook
# LM" — the typical deployment runs llama-cpp-python locally with 7B-9B
# models at 8k-32k context. The previous v0.7.0 defaults (50k per-file,
# 200k combined) were cloud-sized: ~50k tokens, fine for GPT-4 / Claude /
# Gemini but overflowing a Hermes-3 / Qwen 2.5 7B at 8k context.
#
# New defaults (per char ≈ 0.25 tokens):
#   - per-file: 15,000 chars ≈ 3,750 tokens
#   - combined: 60,000 chars ≈ 15,000 tokens
#
# That leaves room for the ~1k-token system prompt and an 8k-token output
# budget within a 32k-context model — and degrades gracefully (input
# truncated, output capped) on 8k-context models too.
#
# Cloud users can opt out via env vars; the defaults still produce useful
# study notebooks for any single-document upload up to ~15 KB of text.
def _env_int(name: str, default: int) -> int:
    """Read a positive int from env; fall back to default on missing/invalid."""
    raw = resolve_env(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError:
        # Don't crash startup over a bad env var; fall back loudly.
        import logging

        logging.getLogger(__name__).warning(
            "Invalid %s=%r; using default %d",
            name,
            raw,
            default,
        )
        return default


# Defaults sized for local 7B-9B models with 8k-32k context. Cloud users
# can raise these via env vars (e.g. DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS=200000).
_MAX_EXTRACT_CHARS_PER_FILE = _env_int(
    "DEEPER_NOTEBOOK_STUDIO_MAX_FILE_CHARS",
    15_000,
)
_MAX_COMBINED_CHARS = _env_int(
    "DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS",
    60_000,
)

# v0.7.1 — Cap warning-message length. Parser libraries (PyMuPDF, mammoth)
# can produce KB-long error strings with paths and partial stack traces.
# 200 chars matches api/routers/gmail.py:406 — long enough to identify
# the cause, short enough to keep response payloads small and avoid
# leaking deep path info to the client.
_MAX_WARNING_LEN = 200


def _brief(exc: BaseException) -> str:
    """Truncate exception text for safe inclusion in user-visible warnings.

    v0.7.132 — Area for Review #10. The previous version did a flat
    character truncation at byte ~199, which on multi-line exceptions
    (PyMuPDF stack traces, mammoth error blocks with embedded paths,
    LangChain provider errors with chained-cause sections) would cut
    in the middle of line 1 and lose the rest entirely. The operator
    saw "could not parse foo.pdf: TypeError: cannot conver…" — no
    indication that the actual cause was 4 lines down.

    New behavior:
      * If exception text is single-line: same as before — truncate
        at _MAX_WARNING_LEN with the ellipsis suffix.
      * If exception text is multi-line: take the first line VERBATIM
        (up to _MAX_WARNING_LEN-32 to leave room for the suffix), then
        suffix with " (… N more lines)". The operator sees the actual
        error head and knows how much was elided.

    The 32-char headroom is sized for the longest realistic suffix
    "(… 999 more lines)" with a leading space. We could be tighter
    but 32 is a clean number and the loss is negligible for messages
    that need this branch (they're invariably hundreds-of-chars long).
    """
    s = str(exc)
    # Multi-line path first — the more interesting case.
    if "\n" in s:
        lines = s.split("\n")
        first = lines[0]
        extra = len(lines) - 1
        suffix = f" (… {extra} more line{'s' if extra != 1 else ''})"
        # Leave room for the suffix when truncating the first line.
        head_budget = _MAX_WARNING_LEN - len(suffix)
        if len(first) > head_budget:
            first = first[: head_budget - 1] + "…"
        return first + suffix

    # Single-line: original behavior.
    if len(s) <= _MAX_WARNING_LEN:
        return s
    return s[: _MAX_WARNING_LEN - 1] + "…"


# v0.7.4 — Common local-model error signatures. When llama-cpp-python /
# ollama / a generic OpenAI-compatible server rejects a request because
# the input is too long, the response usually contains one of these
# substrings. We pattern-match to surface an actionable hint instead of
# the raw error.
_LOCAL_OVERFLOW_PATTERNS = (
    "context length",
    "context window",
    "max_tokens",
    "context size",
    "tokens exceeded",
    "input too long",
    "prompt is too long",
    "exceeds the model's context",
)


def _studio_generation_error_detail(
    exc: BaseException,
    *,
    notebook_id: str,
    source_count: int,
) -> str:
    """Build the 502 detail string for LLM-call failures.

    Always includes the notebook_id so the user can navigate back to
    their uploaded content. When the failure looks like a context-window
    overflow (common for local 7B-9B models with 8k-context), prepend a
    pointer to the relevant env vars so the user knows how to fix it
    rather than just retrying the same prompt against the same model.
    """
    msg = str(exc).lower()
    hint = ""
    if any(pat in msg for pat in _LOCAL_OVERFLOW_PATTERNS):
        hint = (
            "Looks like the model's context window was exceeded. Smaller "
            "local models (Hermes-3 8k, Llama-3.2-3B 4k) can't fit large "
            "documents. Try uploading fewer/smaller files, or tighten the "
            "caps via DEEPER_NOTEBOOK_STUDIO_MAX_FILE_CHARS / "
            "DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS, or pick a chat model with a "
            "larger context window in Settings → Models. "
        )
    return (
        f"{hint}Generation failed: {_brief(exc)}. "
        f"Notebook {notebook_id} was created and contains your "
        f"{source_count} uploaded source(s). Try regenerating, or check "
        "Settings → Models for a working LLM."
    )


# -----------------------------------------------------------------------------
# Prompt template — Notebook mode
# -----------------------------------------------------------------------------
# Inline rather than under prompts/ because the format is tightly coupled to
# the response-parsing logic below. If we ever expose this prompt as a
# customizable template, move it to prompts/studio/notebook.jinja.
NOTEBOOK_SYSTEM_PROMPT = """\
You are an expert educator creating a structured study notebook from the \
supplied source documents.

# Your task

Synthesize the source material into a single coherent study notebook with \
the following structure:

1. **Title** — a concise descriptive title (10 words or fewer), \
formatted as a Markdown `# H1`.
2. **Overview** — 3-5 sentence executive summary of what the material \
covers.
3. **Section-by-section breakdown** — for each major theme present in \
the sources:
   - A clear `## H2` section heading
   - Key concepts explained in your own words
   - Important definitions marked with **bold** for the term
   - Concrete examples drawn from the sources (with the source name cited)
4. **Key terms glossary** — alphabetized list of technical terms with \
definitions. Use `### H3` for this section.
5. **Review questions** — 5-10 questions of varying difficulty (factual \
recall, conceptual, applied). Mix open-ended and short-answer forms.

# Constraints

- **Stay faithful to the source.** Do NOT invent facts, dates, names, \
quotes, or statistics that aren't in the input.
- **Cite specific claims.** When you draw a specific claim from one of \
the sources, cite it inline like `(source: <filename>)` so the reader \
can verify.
- **Surface disagreements.** If the sources conflict on a point, note \
the disagreement explicitly rather than silently picking a side.
- **Don't pad.** If the sources are insufficient for a section, say so \
plainly — don't fill with general knowledge.
- **Output clean Markdown.** Use `##` for section headings, `**bold**` \
for definitions and key terms, `>` for direct quotes from sources.
- Aim for ~1500-3000 words. Shorter is fine for thin source material; \
do not pad to hit a length target.
"""


# -----------------------------------------------------------------------------
# v0.7.89 — Multi-page notebook generation
# -----------------------------------------------------------------------------
# Default ON. Falls back to the legacy single-note path (NOTEBOOK_SYSTEM_PROMPT
# above) if disabled OR if the outline pass returns un-parseable JSON.
_MULTIPAGE_ENABLED = resolve_env(
    "DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_MULTIPAGE", "true"
).strip().lower() not in ("0", "false", "no", "off")
# Caps pages to bound LLM cost. Outline LLM is *also* told this number so it
# doesn't propose more than we can render.
_PAGES_MAX = _env_int("DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_PAGES_MAX", 6)
if _PAGES_MAX < 2:
    _PAGES_MAX = 2  # one overview + at least one detail page
if _PAGES_MAX > 12:
    _PAGES_MAX = 12  # rate-limit defense; local 7B-9Bs would crawl past this

# v0.7.92 — Optional parallel page generation. Default OFF because the
# desktop bundle's local-LLM dual-server (llama-cpp embed + chat) has
# limited concurrency and gathered ainvoke calls can OOM or starve
# tokens. Cloud users (OpenAI, Anthropic, etc.) can opt in for ~Nx
# speedup on multi-page generation. The trade-off: parallel calls
# mean per-page failures can interleave in logs, but the final result
# is identical (each page still gets its own warning on failure).
_PARALLEL_PAGES = resolve_env(
    "DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_PARALLEL_PAGES", "false"
).strip().lower() in ("1", "true", "yes", "on")
# v0.7.93 — Per-page generation timeout. Local LLMs (especially the
# desktop bundle's llama-cpp chat server) can hang indefinitely when
# the model is mid-loading, mid-prompt-eval, or the prompt overflows
# context. Without a cap, ONE stuck page blocks the entire notebook
# generation request — including subsequent pages, the response, and
# the user's browser tab. Default: 180s, plenty for a 7B-9B at 8k
# context. Cloud users with stable APIs can raise via env.
_PAGE_TIMEOUT_SEC = _env_int("DEEPER_NOTEBOOK_STUDIO_PAGE_TIMEOUT_SEC", 180)
# Outline pass gets its own (shorter) timeout — JSON-only response,
# small token budget, should be fast.
_OUTLINE_TIMEOUT_SEC = _env_int("DEEPER_NOTEBOOK_STUDIO_OUTLINE_TIMEOUT_SEC", 90)

# Outline pass: small JSON response. Keep token budget tight — this prompt
# does NOT need to expand on any topic, just identify the structure.
NOTEBOOK_OUTLINE_PROMPT = """\
You are planning the structure of a multi-page study notebook from the \
supplied source documents. You will return ONLY a single JSON object — no \
prose before or after, no markdown fence — matching this schema EXACTLY:

{{
  "headline": "<one-sentence punchy summary of what these documents are about, ≤140 chars>",
  "summary": "<2-3 paragraph executive summary, plain prose, no bullets, no headings>",
  "pages": [
    {{
      "title": "<short page title, ≤60 chars, no markdown>",
      "focus": "<1-2 sentence description of what this page must cover>",
      "key_questions": ["<question 1>", "<question 2>", "..."]
    }}
  ],
  "top_suggestions": [
    "<concrete recommendation a reader should act on>",
    "..."
  ]
}}

# Rules

- Return strictly valid JSON. No trailing commas, no comments, no \
explanation, no markdown fence (`​`​`​`​`​`json … `​`​`​`).
- `pages` MUST have between 3 and {max_pages} entries — pick a count that \
genuinely fits the source material. A 5-paragraph press release deserves 3 \
pages, not 6. A dense technical white-paper can use all {max_pages}.
- Pages must be **distinct subjects** (e.g. "Architecture", "Backend \
internals", "Deployment", "Risks") — not generic ("Introduction", \
"Body", "Conclusion"). Read the source material and let the topics emerge.
- `key_questions` per page: 3-5 specific, source-grounded questions a \
reader of that page should be able to answer.
- `top_suggestions`: 3-6 concrete recommendations the user should consider \
based on what the documents reveal — gaps to fill, decisions to validate, \
risks to mitigate, follow-up reading. Real advice, not platitudes.
- `headline` + `summary` are the user's first impression. Make them \
information-dense and faithful to the sources.
- Stay grounded in the source. Do NOT invent facts. If the sources are \
thin, propose fewer pages — quality over quantity.
"""


# Per-page pass: produce ONE expanded page worth of content. The outline-pass
# decided what this page covers; this prompt fleshes it out. Each page ends
# with a "💡 AI Suggestions for this page" block so guidance shows up in
# context — not buried at the end of the notebook.
NOTEBOOK_PAGE_PROMPT = """\
You are writing **one page** of a multi-page study notebook. The notebook's \
overall topic is: **{notebook_title}**. Other pages cover the rest of the \
material; this page must focus EXCLUSIVELY on the subject below.

# This page

- **Page title:** {page_title}
- **Focus:** {page_focus}
- **Questions this page must answer:**
{page_questions}

# What to produce

Return clean Markdown for ONE page. Use this structure:

```
# {page_title}

<3-6 sentence intro framing what this page covers and why it matters>

## Key concepts
- **Concept** — explanation (source: <filename> if relevant)
- ...

## Details
<Substantive prose explaining the topic in depth, grounded in the sources.
Use ### subheadings if the page has natural sub-topics. Quote sources
with > blockquotes when reproducing exact wording.>

## Open questions for the reader
- <question> — <brief framing of what to look for in the sources to answer>
- ...

## 💡 AI Suggestions for this page
- **<Action verb> ...** — <concrete recommendation tied to what THIS page covered>
- ...
```

# Constraints

- **3-5 suggestions** in the AI Suggestions block, each starting with a verb \
("Verify", "Document", "Replace", "Investigate", "Add", "Defer"). Each one \
specific enough that a reader knows what to do next.
- Stay strictly within this page's focus. Do NOT cover other pages' topics.
- Cite sources inline as `(source: <filename>)` for specific factual claims.
- If the sources don't say enough to answer a key question, say so plainly \
in "Open questions for the reader" — don't pad with general knowledge.
- Output ONLY the Markdown for the page. No preamble like "Here is the \
page:". No closing remarks. Start with the `#` heading.
- Target ~400-900 words per page; thin sources → shorter is fine.
"""


# -----------------------------------------------------------------------------
# Prompt template — Podcast mode briefing suffix
# -----------------------------------------------------------------------------
# Appended to the episode profile's briefing so the generated podcast stays
# focused on the user's documents rather than drifting into general
# conversation. Episode profiles already define the host personas + style.
PODCAST_BRIEFING_SUFFIX = """\
Stay strictly grounded in the user's uploaded source documents below. \
Do not invent statistics, dates, or attributions that aren't in the sources. \
If a topic touches a gap in the sources, say so on-air ("the docs don't \
actually tell us X") rather than filling it with general knowledge.
"""


# -----------------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------------


class StudioGenerateResponse(BaseModel):
    """Returned to the frontend after a Studio generation request.

    The frontend uses `notebook_id` to navigate to the result. For podcast
    mode, `job_id` lets the frontend poll /api/commands/{job_id} for
    transcription + audio rendering progress.

    v0.7.88 — `mode="both"` populates BOTH `note_id` and `job_id`. Either
    half can succeed independently; partial failures land in `warnings`
    so the user keeps whatever did succeed.

    v0.7.89 — notebook + both modes now generate a MULTI-PAGE notebook
    (one Overview note + N per-topic pages, each with an inline
    "💡 AI Suggestions" block). `note_id` continues to point at the
    Overview note for backward compatibility; `note_ids` carries every
    note id in render order (overview first, then pages). When the
    outline pass fails and we fall back to single-note output,
    `note_ids` contains just the one entry.
    """

    notebook_id: str
    mode: str  # "notebook" | "podcast" | "both"
    note_id: Optional[str] = None  # notebook + both: overview note (back-compat)
    note_ids: list[str] = []  # v0.7.89 — all notes in render order
    job_id: Optional[str] = None  # podcast  + both: surreal_commands job id
    source_ids: list[str]
    title: str
    warnings: list[str] = []  # non-fatal issues (e.g. a file couldn't be extracted)


# -----------------------------------------------------------------------------
# Artifact endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/artifacts/{artifact_id}/workflow-runs",
    response_model=StudioWorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_studio_workflow_run(
    artifact_id: str,
    payload: StudioWorkflowRunCreate,
) -> StudioWorkflowRunResponse:
    _require_evidence_studio()
    artifact_id = normalize_artifact_id(artifact_id)
    try:
        artifact = await StudioArtifact.get(artifact_id)
    except (KeyError, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio artifact not found",
        )

    approval_required = payload.approval_required
    run = StudioWorkflowRun(
        artifact_id=str(artifact.id),
        notebook_id=str(artifact.notebook_id),
        title=payload.title,
        status="awaiting_approval" if approval_required else "queued",
        source_ids=payload.source_ids
        or [str(source_id) for source_id in artifact.source_ids],
        approval_required=approval_required,
        steps=_workflow_steps_for_artifact(
            artifact,
            approval_required=approval_required,
        ),
    )
    if not approval_required:
        if run.source_ids:
            artifact.source_ids = [str(source_id) for source_id in run.source_ids]
        await _ensure_artifact_sources_ready(artifact)
    await run.save()
    if not approval_required:
        await _submit_studio_generation_command(artifact, run)
    return _workflow_run_response(run)


@router.get(
    "/artifacts/{artifact_id}/workflow-runs",
    response_model=list[StudioWorkflowRunResponse],
)
async def list_studio_workflow_runs(
    artifact_id: str,
) -> list[StudioWorkflowRunResponse]:
    _require_evidence_studio()
    artifact_id = normalize_artifact_id(artifact_id)
    try:
        await StudioArtifact.get(artifact_id)
    except (KeyError, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio artifact not found",
        )

    runs = await StudioWorkflowRun.get_for_artifact(artifact_id)
    return [_workflow_run_response(run) for run in runs]


@router.post(
    "/workflow-runs/{run_id}/approve",
    response_model=StudioWorkflowRunResponse,
)
async def approve_studio_workflow_run(
    run_id: str,
) -> StudioWorkflowRunResponse:
    _require_evidence_studio()
    try:
        run = await StudioWorkflowRun.get(run_id)
    except (KeyError, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio workflow run not found",
        )
    try:
        artifact = await StudioArtifact.get(str(run.artifact_id))
    except (KeyError, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio artifact not found",
        )

    run.status = "queued"
    run.approval_required = False
    _set_workflow_step_status(run, {"privacy_gate"}, "completed")
    _set_workflow_step_status(run, {"model_route", "artifact_generation"}, "pending")
    await _submit_studio_generation_command(artifact, run)
    return _workflow_run_response(run)


@router.post("/generate", response_model=StudioGenerateResponse)
async def studio_generate(
    files: Optional[list[UploadFile]] = File(
        None, description="One or more documents to ingest"
    ),
    links: Optional[list[str]] = Form(
        None, description="Optional http(s) links to ingest"
    ),
    mode: str = Form(..., description="'notebook', 'podcast', or 'both'"),
    title: Optional[str] = Form(
        None, description="Notebook title; auto-generated if absent"
    ),
    episode_profile_name: Optional[str] = Form(
        None,
        description="Required for podcast / both — name of an EpisodeProfile record",
    ),
    speaker_profile_name: Optional[str] = Form(
        None,
        description="Required for podcast / both — name of a SpeakerProfile record",
    ),
) -> StudioGenerateResponse:
    """One-shot upload → generate. See module docstring for the full flow."""

    # 1. Validate inputs upfront so we don't half-create a notebook then fail.
    # v0.7.88 — `both` mode runs notebook generation synchronously and then
    # submits the podcast command. Either half can independently fail; the
    # warnings array carries any partial-failure context so the user keeps
    # whatever did succeed.
    if mode not in ("notebook", "podcast", "both"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'notebook', 'podcast', or 'both'",
        )
    files = files or []
    normalized_links = _normalize_studio_links(links)
    if not files and not normalized_links:
        raise HTTPException(
            status_code=400, detail="at least one file or link is required"
        )
    if mode in ("podcast", "both"):
        if not episode_profile_name or not speaker_profile_name:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{mode.capitalize()} mode requires both episode_profile_name "
                    "and speaker_profile_name. Available profiles can be fetched "
                    "from /api/episode-profiles and /api/speaker-profiles."
                ),
            )

    for f in files:
        if not f.filename:
            raise HTTPException(
                status_code=400, detail="all files must have a filename"
            )
        ext = Path(f.filename).suffix.lower()
        if ext and ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type {ext!r} for {f.filename!r}. "
                    f"Allowed: {sorted(_ALLOWED_EXTENSIONS)}"
                ),
            )
        # size validation (UploadFile.size is in newer FastAPI; defensive)
        size = getattr(f, "size", None)
        if size is not None and size > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File {f.filename!r} is {size} bytes; per-file cap is "
                    f"{_MAX_FILE_BYTES} bytes (~{_MAX_FILE_BYTES // 1024 // 1024} MB)."
                ),
            )

    # 2. Title default — use the first file's stem if user didn't supply one.
    if not title:
        if files:
            first = Path(files[0].filename or "Untitled").stem
        else:
            first = _studio_link_title(normalized_links[0])
        title = f"Studio: {first[:80]}"

    # 3. Create the Notebook record.
    try:
        notebook = Notebook(
            name=title[:200],
            description=(
                f"Generated via Studio from {len(files)} file(s) and "
                f"{len(normalized_links)} link(s); mode={mode}"
            ),
        )
        await notebook.save()
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.182 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        # v0.7.178 — Sanitize 500 detail (same pattern as v0.7.168
        # / v0.7.177 sweeps). logger.exception above captures the
        # full traceback for ops; the client gets a generic message.
        logger.exception("Studio: failed to create notebook")
        raise HTTPException(status_code=500, detail="Could not create notebook")
    notebook_id = str(notebook.id)

    # 4. Per-input: save/link → Source → extract → link notebook.
    source_ids: list[str] = []
    extracted: list[tuple[str, str]] = []  # (filename, parsed_text)
    warnings: list[str] = []

    # Lazy import to avoid pulling content_core into module load
    from content_core import extract_content
    from content_core.common import ProcessSourceState

    async def _extract_and_persist_source(
        *,
        source: Source,
        label: str,
        process_state,
    ) -> None:
        try:
            _extract_timeout = float(
                resolve_env("DEEPER_NOTEBOOK_STUDIO_EXTRACT_TIMEOUT_SEC", "60").strip()
                or 60
            )
            try:
                processed = await asyncio.wait_for(
                    extract_content(process_state),
                    timeout=_extract_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Studio: extract_content timed out for {!r} after {}s",
                    label,
                    _extract_timeout,
                )
                warnings.append(
                    f"Parsing {label!r} timed out after {_extract_timeout:.0f}s. "
                    "The source may be inaccessible, malformed, or password-protected. "
                    "Raise DEEPER_NOTEBOOK_STUDIO_EXTRACT_TIMEOUT_SEC or provide a cleaner source."
                )
                return
            text = (processed.content or "").strip()
            if not text:
                warnings.append(
                    f"No text could be extracted from {label!r} — the source may be "
                    "empty, inaccessible, image-only (no OCR), or in a corrupt state."
                )
                return
            if len(text) > _MAX_EXTRACT_CHARS_PER_FILE:
                logger.info(
                    "Studio: truncating {!r} from {} → {} chars",
                    label,
                    len(text),
                    _MAX_EXTRACT_CHARS_PER_FILE,
                )
                text = text[:_MAX_EXTRACT_CHARS_PER_FILE] + "\n\n[…truncated…]"
            extracted.append((label, text))
            source.full_text = text
            if processed.title and not source.title:
                source.title = processed.title
            extraction_provenance = {
                key: value
                for key, value in {
                    "content_source_type": getattr(processed, "source_type", None),
                    "identified_type": getattr(processed, "identified_type", None),
                    "extractor": "content_core",
                    "url": getattr(processed, "url", None),
                    "file_path": getattr(processed, "file_path", None),
                }.items()
                if value is not None
            }
            content_metadata = getattr(processed, "metadata", None)
            if isinstance(content_metadata, dict):
                extraction_provenance["content_metadata"] = content_metadata
            if extraction_provenance:
                source.provenance = {
                    **(source.provenance or {}),
                    "extraction": extraction_provenance,
                }
            await source.save()
            try:
                await source.vectorize()
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning(
                    "Studio: vectorize failed (non-fatal) for {!r}: {}", label, exc
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Studio: extract_content failed for {!r}", label)
            warnings.append(f"Could not parse {label!r}: {_brief(exc)}")

    for upload in files:
        filename = upload.filename or "upload"
        try:
            # v0.7.1 — pass max_bytes through so chunked-transfer-encoded
            # uploads can't bypass the size cap (UploadFile.size is None
            # for those, so the pre-check above silently skips).
            saved_path = await save_uploaded_file(upload, max_bytes=_MAX_FILE_BYTES)
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            logger.warning(
                "Studio: save_uploaded_file failed for {!r}: {}", filename, exc
            )
            warnings.append(f"Could not save {filename!r}: {_brief(exc)}")
            continue

        # Create + link the Source first so it's visible even if extract fails
        try:
            source = Source(
                title=Path(filename).name,
                asset=Asset(file_path=saved_path),
                provenance={"origin": "studio_generate", "mode": mode},
                source_type="upload",
            )
            await source.save()
            await source.add_to_notebook(notebook_id)
            source_ids.append(str(source.id))
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            logger.warning("Studio: source create failed for {!r}: {}", filename, exc)
            warnings.append(f"Could not create source for {filename!r}: {_brief(exc)}")
            continue

        await _extract_and_persist_source(
            source=source,
            label=filename,
            process_state=ProcessSourceState(
                file_path=saved_path, output_format="markdown"
            ),
        )

    for link in normalized_links:
        try:
            source = Source(
                title=_studio_link_title(link),
                asset=Asset(url=link),
                provenance={"origin": "studio_generate", "mode": mode, "url": link},
                source_type="link",
            )
            await source.save()
            await source.add_to_notebook(notebook_id)
            source_ids.append(str(source.id))
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Studio: link source create failed for {!r}: {}", link, exc)
            warnings.append(f"Could not create source for {link!r}: {_brief(exc)}")
            continue

        await _extract_and_persist_source(
            source=source,
            label=link,
            process_state=ProcessSourceState(url=link, output_format="markdown"),
        )

    if not extracted:
        # We created an empty notebook + maybe some empty sources. That's
        # actually a valid state (user can manually add content), but the
        # user explicitly asked for a generated output and we have nothing
        # to feed the LLM. Surface as a clear error.
        raise HTTPException(
            status_code=400,
            detail=(
                f"No usable text could be extracted from the {len(files)} uploaded "
                f"file(s) and {len(normalized_links)} link(s). Notebook {notebook_id} "
                "was created and contains the source records (visible in the UI), but generation was "
                "skipped. Warnings: " + "; ".join(warnings)
            ),
        )

    # Build combined context — clearly delimited per-source so the LLM can
    # cite the right one.
    combined_chunks: list[str] = []
    running = 0
    for name, text in extracted:
        block = f"\n\n---\n\n# Source: {name}\n\n{text}"
        if running + len(block) > _MAX_COMBINED_CHARS:
            warnings.append(
                f"Combined context capped at {_MAX_COMBINED_CHARS:,} chars; "
                f"{name!r} and any subsequent sources were excluded from "
                "the LLM prompt. They're still saved as Sources on the notebook."
            )
            break
        combined_chunks.append(block)
        running += len(block)
    combined_context = "".join(combined_chunks).lstrip()

    # 5. Dispatch by mode.
    #
    # v0.7.130 — wrap the dispatch in a try/except so we can emit
    # `studio_generations_total{mode, outcome}` even when the dispatcher
    # itself raises. The `outcome` heuristic:
    #   - 'success'  — dispatch returned with no warnings whose message
    #                  starts with "Podcast " / "Notebook " (those are
    #                  the partial-failure prefixes used inside
    #                  _dispatch_both_modes when one half fails).
    #   - 'partial'  — `both` mode where exactly one half succeeded.
    #                  Detected by the presence of one of the prefixed
    #                  warnings in the returned response.
    #   - 'failed'   — dispatch raised, OR the response indicates both
    #                  halves of a `both` request failed.
    # Best-effort: a metric increment failure must NEVER mask the
    # actual response (success or error) we're trying to give the user.
    def _record_outcome(outcome: str) -> None:
        try:
            from api.metrics import record_studio_generation

            record_studio_generation(mode, outcome)
        except Exception as exc:
            # v0.8.45 — best-effort metric increment must never mask the
            # user's response, but log at DEBUG so a broken metrics path
            # is discoverable (v0.8.27-v0.8.35f silent-except convention).
            logger.debug("Studio: record_studio_generation failed: {}", exc)

    def _classify_outcome(resp: "StudioGenerateResponse") -> str:
        # Look at the response warnings to decide success vs partial.
        # In 'notebook' / 'podcast' mode there's no "partial" — either
        # we returned a usable artifact or we raised. So success only.
        # In 'both' mode, a half-failure produces a warning prefixed
        # with "Podcast " or "Notebook " telling the user which side
        # broke. If both halves broke, the dispatcher itself raises.
        if mode != "both":
            return "success"
        partial_markers = ("Podcast ", "Notebook ")
        for w in resp.warnings or []:
            if any(w.startswith(p) for p in partial_markers):
                return "partial"
        return "success"

    try:
        if mode == "notebook":
            response = await _dispatch_notebook_mode(
                notebook=notebook,
                combined_context=combined_context,
                title=title,
                source_ids=source_ids,
                warnings=warnings,
            )
        elif mode == "podcast":
            response = await _dispatch_podcast_mode(
                notebook_id=notebook_id,
                episode_profile_name=episode_profile_name,  # type: ignore[arg-type]
                speaker_profile_name=speaker_profile_name,  # type: ignore[arg-type]
                title=title,
                source_ids=source_ids,
                warnings=warnings,
            )
        else:
            # v0.7.88 — mode == "both": run notebook synchronously, then
            # submit the podcast job. Half-failures degrade gracefully —
            # whichever half succeeded is preserved, and warnings carry
            # the diagnostic.
            response = await _dispatch_both_modes(
                notebook=notebook,
                notebook_id=notebook_id,
                combined_context=combined_context,
                episode_profile_name=episode_profile_name,  # type: ignore[arg-type]
                speaker_profile_name=speaker_profile_name,  # type: ignore[arg-type]
                title=title,
                source_ids=source_ids,
                warnings=warnings,
            )
    except HTTPException:
        # Typed HTTPExceptions (400/422 etc.) — count as 'failed' for
        # observability even though FastAPI returns them properly.
        _record_outcome("failed")
        raise
    except Exception:
        _record_outcome("failed")
        raise

    _record_outcome(_classify_outcome(response))
    return response


# -----------------------------------------------------------------------------
# Mode dispatchers
# -----------------------------------------------------------------------------


# v0.7.89 — Strip the common ways an LLM wraps a JSON payload (markdown
# fences, "Here is the JSON:" preambles, trailing commentary). Returns
# the raw JSON substring if found, else the original text.
def _strip_json_wrapper(text: str) -> str:
    s = (text or "").strip()
    # Strip ```json … ``` or ``` … ``` fences
    if s.startswith("```"):
        # Drop first fence line
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        # Drop trailing fence
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    # Slice from first { to last } to discard preamble/postamble
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        return s[first : last + 1]
    return s


# v0.7.89 — Validate and normalize the outline JSON. Returns (outline, error).
# A bad outline returns (None, "reason") so caller can fall back to single-note.
def _validate_outline(
    payload: dict, *, max_pages: int
) -> tuple[Optional[dict], Optional[str]]:
    if not isinstance(payload, dict):
        return None, "outline is not a JSON object"
    headline = (payload.get("headline") or "").strip()
    summary = (payload.get("summary") or "").strip()
    pages = payload.get("pages") or []
    top_suggestions = payload.get("top_suggestions") or []
    if not headline:
        return None, "outline missing 'headline'"
    if not summary:
        return None, "outline missing 'summary'"
    if not isinstance(pages, list) or not pages:
        return None, "outline 'pages' must be a non-empty list"
    if len(pages) > max_pages:
        # Soft cap rather than reject — trim to the configured ceiling.
        pages = pages[:max_pages]
    cleaned_pages: list[dict] = []
    for i, p in enumerate(pages):
        if not isinstance(p, dict):
            continue
        ptitle = (p.get("title") or f"Page {i + 1}").strip()[:80]
        pfocus = (p.get("focus") or "").strip()
        pqs_raw = p.get("key_questions") or []
        pqs = [str(q).strip() for q in pqs_raw if str(q).strip()]
        if not pfocus and not pqs:
            # Page is empty — skip; LLM probably padded.
            continue
        cleaned_pages.append({"title": ptitle, "focus": pfocus, "key_questions": pqs})
    if not cleaned_pages:
        return None, "outline 'pages' had no usable entries after validation"
    if not isinstance(top_suggestions, list):
        top_suggestions = []
    top_suggestions = [str(s).strip() for s in top_suggestions if str(s).strip()]
    return (
        {
            "headline": headline[:200],
            "summary": summary,
            "pages": cleaned_pages,
            "top_suggestions": top_suggestions,
        },
        None,
    )


# v0.7.89 — Compose the Overview note's Markdown. This is the user's first
# stop in the multi-page notebook; it bundles headline, summary, table of
# contents (so they can scan), and top-level suggestions.
def _render_overview_note(*, title: str, outline: dict, page_titles: list[str]) -> str:
    headline = outline["headline"]
    summary = outline["summary"]
    top_suggestions = outline.get("top_suggestions") or []
    lines: list[str] = []
    lines.append(f"# 📋 {title} — Overview")
    lines.append("")
    lines.append(f"> **Headline:** {headline}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(summary)
    lines.append("")
    if page_titles:
        lines.append("## Pages in this notebook")
        lines.append("")
        for i, pt in enumerate(page_titles, start=1):
            lines.append(f"{i}. **{pt}**")
        lines.append("")
    if top_suggestions:
        lines.append("## 💡 Top suggestions from the AI reviewer")
        lines.append("")
        for s in top_suggestions:
            lines.append(f"- {s}")
        lines.append("")
    lines.append(
        "_This notebook was generated by Studio (multi-page mode). Open the "
        "individual pages below for the deep dive on each topic; each one "
        "closes with its own 💡 suggestions block._"
    )
    return "\n".join(lines)


# v0.7.89 — Single LLM call → JSON outline. Wrapped so the caller can
# decide whether to fall back gracefully on failure.
async def _generate_outline(
    *,
    combined_context: str,
    notebook_id: str,
    source_count: int,
) -> dict:
    """Returns the validated outline dict. Raises HTTPException on hard failure."""
    import json

    # v0.7.89 — provision_langchain_model can itself raise on credential
    # config errors. Keep it inside the try/except so any failure (provision
    # or ainvoke) yields the proper 502+notebook_id message rather than a
    # bare 500.
    system_prompt = NOTEBOOK_OUTLINE_PROMPT.format(max_pages=_PAGES_MAX)
    try:
        chain = await provision_langchain_model(
            combined_context,
            None,
            "chat",
            max_tokens=2048,
        )
        # v0.7.93 — wrap in wait_for so a hung local LLM (stuck loading /
        # mid-prompt-eval / overflowed context) becomes a typed 502 with
        # an actionable hint instead of hanging the request indefinitely.
        response = await asyncio.wait_for(
            chain.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=combined_context),
                ]
            ),
            timeout=_OUTLINE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Studio multi-page: outline pass timed out after {}s",
            _OUTLINE_TIMEOUT_SEC,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Outline generation timed out after {_OUTLINE_TIMEOUT_SEC}s. "
                "The chat model may be loading or overloaded. Try again, or "
                "raise DEEPER_NOTEBOOK_STUDIO_OUTLINE_TIMEOUT_SEC. "
                f"Notebook {notebook_id} was created and contains your "
                f"{source_count} uploaded source(s)."
            ),
        ) from exc
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.182 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        logger.exception("Studio multi-page: outline pass failed")
        raise HTTPException(
            status_code=502,
            detail=_studio_generation_error_detail(
                exc,
                notebook_id=notebook_id,
                source_count=source_count,
            ),
        )
    raw = extract_text_content(response.content)
    cleaned = clean_thinking_content(raw)
    json_text = _strip_json_wrapper(cleaned)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        # Don't raise — this is the signal for the caller to fall back to
        # legacy single-note generation. Log enough to diagnose.
        # v0.7.89 — loguru uses {} formatting, not %-style. (The existing
        # %s strings elsewhere in this file are silently broken — see
        # report.)
        logger.warning(
            "Studio multi-page: outline JSON parse failed ({}); raw={!r}",
            exc,
            cleaned[:500],
        )
        # v0.7.130 — emit the Prometheus counter. Best-effort: a metrics
        # import failure must not break the caller's fallback flow.
        try:
            from api.metrics import record_studio_outline_parse_failure

            record_studio_outline_parse_failure("json_decode")
        except Exception as metric_exc:
            # v0.8.45 — DEBUG log the swallowed metric failure (the
            # ValueError below is the real signal; this guard only
            # protects the metric increment). v0.8.35f convention.
            logger.debug(
                "Studio: record_studio_outline_parse_failure(json_decode) failed: {}",
                metric_exc,
            )
        raise ValueError(f"outline JSON parse failed: {exc}")
    outline, err = _validate_outline(payload, max_pages=_PAGES_MAX)
    if not outline:
        logger.warning(
            "Studio multi-page: outline validation failed ({}); raw={!r}",
            err,
            cleaned[:500],
        )
        # v0.7.130 — counterpart for the validation-failure path. Same
        # try/except shield so observability can't break the request.
        try:
            from api.metrics import record_studio_outline_parse_failure

            record_studio_outline_parse_failure("validation")
        except Exception as metric_exc:
            # v0.8.45 — DEBUG log the swallowed metric failure
            # (v0.8.35f convention; the ValueError below is the signal).
            logger.debug(
                "Studio: record_studio_outline_parse_failure(validation) failed: {}",
                metric_exc,
            )
        raise ValueError(f"outline validation failed: {err}")
    return outline


# v0.7.89 — Generate one page of markdown content for the multi-page notebook.
# Returns the (possibly empty) markdown string. Caller decides how to handle
# empties / exceptions.
async def _generate_page(
    *,
    combined_context: str,
    notebook_title: str,
    page_spec: dict,
) -> str:
    """Returns the page Markdown. Raises on LLM failure (caller turns into warning).

    v0.7.93 — wrapped in asyncio.wait_for so a stuck local LLM becomes a
    TimeoutError caught by the caller's per-page warning path instead of
    blocking the whole notebook generation request.
    """
    questions_md = "\n".join(f"  - {q}" for q in page_spec.get("key_questions", []))
    if not questions_md:
        questions_md = (
            "  - (No specific questions listed; cover the focus area thoroughly.)"
        )
    system_prompt = NOTEBOOK_PAGE_PROMPT.format(
        notebook_title=notebook_title,
        page_title=page_spec["title"],
        page_focus=page_spec.get("focus") or "(see questions below)",
        page_questions=questions_md,
    )
    chain = await provision_langchain_model(
        combined_context,
        None,
        "chat",
        max_tokens=3072,
    )
    response = await asyncio.wait_for(
        chain.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=combined_context),
            ]
        ),
        timeout=_PAGE_TIMEOUT_SEC,
    )
    raw = extract_text_content(response.content)
    return clean_thinking_content(raw).strip()


# v0.7.92 / v0.7.93 — Generate every page in the outline, with sequential
# (default) or parallel (env-opt-in) execution AND per-page timeouts.
# Returns a list of (note_title, body) pairs in render order (page index
# order — sequential trivially preserves it; parallel mode preserves it
# via the result-zipping below). Failed pages become warnings; survivors
# still ship.
async def _generate_all_pages(
    *,
    combined_context: str,
    notebook_title: str,
    page_specs: list[dict],
    warnings: list[str],
) -> list[tuple[str, str]]:
    page_contents: list[tuple[str, str]] = []

    def _on_page_failure(i: int, page_spec: dict, exc: BaseException) -> None:
        # v0.7.93 — TimeoutError gets a more actionable warning than a
        # generic exception. The user needs to know "raise the timeout
        # or pick a faster model", not just "something failed".
        if isinstance(exc, asyncio.TimeoutError):
            logger.warning(
                "Studio multi-page: page {} ({!r}) timed out after {}s",
                i,
                page_spec["title"],
                _PAGE_TIMEOUT_SEC,
            )
            warnings.append(
                f"Page {i} ({page_spec['title']!r}) timed out after "
                f"{_PAGE_TIMEOUT_SEC}s. Raise DEEPER_NOTEBOOK_STUDIO_PAGE_TIMEOUT_SEC, "
                "or switch to a faster chat model."
            )
        else:
            logger.warning(
                "Studio multi-page: page {} ({!r}) generation failed: {}",
                i,
                page_spec["title"],
                _brief(exc),
            )
            warnings.append(
                f"Page {i} ({page_spec['title']!r}) could not be generated: "
                f"{_brief(exc)}"
            )

    if _PARALLEL_PAGES:
        # All pages in flight at once. return_exceptions=True so a single
        # failure doesn't cancel the rest mid-way.
        coros = [
            _generate_page(
                combined_context=combined_context,
                notebook_title=notebook_title,
                page_spec=p,
            )
            for p in page_specs
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for i, (page_spec, result) in enumerate(zip(page_specs, results), start=1):
            if isinstance(result, BaseException):
                _on_page_failure(i, page_spec, result)
                continue
            if not result:
                warnings.append(
                    f"Page {i} ({page_spec['title']!r}) returned empty content."
                )
                continue
            page_contents.append((f"📄 {i:02d} · {page_spec['title']}", result))
        return page_contents

    # Sequential (default, local-LLM-safe).
    for i, page_spec in enumerate(page_specs, start=1):
        try:
            body = await _generate_page(
                combined_context=combined_context,
                notebook_title=notebook_title,
                page_spec=page_spec,
            )
        except HTTPException:
            # v0.7.108 — re-raise typed HTTPExceptions so the next
            # `except Exception` doesn't clobber them to 500.
            raise
        except Exception as exc:
            _on_page_failure(i, page_spec, exc)
            continue
        if not body:
            warnings.append(
                f"Page {i} ({page_spec['title']!r}) returned empty content."
            )
            continue
        page_contents.append((f"📄 {i:02d} · {page_spec['title']}", body))
    return page_contents


# v0.7.89 — Save a list of (title, content) notes to the notebook. Returns
# the list of saved note IDs in input order. Stops on the first save failure
# so we don't leak partially-attached notes; partial successes are returned
# to the caller via the IDs already in the list.
async def _save_notebook_notes(
    *,
    notebook_id: str,
    notes_to_save: list[tuple[str, str]],
) -> list[str]:
    saved_ids: list[str] = []
    for note_title, note_content in notes_to_save:
        # v0.7.89 — Note.content_must_not_be_empty rejects empty/whitespace.
        # Substitute a sentinel so the user gets *something* attached to the
        # notebook rather than silently dropping the page.
        body = note_content.strip() if note_content else ""
        if not body:
            body = "(The model returned no content for this page.)"
        note = Note(title=note_title[:200], content=body, note_type="ai")
        await note.save()
        await note.add_to_notebook(notebook_id)
        saved_ids.append(str(note.id))
    return saved_ids


async def _dispatch_notebook_mode(
    *,
    notebook: Notebook,
    combined_context: str,
    title: str,
    source_ids: list[str],
    warnings: list[str],
) -> StudioGenerateResponse:
    """v0.7.89 — Multi-page notebook generation.

    Flow:
      1. Outline pass (one LLM call → JSON: headline, summary, pages,
         top_suggestions). On failure → fall back to legacy single-note.
      2. Per-page pass (one LLM call per page, sequential). Per-page
         failures become warnings; surviving pages still ship.
      3. Persist: one Overview note (headline + summary + TOC + top
         suggestions) + one note per page (each ending in
         "💡 AI Suggestions"). Saved in render order so the user sees
         the Overview first in the notebook UI.

    The legacy single-note path remains reachable via the
    DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_MULTIPAGE=false env var or whenever the
    outline pass returns un-parseable JSON. That keeps the user shielded
    from regressions during the rollout window.

    Returns a StudioGenerateResponse with `note_id` pointing at the
    Overview note (back-compat) and `note_ids` carrying all saved notes.
    """
    notebook_id = str(notebook.id)

    if not _MULTIPAGE_ENABLED:
        return await _dispatch_notebook_mode_singlenote(
            notebook=notebook,
            combined_context=combined_context,
            title=title,
            source_ids=source_ids,
            warnings=warnings,
        )

    # 1. Outline pass.
    try:
        outline = await _generate_outline(
            combined_context=combined_context,
            notebook_id=notebook_id,
            source_count=len(source_ids),
        )
    except ValueError as exc:
        # JSON parse / validation failure — fall back to single-note so
        # the user still gets a usable artifact.
        logger.warning(
            "Studio multi-page: falling back to single-note ({})",
            exc,
        )
        # v0.7.130 — emit the fallback counter. Specific outline-parse
        # failure reason (json_decode vs validation) was already recorded
        # inside _generate_outline; here we just track that we DID fall
        # back rather than crashing or succeeding multi-page.
        try:
            from api.metrics import record_studio_single_note_fallback

            record_studio_single_note_fallback()
        except Exception as metric_exc:
            # v0.8.45 — DEBUG log the swallowed metric failure
            # (v0.8.35f convention). Fallback proceeds regardless.
            logger.debug(
                "Studio: record_studio_single_note_fallback failed: {}",
                metric_exc,
            )
        warnings.append(
            "Multi-page outline could not be parsed; fell back to a single "
            "study-note. Try regenerating, or pick a stronger chat model."
        )
        return await _dispatch_notebook_mode_singlenote(
            notebook=notebook,
            combined_context=combined_context,
            title=title,
            source_ids=source_ids,
            warnings=warnings,
        )

    # 2. Per-page pass. Default sequential — slamming llama-cpp-python with
    #    concurrent requests degrades quality and can OOM the embed+chat
    #    dual-server desktop setup. Cloud users (OpenAI/Anthropic/etc.)
    #    can opt into v0.7.92's parallel mode via env knob. Either way,
    #    one page failing must not abort the rest, and timeouts are
    #    treated as failures (per-page warning) rather than fatal.
    page_specs = outline["pages"]
    page_contents = await _generate_all_pages(
        combined_context=combined_context,
        notebook_title=title,
        page_specs=page_specs,
        warnings=warnings,
    )

    # 3. Persist. Overview always goes first so it sorts at the top of
    #    the notebook UI's notes list.
    overview_md = _render_overview_note(
        title=title,
        outline=outline,
        page_titles=[p["title"] for p in page_specs],
    )
    notes_to_save: list[tuple[str, str]] = [
        (f"📋 00 · {title} — Overview", overview_md),
    ]
    notes_to_save.extend(page_contents)
    try:
        saved_ids = await _save_notebook_notes(
            notebook_id=notebook_id,
            notes_to_save=notes_to_save,
        )
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.182 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        # Saving even the Overview note failed — surface a 500 so the
        # frontend doesn't claim success. Notebook + sources are intact.
        logger.exception("Studio multi-page: could not save notes")
        raise HTTPException(
            status_code=500,
            detail=(
                f"Generated content but could not save it: {exc}. "
                f"Notebook {notebook_id} was created and contains your "
                f"{len(source_ids)} uploaded source(s)."
            ),
        )

    if len(saved_ids) == 1:
        warnings.append(
            "All page-generation calls failed — only the Overview note "
            "was saved. Try regenerating, or switch to a chat model with "
            "a larger context window."
        )

    return StudioGenerateResponse(
        notebook_id=notebook_id,
        mode="notebook",
        note_id=saved_ids[0],  # overview — back-compat with v0.7.88
        note_ids=saved_ids,
        source_ids=source_ids,
        title=title,
        warnings=warnings,
    )


# v0.7.89 — Pre-v0.7.89 single-note path, preserved as a fallback. Reached
# when DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_MULTIPAGE=false OR when the outline pass returns
# un-parseable JSON. Identical to the original v0.7.0 implementation.
async def _dispatch_notebook_mode_singlenote(
    *,
    notebook: Notebook,
    combined_context: str,
    title: str,
    source_ids: list[str],
    warnings: list[str],
) -> StudioGenerateResponse:
    notebook_id = str(notebook.id)
    try:
        chain = await provision_langchain_model(
            combined_context,
            None,
            "chat",
            max_tokens=8192,
        )
        # v0.7.99 — same timeout protection as the multi-page paths.
        # Before this, the legacy fallback was the one ainvoke in this
        # module that could still hang indefinitely on a stuck local
        # LLM. Re-uses _PAGE_TIMEOUT_SEC (180s default) since the
        # output budget is comparable.
        response = await asyncio.wait_for(
            chain.ainvoke(
                [
                    SystemMessage(content=NOTEBOOK_SYSTEM_PROMPT),
                    HumanMessage(content=combined_context),
                ]
            ),
            timeout=_PAGE_TIMEOUT_SEC,
        )
        raw_text = extract_text_content(response.content)
        clean_text = clean_thinking_content(raw_text)
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Studio notebook (single-note fallback): timed out after {}s",
            _PAGE_TIMEOUT_SEC,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Notebook generation timed out after {_PAGE_TIMEOUT_SEC}s. "
                "The chat model may be loading or overloaded. Raise "
                "DEEPER_NOTEBOOK_STUDIO_PAGE_TIMEOUT_SEC, switch to a faster model, "
                f"or try again. Notebook {notebook_id} was created and "
                f"contains your {len(source_ids)} uploaded source(s)."
            ),
        ) from exc
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.182 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        logger.exception("Studio notebook (single-note fallback): LLM call failed")
        raise HTTPException(
            status_code=502,
            detail=_studio_generation_error_detail(
                exc,
                notebook_id=notebook_id,
                source_count=len(source_ids),
            ),
        )
    try:
        note = Note(
            title=f"{title} — Study Notes",
            content=clean_text or "(empty response from model)",
            note_type="ai",
        )
        await note.save()
        await note.add_to_notebook(notebook_id)
    except HTTPException:
        # v0.7.108 — re-raise typed HTTPExceptions so the next
        # `except Exception` doesn't clobber them to 500.
        raise
    except (NotFoundError, InvalidInputError):
        # v0.7.182 — bubble typed exceptions to the global handlers.
        raise
    except Exception as exc:
        # v0.7.178 — Sanitize 500 detail (see above). logger.exception
        # captures the full traceback for ops.
        logger.exception("Studio notebook (single-note fallback): could not save Note")
        raise HTTPException(
            status_code=500,
            detail="Generated content but could not save it",
        )
    note_id = str(note.id)
    return StudioGenerateResponse(
        notebook_id=notebook_id,
        mode="notebook",
        note_id=note_id,
        note_ids=[note_id],
        source_ids=source_ids,
        title=title,
        warnings=warnings,
    )


async def _dispatch_podcast_mode(
    *,
    notebook_id: str,
    episode_profile_name: str,
    speaker_profile_name: str,
    title: str,
    source_ids: list[str],
    warnings: list[str],
) -> StudioGenerateResponse:
    """Podcast mode: submit a generation job against the just-created notebook.

    PodcastService.submit_generation_job handles the whole pipeline:
    pulls notebook context, runs outline → transcript → audio via the
    selected episode + speaker profiles, persists the Episode record,
    fires off TTS via the configured TTS model (Piper on desktop bundle).
    """
    try:
        job_id = await PodcastService.submit_generation_job(
            episode_profile_name=episode_profile_name,
            speaker_profile_name=speaker_profile_name,
            episode_name=title,
            notebook_id=notebook_id,
            briefing_suffix=PODCAST_BRIEFING_SUFFIX,
        )
    except HTTPException:
        # podcast_service raises HTTPException directly; re-raise so its
        # detail message reaches the client.
        raise
    except Exception as exc:
        logger.exception("Studio podcast mode: submit failed")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not submit podcast generation: {exc}. Notebook "
                f"{notebook_id} was created with your sources; you can retry "
                "from /podcasts."
            ),
        )

    return StudioGenerateResponse(
        notebook_id=notebook_id,
        mode="podcast",
        job_id=str(job_id),
        source_ids=source_ids,
        title=title,
        warnings=warnings,
    )


# v0.7.88 / v0.7.89 — mode="both": multi-page notebook generation AND a
# podcast job in one shot. Either half can fail independently. We never
# 502 just because one half broke; instead, the user gets a 200 with
# whatever succeeded populated and the failure described in `warnings`.
# The notebook + uploaded sources are durable regardless.
async def _dispatch_both_modes(
    *,
    notebook: Notebook,
    notebook_id: str,
    combined_context: str,
    episode_profile_name: str,
    speaker_profile_name: str,
    title: str,
    source_ids: list[str],
    warnings: list[str],
) -> StudioGenerateResponse:
    note_id: Optional[str] = None
    note_ids: list[str] = []
    job_id: Optional[str] = None

    # Notebook half — full multi-page pipeline. We catch HTTPException
    # here so a notebook failure doesn't prevent the podcast from being
    # submitted; partial success is the whole point of `both`.
    try:
        notebook_resp = await _dispatch_notebook_mode(
            notebook=notebook,
            combined_context=combined_context,
            title=title,
            source_ids=source_ids,
            warnings=warnings,
        )
        note_id = notebook_resp.note_id
        note_ids = notebook_resp.note_ids
        # _dispatch_notebook_mode may have appended its own warnings to
        # the shared list — those land here automatically.
    except HTTPException as exc:
        warnings.append(
            f"Notebook generation failed (HTTP {exc.status_code}): "
            f"{_brief(Exception(str(exc.detail)))}"
        )
    except Exception as exc:
        logger.exception("Studio both: notebook half raised unexpected error")
        warnings.append(f"Notebook generation failed: {_brief(exc)}")

    # Podcast half — independent submit. Same partial-failure rule.
    try:
        podcast_resp = await _dispatch_podcast_mode(
            notebook_id=notebook_id,
            episode_profile_name=episode_profile_name,
            speaker_profile_name=speaker_profile_name,
            title=title,
            source_ids=source_ids,
            warnings=warnings,
        )
        job_id = podcast_resp.job_id
    except HTTPException as exc:
        warnings.append(
            f"Podcast submission failed (HTTP {exc.status_code}): "
            f"{_brief(Exception(str(exc.detail)))}"
        )
    except Exception as exc:
        logger.exception("Studio both: podcast half raised unexpected error")
        warnings.append(f"Podcast submission failed: {_brief(exc)}")

    # Both halves failed → still a 200 so the user sees the notebook +
    # sources they uploaded. The warnings array carries the diagnostic.
    # If you wanted a 502 instead, this is where to gate it; current
    # design favours "user keeps their uploaded data" over loud failure.
    return StudioGenerateResponse(
        notebook_id=notebook_id,
        mode="both",
        note_id=note_id,
        note_ids=note_ids,
        job_id=job_id,
        source_ids=source_ids,
        title=title,
        warnings=warnings,
    )
