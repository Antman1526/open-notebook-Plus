# Open Notebook Plus — Changelog

> **Historical release record:** the current successor is
> [Deeper Notebook](https://github.com/Antman1526/Deeper-Notebook). Artifact,
> configuration, and path names below remain unchanged to describe what each
> release actually shipped.

The desktop-app fork's version history. Upstream `open-notebook` releases
are tracked in [`../CHANGELOG.md`](../CHANGELOG.md); this file covers
the Plus-specific commits on top of upstream.

Format: each release is grouped by theme. Severity tags:
- 🔒 **Security/safety**
- 🐛 **Bug fix**
- 🎨 **UX/UI**
- ⚡ **Performance**
- 🛠 **Infra/tooling**
- ✨ **Feature**

Versions reflect commit cadence, not strict semver — this is a desktop
fork on its own release rhythm. Patch numbers (`v0.7.N`) increment per
focused commit; each ships with regression tests.

---

## Unreleased

## v0.8.127 — 2026-09-11 — The five v0.8.126 open items

✨ **Background worker heartbeat.** The worker upserts
`worker_heartbeat:primary` every 60 s; detection is gated on
`DEEPER_NOTEBOOK_WORKER_PROCESS` (set by the Makefile and the desktop
launcher on the worker spawn) or the worker argv, so the API, which
imports the same `commands` package, never heartbeats. `/healthz/deep`
gains a `worker` subsystem (`degraded` when absent, never `not_ready`),
and the setup wizard renders the row with a copy-paste hint. Verified
live: degraded with no worker, online within one tick after starting it.

🐛 **Processed sources without a command row report "completed."** The
v0.8.126 in-process sync path (and sources predating command tracking)
returned no status; list and detail now derive it from extracted text.

🐛 **Visual exports write over their recorded paths** (slide deck,
infographic, SVG chart), completing the v0.8.126 orphan fix.

✨ **Podcast Studio submissions carry the notebook id**, so Studio-made
episodes are scoped and bundle with the notebook. The Quick dialog from a
source detail view still does not; recorded below.

🛠 **Tests no longer write to the operator's log directory.** conftest
points `DEEPER_NOTEBOOK_LOG_DIR` at a per-session temp dir.

## v0.8.126 — 2026-09-11 — Stack robustness and the seven live-run items

🛠 **Source creation no longer depends on a worker.** The "synchronous"
path submitted to the SurrealDB command queue and waited up to 300 s for
`surreal-commands-worker`; with no worker, every source create hung. The
`@command` decorator returns the plain coroutine, so the API now awaits
`process_source_command` in-process under
`DEEPER_NOTEBOOK_SOURCE_SYNC_TIMEOUT_SEC` (300). The queued async path is
unchanged and still needs the worker.

🛠 **A dev reload no longer wedges the port.** `run_api.py` passes
`timeout_graceful_shutdown` (`API_GRACEFUL_SHUTDOWN_SEC`, default 20 s) so
an in-flight request cannot keep the old process holding :5055. `API_HOST`,
`API_PORT`, `API_RELOAD` are now documented too.

🐛 **SurrealDB 2.x rejects `ORDER BY` on an unprojected field.** The
browse-time 500s from the previous live run were `GET /capture/roots`:
`SELECT path … ORDER BY created` fails to parse. Fixing it exposed
`repo_create` returning the driver's one-element list despite its dict
annotation. Both fixed; the same query shape was confirmed live and fixed
in the MCP notebook list and the vault-note context builder.
`tests/test_ordered_projection_queries.py` scans backend literals for the
shape from now on.

🐛 **Studio routes accept bare ids** (`studio_artifact:` / `notebook:`
prefix optional, matching other routers). **Re-persisting an export writes
over the recorded path** instead of allocating `-2` and orphaning the old
file (research bundle included; slide-deck/infographic visual exports not
yet). **Notebook delete cascades to studio artifacts** (revisions and files
included) and reports the count; source unlink policy unchanged.

✨ **Podcast episodes are notebook-scoped.** `PodcastEpisode.notebook_id`
with migration 52 (the table is SCHEMAFULL), persisted at generation
through the worker command, `get_for_notebook`, an optional filter on
`GET /podcasts/episodes`, and the bundle export now includes a notebook's
podcasts; a lookup failure is a warning, never a 500.

🎨 **Frontend.** `ExportNotebookDialog` creates the destination (folder
formats) or its parent (zip formats) before exporting and shows API errors
inline. Mind map: "1 match", preview popover clamped inside the canvas,
MiniMap colours resolved from CSS variables so it is no longer a blank
white box in dark theme.

🛠 **Degraded mode, settled by evidence.** The transformation-model
provisioning seen in the live log was the suggested-questions route, which
already degrades to 200; regression tests lock that in. The credentials
500s in the shared log come from the test suite's fake-key fixtures, not
from browsing.

## v0.8.125 — 2026-09-11 — The v0.8.124 areas, then the app run for real

This release closed the five areas left by v0.8.124 and then launched the
stack (SurrealDB, API without reload, commands worker, Next dev server),
seeded a notebook with two sources, a note and a structured course pack,
and drove every new feature in the browser. Two bugs no unit test had
caught fell out of that pass.

🐛 **Notebook source list returned 500 when any source had no extracted
text.** A source whose processing never ran (worker down, command reaped on
restart) has `full_text = NONE`, and both list queries computed
`string::len(full_text)`, which SurrealDB rejects. One such row broke
`GET /api/sources` for the whole notebook. Coalesced to an empty string;
verified against the live database and the route; regression tests on
both queries.

🐛 **First "Export all artifacts" failed with a 400 the dialog never
showed.** The default destination is `~/DeeperNotebook-Exports`, which does
not exist on a fresh machine, and the zip routes deliberately refuse to
create parents. The dialog now creates the parent through the existing fs
mkdir mutation before bundling and renders the API error inline with
`role="alert"`. The older notebook export dialog has the same gap; recorded
below rather than widened into this change.

🐛 **Artifact delete returned 500 after deleting, and orphaned revisions.**
The driver returns the deleted `RecordID` from `delete()`; the domain
method passed it through and the route echoed it into JSON, which pydantic
cannot serialise, so the client saw a 500 for a delete that had worked.
Separately, the update endpoint's revision snapshot was left behind (row
and export file) when its parent was deleted. `base.delete()` now returns
a real bool, the route coerces again, and a top-level artifact deletes its
revisions first.

✨ **Retention job visible in Settings.** `GET /studio/retention/status`
and `POST /studio/retention/dry-run` (never a real prune), plus a
`RetentionCard` showing the enabled state, the three knobs, last run and
last report, a "Dry run now" button with inline counters, and the env var
to enable it. Note: the router imports the retention module qualified;
importing the function by name makes it one of the legacy facade's synced
symbols, reset before every request.

✨ **Bundle export media flag.** `include_media` (default on) gates podcast
audio (when episodes are notebook-scoped — they are not today, so zero
episodes are bundled and the code says so) and the video-overview files the
bundle already carried, so the dialog checkbox has a real effect now.

✨ **Mind map: source and note previews, search-to-focus, persisted
state.** A plain click on a source or note node opens the in-canvas
preview (Shift-click still navigates); Enter fits the view to the search
matches and Up/Down step through them with focus; filter, query and the
cluster toggle persist per notebook and were confirmed restored on reopen.

🛠 **Test isolation.** The retention API status test read a last report
left behind by the loop tests when both files ran in one session; reset
fixture added.

**Live-run facts for the next reader.** The API's dev reload watches the
tree, so concurrent edits reload it mid-request and wedge the port
(`API_RELOAD=false` for verification). The "synchronous" source path
submits to the SurrealDB command queue and waits up to 300 s for the
`surreal-commands-worker`; without the worker every source create hangs.
The setup wizard auto-advances once any notebook exists. Studio routes
require the full `studio_artifact:` id in the path.

## v0.8.124 — 2026-09-11 — The v0.8.123 strategic areas, plus housekeeping on the inherited commits

🛠 **Housekeeping first.** The twelve v0.8.120–v0.8.123 commits were local
only; before building on them the gates were re-run. Frontend green (260
files, 1936 tests). Backend ruff had twelve import-order findings (three
of them the v0.8.116 keepalive imports) and the rebrand audit had one stale
pin from the handoff edit; both fixed and pushed with the inherited work.

🛠 **Studio retention job, OFF by default** (`deeper_notebook/studio/retention.py`).
A lifespan task wired exactly like the checkpoint pruner: keeps the newest
N revisions per artifact and deletes the rest through
`StudioArtifact.delete()` (which already removes export files under
containment guards); unlinks export files that are both content-stale and
older than a day threshold, dropping the key so the UI offers "Generate"
again. `DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS` defaults to 0 and
the loop returns immediately; unlike checkpoint rows these are user-facing
artefacts, so an operator opts in, and `..._DRY_RUN=true` counts without
touching anything. Counter `studio_retention_runs_total`. Four settings
registered and documented.

✨ **Export every completed artifact as one zip.**
`POST /studio/notebooks/{id}/exports/bundle` writes
`{artifact-slug}/{format}/{filename}` plus a `manifest.json` to a chosen
path, through the same destination and overwrite guards as the notebook
export; stale formats are regenerated first, with a failing format
recorded as a warning rather than a 500. "Export all artifacts" in the
Studio rail header opens a trimmed export dialog.

✨ **Mind map: in-canvas media preview, search, cluster-by-type.** Clicking
a slide-deck node with a video overview opens a preview popover with the
inline player instead of leaving the map (Shift-click still navigates); an
Open button hands off to the rail via `dn:select-artifact`. A search box
dims non-matching nodes and edges without disturbing the radial layout,
with a live match count. A cluster toggle lays each node type out on its
own sub-circle around a per-type satellite hub, same angle formula, no
React Flow group nodes. `NotebookGraphNode` gains `artifact_type`, which
the backend already sent.

Caveat recorded for the next reader: nothing creates a `podcast_audio`
studio artifact today (the generation registry rejects it), so the audio
branch of the preview is defensive and falls back to "unavailable" copy.

## v0.8.123 — 2026-09-10 — Mind Map Deep-Linking, Semantic MiniMap, Bundle Cleanup & Canvas Filter Chips

✨ **Mind map to studio artifact deep-linking.** Connected `onSelectArtifact` in `MindMapButton.tsx` to dispatch `dn:select-artifact` custom event and close the canvas modal, and registered a responsive listener in `ArtifactRail.tsx`. Clicking any studio artifact node in the Mind Map immediately opens the full artifact drawer/modal with markdown preview, citations, export options, and revision history.

🎨 **Semantic MiniMap styling & graph node type alignment.** Configured React Flow's `<MiniMap />` in `MindMap.tsx` with dynamic `nodeColor` callback mapping each node to its semantic CSS design token (`--dn-graph-source`, `--dn-graph-note`, `--dn-graph-artifact`, and fallback `--dn-graph-fallback`). Aligned frontend `NotebookGraphNode` and `NotebookGraphEdge` TypeScript definitions with backend models (`studio_artifact`, `grounded_in`).

🔒 **Multi-file bundle export directory disk cleanup.** Extended `StudioArtifact._cleanup_export_files()` in `deeper_notebook/domain/notebook.py` to sweep and safely unlink all files and child bundle directories (e.g. SCORM, xAPI, research bundles matching `{slug}-*` or `{slug}`) within `_artifact_export_dir()`, with strict path traversal containment guards, ensuring zero orphaned directory trees upon artifact deletion.

✨ **Mind map node-type filter chips & 14-locale i18n.** Added interactive filter chips bar atop the Mind Map canvas (`All ({count})`, `Sources ({count})`, `Notes ({count})`, `Artifacts ({count})`) with dynamic radial coordinate redistribution and edge filtering. Added `filterAll`, `filterSources`, `filterNotes`, and `filterArtifacts` under `mindMap` across all 14 supported locales with 100% `{count}` placeholder parity and 0 unused keys.

## v0.8.122 — 2026-09-10 — Video Overview Disk Safety, Mind Map Note Editing, Graph Artifact Grounding & i18n Polish

🔒 **Video overview orphaned file disk safety.** `StudioArtifact._cleanup_export_files()` now unlinks video overview assets (`video_mp4`, `video_captions`) located under `DATA_FOLDER / "video-overviews" / {artifact_slug}` upon artifact deletion, preventing disk bloat while enforcing strict directory containment to protect against path traversal. It also safely prunes empty artifact video directories.

✨ **Mind map interactive note reading and editing.** In `MindMapButton.tsx`, wired `onSelectNote` to open `NoteEditorDialog` with full query invalidation, markdown editing, and title management, enabling immediate inline reading and editing of note nodes directly from the React Flow mind map without leaving the view.

✨ **Studio artifact integration and source grounding in knowledge graph.** In `Notebook.get_graph()`, Studio artifacts are now retrieved and incorporated into the notebook's graph as `studio_artifact` nodes linked to the notebook hub. Grounding edges (`kind: "grounded_in"`) are automatically generated between each artifact and its referenced sources (`source_ids`). Extended `MindMap.tsx` and theme styling to render studio artifact nodes seamlessly.

🌐 **14-locale i18n polish & placeholder parity.** Replaced remaining hardcoded UI text in `SearchResultsList.tsx` (`"View evidence for {title}"`) and `studio/page.tsx` (`trustMargin`) with `t('searchPage.viewEvidence')` and `t('studio.trustMargin')`. Both keys were added across all 14 supported locales (`en-US`, `zh-CN`, `zh-TW`, `ja-JP`, `pt-BR`, `es-ES`, `fr-FR`, `de-DE`, `it-IT`, `ru-RU`, `tr-TR`, `pl-PL`, `ca-ES`, `bn-IN`) with 100% `{title}` placeholder parity and 0 unused keys.

## v0.8.121 — 2026-09-10 — Export & Synthesis Content Integrity, Artifact File Cleanup & Batch Refresh

🐛 **Notebook export and executive synthesis content integrity.** `Notebook.get_sources()` and `Notebook.get_notes()` were previously omitting `source.full_text` and `note.content` via SurrealQL `omit` clauses. When exporting notebooks to disk (`/notebooks/{id}/export`) or generating executive cross-source synthesis (`/notebooks/{id}/synthesis`), note bodies rendered as `(no content)` and source texts rendered as `(no full text)`. Added `include_full_text: bool = False` to `get_sources()` and `include_content: bool = False` to `get_notes()`, keeping sidebar listing fast while allowing export and synthesis callers to fetch full text. Added lazy hydration fallbacks in both endpoints so note and source bodies are always preserved even if called with mocked or partial projections.

🔒 **Orphaned export file cleanup on artifact deletion.** When deleting a `StudioArtifact`, previously only the database record was removed, leaving generated files (`.docx`, `.pptx`, `.pdf`, `.zip`, `.epub`, `.csv`, `.svg`) on disk. `StudioArtifact.delete()` now safely unlinks persisted export paths while strictly verifying that each file is contained within the configured `_artifact_export_dir()`, preventing path traversal.

✨ **Batch stale export refresh in Evidence Studio.** In `ArtifactExportMenu.tsx`, when an artifact has multiple outdated exports (`stale_export_formats.length > 1`), a "Refresh all outdated ({count})" batch button is now displayed. Added `refreshAllOutdated` and `refreshingAll` translations across all 14 supported locales with 100% `{count}` placeholder parity.

## v0.8.120 — 2026-09-10 — Evidence Studio Export Regeneration, Freshness Tracking & i18n Gaps


✨ **Multi-file on-demand export regeneration & format aliases.** The single-format export endpoint (`POST /studio/artifacts/{id}/exports/{format}`) now supports course pack multi-file bundles (`scorm_package`, `xapi_package`, `research_bundle`, `instructor_guide`, `learner_handout`, `module_checklist`, `assessment`) alongside single-file formats (`docx`, `pptx`, `pdf`, `xlsx`, `csv`, `markdown`, `json`, `png`, `svg`). Callers can also use friendly format aliases (`scorm`, `xapi`, `bundle`, `svg`, `checklist`).

✨ **Export freshness tracking & UI stale indicators.** Artifact content hashes (SHA-256) are now persisted per export in `output_payload["export_hashes"]`. The backend automatically tracks `stale_export_formats` using `is_export_stale` and exposes them in `StudioArtifactResponse`. In Evidence Studio, the export menu surfaces a subtle amber "Outdated" badge when an export was generated from an older version of the artifact content, and provides a per-item 1-click regenerate action to refresh the file on disk without re-running the full LLM pipeline.

🐛 **PDF bold font pairing on macOS.** On macOS, when `Arial Unicode.ttf` is resolved as the Unicode body font, headings now properly pair with `Arial Bold.ttf` for high-contrast bold titles and subheadings instead of falling back to regular weight or unstyled fonts.

🐛 **Translation gaps resolved across all 14 locales.** Audited and resolved all 141 unlocalized translation keys identified by `keys-exist.test.ts` (e.g. `updates.*`, `localModels.*`, `mcp.recommendations.*`, `intro.*`, `dbRepair.*`, `mindMap.*`, `sources.discover*`, `chat.*`, `models.smartRouting.*`). All 14 locales (`en-US`, `es-ES`, `fr-FR`, `de-DE`, `it-IT`, `pt-BR`, `ja-JP`, `zh-CN`, `zh-TW`, `ru-RU`, `pl-PL`, `tr-TR`, `ca-ES`, `bn-IN`) maintain 100% leaf-key and placeholder parity with zero `@typescript-eslint/no-explicit-any` warnings.

## v0.8.119 — 2026-09-10 — The v0.8.118 open items, closed

🐛 **Podcast toasts showed a literal `{name}`.** The eight
`podcasts.*Desc` strings carry a single-brace placeholder, but
`use-podcasts.ts` called a bare `t(key)`, so every create/update/delete/
duplicate toast rendered the placeholder verbatim. The mutations now
substitute the name: from the payload on create and update, from the
returned profile on duplicate, and from the cached list (read before the
invalidation clears it) on delete.

🛠 **Placeholder parity test** (`locales/placeholders.test.ts`) found that
bug and the drift behind it: zh-CN and zh-TW had dropped `{name}` from all
eight strings. Every locale string must now carry exactly the placeholders
its en-US counterpart carries, in the same brace style. This settles the
"interpolation convention" question the other way from how it was filed:
single-brace `{param}` and i18next `{{param}}` are both established across
many namespaces, so neither is being migrated; the parity test guards the
mixed convention instead.

🛠 **Key guard widened.** `keys-exist.test.ts` now checks single-segment
keys too (en-US has top-level string keys) and prints a non-blocking count
of calls that only work because of an English `defaultValue` — real
translation gaps for a future locale pass.

✨ **Non-Latin text in the course-pack PDF.** Font resolution runs once per
process: a body font discovered from `DEEPER_NOTEBOOK_PDF_FONT` or the
platform's usual Unicode faces (macOS Arial Unicode, Windows Arial, Linux
DejaVu), plus reportlab's CID faces for Simplified and Traditional Chinese,
Japanese and Korean, which need no font files at all. Han, Kana and Hangul
runs are wrapped in the matching CID font inside each paragraph, so a mixed
"Hello 世界" line renders both halves. When the chosen family ships no bold
file, headings use the regular Unicode face rather than Helvetica-Bold:
losing the weight is cosmetic, losing the glyphs is not. Nothing is
bundled and no dependency was added.

✨ **On-demand single-format export.**
`POST /studio/artifacts/{id}/exports/{format}` regenerates one format for
an already-generated artifact, so course packs created before v0.8.117 can
get an EPUB or PDF without regenerating the artifact. Writes over the
recorded path when there is one, so links already handed out stay valid.
The menu shows "Generate EPUB" / "Generate PDF" on course packs missing
either, with a per-format pending state.

## v0.8.118 — 2026-09-09 — Open items from v0.8.117

🛠 **Every literal translation key is now checked.**
`frontend/src/lib/locales/keys-exist.test.ts` scans all non-test source for
dotted `t('...')` literals and fails if one does not resolve in en-US
(calls with a string fallback or `defaultValue` are accepted). This is the
class of bug behind the v0.8.116 retry label; a planted bad key fails the
test, and the codebase currently has zero unguarded misses. Chosen over
renaming `common.retry`: a rename does not stop the next wrong guess.

🎨 **Studio export menu localized.** New `studio.export` namespace (18
keys, 14 locales) replaces the last hardcoded English UI strings; format
names stay as proper nouns. Test asserts EPUB under Bundle and PDF under
Visual.

🛠 **Interrupted marker across refetch, settled by test.** react-query's
structural sharing keeps the session reference stable when a refetch
returns the same list, so the local `interrupted` message survives; when
the server list actually changes (a completed turn) the canonical list
replaces it. Both chat hooks have a behavioural test for each side. No
server-side marker needed.

Not in this release: removing the dead `_persist_artifact_exports`
duplicate in `api/routers/studio/artifacts.py` is being handled in a
separate session.

## v0.8.117 — 2026-09-09 — The v0.8.116 open items, closed

Seven commits working through the open-items list left in
`HANDOFF_CLAUDE.md` by v0.8.116.

🎨 **"Interrupted" badge with inline retry.** A stalled stream that kept
partial text now marks that message `interrupted` (local-only, cleared by
the next session refetch) and `ChatPanel` renders
`ChatMessageInterruptedBadge` in the AI actions row: an outline chip plus
a ghost retry button that re-sends the preceding question through the
panel's existing send path, offered only when not streaming. New
`chat.interrupted` key in all 14 locales.

🐛 **Retry toast label.** The v0.8.116 stall toast used
`t('common.accessibility.retry')`, which does not exist (the leaf is
`common.retry`, "Try Again"), so the button would have shown the raw key.
Fixed in all three hooks and the tests that assert on it.

🛠 **Behavioural test for `useNotebookChat`.** `chatApi.streamMessage` is
mocked as a clock-paced async generator and the hook is driven for real:
rAF-batched tokens, canonical `done` replacement, stall keeps partial and
retries, stall with nothing cleans up, server error uses the generic toast.

🛠 **HTTP-level heartbeat tests** (`tests/test_stream_keepalive_http.py`).
Each wrapped endpoint is exercised through `TestClient` with the inner
generator stubbed to go silent past a 50 ms interval: `/chat/stream`
emits `{"type":"heartbeat"}` lines and, with a 0.2 s idle limit, ends in
the "produced no output" error frame; `/search/ask` and source chat emit
`: heartbeat` comment lines between `data:` frames.

✨ **EPUB 3 export for course packs** (`deeper_notebook/studio/exporters/epub.py`).
Stdlib `zipfile` plus the already-present markdown-it-py, no new
dependency: mimetype first and stored, container, OPF manifest and spine
(title page, one XHTML per module, assessment), nav, stylesheet. markdown-it
runs with `xhtmlOut` and `html=False` so every member is well-formed XML
even for hostile markdown. `export_paths["epub"]`; the export menu shows it
in the Bundle group. docx and epub now fail independently.

✨ **Text-flow PDF export for course packs** (`exporters/pdf.py`). New
dependency `reportlab>=4.2,<6.0` (pure Python, no system libraries; the
diff to `uv.lock` and `desktop/requirements.lock` is reportlab alone,
bootstrap lock tests pass). Title page, per-module sections with lesson
headings, assessment, Sources appendix; a small markdown-it token walker
maps headings, bold/italic, lists and fenced code and skips anything else.
`export_paths["pdf"]`; the menu already rendered `pdf`. Documented in
`docs/recreation/TECHNOLOGY-AUDIT.md`.

🛠 **Search page split.** `app/(dashboard)/search/page.tsx` (945 lines)
became a 472-line orchestrator plus `components/SearchResultsList.tsx`,
`AskPanel.tsx`, and `DeepResearchPanel.tsx`. Pure move; the page test is
untouched and passes.

Path corrections for the next reader: Studio's backend is the package
`api/routers/studio/` (not `studio.py`), and on-disk exports are written by
`deeper_notebook/studio/generation/persistence.py`, not the router.

## v0.8.116 — 2026-09-09 — Streams that tell you they are alive, and recover when they are not

Follow-up to v0.8.115 across five commits. The handoff backlog that
motivated them cited files that do not exist; the real gaps were found by
reading the code and are listed here with their actual locations.

✨ **Backend heartbeat frames + server-side idle limit**
(`api/utils/stream_keepalive.py`). `/chat/stream`, `/search/ask`, and
source chat now emit a heartbeat every `DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC`
(10 s) of model silence, and end the stream with an error frame if the
model produces nothing for `DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC`
(300 s), cancelling the model call instead of leaking it. SSE streams get
a `: heartbeat` comment line (invisible to their `data:` parsers by
construction); NDJSON gets `{"type":"heartbeat"}`, which
`chatApi.streamMessage` drops after the bytes have reset the client's
idle timer. Bounded queue preserves back-pressure; closing the wrapper on
client disconnect still runs the source generator's `finally` (session
lock, memory extraction). Both settings are registered in
`deeper_notebook.environment` and documented in the env reference.
Client default `NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS` drops 120 s → 60 s.

🎨 **Stall recovery in the chat hooks.** On a `StreamStallError`,
`useNotebookChat` and `useSourceChat` keep the partial AI message (and the
user's bubble) when any text arrived, and only fall back to the ordinary
cleanup when the placeholder is empty. All three hooks (incl. `useAsk`)
add a "Try Again" action to the stall toast that re-sends the same input
through a ref to the latest send function.

⚡ **Search results virtualized at 50+ cards.** The list is capped at 100
variable-height cards inside a 60vh scroll container; above the same
threshold `SourcesColumn` uses, it renders through the shared
`VirtualizedListAuto`. Cards are keyed by `result.id` on both paths
(index keys were a pre-existing smell). Real location:
`app/(dashboard)/search/page.tsx`, not the handoff's `SourceGallery.tsx`.

🎨 **Keyboard navigation for the vault graph** (`components/vault/VaultGraph.tsx`,
React Flow). Nodes were already focusable but React Flow's own keydown
only toggles selection, so Enter never reached `onNavigate` and arrows
tried to drag. Enter/Space now opens the focused node (never an
`unresolved:` placeholder); arrow keys move focus to the nearest node in
that direction. A visually hidden hint (`knowledge.graphKeyboardHint`, 14
locales) is wired via `aria-describedby`. `/` already opens the knowledge
command surface, so no second handler was added.

🛠 **Behavioural streaming tests.** `src/test/stream-harness.ts` (push /
close / error a `ReadableStream`, `cancelled` flag, rAF-as-setTimeout
stub) plus `use-ask.behaviour.test.ts` and
`useSourceChat.behaviour.test.tsx`, which drive the hooks for real: happy
path, stall-keeps-partial-and-retries, stall-with-nothing-cleans-up,
server error vs stall toast, superseding send aborts silently. The
partial-answer test fails against the pre-v0.8.116 hook.

## v0.8.115 — 2026-09-09 — Stalled model streams no longer hang the UI

🐛 **A silent streaming response parked the chat, source chat, and Ask
panels in "streaming" forever.** All three consumers sat in a bare
`await reader.read()` with no upper bound. When a local model daemon
(Ollama, llama.cpp, MLX) died mid-generation, or the Mac slept and woke
with a half-open socket, no bytes ever arrived and nothing failed: the
spinner stayed, Stop was the only way out, and the backend never saw a
disconnect. The server sends no heartbeat frames, so silence is the only
signal available.

- New `readWithIdleTimeout` (`frontend/src/lib/utils/stream-stall.ts`)
  races each read against an idle timer. The timer is per read, so a
  slow-but-alive model that keeps trickling tokens is never cut off; only
  total silence trips it. On a stall it rejects with `StreamStallError`
  and cancels the reader so the HTTP body is torn down and FastAPI's
  `is_disconnected()` fires.
- Wired into `chatApi.streamMessage` (notebook chat), `useSourceChat`,
  and `useAsk`. Each hook branches on the stall before the generic error
  path and shows dedicated copy (`apiErrors.streamStalled` /
  `streamStalledHint`, all 14 locales) that points at the local daemon.
- Default 120 s. Deliberately generous: a cold model load plus a
  long-context prefill on Apple Silicon can legitimately produce no bytes
  for a minute. Tune with `NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS`; `0`
  disables the guard.
- Ordering matters: the guard rejects *before* cancelling. `cancel()`
  settles the pending read as `{ done: true }`, and if that reaction is
  queued first the race resolves as a clean end-of-stream and the caller
  never learns it stalled. Caught by a regression test against a real
  `ReadableStream`.
- Tests: unit (`stream-stall.test.ts`), behavioural through the real
  NDJSON generator with a hanging fetch body
  (`chat.stream-stall.test.ts`), and a source-text contract that fails if
  a bare `reader.read()` is reintroduced (`stream-stall-guard.test.ts`).

## v0.8.114 — 2026-08-20 — Semantic search actually returns results

🐛 **`fn::vector_search` returned nothing for every query, and had since
migration 21.** HTTP 200, empty result list, no error, no log line, against a
fully embedded corpus. Every surrounding signal looked healthy — sources
reported embedded, chunk counts were non-zero, the query vector was a
well-formed 768-dim array, the HNSW index reported the matching DIMENSION and
TYPE, and `REBUILD INDEX` returned OK. Nothing failed; the filter simply never
matched.

There were **three** independent defects, and fixing any one alone still
returned nothing. All three were found by bisecting the WHERE clause against
SurrealDB 2.6.5.

1. **Wrong KNN operator arity.** Migration 21 wrote `embedding <|100|> $query`.
   `<|K|>` targets an MTREE index; HNSW requires `<|K,EF|>`. No MTREE index
   exists on these tables. Measured with one matching row: `<|100|>` → 0 rows,
   `<|100,100|>` → 1 row.

2. **Function calls in a WHERE clause that also drives a KNN scan.** With the
   operator corrected the function still returned nothing: on 2.6.5, a KNN
   predicate combined with a function-based condition over the same indexed
   field yields no rows. A plain comparison (`embedding != none`) survives; a
   function call (`array::len(...)`, `vector::similarity::cosine(...)`) does
   not. The same expressions compute correctly in the PROJECTION of a KNN
   query — only the filter position is affected. Fixed by letting the KNN
   operator select candidates in a subquery and running the length and
   similarity filters in the outer query, which keeps the HNSW index doing the
   work it was added for instead of falling back to a full-table scan.

3. **`ORDER BY` ignored on a grouped SELECT.** Visible only once rows finally
   came back. The final aggregation carried `GROUP BY` and `ORDER BY` on the
   same statement, which 2.6.5 does not apply, so `LIMIT` sliced an *unordered*
   set: the "top 10" was an arbitrary 10 of everything above the similarity
   threshold, in meaningless order. Measured on the live 320-chunk corpus:
   `0.835, 1.0, 0.723, 0.709, 0.710 …` before, `1.0, 0.835, 0.723, 0.723 …`
   after. Fixed by wrapping the grouped result and sorting outside it.

Shipped as forward migration `49.surrealql` (with `49_down.surrealql`), because
migration 21's definition is already applied and guarded by `IF NOT EXISTS`.

🐛 **`fn::text_search` had defect 3 too, and had since migration 1.** Found by
grepping for the pattern after fixing `fn::vector_search`. Shipped as migration
50. This one was never invisible — it looked like "search quality is mediocre"
rather than like a bug, because an unordered `LIMIT` returns an arbitrary slice
of the matching set rather than the best matches. On the live corpus:

    query "architecture"  before: 1.889, 1.2, 2.396, 2.586, 1.2, 1.896  (unsorted)
                           after: 6.363, 5.229, 5.216, 5.191, 4.227, 2.586
    query "project"       before: -0.527, -1.054, -0.539, -0.427, -0.152, -1.267
                           after: 4.409, 4.246, 4.226, 4.216, 3.676, 3.493

The scores rise because the query now returns the top N rather than an arbitrary
N — the previous all-negative "project" scores were simply a low-relevance slice.
This also mattered for v0.8.113's hybrid search, which fuses the two legs with
Reciprocal Rank Fusion: RRF scores documents purely by their RANK within each
leg, so an arbitrarily ordered leg fed meaningless ranks into the fusion.

Verified against the live 320-chunk database: **0 results before, 10 correctly
ranked after**, with the exact-match chunk at similarity 1.0 on top. Through the
API, the natural-language query "what does DeeperCode do" now returns
`00_What_DeeperCode_Does.md` at 0.81 for both `type: "vector"` and
`type: "hybrid"`. This also explains why v0.8.113's hybrid search only ever
showed text-leg results.

🛠 **Two regression tests, deliberately overlapping.**
`tests/integration/test_vector_search_returns_results.py` exercises the real
query planner but only runs with `SURREAL_INTEGRATION=1`;
`tests/test_v0_8_114_knn_operator_arity.py` reads the migration text and runs
unconditionally. A defect that survived because nothing exercised it deserves a
guard that cannot be skipped. Each defect was confirmed to fail its test in
isolation before the fix was restored.

🛠 **`test_repair_desktop_db_script.py` no longer depends on whether the app is
running.** The repair script aborts early if Deeper Notebook or SurrealDB is
live, so `test_explicit_exact_target_resolves_both_roots_conflict` failed on a
machine with the app open and passed everywhere else. `pgrep` is now stubbed for
these tests, which assert data-root resolution and never asserted that guard.

---

## v0.8.113 — 2026-08-19 — Hybrid retrieval: fuse both search legs

✨ **`/api/search` gains `type: "hybrid"`.** It has always run exactly ONE leg —
`if effective_type == "vector": ... else: text_search(...)` — and discarded the
other. Keyword and semantic retrieval fail in different directions: text search
misses paraphrase, vector search misses exact identifiers, rare proper nouns and
error strings. Answering with one lost recall on every query, and the codebase
already computed both.

Results are combined with **Reciprocal Rank Fusion**, an architecture taken from
qmd (github.com/tobi/qmd, MIT). Only the approach is borrowed — qmd is
Node/TypeScript over SQLite FTS5 + sqlite-vec and is not a drop-in for a
Python/SurrealDB app. Its third leg, an LLM reranker, is deliberately NOT
implemented: it would add another GGUF to the sidecar fleet for a benefit this
codebase has not measured.

**Why RRF rather than blending scores.** The legs produce incomparable numbers —
cosine similarity in [0, 1] against an unbounded SurrealDB relevance score that
shifts with corpus size. Normalising them needs calibration that rots as the
corpus grows. RRF uses only ORDER, so a leg reporting enormous magnitudes gains
nothing, which is pinned by test.

**Degrades, never fails.** With no embedding model the vector leg is skipped and
hybrid is plain text search — strictly better than the 400 the `vector` branch
raises. If either leg errors or times out, the other still answers; only when
both are gone does it report 504.

**Additive.** `text` and `vector` behave exactly as before and the default is
unchanged, so no existing caller is affected. Exposed in the search UI as a
third option, enabled even without an embedding model, so the feature is
reachable rather than API-only.

Two bugs in my own work, caught by the tests I wrote for it: the fusion kept
whichever leg was iterated FIRST rather than whichever ranked a document higher
(contradicting its own docstring), and my first k-sensitivity test proved
nothing because agreement won at every value of k.

- **v0.8.114** 🐛 **Semantic search returned nothing, for every query, since
  migration 21.** HTTP 200, empty list, no error, against a fully embedded
  corpus — and every surrounding signal (embedded flags, chunk counts, index
  DIMENSION/TYPE, `REBUILD INDEX`) reported healthy. Three independent defects
  in `fn::vector_search`, each of which alone still returned nothing: the KNN
  operator used the MTREE arity `<|100|>` against HNSW indexes that require
  `<|100,100|>`; SurrealDB 2.6.5 drops all rows when a KNN predicate shares a
  WHERE clause with a function call over the same indexed field (fixed by
  moving the KNN into a subquery and the filters outside); and `ORDER BY` is
  ignored on a statement carrying `GROUP BY`, so `LIMIT` returned an arbitrary
  unordered slice rather than the best matches. Shipped as migration 49.
  Verified on the live 320-chunk corpus: 0 results before, 10 correctly ranked
  after. The same ordering defect was then found in `fn::text_search` (present
  since migration 1) and fixed in migration 50 — an unordered `LIMIT` returned
  an arbitrary slice, so text search had been answering with mediocre matches;
  live top score for "architecture" went 2.586 → 6.363. That leg feeds
  v0.8.113's hybrid fusion, which ranks purely by position, so it degraded
  hybrid results too. Guarded by a real-planner integration test plus a static test that
  runs unconditionally, since the integration suite is opt-in.

- **v0.8.113** ✨ **Hybrid retrieval.** `/api/search` ran one leg and threw the
  other away; it can now run both and fuse them with Reciprocal Rank Fusion
  (architecture from qmd, MIT). Additive, degrades to a single leg rather than
  failing, and exposed in the search UI.


## v0.8.112 — 2026-08-19 — Source visuals can be enabled in a packaged build

🐛 **The source-visual kill switch was unreachable in a packaged app.**
`DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED` is documented as the runtime toggle,
but a Dock-launched `.app` inherits no shell environment, and the launcher seeds
its children from its own `os.environ` — so there was no supported way to set
it. `launcher.env` looked like the place and silently ignored it: that file is
filtered through a five-key whitelist.

Added to `ALLOWED_KEYS`. That list carries an explicit "do not add arbitrary env
vars" rule and the addition fits it rather than bending it — a documented on/off
knob, not a secret. The alternative was `launchctl setenv`, which does not
survive a reboot.

✅ **Verified live rather than assumed.** With the flag on, the gated endpoints
answer 409/422 instead of the 404 they return when disabled, and the extractor's
native dependencies are present and working in the API's actual venv
(`~/.deeper-notebook/venv`): PyMuPDF 1.27.2.3, imageio-ffmpeg 0.6.0 with its
bundled ffmpeg binary, Pillow 11.3.0. Rendering `tests/fixtures/source_visuals/
fixture.pdf` in that interpreter produces a 640x360 PNG. Checked without
uploading anything to the user's notebook.

- **v0.8.112** 🐛 **Source visuals could not be enabled in a packaged build** —
  the documented kill switch was unreachable because `launcher.env` filters
  through a five-key whitelist and a Dock-launched app inherits no shell env.
  Added to the whitelist; verified live end to end.


## v0.8.111 — 2026-08-19 — `ruff format` adopted

Enabled in `.pre-commit-config.yaml`; 701 files reformatted and the tree is now
stable (1,126 files already formatted, so the hook is a no-op going forward).

It stayed off for three releases because the rebrand allowlist pinned approvals
to line and column, so a repo-wide reformat invalidated them wholesale and
`--regenerate` refused to rebuild. v0.8.109 re-keyed approvals onto content;
this is the payoff.

**Adoption was reviewed, not driven to green.** ~1,046 pins relocated
automatically; 38 re-approvals were listed with their before/after text in
`docs/verification/2026-08-19-formatter-adoption-review.md` and checked before
being applied. One proposed mapping was WRONG — it would have moved a regex
approval onto an unrelated `assert` — and the review caught it. Two further
approvals were carried onto the occurrence the audit itself named, where
substring nesting made ordinal mapping ambiguous. No new approvals were created
and no category was re-judged.

🐛 **A third `repair_rebrand_pins.py` bug fixed.** It set the inventory digest
and then ran `--regenerate`, which rewrites entries and invalidates the digest
just written. The stale constant made the pinned inventory load EMPTY, every
compatibility contract resolve to `None`, and the failure surface as
"compatibility entry must use its exact canonical compatibility contract" — a
message that says nothing about digests. The digest is now re-settled after
regeneration.

⚠️ **Do not remove the `# fmt: skip` / `# fmt: off` guards** in
`tests/test_environment_aliases.py` and `desktop/tests/test_data_root_migration.py`.
Ruff joins adjacent string literals, and those tests write `"open_" "notebook"`
split on purpose so they do not trip their own scan.

- **v0.8.111** ✨ **`ruff format` adopted** — 701 files, hook enabled, tree
  stable. 38 re-approvals reviewed line by line (one proposed mapping was wrong
  and the review caught it); a third repair-tool bug fixed along the way.


## v0.8.110 — 2026-08-19 — Formatter adoption attempted; two real hazards fixed instead

Adoption was driven end to end and **reverted again**, deliberately. The
reversion is the finding, not a failure to finish.

🔒 **`ruff format` silently undoes a deliberate identity-hygiene device.** It
joins adjacent string literals, and two tests that SCAN production source for
the legacy token write it split precisely so they do not trip their own scan:

    production_roots = ("api", "commands", "desktop", "open_" "notebook")

Joining that reintroduces the exact token the codebase is designed not to
contain, and the audit then correctly demands an approval for it. Adding those
two approvals — which is what "just get it green" would have done — would have
quietly retired the device. Both sites now carry `# fmt: skip` (and
`# fmt: off/on` for the multi-line one, which `skip` does not cover) plus a
comment explaining why, so the protection holds whether or not a formatter is
ever enabled.

🐛 **Two real bugs in `repair_rebrand_pins.py`**, both found by using it:

  * It computed each pin's digest from the **stored** column against the NEW
    line text. Reindentation moves that column, so the ordinal came out wrong,
    so the digest came out wrong, and pins whose indentation merely changed were
    reported as "content changed" — 49 of them on a 702-file reformat, nearly
    all false. It now tries every occurrence on the candidate line and corrects
    the column as well as the line.
  * It could relocate two entries onto the **same** occurrence when their
    approved lines were identical, producing "duplicate allowlist entry" on the
    next load. Occurrences are now claimed at most once.

Both matter for ordinary edits, not just formatting. Verified by provoking a
break and repairing it round-trip.

📋 **ROADMAP §1.3 now carries the full shape of adoption**: ~1,040 pins
relocate automatically, ~39 are genuinely re-split (unambiguous mapping — live
occurrence count equals pinned count per file), ~160 collapse as legitimate
duplicates under content-keying, and the split-literal hazard above. The
remaining cost is a reviewed diff, not an engineering problem.

- **v0.8.110** 🔒 **`ruff format` joins adjacent string literals**, undoing the
  deliberate `"open_" "notebook"` splits two self-scanning tests rely on. Both
  sites are now protected and documented; formatter adoption was reverted rather
  than papered over with two new approvals.
- **v0.8.110** 🐛 **Two bugs fixed in `repair_rebrand_pins.py`** — it used the
  stale column (misreporting reindented pins as changed) and could claim one
  occurrence twice (producing duplicate allowlist entries).


## v0.8.109 — 2026-08-19 — The allowlist ratchet, actually fixed

ROADMAP §2.1, and the second attempt. The first was reverted on 2026-08-19 for a
good reason; this one keeps the property that reversion protected.

🛠 **Approvals are keyed on content, not position.** They were pinned to
`(path, pattern, source, line, column, sha256(RAW line))`. Every positional
component moves when a file is edited, so an edit ABOVE a pinned line
invalidated a still-correct approval — a ratchet that fired repeatedly, once
relocating 416 pins for a single-line workflow edit — and a reformat invalidated
everything at once, after which `--regenerate` aborted rather than rebuilt.

The digest now hashes whitespace-normalized content, and lookups fall back to
`(path, pattern, source, digest)` when the positional key misses.

🔒 **What the first attempt got wrong, and how this fixes it.** Dropping
`column` outright is a real weakening, and the suite proves it:
`test_exact_context_does_not_hide_distinct_same_line_active_occurrence` puts the
legacy token on ONE line twice, approves one occurrence, and asserts the other
is still flagged. Ignoring column lets the approval cover both.

`column` was never merely location — it is the only thing separating two
occurrences on a line. So the **intra-line ordinal is folded into the digest**:
the first and second occurrence of a token on the same line hash differently.
Position leaves the key; the distinction it carried does not. Both tests pass.

`context_sha256` is now defined as the ordinal-0 digest, deliberately identical
to `occurrence_digest` for the first occurrence on a line. That equivalence
collapsed what would have been ~18 fragile per-fixture rewrites to zero: every
whole-path approval and every single-occurrence line approval keeps working
untouched, and only a genuine second-occurrence approval says so explicitly.

⚠️ **One assertion was changed, deliberately, and is flagged for review.**
`test_allowlist_uses_exact_persisted_context_not_broad_module_pattern` shifted a
key's column by +1 and asserted rejection — i.e. it asserted absolute column is
part of identity. That is now unreachable rather than relaxed: the scanner
derives digest and column together from the same `(context, column)`, so no code
path can produce a shifted column with an unchanged digest; only a hand-built
tuple can. The assertion was replaced with the reachable property — a key naming
a genuinely DIFFERENT occurrence is still rejected — which is strictly stronger
than checking one integer field.

**Measured payoff.** A 702-file `ruff format` previously invalidated the
allowlist wholesale and `--regenerate` refused. It now leaves **1,044 pins
auto-relocated by content** and **49 entries genuinely changed** (lines ruff
re-split), which is exactly what should require review. The formatter is no
longer blocked by the allowlist; adopting it is now a bounded 49-entry review
rather than an impossibility.

Migration was verifying, not blind: an approval was re-keyed only after
confirming the file still contained the recorded content. 2,608 migrated, 19
relocated, 0 unresolved. `scripts/persisted_queue_inventory.py` duplicates the
hashing (rebrand_audit imports it, so it cannot import back) and was updated in
lockstep; when only one side normalized, 30 compatibility entries resolved to a
null contract.

- **v0.8.109** 🛠 **The allowlist ratchet is fixed (ROADMAP §2.1).** Approvals
  are keyed on whitespace-normalized content with the intra-line ordinal folded
  into the digest, so an edit above a pinned line no longer invalidates it —
  while two occurrences on one line still hash differently, which is the
  property that forced the first attempt to be reverted. A 702-file reformat now
  costs a 49-entry review instead of being impossible.


## v0.8.108 — 2026-08-19 — The security gate now runs, and there is a roadmap

🔒 **`make security-scan` was a manual target that ran in no workflow.** Not a
theoretical gap: Bandit's B608 count was burned down to 0 on 2026-08-17, v0.8.97
reintroduced one in `study/exams.py`, and it shipped and sat undetected until
someone ran the scan by hand. A gate nobody runs is not a gate. `test.yml` now
has a `security-scan` job.

Split deliberately. **Bandit fails the build** on HIGH severity in project code
— offline, deterministic, and its clean state is worth enforcing. **pip-audit
reports without failing**, because it queries the live PyPI advisory database
and a CVE published overnight would otherwise turn every unrelated PR red; its
residuals are triaged in `docs/verification/2026-08-16-security-scan.md`.

✨ **`docs/ROADMAP.md`.** An inventory found no roadmap or backlog anywhere —
only retrospective per-feature plans. Answering "what needs building?" meant
reconstructing it from flag defaults, CI config, and §4 of PROJECT-DEEP-DIVE.
For 49 routers and 5,754 tests, that reconstruction cost was the single most
consequential missing artifact. Every figure in it is measured, with the
commands named so they can be re-run rather than trusted.

🐛 **Model-config health now covers the embedding default too.** A dangling
embedding default is quieter than a dangling chat default and arguably worse:
chat fails loudly on the next turn, while embedding failure degrades search and
every retrieval-grounded answer with no obvious error. Codes are derived per
slot, so chat emits exactly the strings it always has and embedding gets its
own. Still deliberately not every slot — listing TTS/STT would train people to
ignore the panel.

🔍 **One finding that reversed on inspection.** `source_visuals_enabled()` reads
`os.environ` directly instead of using `resolve_env`, which looked like a
one-line inconsistency worth fixing. It is not: the name is absent from
`environment.SETTINGS`, so `resolve_env` cannot resolve it at all — routing it
through silently makes the flag unreadable (four tests fail immediately). The
change was reverted and the roadmap entry rewritten to say what the real fix is
(register the setting).

- **v0.8.108** 🔒 **The security scan now runs in CI.** It was a manual make
  target in no workflow, which is how a B608 regression shipped and went
  undetected for two days. Bandit fails on HIGH; pip-audit reports without
  failing, since it queries a live advisory database.
- **v0.8.108** ✨ **`docs/ROADMAP.md`** — the repository had no roadmap or
  backlog at all; "what needs building?" required reconstruction from flag
  defaults and CI config.
- **v0.8.108** 🐛 **Model-config health extended to the embedding default**,
  whose failure is quieter than chat's and degrades search silently.


## v0.8.107 — 2026-08-19 — Quiet Bandit, and a packaged build that can be rolled back

🛠 **86 `# nosec` tags stripped of prose.** Bandit parses everything after
`nosec` as a comma/space separated list of test IDs, so
`# nosec B608 - constants/whitelisted identifiers; values bound` was read as
five bogus IDs and rejected one at a time. Suppression still worked — these were
noise — but the noise buried the warnings that matter.

Truncated IN PLACE, never moved to a preceding line: adding lines shifts every
pinned line below it and detonates the rebrand-allowlist ratchet. The prose is
not lost information; v0.8.99 established that exactly this claim is not a
control when it lives in a comment, and the real enforcement is
`_validate_vector_id`, `ensure_record_id`, the table/order-by allowlists, and
`tests/test_v0_8_99_identifier_guards.py`.

Also reworded six comments that merely *mentioned* the tag — including two
containing the word **"nanoseconds"**, which contains the literal token
(na-**nosec**-onds) and made Bandit parse the following prose as test IDs.
Parse warnings: 101 → **0**, with B608 still at 0 and the four triaged residuals
unchanged.

✨ **`GET /api/features` — runtime feature state.** Frontend flags are
`NEXT_PUBLIC_*`, which Next INLINES at build time, so a packaged .app has its UI
feature set frozen in the bundle: turning a feature off server-side left its
controls rendered and dead (§4.3; this produced the dead Refresh/Remove buttons
in the source gallery, patched for that one surface in v0.8.86 without
addressing the cause).

Six frontend flags already had a backend predicate in
`deeper_notebook/feature_flags.py` that is the real authority. The endpoint
publishes them; `applyRuntimeFeatures` in `frontend/src/lib/features.ts` adopts
them over the inlined defaults, and `useRuntimeFeatures` fetches once per
session from the dashboard layout.

Fail-soft by construction: the inlined value stays the DEFAULT, so an
unreachable endpoint, a backend predating this, or a malformed payload all leave
behaviour exactly as today. Only booleans are adopted — a truthy string would
silently re-enable a rolled-back feature, which is the precise failure this
prevents. A separate endpoint rather than a key on `/api/config`, because that
one performs a GitHub update check and feature resolution must not inherit its
latency or its failure modes.

🔍 **React `act()` warnings: diagnosed, deliberately not fixed.** ~100 warnings
across 15+ components with at least three unrelated causes — a
`MutationObserver` in `GuidedTipsProvider`, Radix internals in
`Tooltip`/`Popper`/`Presence` (20), and assorted unawaited async state. Every
test passes; these are noise. Editing ~25 currently-passing test files
immediately after v0.8.102 made this suite deterministic is a bad trade, and the
grouping is recorded in §4.6a so it can be done per-cluster when someone is
already in that area.

- **v0.8.107** 🛠 **86 `# nosec` tags stripped of prose** that Bandit parsed as
  bogus test IDs (including two comments containing "nanoseconds", which hides
  the literal token). Parse warnings 101 → 0; B608 still 0.
- **v0.8.107** ✨ **`GET /api/features`** publishes backend-authoritative flag
  state so a packaged build can be told a feature was rolled back instead of
  rendering dead controls. Fail-soft: the build-time inlined value remains the
  default.
- **v0.8.107** 🔍 **`act()` warnings diagnosed and deliberately deferred** —
  three unrelated causes, ~25 passing test files, recorded in §4.6a.


## v0.8.105 — 2026-08-19 — The health check cried wolf at a correct setup

🐛 **Removed `auto_route_without_cloud`, added one release earlier.** It called
`health.add()`, which sets `ok=False`, so "auto-route enabled with no cloud
model" pushed the whole runtime snapshot to **degraded** and raised an alert in
the status panel — for a configuration that is not merely valid but is the
expected one. This product's governing constraint is that everything works with
the network cable unplugged; a local-only install having no cloud model is the
default case, not a fault.

Since v0.8.100 auto-route degrades cleanly to the configured default in exactly
that situation, so nothing is broken and nothing needs fixing. A panel that
cries degraded at a correct setup trains people to ignore it, which costs more
than the note was ever worth — and the entire point of v0.8.104 was to make that
panel worth reading.

The test is inverted rather than deleted: it now pins that a local-only
auto-route install reports healthy, so this cannot regress quietly.

- **v0.8.105** 🐛 **The model-config health check flagged a correct local-only
  setup as degraded.** `auto_route_without_cloud` (added in v0.8.104) marked the
  runtime unhealthy for an install with no cloud model — the expected shape for
  a local-first product. Removed; the test is inverted to pin healthy.


## v0.8.104 — 2026-08-19 — Broken model configuration now says so

`get_default_model` has logged "the configured model_id may have been deleted or
misconfigured" since long before this release. That log goes to a file nobody
opens, while the UI simply fails to answer. The information needed to explain
the failure existed in the process; none of it reached the person who could act
on it.

✨ **`deeper_notebook/health/model_config.py`** evaluates the default model
assignments structurally and surfaces the result through
`GET /api/runtime/snapshot` — which the dashboard and Settings already read, so
it reaches the UI without a new endpoint. Detected states:

    chat_default_missing              no chat default set
    chat_default_dangling             default points at a row that no longer exists
    chat_default_credential_missing   endpoint provider with no credential
    chat_default_endpoint_missing     credential with no base URL
    auto_route_without_cloud          toggle promises routing it cannot perform
    model_defaults_unreadable         the defaults record itself will not load

Each carries a `detail` naming the offending id and a `remedy` naming the screen
that fixes it. The panel renders both verbatim: a reason code alone ("Model
configuration needs attention") is the same unhelpful shape as the log.

🔒 **Deliberate limits, stated rather than glossed.** These are structural checks
— no model is called, nothing spawns, no network I/O — because the runtime
snapshot must stay a bounded read-only projection. So this CANNOT catch "the row
is valid but the server rejects its name", which is the v0.8.97 MLX defect. Only
asking the server catches that, which is what
`tests/integration/test_chat_model_seams.py` does. Claiming broader coverage
would be worse than checking nothing.

Additive and backward compatible: the snapshot field is defaulted and the
frontend type optional, so a client predating this parses a v0.8.104 payload and
a v0.8.104 client parses an older backend's. Both directions are tested.

- **v0.8.104** ✨ **A broken model configuration now reports itself** through the
  runtime snapshot and the runtime status panel, with a detail naming the
  offending id and a remedy naming the screen that fixes it — instead of a log
  line nobody reads and a UI that silently fails to answer. Structural checks
  only; the "valid row the server rejects" case is covered by the seam tests
  instead, and that limit is documented rather than glossed.


## v0.8.103 — 2026-08-19 — Seam tests: the composition, not the units

Three catastrophic defects shipped this month while every unit test passed,
because each lived in the composition rather than in any single unit: auto-route
hard-failing (v0.8.100), MLX models registered under a name no server answers to
(v0.8.97), and a `default_chat_model` pointing at a legacy migration artifact.
Unit tests that mock the layer below cannot see any of them.

✨ **`tests/integration/test_chat_model_seams.py`** drives the real chain —
defaults -> model row -> credential -> base_url -> adapter -> HTTP -> parsed
reply — with nothing mocked below `provision`. The only fake is the process at
the far end of the socket.

That fake is the point. `_ProtocolFaithfulModelServer` reproduces the one
upstream behaviour that turns a wire-id mismatch from silent into loud: **404 on
any `model` value it was not launched with**, exactly as `mlx_lm.server` does
(verified live during the v0.8.97 investigation). A fake that answered every
request would pass all four tests while catching none of the bugs.

Four tests: a turn completes against a real socket; a prettified display name
fails loudly rather than silently; auto-route with no benchmark history still
answers; a dangling default resolves to None instead of raising.

**Verified by reintroducing the bug.** Reverting the v0.8.100 fix fails
`test_auto_route_with_no_benchmark_history_still_answers` and *only* that test —
targeted detection, not incidental coverage.

🛠 **Root `conftest.py` gains an opt-out for session-scoped env owners.** Writing
these surfaced a real defect in v0.8.102's env-restore hook:
`tests/integration/conftest.py` exports `SURREAL_NAMESPACE`/`SURREAL_DATABASE`
from a session fixture, whose setup runs inside the FIRST test's protocol — so
the hook restored them away and starved every later test ("params.0: Input
should be a valid string"). Items marked `integration_surreal` now opt out; they
are gated, run as their own CI job, and manage env deliberately.

- **v0.8.103** ✨ **Seam tests for the config -> resolution -> routing -> HTTP
  chain**, with a protocol-faithful fake model server that 404s unknown model
  ids the way mlx_lm does. Proven to catch the v0.8.100 auto-route defect:
  reverting that fix fails exactly one of the four and nothing else.
- **v0.8.103** 🛠 **Fixed v0.8.102's env-restore hook starving integration
  tests** — session-scoped fixtures that own env now opt out via marker.


## v0.8.102 — 2026-08-18 — The flaky suite, root-caused

Four consecutive full runs failed four different sets of tests, all passing in
isolation. Test order is NOT randomised, so the non-determinism had to be
runtime state. It was.

🛠 **Root cause: env leakage across tests.** `monkeypatch.setenv` undoes only the
key it wrote, but `normalize_product_environment` deliberately MIRRORS a
canonical name into its legacy spellings (`DN_*`, `ONP_*`, `OPEN_NOTEBOOK_*`),
and monkeypatch has no record of writes it did not make — so the mirrors outlive
the test. This is the footgun already recorded as §4.7 in PROJECT-DEEP-DIVE;
what was missing was proof of which tests actually did it. Instrumenting a full
run found exactly four:

    test_evidence_studio_artifact_api.py  DN_/ONP_/OPEN_NOTEBOOK_EVIDENCE_STUDIO
    test_v0_8_40b_hot_swap.py             DEEPER_NOTEBOOK_/OPEN_NOTEBOOK_ACTIVE_GGUF_MODEL
    test_v0_8_40d_env_refresh.py (x2)     OPEN_NOTEBOOK_LOCAL_N_CTX

Leaked feature flags are how a deterministically-ordered suite still varies run
to run: `tests/test_environment_aliases.py` builds a subprocess env from
`dict(os.environ)`, so it inherits whatever ran before it.

Fixed at the class rather than per-test: a root `conftest.py` snapshots and
restores `os.environ` around every test. Fixing the four individually would
require every future test to know every mirror spelling — the same
caller-discipline assumption the SurrealQL identifier guards exist to reject.

Two implementation details that were measured, not guessed. It is a
**hookwrapper, not an autouse fixture**: an autouse fixture tears down while
other fixtures are still finalising, so it sees monkeypatch's own not-yet-undone
writes as leaks — 67 false positives across three files. And it lives at the
**repo root**, not under `tests/`, because `testpaths` is
`["desktop/tests", "tests"]` with `tests/integration/` a third root, and
desktop/tests runs FIRST — so a tests/-only conftest would leave the actual
upstream polluter unprotected.

`DEEPER_NOTEBOOK_TEST_STRICT_ENV=1` prints leaks as they happen (still
repairing them). That is how these four were isolated, and how the next one
gets found.

Verified: 5621 passed, 0 failed, with all four leaks reported and repaired.

- **v0.8.102** 🛠 **Root-caused the flaky suite.** Four full runs, four different
  failure sets, all passing in isolation. Cause: `monkeypatch` cannot undo the
  legacy-spelling env mirrors `normalize_product_environment` writes, so four
  tests leaked feature flags into everything that ran after them. A root
  `conftest.py` now restores `os.environ` around every test — a hookwrapper
  rather than an autouse fixture, because the latter observes teardown too early
  and produced 67 false positives.


## v0.8.101 — 2026-08-17 — Gate sweep: a B608 regression, a hard-failing test, dead broken markup

Ran every gate to find what was actually broken rather than guessing. Three
real defects; the rest of the sweep came back clean and is recorded as such.

🔒 **Bandit B608 regressed 0 → 1.** The 2026-08-17 burn-down took SurrealQL
string-composition findings to zero; `study/exams.py::list_recent` (added in
v0.8.97 with ExamLab) reintroduced one. Verified a false positive — `where` is
either `""` or a literal assigned three lines above, a local rather than a
parameter, and both `notebook_id` and the `[1,100]`-clamped `limit` travel as
`$`-bound vars — so it takes the standard `# nosec B608` tag. Back to 0; the
four remaining MEDIUMs are exactly the residuals triaged in
`docs/verification/2026-08-16-security-scan.md`.

The tag is deliberately bare. The house form appends prose (`- constants/…`),
which Bandit parses as further test IDs and rejects one word at a time
("Test in comment: constants is not a test name or id, ignoring"). Suppression
still works, so the ~90 existing tags are noisy rather than broken — left alone
rather than churned, but new ones need not add to it.

🐛 **`test_real_pywebview_exposes_the_keys_we_pin` hard-failed everywhere the
desktop runtime isn't installed.** `pywebview==5.4` is declared in
`desktop/requirements.txt` (bundled app runtime), not `pyproject.toml`, so it is
legitimately absent from the dev/server venv. The test already *intended* to
tolerate that — it carries a `# pragma: no cover - env without pywebview`
guard — but the bare `__import__("webview")` raised `ModuleNotFoundError`
before that guard could run. Now `pytest.importorskip`, which reaches the
documented intent. Assertions untouched: they still run wherever pywebview is
present, which is the environment whose API they guard.

🐛 **`VirtualizedList`'s `containerAs="tbody"` mode emitted invalid DOM.** It
rendered `<tbody>` inside the component's hardcoded `<div>` scroll parent *and*
wrapped each row in a `<div>` inside that `<tbody>` (which may only hold
`<tr>`), so React warned "In HTML, `<tbody>` cannot be a child of `<div>`. This
will cause a hydration error." on every frontend test run. **Nothing used it** —
`SourcesColumn` is the only caller in the app and uses `VirtualizedListAuto`
with no `containerAs` — and its one test asserted only that a `<tbody>` existed,
which is true of invalid markup too, so it pinned the element swap while saying
nothing about validity.

Removed rather than fixed blind: real in-table virtualization needs more than an
element swap (the row wrappers are absolutely positioned, which table layout does
not honour), so it should be designed against an actual table consumer instead of
left as a broken stub. The replacement test asserts the valid rowgroup/row
structure and that no `<tbody>` appears.

- **v0.8.101** 🔒 **Bandit B608 regressed 0 → 1.** `study/exams.py::list_recent`
  (added with ExamLab in v0.8.97) reintroduced a SurrealQL string-composition
  finding after the 2026-08-17 burn-down. Verified a false positive and tagged
  `# nosec B608`; back to 0, with the four remaining MEDIUMs exactly the
  residuals already triaged in `docs/verification/2026-08-16-security-scan.md`.
- **v0.8.101** 🐛 **`test_real_pywebview_exposes_the_keys_we_pin` hard-failed**
  wherever the bundled desktop runtime isn't installed. The test already meant
  to tolerate a venv without `pywebview`, but its `__import__` threw before the
  guard that says so could run. Now `pytest.importorskip`; assertions untouched.
- **v0.8.101** 🐛 **`VirtualizedList`'s unused `containerAs="tbody"` mode emitted
  invalid DOM** (`<tbody>` inside a `<div>`, `<div>` rows inside the `<tbody>`),
  producing a React hydration warning on every frontend test run. No caller in
  the app used it; removed, with a replacement test that asserts valid
  rowgroup/row structure instead of merely that a `<tbody>` existed.


## v0.8.100 — 2026-08-17 — Auto-route no longer hard-fails a local-only install

🐛 **Bug fix — chat was dead for anyone who enabled auto-route without
benchmarking a model.** `provision_langchain_chat_model` resolves its local
candidate from `DEEPER_NOTEBOOK_LOCAL_CHAT_MODEL_ID` or, failing that, from
`_measured_local_chat_model_id()` — which only returns a model when *benchmark
history proves one*. A fresh install has no benchmark history. With no cloud
credential either, both candidates came back `None`, `pick_provider` hit its
step-5 `ValueError("No model available — neither local nor cloud")`, and every
chat turn died. The configured `default_chat_model` was valid the whole time,
and the identical turn with the toggle OFF answered normally. The error named
neither the toggle nor the missing benchmark, so it read as "no models
installed" on a machine with ninety of them.

Routing between zero candidates is not routing. Auto-route now degrades to the
default path — the same call the toggle-off branch already makes.

The router itself is unchanged and still raises on two `None`s; that is correct
for a pure function, and a test pins it so the fix stays in the caller. The
tempting one-liner — assigning `default_chat_model` to `local_model_id` — was
rejected as a **privacy regression**: the privacy gate uses `local_model_id` as
its "safe to keep on-device" reroute target, so mislabelling a cloud default as
local would let the gate send secrets *to* the cloud while reporting them kept
on-device. The degraded path does not run the privacy gate, which is unchanged
from the toggle-off path today and not a new gap — with no candidates the gate
has no reroute target regardless.

Diagnosed by driving the real installed app, where this masked a second,
data-only problem: a `default_chat_model` pointing at a legacy env-migration
artifact — a model row literally named `default_model` that no server can
answer to. That row is structurally valid, so no static check can reject it;
only the model server can. With this fix the operator now sees the server's
actual rejection instead of a misleading "no model available". No code change
was warranted for it, and none was made.

🛠 **Infra — `ruff-format` removed from `.pre-commit-config.yaml`.** Listed
since v0.7.120 but never run, because pre-commit was never installed. The first
`pre-commit install` would have rewritten 697 files and broken the build: a
trial sweep failed 5 source-shape guards. Those guards are the underlying smell
— replacing them with behavioural assertions is the real fix and is a project,
not a polish-pass edit. `ruff check --fix` stays.

- **v0.8.100** 🐛 **Auto-route hard-failed a local-only install.** With the
  Settings toggle on and no benchmark history, `provision_langchain_chat_model`
  resolved neither a local nor a cloud candidate and `pick_provider` raised
  "No model available — neither local nor cloud" on every chat turn — while a
  valid `default_chat_model` sat unused and the same turn with the toggle off
  answered normally. Auto-route now degrades to the default path. The router is
  unchanged and still raises on two `None`s (pinned by test); the fix belongs in
  the caller. Assigning the default to `local_model_id` was rejected as a
  privacy regression — the privacy gate treats `local_model_id` as its
  keep-on-device target.
- **v0.8.100** 🛠 **`ruff-format` dropped from `.pre-commit-config.yaml`.**
  Configured since v0.7.120 but never run, because pre-commit was never
  installed; the first `pre-commit install` would have rewritten 697 files and
  failed 5 source-shape guards. `ruff check --fix` stays.


## v0.8.99 — 2026-08-17 — Release-gate audit: two passes

Two systematic audit passes over the whole application. Most checked classes
came back already clean and were reported rather than churned; these are the
real defects found and fixed.

- **v0.8.99** 🐛 **Command input schemas.** `extract_source_visual` registered
  an input model whose JSON schema could not be built:
  `commands/source_visual_commands.py` used `from __future__ import
  annotations`, so the `@command` decorator saw the string
  `"ExtractSourceVisualInput"` and the generated model was never fully defined.
  Registration still succeeded — the queue worked and nothing looked broken —
  so it only surfaced when the module was imported in isolation, which read as
  runner-dependent flake. 1 of 17 commands affected; now 0, guarded by a suite
  that imports every command module in one process.
- **v0.8.99** 🔒 SurrealQL identifier interpolation is now validated **by
  construction** at every site that takes a caller-supplied name:
  `memory.surreal_store.count`, `database.dedup_edges`, and
  `knowledge_engine.navigation_repository._random_where`. Each carried a
  `# nosec B608` asserting whitelisted identifiers while relying on caller
  discipline; an assertion resting on convention is a comment, not a control.
  (`evaluation.repository.latest_run` already had the correct guard — the
  house pattern the others now match.)
- **v0.8.99** ⚡ `artifact_sources` fetched every selected source with a
  sequential `await` in a loop — an N+1 on the path of every Evidence Studio
  generation, duplicated byte-for-byte in the router. Now concurrent via
  `asyncio.gather`, with the 404 contract preserved exactly (result order kept;
  the first *id-order* failure raises). The duplicate is gone: the router
  injects `source_cls`/`notebook_cls` from its own namespace, which keeps the
  26-site Evidence Studio patch seam intact without coupling
  `deeper_notebook/` to `api/`.
- **v0.8.99** 📊 Startup instrumentation. `core_ready` was one opaque bucket
  spanning all of `start_all()`; a fresh install measured 114,328 ms with no
  way to attribute it. The Supervisor now records `database_up`, `api_up`,
  `worker_up`, `frontend_up`, and `sidecars_up`. Purely observational — a
  recorder that raises is swallowed, because instrumentation must never fail a
  boot.
- **v0.8.99** 🧪 Five source-shape guards pinned the formatter's line-breaking
  along with the tokens they protect. They now match on whitespace-normalised
  source or tolerant regexes — same invariants, immune to reflow, verified by
  reformatting a scratch copy of the tree and re-running them there.
- **v0.8.99** 🐛 The launcher-dialog tests required `_tkinter`, which the
  Homebrew interpreter lacks while the bundled runtime ships it. An autouse
  fixture stubs the module only when absent, so the branch logic is covered
  everywhere rather than skipped.
- **v0.8.98** 🎨 Visual: ExamLab no longer repeats its own name and description
  directly beneath the section heading that already carries both; the Evidence
  Studio output-mode rail widened from the shared 15rem minimum to 18rem via a
  scoped variant, so mode descriptions stop wrapping to one or two words a line.
- **v0.8.98** 🧹 `graphify-out/` (782 generated AST dumps, ~53 MB) is
  gitignored. It indexes this repository, so once tracked it made the identity
  audit report ~103,000 false positives quoted out of its own index.

## v0.8.97 — 2026-08-17 — ExamLab, Debate mode, Cornell Notes

Three study features. The first two adopt ideas from PageLM (CaviraOSS);
implementations are original — their license does not permit code reuse.

- **v0.8.97** ✨ **ExamLab** — timed exam simulation in the Study workbench,
  built on Evidence Studio quiz artifacts. Starting an attempt snapshots the
  quiz's questions (so mid-attempt artifact edits can never corrupt grading),
  stamps a server-side deadline, and serves the taking view WITHOUT the answer
  key. Grading is deterministic and local — submitted option ids against the
  snapshot, no model call — so results are instant and identical offline. Late
  submissions are graded and flagged, not rejected. Missed questions can be
  seeded into the FSRS review deck (idempotent via `seeded_indices`; evidence
  uses the Anki-import self-referential-span precedent). New table
  `study_exam_attempt` (migration 48), router `/api/study/exams/*`, and an
  ExamLab panel on /study.
- **v0.8.97** ✨ **Debate mode** — a per-turn `chat_mode` on notebook chat
  (`disabled_mcp_servers` precedent) that swaps the system template for
  `prompts/chat/debate.jinja`: steelman the user's position first, argue the
  strongest opposing case FROM THE SELECTED SOURCES with the standard citation
  contract, concede what the sources concede, and mark pure reasoning as such.
  Toggle (⚔ Debate) sits beside the MCP tool picker; `debateMode` joins the
  sendMessage deps array (the v0.8.46b stale-closure lesson applied on day
  one).
- **v0.8.97** ✨ **Cornell Notes** — a default-library transformation
  (migration 47) that restructures any source into cue/notes/summary Cornell
  format, grounded-only by instruction.

## v0.8.96 — 2026-08-17 — Command row stops covering itself

- **v0.8.96** 🐛 Bug fix: the Focus mode control was `position: absolute` at the
  shell's top-right and was drawn directly on top of the Quick actions command
  trigger, so both labels rendered stacked in the corner of every dashboard
  screen at every width (measured at 320, 768, 1024, and 1440 px). It is now a
  flow item inside a new `.dn-command-actions` group in the command bar, so
  flexbox lays the two controls out side by side. The legacy shell has no
  command bar and still floats it; that path is unchanged.
- **v0.8.96** A width-reservation rule added earlier to work around this never
  took effect: it selected `.dn-workspace-shell-body > .dn-command-bar`, but the
  rendered command bar's parent is `.dn-luminous-workspace`, so it matched
  nothing. Removed rather than re-pointed — there is no width to reserve now.
- **v0.8.96** The audit guard only compared the focus control with
  `.dn-command-title`, and only at 320 px, which is why the overlap with
  `.dn-command-trigger` went unseen. A new guard compares the control against
  every command-row control at every canonical width; it fails on the old
  layout and passes on the new one.

## v0.8.95 — 2026-07-17 — Portable podcast audio paths

- **v0.8.95** Bug fix: podcast episode path tests now assert `Path` structure
  rather than POSIX-only rendering, and canonical Windows `file:///C:/...`
  audio URIs are converted to native paths before the existing resolved-path
  containment gate runs. Remote file URI authorities remain rejected.

## v0.8.94 — 2026-07-17 — Verified desktop release baseline

- **v0.8.94** 🛠 **Verified desktop release baseline.** Added deterministic
  release manifests (version, commit, UTC build time, artifact size, and
  SHA-256), checksum publishing, and CI package gates. Windows now ships a
  per-user Inno Setup 6.7.1 installer alongside the complete onedir ZIP; both
  paths include the full PyInstaller bundle, Start Menu entry, upgrade identity,
  and uninstall support. CI verifies the setup package with a silent
  install-launch-upgrade-uninstall probe. Also fixed the notebook unused import and the
  podcast submit callback dependency.

- **✨ v0.8.93 — Evidence Studio visual deliverables and responsive previews**
  - Structured Slide deck artifacts now export an editable 16:9 **PPTX** plus a deterministic multipage **PDF**. Titles and bullets remain real PowerPoint text, while speaker notes retain the generated notes, visual direction, and citation markers. Structured Infographic artifacts now export nonblank **PNG** plus one-page **PDF** files in portrait, landscape, or square orientation, with distinct treatments for text, metric, timeline, comparison, process, and chart panels.
  - Added purpose-built in-app Slide deck and Infographic viewers. Slide decks have a thumbnail rail, keyboard/icon navigation, a stable 16:9 stage, notes disclosure, and citations; infographics use orientation-aware constrained layouts. Saved-file actions prioritize PPTX/PDF/PNG ahead of Markdown/JSON while preserving the existing Open, Copy, and Folder workflow.
  - Visual export generation is local and failure-isolated: incomplete files are removed, warnings contain no source content, and a renderer failure leaves the validated Markdown/JSON artifact completed. A valid structured PATCH snapshots the prior revision and refreshes all existing exports so edited documents cannot present stale visual files.
  - **🐛 Mobile artifact viewer:** the desktop `max-w-4xl` dialog override could exceed a phone viewport and clip the export/citation/footer controls. The dialog now keeps a fixed viewport gutter and scrolls on small screens, while retaining the constrained desktop layout.
  - **🛠 Dependencies and proof:** added direct `python-pptx` and Pillow application dependencies, reopen tests for every file format/orientation, API generation/edit/failure tests, viewer interaction tests, desktop/mobile browser checks, and full backend/frontend build gates.

- **✨ v0.8.92 — Validated, model-independent Evidence Studio documents**
  - New Evidence Studio generations use versioned Pydantic documents, provider-native structured output when available, exact JSON Schema fallback for plain/local chat models, and one bounded repair attempt before failing closed. Deterministic renderers produce canonical Markdown while preserving the legacy `content` alias, so existing artifacts continue to open without a migration.
  - Structured PATCH edits are revalidated and recompute derived viewer/export metadata. Markdown-only legacy records, revision history, Course Pack study progress, and existing sidecars remain backward compatible.

- **🏷 v0.8.91 — Source key-topics extraction on ingest, opt-in (roadmap "later" idea)**
  - New **opt-in** setting (Settings → Sources, **default OFF**): when enabled, adding a source also runs a built-in **"Key Topics"** transformation on ingest; the result is **parsed into the source's `topics` field**, so the source card's previously-empty **topic tags finally populate**. Mirrors the v0.8.88 auto-summary pattern: the Key Topics transformation is seeded lazily (idempotent get-or-create) and is editable in Settings → Transformations; the same ingest hook appends it; a new `parse_topics()` helper turns the LLM's bulleted output into a clean, de-duped, capped (≤8) list. Best-effort throughout (never fails ingest; topics population is non-fatal). New `ContentSettings.auto_extract_topics_on_ingest`, `get_or_create_key_topics_transformation()` + `parse_topics()`, the post-ingest topics write in `process_source_command`, and a Settings toggle. (`content_settings.py`, `transformation.py`, `source_commands.py`, `api/models.py`, `api/routers/settings.py`, `SettingsForm.tsx`, `lib/types/api.ts`; 5 new backend tests + meta-test, tsc clean, `npm run build` + 71 frontend tests pass.) **⚠️ Actual extraction needs an in-app test** (toggle ON + a configured LLM).

- **♿ v0.8.90 — Accessibility sweep: ARIA labels on icon-only buttons (roadmap Batch 4)**
  - Added accessible names (`aria-label`) to icon-only buttons that lacked them, so screen readers announce them: the API-key **clear** buttons, the source **delete** button (`/sources`), the chat **send** button, and the episode/speaker profile **action menus**. Audited the rest of the `size="icon"` buttons — the dialog close buttons, source-detail menu, collapse buttons, etc. already carry labels — and confirmed dialog **focus-trap / restore / Escape** are handled natively by Radix. (`api-keys/page.tsx`, `sources/page.tsx`, `ChatPanel.tsx`, `EpisodeProfilesPanel.tsx`, `SpeakerProfilesPanel.tsx`; labels use inline `defaultValue`; tsc clean, build + tests pass.)

- **🔎 v0.8.89 — Chat context indicator: "Using X of Y sources" (roadmap Batch 4)**
  - The notebook chat's context bar now shows **"Using X of Y sources"** (X = sources set to insights/full, Y = total in the notebook) with a **popover listing the in-context sources by name** — making the existing per-source filtering (the off/insights/full toggles, already wired through `buildContext`) **visible and discoverable**. Per-source filtering itself already worked; this closes the transparency gap. (`ChatColumn.tsx` computes the total + in-context titles, `ChatPanel.tsx` threads them, `ContextIndicator.tsx` renders the summary + popover; source-chat callers without totals keep the prior badges-only display. tsc clean, build + 50 tests pass.)

- **📝 v0.8.88 — Source auto-summary on ingest, opt-in (roadmap Batch 4)**
  - New **opt-in** setting (Settings → Sources, **default OFF**): when enabled, adding a source also runs a built-in **"Summary"** transformation on ingest, producing a Summary insight and a **one-line preview on the source card**. The Summary transformation is seeded lazily on first use (idempotent get-or-create) and is then editable in Settings → Transformations. Reuses the existing ingest→transform→insight pipeline (no new graph); best-effort (a summary failure never fails ingest); default OFF respects local-LLM cost (one extra call per source). New `ContentSettings.auto_summarize_on_ingest`, `get_or_create_summarize_transformation()`, a `summary_preview` field on the `/sources` list response (first ~140 chars of the Summary insight), the SourceCard preview line, and a Settings toggle. (`content_settings.py`, `transformation.py`, `source_commands.py`, `api/routers/sources.py`, `api/models.py`, `api/routers/settings.py`, `SourceCard.tsx`, `SettingsForm.tsx`; 4 new backend tests + meta-test, tsc clean, `npm run build` + 83 frontend tests pass.) **⚠️ Actual summary generation needs an in-app test** (toggle ON + a configured LLM).

- **🧭 v0.8.87 — Discover sources: guarded web search (roadmap Batch 3, item 4 — Batch 3 COMPLETE)**
  - A new **"Discover sources"** entry in the notebook Sources panel opens a dialog where you type a topic, see candidate web results (title / URL / snippet), and **pick which to add** as link sources (reusing the existing extraction + vectorize pipeline). Backend `POST /api/notebooks/{id}/discover-sources` is **search-only** over the existing env-keyed `web_search` tool (Serper / Tavily / self-hosted SearXNG). **Privacy-preserving by construction:** opt-in by provider-key presence — with no key it returns `enabled:false` and the dialog shows a setup hint; nothing reaches the network until you run a search with a provider configured, and the dialog names the active provider first. Best-effort (provider errors → empty, never a 500); no new dependencies. (`api/routers/notebooks.py`, `api/models.py`, `DiscoverSourcesDialog.tsx`, `SourcesColumn.tsx`, `notebooksApi.discoverSources`; 4 new backend tests + meta-test pass, tsc clean, `npm run build` + locale tests pass.) **⚠️ Live results need a provider key** (in-app/user test).
  - **Batch 3 of the improvement roadmap is now complete:** mind map · resizable workspace · podcast depth · discover sources (+ the DB repair-restart fix).

- **🎙 v0.8.86 — Podcast depth: per-episode length control (roadmap Batch 3, item 3)**
  - The **Generate Podcast** dialog now has a **Length** selector — *Profile default / Short (~4–6 min) / Medium (~8–10 min) / Long (~15–20 min)* — that overrides the episode profile's segment count **for that one episode** (short→3, medium→5, long→8 segments; "Profile default" keeps the profile's `num_segments`, so existing behavior is unchanged). Threaded request → `PodcastService.submit_generation_job` → command → `build_state_and_config` (`segments_for_length`). The **focus** half of "podcast depth" already exists: the dialog's *Instructions* field is appended to the briefing (`briefing_suffix`) and steers what the episode emphasizes. (`commands/podcast_staged.py`, `commands/podcast_commands.py`, `api/podcast_service.py`, `api/routers/podcasts.py`, `GeneratePodcastDialog.tsx`, `lib/types/podcasts.ts`; 3 new backend tests + meta-test pass, tsc clean, `npm run build` + locale tests pass.) **⚠️ Generating with a chosen length needs an in-app test** (requires TTS credentials).

- **↔️ v0.8.85 — Resizable 3-pane notebook workspace (roadmap Batch 3, item 2)**
  - The desktop notebook layout (**sources │ notes │ chat**) is now **resizable via draggable handles**, and the widths are **remembered** across sessions (`autoSaveId` → localStorage). Built on the shadcn `resizable` primitive (`react-resizable-panels`). The existing per-column collapse buttons still work and stay in sync with the panels (and dragging a pane shut also collapses it); chat is always present. Mobile's tabbed layout is untouched. (`components/ui/resizable.tsx`, `notebooks/[id]/page.tsx`; tsc clean, `npm run build` passes, notebook tests pass.) **Note:** pinned `react-resizable-panels@^2` — the latest v4 is a breaking API rewrite incompatible with the shadcn component. ⚠️ drag + width-persistence need an in-app check.

- **🐛 v0.8.84 — Fix: "Repair & restart" button now actually relaunches**
  - The v0.8.81 one-click "Repair & restart" closed the window but the app **never reopened** — verified in-app: `relaunch()` fired and spawned its helper, but `window.destroy()` doesn't make the launcher process exit, so the old helper's "wait for the pid to die, then reopen" loop waited forever (the user was left with a vanished window). Fix: the detached helper now **actively terminates** the process — `SIGTERM` (clean child teardown) → wait ~6s → `SIGKILL` backstop → reopen. Confirmed the rest of the chain works once the process exits: helper reopens → boot auto-repair rebuilds a clean DB (backup-first) → flag clears. (`desktop/window.py` `_OnpJsApi.relaunch`; window.py parses, 60 window tests pass. ⚠️ relaunch still exercised in-app only.)

- **🕸 v0.8.83 — Mind map / knowledge graph (roadmap Batch 3, item 1)**
  - **Backend:** `GET /api/notebooks/{id}/graph` returns the notebook as a hub node with its **sources and notes** as connected nodes, grounded in the existing `reference` (source→notebook) and `artifact` (note→notebook) edges — **no schema change**. Node ids are record ids for deep-linking; labels trimmed to ≤80 chars. (`Notebook.get_graph()`, `NotebookGraphResponse`, router; HTTPException-reraise + NotFoundError→404 compliant; 3 tests, meta-test green.)
  - **Frontend:** a **"Mind map"** button on the notebook header opens a near-full-screen dialog rendering the graph with **React Flow** (`@xyflow/react`) in a radial layout (notebook hub centered, sources/notes around it; color-coded). Clicking a **source** node opens the existing source viewer. Loaded via `next/dynamic` `ssr:false` (React Flow needs the DOM); fetched only while the dialog is open (`useNotebookGraph`); loading/empty/error states. (`MindMap.tsx`, `MindMapButton.tsx`, `notebooksApi.getGraph`, `use-notebook-graph.ts`, `NotebookHeader`; tsc clean, **`npm run build` passes** with React Flow, 17 notebook tests pass.) **⚠️ The graph render needs an in-app check** (open a notebook with sources/notes → Mind map).

- **📄 v0.8.82 — Inline PDF rendering (roadmap Batch 2, closes the source-viewer epic)**
  - Uploaded **PDF sources now render inline** in the source view (NotebookLM parity), above the extracted text, via `react-pdf`. The pdfjs worker is **bundled locally** (`frontend/public/pdf.worker.min.mjs`, copied from the `pdfjs-dist` version react-pdf ships) — **no CDN**, keeping the app fully offline/local-first. Loaded with `next/dynamic` `ssr:false` (react-pdf needs the DOM + worker), so it never server-renders. Robust fallback: on any fetch/parse failure the viewer reports up (`onUnavailable`) and the **extracted-text + cited-passage callout stays as the default view**; the object URL is revoked on unmount (no blob leak) and the fetch is cancel-guarded. Gated on `.pdf` + the file being present. (`PdfSourceViewer.tsx`, `SourceDetailContent.tsx`; tsc clean, **`npm run build` passes** with react-pdf, 24 source tests pass.) **⚠️ The actual PDF render + offline worker can't be verified headlessly — needs an in-app test** (open a PDF source; it should render inline, and gracefully fall back to text if not).

- **🛠 v0.8.81 — One-click "Repair & restart" for the DB-corruption banner (roadmap Batch 2)**
  - The "Database needs repair" banner previously told the user to **manually ⌘Q + reopen** so the boot-time auto-repair (backup-first, already shipped) could run. It now has a one-click **"Repair & restart"** button that relaunches the app for you. Mechanism: a new pywebview `js_api` bridge (`window.ONP.relaunch` → `_OnpJsApi.relaunch` in `desktop/window.py`) spawns a **detached helper that waits for this process to fully exit, then reopens the .app bundle**, and cleanly closes the window — so there's no port/singleton race and it reopens exactly once (no relaunch loop). In a plain browser (dev) the bridge returns false and the banner falls back to a reload. (`desktop/window.py`, `DbRepairBanner.tsx`; window.py parses, 72 desktop tests pass, tsc clean.) **⚠️ The relaunch behavior needs an in-app test** — js_api + self-relaunch can't be exercised headlessly (covered by the batch build + a manual check).

- **🎨 v0.8.80 — First-run "Explore a sample notebook" (roadmap Batch 2)**
  - When a brand-new user has **no notebooks**, the empty state now offers a one-click **"Explore a sample notebook"** that seeds an example notebook + a bundled "Getting started" text source, then opens it — so first use shows value instead of a blank list (NotebookLM/onboarding parity). Content is bundled (no network — local-first), and once processed the v0.8.74 starter-question chips appear automatically. Reuses the existing create-notebook / create-source mutations; best-effort (notebook still opens if the source add fails). Shows only when genuinely empty (not a filtered-empty search). (`lib/hooks/use-sample-notebook.ts`, `NotebookList` `extraAction` slot, `notebooks/page.tsx`; tsc clean, 12 notebook tests pass; label uses inline `defaultValue`.)

- **✨ v0.8.79 — Citation jump-to-highlight: wired end-to-end (roadmap Batch 2)**
  - Clicking a `[source:ID]` citation pill now shows a **"View source →"** action in its popover that opens the source reading view and **highlights the passage that claim is grounded in** (scrolled into view) — the NotebookLM trust mechanic, now functional in ONP. ChatPanel derives the **citing sentence** (the last sentence of the text preceding the marker) and passes it as the highlight query to a local `SourceDialog` → `SourceDetailContent`; the backend locator (v0.8.78) finds the matching passage. Kept separate from the global reference modal so it can carry the query without touching shared modal state; the hover-preview popover is unchanged. (`CitationPill.tsx`, `ChatPanel.tsx`, `SourceDialog.tsx`; tsc clean, 82 chat/source tests pass.) Inline PDF rendering still to come.

- **✨ v0.8.78 — Citation passage locator: backend (roadmap Batch 2, source viewer)**
  - Foundation for citation **jump-to-highlight**. ONP citations are bare record IDs with no passage offsets, so there's nothing to scroll to. Rather than change the citation format (which CitationPill + many tests depend on), an **on-demand locator** finds the cited passage: `open_notebook/utils/citation_offsets.py:locate_passage(text, query)` returns the char-offset range of the window in a source's `full_text` that best matches the citing sentence (deterministic token-containment over a sliding window; stopword-filtered; word-boundary-snapped; `None` when no decent match). Exposed as `POST /api/sources/{id}/locate-passage` (best-effort → `{"match": null}` when no text/match; HTTPException-reraise compliant). (`tests/test_citation_offsets.py` 5 tests; meta + offsets suites green; endpoint registered.)
  - **Viewer side:** `SourceDetailContent` takes a `highlightQuery` prop — when a source is opened from a citation, it calls `sourcesApi.locatePassage()` and renders the grounded passage as a highlighted **"Cited passage" callout** at the top (scrolled into view). This sidesteps the fragile char-offset-in-rendered-markdown problem by showing the located snippet explicitly + reliably. Citation-click wiring (open the viewer with the citing sentence) and inline PDF rendering follow. (tsc clean; SourceDetailContent test passes.)

- **🎨 v0.8.77 — Drag-drop files onto the sources panel (roadmap Batch 1)**
  - Dropping files onto a notebook's **Sources** panel now opens AddSourceDialog with the **upload tab preselected and the files prefilled** (NotebookLM/rivals parity). A dashed "Drop files to add as sources" overlay appears while dragging (file drags only; child-element drag-leave is guarded to avoid flicker). Files are prefilled via a `DataTransfer`-built `FileList` and **degrade gracefully** — if the prefill fails, the dialog still opens on the upload tab for a manual pick; dropped files are cleared on close so a later manual "Add source" isn't re-prefilled. (`SourcesColumn.tsx` drop zone, new `initialFiles` prop on `AddSourceDialog`; tsc clean, 24 sources/notebook component tests pass.) **⚠️ Needs an in-app file-drag test** to confirm the `DataTransfer` prefill lands — jsdom/vitest can't fully exercise it (covered by the Batch 1 verification build).

- **🎨 v0.8.76 — Citation hover-preview (roadmap Batch 1)**
  - Inline citation pills (`CitationPill`) already showed the cited document's title + snippet on **click**; they now also open on **hover** (and keyboard focus), so users can skim the grounding without leaving the answer — closer to NotebookLM's at-a-glance citation UX. Implemented by controlling the existing Popover's open state with small open (280ms) / close (140ms) delays and a grace window (moving the cursor pill→popover keeps it open); hover-open does not steal focus mid-read. **Click still toggles via Radix's own `onOpenChange`**, so the proven click path is unchanged if hover timing ever misbehaves. (`CitationPill.tsx`; tsc clean, all 58 chat tests pass incl. CitationPill.test.tsx.)

- **🎨 v0.8.75 — Actionable empty state for sources (roadmap Batch 1)**
  - The notebook's empty sources list now shows a clear **"Add source" CTA button** (opens the existing AddSourceDialog) instead of a dead-end "no sources yet" message — lowers friction to the first source. (`SourcesColumn.tsx`, reusing `EmptyState`'s `action` slot; tsc clean.) Drag-drop-anywhere (the other half of this roadmap item) is tracked separately — it needs a real file-drag test in the running app to verify the `DataTransfer` prefill.

- **✨ v0.8.74 — Suggested starter questions (roadmap Batch 1)**
  - **Backend:** new `GET /api/notebooks/{id}/suggested-questions?limit=N` generates a few concise, **corpus-grounded** starter questions from the notebook's source titles + topics via one bounded (30s) LLM call — so chat needn't open to a blank box (NotebookLM parity). Strictly **best-effort**: no sources / no model / LLM error / unparseable output all degrade to `{"questions": []}` and can never block the notebook UI; NotFound/InvalidInput still surface as 404/400. (`api/routers/notebooks.py`; `tests/test_suggested_questions.py`, 4 tests.)
  - **Frontend:** the notebook chat's empty state now shows clickable starter-question **chips** (fetched only when sources exist and no messages yet; `retry:false`, 5-min cache). Clicking a chip sends it. Added as optional `suggestedQuestions` / `onSuggestedQuestionClick` props on the shared `ChatPanel` (source chat omits them → no change there). Header label uses an inline `defaultValue` (no locale-parity keys). (`ChatPanel.tsx`, `ChatColumn.tsx`, `lib/api/notebooks.ts`; ChatColumn test wrapped in a QueryClientProvider.)

- **✨ v0.8.74 — Chat grounding guardrail (roadmap Batch 1)**
  - Added an explicit **GROUNDING & HONESTY** section to the chat (`prompts/chat/system.jinja`) and source-chat (`prompts/source_chat/system.jinja`) system prompts: answer from the provided CONTEXT/sources (or this turn's tool results); when the sources don't cover something, **say so plainly** rather than inventing facts or citations; never attach a document-ID citation to an unsupported claim; and prefer "I'm not certain" to a confident guess. Deliberately preserves the `web_search`/`mcp_*` tool path and general-knowledge fallback (with a "not from your sources" caveat). Reduces hallucination and mirrors NotebookLM's strict source-grounding. First item of the design/functionality improvement roadmap (`~/BrainPulseKnowledge/.../Projects/OpenNotebookPlus/Roadmap`).

- **🐛 v0.8.73 — THE fix for the startup "This page couldn't load" reload screen: persist the webview store**
  - **Root cause (finally):** pywebview's `webview.start()` defaults to **`private_mode=True`** — an *ephemeral* `WKWebsiteDataStore` that macOS wipes on **every app close**. The desktop called it with no args, so the `wizard_completed` cookie (set with a 1-year expiry) never survived a restart. **Every** launch therefore redirected `/` → `/setup-wizard`, and the wizard's client-side auto-skip (`router.replace('/')`) raced the cold boot straight into WebKit's native **"This page couldn't load"** error page — the reload screen hit on every single launch. The same ephemeral wipe is also why the launch intro replayed.
  - **Fix:** `webview.start(private_mode=False, storage_path=~/.open-notebook-plus/webview_data)` so the cookie/localStorage store **persists across launches AND rebuilds** (the stable signing identity keeps the same data container). Verified across two consecutive launches: the Setup Wizard shows **once** then never, the intro shows **once** then never, and the app boots straight to the dashboard — no reload screen, no manual reload.
  - **Diagnosis note:** the v0.8.72 handoff retry-budget widening (kept as a low-risk defensive improvement) was based on the wrong theory that `load_url` was failing. A live pywebview-5.4 test proved `load_url` works fine, and instrumenting the frozen app showed the handoff *succeeding* on attempt 0 onto `/setup-wizard` — redirecting the investigation to the ephemeral cookie store + wizard auto-skip race, the true cause.

- **✨ v0.8.72 — Premium theme pack, theme-aware aurora visuals & cold-start fix**
  - **🎨 8 new premium themes** (picker now offers 17): **Midnight Aurora** (the signature dark theme — its indigo→violet palette matches the launch splash + Aurora Reveal intro), **Tokyo Night**, **Catppuccin Mocha**, **Catppuccin Latte**, **Rosé Pine**, **Rosé Pine Dawn**, **Gruvbox Dark**, and **One Dark**. Every theme's body text clears WCAG **AAA** (7:1) and muted text **AA** (4.5:1) — enforced by the parametrized tests in `desktop/tests/test_window.py`, so a palette tweak can't silently regress legibility.
  - **🎨 Theme-aware aurora visuals:** the in-app aurora-glass system (`components/onp/tokens.css`) now derives its gradient/glow hues from the **live theme's `--primary` / `--accent`** (via `color-mix`) instead of fixed brand colors — so every theme gets a cohesive hero header and glow in its own palette. Midnight Aurora reproduces the exact indigo→violet brand look. The theme picker's swatches now render a background ring + accent dot so the (now many) dark themes are distinguishable at a glance.
  - **🛠 Lockstep + drift guard:** the theme palette (`desktop/window.py:_THEMES`), API allowlist (`api/routers/onp.py:_VALID_THEMES`), and picker (`ThemeSwitcher:ONP_THEMES`) were updated together; a new test asserts the palette and API allowlist can't drift out of sync.
  - **🐛 BUG FIX — startup "This page couldn't load" reload screen (the real one):** the splash→app handoff (`desktop/window.py:_start_handoff_controller`) gave up after **10 attempts (~2 min)**. On a slow ad-hoc cold boot WKWebView's `load_url` keeps failing for minutes even though the frontend is provably serving (an httpx probe AND a manual webview Reload both succeed — a known WebKit "probe passes, real navigation races/fails" quirk, made worse by the first-launch `/`→`/setup-wizard` 307 when the `wizard_completed` cookie was wiped by the ad-hoc rebuild). When the budget ran out it rested on WebKit's native **"This page couldn't load / Reload"** page, and only a manual Reload recovered it. The retry budget is now **40 attempts (~4 min)** with a shorter per-attempt timeout (so a failed attempt's error page flashes briefly before the splash is restored, never resting on it) — it keeps re-issuing the navigation until WKWebView finally loads the app, which a manual reload proves always eventually works. A stable code-signing identity keeps boots ~30s where the first attempt just succeeds; this covers the ad-hoc slow path. (`desktop/tests/test_v0_8_68_launch_race.py` +2 tests.)
  - **🐛 BUG FIX — cold-start `/api/config` overlay:** separately, `ConnectionGuard`'s startup check retried for only ~6s, so if the API lost the cold-boot race it could latch the "Unable to connect — Retry" overlay. It now retries against a **120s budget** showing a quiet "Connecting…", with a **background poll** that self-heals the overlay if it ever shows. (Supersedes the ~6s backoff added in v0.8.71.)

- **✨ v0.8.71 — Aurora visual system: launch intro, hero glow-up, smoother streaming**
  - **🎨 "Aurora Reveal" launch intro:** a premium, skippable, once-per-user opening animation (`components/intro/IntroReveal.tsx`, mounted in `app/layout.tsx` inside `ConnectionGuard`). Animated indigo→violet→teal aurora (matching the desktop splash palette) with a staggered logo/wordmark/tagline reveal via Framer Motion. Skippable with a Skip button or `Esc`, auto-dismisses, and is shown only once (localStorage `onp_intro_seen`); Settings → "Replay" re-triggers it without a reload. Fully `prefers-reduced-motion` aware (collapses to a quick fade). Intro strings use inline `defaultValue` so they add no locale-parity keys.
  - **🎨 Aurora-glass design system:** new tokens + utilities in `components/onp/tokens.css` — `.onp-glass` (frosted translucent panels), `.onp-aurora-bg` (drifting radial-gradient field), `.onp-aurora-text` (gradient-clipped wordmark), and `--onp-glow-accent`. All GPU-composited (transform/opacity/filter) and theme-adaptive via `color-mix` on the live shadcn variables.
  - **🎨 Hero surfaces glow-up:** the dashboard gets an aurora hero header, frosted-glass quick-action cards with hover-lift + accent glow (Studio), and a staggered entrance; the sidebar's active item is now a Framer Motion `layoutId` accent pill that smoothly slides between routes.
  - **⚡ Smoother chat streaming:** `useNotebookChat` now batches streamed tokens with `requestAnimationFrame` instead of a `setMessages()` per token — at 50–150 tokens/sec the old path re-rendered the whole message list 50–100×/sec (visibly janky in WKWebView); rAF coalesces to ≤1 render per paint frame.
  - **⚡ List skeletons:** the notebooks list now renders skeleton cards instead of a centered spinner (layout settles instantly, reads faster).
  - **🐛 BUG FIX — "reload error" on cold startup:** `ConnectionGuard` made a single `/api/config` check on mount and, if it lost the cold-boot race (the Next proxy briefly returns `ECONNREFUSED` before the dynamic API port is listening, or the DB momentarily reports `offline` during migrations), latched the full-screen "Unable to connect — Retry" overlay even though the backend came up a beat later. It now polls with a short backoff (~up to 6s) before surfacing the error, and shows a quiet themed "Connecting…" during the check instead of a blank screen. (Pre-existing race, surfaced more often by the slower first boot of a freshly-signed build.)
  - **🛠 Dependency:** added `framer-motion` (React 19 compatible) for GPU-composited, reduced-motion-aware animation.
  - **⚡ Streaming everywhere:** extended the rAF token-batching to `useSourceChat` (source chat) and `use-ask` (the Ask/synthesis stream), so all three streaming surfaces coalesce to ≤1 render per paint frame instead of one render per token.
  - **🎨 Chat bubbles:** softer rounded-2xl bubbles with depth — a subtle gradient on the user's messages, a bordered card surface for the assistant (no per-bubble backdrop-blur, to stay cheap in long threads) — and a "typing" dot-wave indicator in place of the spinner.
  - **📝 Deferred (follow-up):** virtualizing **long chat threads** specifically (the sources list already virtualizes at ≥50 items via `VirtualizedListAuto`; chat threads rarely reach the count where it pays off, and restructuring the tuned `isNearBottom` stick-to-bottom + streaming-growth scroll is higher-risk and needs live validation), plus prefetch-on-hover and row memoization.

- **✨ v0.8.70 — In-app update notifier + correctness fixes**
  - **✨ Update notifier:** new `GET /api/updates/check`, `POST /api/updates/skip`, and `PUT /api/updates/settings` endpoints (backed by `api/updates_service.py`) check the GitHub Releases API for a newer version than the running build and surface a dismissible "Update available" banner in the app shell, plus a Settings → Updates card (current version, automatic-check toggle, "Check now"). Notifier only — it links to the release page and never downloads or installs anything (the desktop app ships unsigned today). Privacy-gated: the GitHub request only fires when checking is enabled (default on, user-togglable), with a one-line disclosure; results are cached for 6h and every failure mode degrades to "no update".
  - **🔒 BUG FIX — API-key env leak:** `connection_tester.py` set `os.environ[PROVIDER_API_KEY]` to validate a credential and never restored it, so clicking "Test connection" in Settings left that key set for the process lifetime, shadowing all later env-fallback provisioning of that provider until restart. Now wrapped in a `try/finally` that restores (or removes) the prior value on every path.
  - **🐛 BUG FIX — silent Studio failures:** the Evidence Studio mutation hooks (`use-studio.ts`) had no `onError`, and the axios interceptor only toasts 5xx, so 4xx errors (e.g. `sources_not_ready`, validation) made the artifact buttons silently do nothing and threw unhandled promise rejections. All studio mutations now show an error toast with the backend's descriptive message.
  - **🐛 BUG FIX — command status 404:** `CommandService.get_command_status` re-raised `surreal_commands`' "not found" `ValueError` as a generic HTTP 500 for unknown/expired job ids (the `None`→404 branch was dead because the upstream call raises rather than returning `None`). Polling a stale `job_id` now correctly yields 404.
  - **🐛 BUG FIX — podcast retry race:** the retry handler's terminal-state check and the destructive delete+resubmit were non-atomic, so a double-click or two concurrent retries could both pass the check and both delete+resubmit (duplicate/destroyed episodes). Retries of the same episode are now serialized with a per-episode in-process lock.
  - **🛠 Tests:** added `tests/test_updates_service.py` (24 cases: version parsing, availability math, cache/disabled gating, skip, corrupt-state tolerance), `tests/test_v0_8_70_bugfixes.py` (env restore + command 404 mapping), and `frontend/.../UpdateBanner.test.tsx` (banner visibility + skip).
  - **🌐 Locale completion:** the four locales `ca-ES`/`de-DE`/`pl-PL`/`tr-TR` were each missing ~290 keys across many namespaces (not just `studio.*`), so the frontend locale-parity test was already red. Completed all four to full key parity with `en-US` and translated ~277–279 of the previously-English placeholders per language (Catalan/German/Polish/Turkish), preserving every `{{interpolation}}` token and leaving genuine technical/product terms in English. Locale test suite now 14/14 (was 9/14). All new update-notifier UI uses inline `defaultValue` strings (the existing banner pattern). The ~24–52 remaining English strings per locale are mostly intentional (product/technical terms) and pending optional native review.
  - **🐛 BUG FIX — splash hung forever on Next 16 (window never opened):** the splash→app handoff's Python readiness probe (`desktop/window.py:_frontend_server_ready`) rejected any page whose body contained the substring `next-error-h1`. Next.js 16 streams the global `notFound` boundary — including its `.next-error-h1` style block — into the RSC payload of **every** page, so the probe was permanently False: it never navigated the window to the app and the splash ("Taking a little longer than usual…") stayed up indefinitely. Now detects Next's actual not-found page by its `<title>` ("404: …", mirroring the JS sentinel) and requires the `__next_f` runtime marker, so real pages pass while warm-up 404s are still rejected. Regression test added.
  - **🐛 BUG FIX — slow first open / Setup Wizard on rebuilds:** running the app off the mounted DMG (read-only compressed image) made launch ~2× slower and the ad-hoc code-signing identity (which changes every rebuild) gave the app a fresh WebKit cookie store, so the lost `wizard_completed` cookie forced returning users back through the first-launch Setup Wizard. Fixes: (1) the macOS DMG now ships an `/Applications` drag target so users install to local SSD instead of running in place (post_build_mac.sh); (2) the Setup Wizard now auto-skips for returning users — when notebooks already exist and the backend is reachable (healthy OR degraded), it completes and routes to the dashboard instead of requiring a manual "Continue", so a lost cookie no longer strands returning users on the wizard. First-launch users (no notebooks) still get the guided wizard.
  - **🛠 Versioning:** the macOS bundle `CFBundleShortVersionString` no longer hardcodes `0.1.0` — it (and `CFBundleVersion`) now derive from `desktop/__init__.py` `__version__` at build time. Added a real drift guard pinning `__version__` to the latest released `## v` CHANGELOG header, and documented that `pyproject.toml`'s version is a separate track (the upstream/Docker image tag). Windows `VERSIONINFO` is left as a documented follow-up needing a Windows build host.

- **✨ v0.8.69 — Evidence Studio foundation + broader local model inventory**
  - **✨ Evidence Studio foundation:** added stable-on backend and frontend feature flags (`ONP_VISUAL_REFRESH` / `NEXT_PUBLIC_ONP_VISUAL_REFRESH`, `ONP_EVIDENCE_STUDIO` / `NEXT_PUBLIC_ONP_EVIDENCE_STUDIO`, `ONP_MODEL_FLEET` / `NEXT_PUBLIC_ONP_MODEL_FLEET`) plus an experimental-off research-runs flag (`ONP_RESEARCH_RUNS` / `NEXT_PUBLIC_ONP_RESEARCH_RUNS`) and a durable `studio_artifact` domain model/schema for reports, study guides, flashcards, quizzes, mind maps, slide decks, podcasts, and research runs.
  - **✨ Studio artifact API:** `/api/studio/artifacts` now exposes create/list/get/update/delete endpoints for durable notebook artifacts by default without changing the existing one-shot `/api/studio/generate` flow; `ONP_EVIDENCE_STUDIO=0` remains a kill switch.
  - **✨ Text artifact generation:** `POST /api/studio/artifacts/{id}/generate` now creates source-grounded markdown for the first text artifact types: Report, Study guide, Briefing, FAQ, Timeline, Flashcards, and Quiz. Outputs store the payload/citations/status on the artifact and failed generations are marked for retry instead of leaving records stuck pending.
  - **✨ Mind map artifacts:** Evidence Studio now generates source-grounded mind maps as nested Markdown outlines with relationship labels and citation markers, and exposes Mind map as a quick-create action from the notebook artifact rail.
  - **✨ Visual study artifacts:** Evidence Studio now generates source-grounded Slide deck outlines and Infographic briefs as Markdown artifacts, with speaker notes, visual sections, data callouts, and citation markers, and exposes both from the notebook artifact rail.
  - **✨ Podcast outline artifacts:** Evidence Studio now generates source-grounded Podcast outline artifacts as an Audio Overview bridge, with cold opens, host segments, key beats, listener takeaways, discussion questions, and citation markers.
  - **✨ Course Pack artifacts:** Evidence Studio now creates instructor-ready Course Pack artifacts from selected notebook sources, including links, PDFs/docs, and transcribed audio/video source text. Course Packs include audience, outcomes, prerequisites, source-readiness notes, module roadmaps, timed lesson blocks, exercises, facilitator notes, learner handouts, checks, final assessments, citations, and follow-up resources. The legacy `training_guide` artifact type remains accepted for older records, and the one-shot Studio upload path accepts common training media extensions such as `.mp3`, `.mp4`, `.m4a`, `.wav`, and `.mov` so extracted transcripts can feed course material.
  - **✨ Course Pack sidecar exports:** generated Course Packs now save instructor-guide Markdown, learner-handout Markdown, module-checklist JSON, and assessment Markdown sidecars alongside the normal artifact Markdown/JSON exports, giving training material a cleaner handoff path before LMS/SCORM packaging lands.
  - **✨ Course Pack LMS packages:** generated Course Packs now also save SCORM-style `.zip` packages with `imsmanifest.xml` launch metadata and xAPI `.zip` packages with `tincan.xml` plus statement templates, bundled with the instructor guide, learner handout, checklist, and assessment assets for LMS handoff.
  - **🎨 Course Pack viewer:** completed Course Pack and legacy Training guide artifacts now open in an interactive workspace with module navigation, a local completion checklist, learner/facilitator view toggles, hidden learner handout notes, saved export visibility, and citation inspection.
  - **✨ Research Run artifacts:** the experimental `ONP_RESEARCH_RUNS` / `NEXT_PUBLIC_ONP_RESEARCH_RUNS` flag now unlocks a source-grounded Research run quick action in the notebook artifact rail, backed by a dedicated multi-step investigation prompt with hypotheses, findings, gaps, follow-up questions, next actions, and citation markers.
  - **✨ Research Run metadata:** generated Research run artifacts now persist parsed stage metadata (`research_stages`) alongside markdown content, so the API response and saved JSON export carry structured objective/hypothesis/finding/follow-up sections for downstream BrainPulseKnowledge imports.
  - **✨ Durable artifact exports:** generated Evidence Studio artifacts now write Markdown plus metadata JSON sidecars to `~/BrainPulseKnowledge/open-notebook-plus-imports/evidence-studio` by default, or `OPEN_NOTEBOOK_ARTIFACT_EXPORT_DIR` when configured, and persist those paths on the artifact for later import/export workflows.
  - **✨ Artifact revision snapshots:** regenerating a completed artifact now preserves the previous output, citations, export paths, prompt, model, provider, and source scope as a linked `revision_of_id` artifact before replacing the current output.
  - **✨ Artifact revision history:** linked revision snapshots are hidden from the notebook's primary artifact list, exposed through `/api/studio/artifacts/{id}/revisions`, and shown in the artifact viewer so older generated outputs can be reopened without cluttering the main rail.
  - **🎨 Notebook artifact rail:** notebook pages now show an ONP shadow-layer artifact rail by default, backed by the durable Studio artifact API. The rail summarizes stored artifacts, status, and type, can scope generation to selected notebook sources, shows source readiness in the selector, blocks generation from failed/processing/not-embedded source scopes, and can create/generate the first text artifact set including NotebookLM-style Flashcards, Quiz, Mind map, Slide deck, Infographic, and Podcast outline; `NEXT_PUBLIC_ONP_EVIDENCE_STUDIO=0` hides it when needed.
  - **🎨 Source health visuals:** added `SourceHealthPill` as a reusable ONP shadow component for Ready, Processing, Queued, Failed, and Not embedded source states, and reused it in the artifact source selector.
  - **🎨 Extraction-aware artifact readiness:** Evidence Studio source health now marks completed sources with no extracted text as blocked for artifact generation and completed sources with low extracted text as warning-state inputs, so reports/study guides are not generated from empty source bodies.
  - **🎨 Citation coverage visuals:** added `CitationCoverageBadge` so artifact cards and citation panels show whether an output has stored evidence before the user opens or exports it.
  - **🎨 Citation evidence drawer:** artifact citations now expose a focused evidence panel with source title, source ID, stored quote preview, optional location, close control, and source-record jump, while keeping the compact citation list visible.
  - **🎨 Citation marker grounding:** Evidence Studio artifact prompts now label source context with stable markers like `[S1]`, ask generated outputs to cite those markers inline, persist the markers on citation records/exports, and show them in the evidence drawer when no page or section location is available.
  - **🎨 Artifact viewer/export:** completed artifacts can be opened from the notebook rail, rendered as Markdown, inspected with stored citation/source IDs plus source-text previews, jumped back to the cited source record, regenerated/retried in place, deleted after confirmation, and downloaded as Markdown or JSON files without leaving the notebook workspace.
  - **🎨 Research Run viewer:** completed Research run artifacts now open in a staged investigation workspace that prefers persisted `research_stages` metadata from generated artifacts and JSON exports, labels whether the stage view came from structured metadata or parsed markdown, and falls back to markdown section parsing only for older artifacts.
  - **🎨 Saved export visibility:** artifacts with persisted Markdown/JSON sidecars now show their saved paths in the viewer, making the `~/BrainPulseKnowledge/open-notebook-plus-imports/evidence-studio` handoff visible from inside the notebook workspace.
  - **🎨 Evidence Studio rail polish:** the notebook rail now reads as a research workspace instead of a narrow toolbar, with a raised header, artifact/citation status counters, steadier artifact card dimensions, wrapping create controls, and a calmer empty state.
  - **🎨 Artifact type icons:** saved Infographic and Podcast outline artifacts now keep their distinct visual/audio icons in the artifact rail instead of falling back to the generic report icon.
  - **🐛 BUG FIX:** Evidence Studio artifact buttons now stay disabled until notebook sources have loaded and at least one ready source exists, avoiding an empty-notebook generation path that could only fail downstream.
  - **🐛 BUG FIX:** Evidence Studio now treats blank model responses as failed generations instead of saving empty completed artifacts or exporting empty Markdown files.
  - **🐛 BUG FIX:** Research run artifacts with older flat markdown output now fall back to the regular markdown viewer instead of opening an empty staged workspace when no `research_stages` metadata or stage headings are available.
  - **🐛 BUG FIX:** Evidence Studio generation now returns a clear 404 when a selected source was deleted before generation, instead of logging an expected lookup failure as a generic 502.
  - **🐛 BUG FIX:** Evidence Studio generation now also returns a clear 404 when an artifact falls back to all notebook sources after its notebook has been deleted, and skips model provisioning for that stale record.
  - **🎨 Study artifact viewers:** Flashcards now open as a reveal/advance review deck and quizzes open as an answerable runner with correctness, score, explanations, and source notes, while malformed model output still falls back to markdown rendering.
  - **🐛 BUG FIX:** Settings → Local Models now scans recursively through HuggingFace-style repo folders under the configured model directory. The prior one-level scan missed real installed models such as `/Users/Antman/Desktop/AI_Models/GGUF/<repo>/<model>.gguf`, even though launcher auto-registration already found them recursively.
  - **🐛 BUG FIX:** Recursive inventory skips `mmproj` auxiliary projector GGUFs so vision companion files are not offered as standalone hot-swap chat models.
  - **✨ Transformers inventory:** Settings → Local Models now also surfaces complete HuggingFace-style repos under `AI_Models/Transformers` as `Transformers` runtime assets with architecture, context, parameter, and size metadata, while role routing keeps them out of runnable recommendations until a provider is configured.
  - **✨ Local runtime capability contract:** `/api/local-models/inventory` and role-route model payloads now include `runnable`, `activation_supported`, `runtime_status`, `runtime_note`, `setup_href`, and `setup_label` fields so the UI can distinguish runnable providers from inventory-only assets and show runtime setup actions without hardcoding runtime names.
  - **🎨 Model fleet summary:** Settings → Local Models now shows scan-friendly fleet readiness blocks, total storage footprint, native launcher provider/default-model status, local endpoint connection checks, MLX set-launch-default actions, readiness filter tabs, metadata search, sort controls, per-card model path and launcher-reference copy actions, a clear-filters empty state, and per-runtime totals for installed, runnable, inventory-only, and per-runtime assets so large local libraries are readable before opening individual model cards.
  - **⚡ Local runtime health probes:** `/api/local-models/health` now probes registered local runtimes with bounded concurrency while preserving response order, so one slow or dead Ollama/LM Studio/llama.cpp/MLX-compatible endpoint no longer serializes every other connection check.
  - **🎨 Inventory-only model clarity:** Transformers rows now show an `Inventory only` note with a backend-provided Launcher preferences link, and the Local benchmark / Recommended roles panels stay hidden when the installed fleet has no runnable GGUF or MLX provider, avoiding no-op benchmark controls for assets that still need a runtime.
  - **🐛 BUG FIX:** Settings → Local Models now only shows the GGUF hot-swap action on GGUF or legacy inventory rows, avoiding a misleading Set Active button on MLX and Transformers assets that the GGUF-only endpoint would reject.
  - **🐛 BUG FIX:** The frontend locale unused-key test now ignores generated build directories and tolerates disappearing generated files, preventing false failures when a production build and test run overlap.
  - **🐛 BUG FIX:** Source-list requests for a notebook deleted moments earlier now return 404 instead of surfacing as a generic 500, avoiding a misleading server-error toast during notebook delete cleanup.
  - **🐛 BUG FIX:** Source creation now normalizes the legacy single `notebook_id` field with the newer multi-notebook `notebooks` list before validation, notebook linking, and queued processing. Quick uploads can no longer create orphaned sources that are processed but missing from the target notebook.
  - **🐛 BUG FIX:** Multipart source creation now merges duplicate legacy/new notebook form fields before `SourceCreate` validation, matching the frontend quick-upload payload that sends both `notebook_id` and `notebooks`. The parser also rejects non-array `notebooks` and `transformations` JSON with clear 400 responses instead of leaking validation errors.
  - **🐛 BUG FIX:** The frontend `sourcesApi.create` helper now defaults omitted `embed` to `true`, matching the backend source-create default and the Add Source wizard expectation that new imports become searchable unless the caller explicitly chooses `embed=false`.
  - **🐛 BUG FIX:** The frontend `sourcesApi.create` helper now also defaults omitted `async_processing` to `true`, matching the Add Source wizard and upload helper so browser-created sources queue processing by default while explicit `async_processing=false` remains available for legacy sync callers.
  - **🐛 BUG FIX:** Backend source creation now also defaults omitted `async_processing` to `true` across multipart and `SourceCreate` callers, matching the UI's queued-processing behavior and avoiding accidental long synchronous imports for direct API users. `embed` now defaults to `true` on the model as well as the form parser.
  - **🐛 BUG FIX:** Async source-create responses now include the persisted link/upload asset immediately while processing is queued, so direct API clients and future optimistic UI paths can classify the source type without waiting for the processed record refresh.
  - **🐛 BUG FIX:** Source-create toasts now use the same effective queued-processing default as the request payload, so callers that omit `async_processing` see the background-processing message instead of a misleading immediate-success message.
  - **⚡ Cache hygiene:** Source list, source detail, and source status React Query keys are now distinct families. List-only invalidation no longer accidentally sweeps open source detail caches while still avoiding broad status-poll refetches.
  - **🎨 Source progress clarity:** queued/running source cards now render progress for `0%`, clamp invalid values, derive progress from common command-status shapes such as `processed/total`, and no longer leak a stray `0` into the card when processing has just started.
  - **🐛 BUG FIX:** Source status now forwards the underlying command `progress` payload inside `processing_info`, so source cards can display real queued/running progress instead of only status/result metadata.
  - **🐛 BUG FIX:** Source cards now fall back to source-list `processing_info` progress before status polling returns data, so queued/running imports can show their first progress value immediately from list responses.
  - **🐛 BUG FIX:** Source-list responses now preserve fetched command `progress`, `result`, and status metadata inside `processing_info`, giving list cards and dashboards the same processing context that detail/status endpoints expose.
  - **🐛 BUG FIX:** Source creation now returns a clear 404 when a target notebook disappears during import validation, instead of converting the domain `NotFoundError` into a generic 500.
  - **🐛 BUG FIX:** The all-sources page now refreshes after creating a source from its Add Source dialog. Previously the source could queue successfully while the table stayed stale because the page used local list state instead of the React Query caches invalidated by the dialog mutation.
  - **🐛 BUG FIX:** The inline Retry button on failed notebook source cards now stops click propagation. Previously retrying a failed source also opened the source detail modal, interrupting the recovery flow.
  - **🐛 BUG FIX:** Source retry now preflights uploaded-file assets before queueing worker processing. Missing originals return a clear retry error, and file paths outside the configured uploads directory are blocked instead of being handed to the extraction worker.
  - **🎨 Source attention state:** source-list responses now populate `file_available` for uploaded-file assets, and SourceCard shows a file-unavailable warning badge when the original upload is missing or no longer accessible.
  - **🎨 Source retry clarity:** SourceCard now disables failed-source retry actions when the source is an upload whose original file is unavailable, keeping the UI aligned with the retry preflight API.
  - **🐛 BUG FIX:** Source retry responses now include `extracted_char_count` and `extraction_quality: "pending"` when a retry is queued, keeping the API response aligned with source list/detail extraction-quality contracts.
  - **🎨 Extraction quality visibility:** source list and detail responses now include `extracted_char_count` plus an `extraction_quality` classification (`pending`, `no_text`, `low_text`, `ok`), and source cards plus detail views show localized no-text and low-extracted-text warnings with recovery guidance and an inline Retry Processing action when completed PDFs/media imports produce missing or suspiciously thin text.
  - **🐛 BUG FIX:** Frontend mutations no longer retry 4xx client/validation failures. Oversized source uploads now submit once, show the upload-limit error, and avoid duplicate rejected POSTs.
  - **🎨 Upload-limit clarity:** source upload size-limit errors now map to a friendly localized message instead of showing the raw byte-count backend detail in the Add Source toast.
  - **🐛 BUG FIX:** The Next.js rewrite proxy upload cap now defaults to `500mb`, matching the backend `ONP_SOURCE_UPLOAD_MAX_BYTES` default. Browser uploads between 100 MB and 500 MB no longer fail at the frontend proxy before FastAPI can stream, clean up, and return the app's normal source-upload response.
  - **🎨 Upload preflight clarity:** `/api/config` now exposes the active source upload cap and the Add Source wizard blocks oversized files before upload, listing each offending file beside the configured limit instead of waiting for a rejected multipart request.
  - **🐛 BUG FIX:** Source uploads now reserve their final file path with exclusive creation before streaming bytes. Same-name concurrent uploads can no longer race between unique-name selection and write-open to overwrite or truncate another upload.
  - **🐛 BUG FIX:** `npm run start` now supports both the flattened packaged frontend bundle and the local `.next/standalone` build layout. Local production starts now link/copy `.next/static` and `public` beside the nested standalone server so generated chunks return 200 instead of a blank, unhydrated shell.
  - **🐛 BUG FIX:** Evidence Studio artifact-list requests for a missing/deleted notebook now return a quiet 404 instead of a misleading empty list or expected-not-found stack trace, matching source-list stale-page cleanup behavior.
  - **✨ MLX inventory:** `/api/local-models/inventory` now includes complete MLX repos under `/Users/Antman/Desktop/AI_Models/MLX`, with `runtime: "mlx"`, inferred architecture, context length, quantization, parameter count, file size, and repo-style display names.
  - **✨ MLX provider foundation:** the desktop launcher and first-run wizard now accept `provider = "mlx"` for local Apple Silicon model repos. `MlxProvider` scans complete repos under the configured model directory's `MLX/` folder, starts `mlx_lm.server` on a local OpenAI-compatible endpoint, injects `OPENAI_COMPATIBLE_BASE_URL` / `OPENAI_COMPATIBLE_API_KEY`, and shuts the runtime down with the app.
  - **✨ Local model role routing foundation:** added read-only `/api/local-models/role-routing`, which scores the installed local fleet for Default chat, Source synthesis, Coding and technical research, Fast study tools, and Embedding/retrieval roles without changing active defaults. Evidence Studio artifact generation now uses those recommendations when a matching registered local language model exists, while explicit artifact model overrides still win.
  - **✨ Manifest-aware role routing:** `/api/local-models/role-routing` now parses `/Users/Antman/Desktop/AI_Models/manifests/model_inventory.md` when present and attaches matching curated manifest rows to role recommendations. Settings → Local Models shows manifest-intent badges such as coding-primary beside recommended local roles, making scanned recommendations easier to compare with Antman's curated fleet plan.
  - **🎨 Manifest alignment badges:** role-routing responses now include per-role manifest alignment (`primary`, `curated`, `untracked`, `missing_model`, or `no_manifest`) plus aggregate alignment counts, and Settings → Local Models renders that comparison beside each recommended local role so benchmark/heuristic winners can be checked against Antman's curated AI_Models manifest.
  - **🎨 Manifest-backed role alternatives:** untracked or missing role-routing winners now include matched curated manifest alternatives when relevant, plus explicit manifest-gap notes when no role-specific alternative exists. Settings → Local Models shows those curated alternatives directly inside each recommended role card.
  - **🎨 Manifest draft-row helpers:** Settings → Local Models now adds non-destructive `Copy row` actions for curated role alternatives and untracked local winners, generating paste-ready Markdown table rows so Antman can update `/Users/Antman/Desktop/AI_Models/manifests/model_inventory.md` manually without the app rewriting the curated manifest.
  - **✨ Manifest row apply workflow:** `/api/local-models/manifest/rows/preview` validates one generated Markdown manifest row without writing, while `/api/local-models/manifest/rows/apply` appends one validated row to the configured AI_Models manifest with a timestamped backup and duplicate protection. Settings → Local Models now adds `Apply row` next to draft-row copy actions so curated alternatives and untracked local winners can be saved directly, then refreshes role routing.
  - **🎨 Manifest gap warnings:** role routing now also reports curated manifest rows that do not match any scanned local model, and Settings → Local Models shows a compact Curated manifest gaps card with repo, runtime, role, and status details so missing, moved, or unsupported model assets are visible.
  - **🎨 Manifest reconciliation view:** Settings → Local Models now shows a filtered manifest reconciliation panel with All, Matched, Missing, and Unsupported runtime tabs. The backend classifies each manifest row against the current scan, including matched model runtime/name when available, so the curated AI_Models plan can be audited without leaving the app.
  - **🎨 Manifest reconciliation actions:** reconciliation rows now include safe copy actions for manifest local paths and matched scanned model paths, a bounded Reveal action that opens existing matched model paths inside the configured model directory, plus a Launcher preferences setup action for unsupported runtime rows.
  - **✨ GGUF launch defaults:** Settings → Local Models can now persist GGUF files as the native startup default, writing `provider = "llamacpp"` plus the relative launcher model reference into the desktop config. Legacy runtime-less inventory rows are treated as GGUF for this action, MLX launch defaults continue to write `provider = "mlx"`, and Transformers rows remain inventory-only until a runnable provider exists.
  - **✨ Manifest setup tasks:** missing manifest rows now include backend-generated setup tasks. Exact `.gguf` targets can start the managed downloader, while repo-folder MLX/Transformers/Experimental rows expose a copyable `huggingface-cli download ... --local-dir ...` command instead of guessing a filename.
  - **✨ Managed snapshot installs:** repo-folder setup tasks can now start managed Hugging Face snapshot install jobs through `/api/local-models/snapshot-installs`, with list/status endpoints, log-tail progress, model-directory containment checks, and a Snapshot installs status card in Settings → Local Models.
  - **🐛 Snapshot install interruption recovery:** managed snapshot installs now write `.snapshot-install.meta` sidecars, reconcile interrupted repo-folder installs after API restart as resumable cancelled jobs, expose `POST /api/local-models/snapshot-installs/{job_id}/cancel`, and add a Cancel action to the Settings → Local Models snapshot status card.
  - **✨ Local benchmark jobs:** added `/api/local-models/benchmarks` start/list/status endpoints plus a benchmark runner that measures recommended registered language models per role, marks downloaded-but-unregistered recommendations as skipped instead of failing opaquely, persists benchmark history under the configured model directory's `Manifests/`, and lets role routing prefer measured winners over heuristic picks.
  - **✨ Measured smart routing:** notebook chat and source chat now use the best measured local `chat` benchmark winner as the smart-router local candidate when `OPEN_NOTEBOOK_LOCAL_CHAT_MODEL_ID` is unset, while explicit local model overrides still take precedence.
  - **🎨 Model fleet visuals:** added `ModelFleetBadge`, surfaced GGUF/MLX runtime badges, added Recommended local roles and Local benchmark panels on Settings → Local Models, and shows the model/provider used in the artifact viewer so the local fleet under `/Users/Antman/Desktop/AI_Models` is readable by runtime, job fit, measured speed, and generated output provenance.
  - **🎨 Local model empty-state clarity:** Settings → Local Models now keeps role routing and benchmark controls hidden until at least one GGUF or MLX model is installed, so an empty `/Users/Antman/Desktop/AI_Models` workspace points users toward installation instead of a no-op benchmark.
  - **🎨 Runtime-specific connection checks:** `/api/local-models/health` now includes local Ollama credentials in addition to OpenAI-compatible local sidecars, probes Ollama through `/api/tags`, and returns runtime, endpoint, and probe-path metadata so Settings → Local Models shows exactly which local service was checked.
  - **🐛 BUG FIX:** Dashboard routes now wait for the initial `/api/auth/status` result before redirecting. Auth-disabled desktop installs no longer bounce from deep links such as Settings → Local Models to `/login` or `/notebooks` during startup.
  - **🐛 BUG FIX:** The setup/deep-health banner now tolerates older or malformed credential-status responses without crashing the dashboard while the API surface is starting up.
  - **🎨 Browser-verified visual polish:** Settings → Local Models and the notebook Evidence Studio rail were verified in a real browser at desktop and mobile widths. The pass fixed the clipped mobile Refresh row, the squeezed mobile notebook header actions, Evidence Studio artifact/quick-action overlap, and low-contrast artifact dialogs.
  - **🛠 Source-ingestion browser smoke:** the production-style Playwright smoke now drives the Add Source wizard through URL, text, file-upload, oversized file rejection, batch URL source creation, invalid URL batch blocking, and partial batch failure from `/sources`, validates multipart payloads, verifies refreshed queued rows, retries a failed notebook source card, checks selected-source Evidence Studio artifact generation plus citation-drawer inspection, and captures screenshots for ingestion, upload-limit, retry recovery, and artifact evidence modes.
  - **🐛 BUG FIX:** Frontend lint now runs cleanly with zero warnings after removing stale imports/disable comments, switching React Hook Form render-time `watch()` calls to `useWatch`, moving Add Source form submission work out of render, and documenting the two intentional TanStack Virtual compiler-boundary exceptions.
  - **🛠 Scan policy:** added a root `.codex-scanignore` and `docs/7-DEVELOPMENT/scan-policy.md` so project audits stay inside the Open Notebook Plus source tree while ignoring generated builds, caches, runtime databases, packaged installers, and local model weights.
  - **Tests:** `tests/test_evidence_studio_foundation.py`, `tests/test_evidence_studio_artifact_api.py`, `tests/test_v0_8_39_local_models_inventory.py`, `tests/test_phase1_local_model_health.py`, `tests/test_v0_8_37_smart_routing_ui_toggle.py`, `tests/test_sources_api.py`, `tests/test_config_source_upload_cap.py`, `tests/test_source_upload_cap.py`, `tests/test_source_upload_proxy_cap.py`, `tests/test_local_model_manifest.py`, `tests/test_local_model_role_routing.py`, `tests/test_local_model_benchmarks.py`, `desktop/tests/test_mlx_provider.py`, `desktop/tests/test_mlx_app_provider.py`, `frontend/start-server-utils.test.ts`, `frontend/src/lib/features.test.ts`, `frontend/src/lib/api/query-client.test.ts`, `frontend/src/lib/api/studio.test.ts`, `frontend/src/lib/api/sources.test.ts`, `frontend/src/lib/config.test.ts`, `frontend/src/lib/utils/error-handler.test.ts`, `frontend/src/components/sources/SourceCard.test.tsx`, `frontend/src/components/sources/steps/SourceTypeStep.test.ts`, `frontend/src/components/onp/SourceHealthPill.test.tsx`, `frontend/src/components/onp/ModelFleetBadge.test.tsx`, `frontend/src/components/onp/CitationCoverageBadge.test.tsx`, `frontend/src/components/onp/ArtifactRail.test.tsx`, `frontend/src/components/ui/virtualized-list.test.tsx`, `frontend/src/components/chat/SidecarLogPopover.test.tsx`, and `frontend/src/app/(dashboard)/settings/local-models/page.test.tsx` pin the new artifact contract, API gating, frontend flag parsing, frontend client calls, config upload-cap exposure, quick-upload source linking/embedding defaults, missing-notebook source-create 404s, oversized-upload 413 cleanup, frontend/backend upload-cap alignment, Add Source oversized-file preflight, no-retry 4xx mutation behavior, friendly upload-limit error mapping, local and packaged standalone frontend start layouts, failed-source retry click isolation, source health rendering, model runtime badges, citation coverage rendering, citation evidence drawer behavior, artifact revision snapshots, revision-list filtering/API/viewer behavior, artifact rail rendering, artifact rail counters/empty state, saved artifact type icons, experimental Research run gating, prompt routing, staged viewer rendering, metadata-first Research Run viewer rendering, and structured stage export metadata, stale deleted-notebook source requests, stale selected-source and stale notebook-fallback artifact generation, source-scoped artifact creation, source-readiness generation guards, blank artifact output failures, flashcard/quiz/mind-map/slide-deck/infographic/podcast-outline prompt routing, interactive flashcard/quiz viewer behavior, artifact viewer/download/regenerate/retry/delete behavior, saved export path visibility, nested-GGUF discovery, `mmproj` filtering, MLX rows, Transformers repo rows, runtime capability API fields, backend-provided inventory setup actions, fleet summary counters, readiness filter tabs, model metadata search, model inventory sort controls, clear-filters recovery, model path copying, inventory-only runtime clarity/setup links, GGUF-only hot-swap controls, runtime-specific local health checks for OpenAI-compatible and Ollama endpoints, partial-download filtering inside subfolders, MLX provider launch/env behavior, MLX config/wizard acceptance, MLX runtime lifecycle wiring, local role-routing recommendations, manifest-aware route matching, manifest gap warnings, manifest reconciliation filters, local model empty-state gating, artifact role-routed model selection, explicit artifact model override precedence, artifact model/provider display, benchmark job status/results, skipped unregistered benchmark recommendations, persisted benchmark history, measured-winner role routing, measured smart-router chat fallback, explicit local chat override precedence, benchmark UI rendering, the warning-free frontend lint cleanup, browser smoke screenshots under `output/playwright/`, and real-browser source-ingestion URL/text/file/oversized-file/batch creation, invalid URL blocking, partial batch failure, plus retry recovery coverage.

- **✨ v0.8.68 — Offline/online smart switching + Offline-mode toggle**
  - **✨ Network-state service (`open_notebook/health/network.py`):** 2s TCP probe + 20s TTL cache + passive flips from real cloud-call failures/successes; `ONP_NET_PROBE_HOSTS` / `ONP_NETWORK_STATE_TTL_SEC` tunable. "unknown" is treated as online — a flaky probe can never block cloud calls.
  - **✨ Offline gate (`open_notebook/ai/offline_gate.py`):** when offline (real or forced) and the turn's model is a cloud provider, provisioning substitutes the best local model instantly (DefaultModels chat slot if local, else first registered local language model) — no more 300s hangs. Offline with no local model fails fast with an actionable message. Local-provider models are never gated and pay zero probe cost; a mid-turn cloud NetworkError retries once on the local model (captive-portal leg).
  - **✨ Offline-mode toggle:** persisted `offline_mode` on ContentSettings + a Settings → Network control — forces the app fully local even when online (cloud chat, web search, Gmail digests all gated). The settings PUT busts the cache so it takes effect on the next turn.
  - **🎨 UI:** `GET /api/system/network-status` + `use-network-status` drive an amber "Offline — answering with <model>" badge in the app shell; chat messages answered by the fallback get an "Answered with <model> (offline)" pill (extends ChatMessageProviderBadge; `offline_fallback` rides the same done-event/response plumbing as `selected_provider`). i18n'd across all 10 locales.
  - **🐛 Gmail digests no longer silently drop offline:** a due digest on an offline machine previously burned a 20s send attempt and escalated the failure backoff up to 6h. Now the scheduler defers cheaply (no backoff escalation), retries every 5-minute tick, sends as soon as connectivity returns, and surfaces `pending_digest` in /gmail/status.
  - **⚡ `web_search` short-circuits offline** instead of burning its 25s provider-failover budget per tool call.
  - **🐛 BUG FIX — crawl4ai was un-selectable:** `SettingsUpdate` (PUT /settings) and the Settings form were never updated for the v0.8.67u `crawl4ai` URL-engine option, so picking it 422-rejected / wasn't offered. Both now list it.
  - **🎨 Source-chat parity:** the offline gate's substitution info now also flows through the source-chat surface — node threads `fallback_out`, the SSE stream emits an `offline_fallback` event, `useSourceChat` stashes it under the badge cache key, and the existing per-message provider badge in the source ChatPanel renders the amber pill. Source chat also gets the mid-turn network retry.
  - **🐛 Malformed MCP `server_id` 500'd instead of 400:** surrealdb 2.0 raises its own `SurrealError` subclass from `RecordID.parse`, so the v0.8.66 H2/H3 `except (ValueError, TypeError)` guards in `api/routers/mcp.py` no longer caught a bad id at any of the three PATCH/DELETE/test sites. Now caught alongside the stdlib errors.
  - **🛠 crawl4ai tests no longer require the optional package:** `tests/test_v0_8_67u_crawl4ai.py` registers a `sys.modules` stub when crawl4ai (a heavy Playwright-pulling extra) isn't installed, so the suite passes on any dev machine.
  - **🐛 Retired model ids refreshed:** "Test Connection" used `gpt-3.5-turbo` / `claude-3-haiku-20240307` / `grok-beta` — all retired upstream — so valid API keys reported failure during setup; five of seven Anthropic entries in the credential-discovery static list 404'd on first use. Now current cheap-tier ids (`gpt-4o-mini`, `claude-haiku-4-5`, …) and a current Anthropic catalog, guarded by a retired-id regression test.
  - **🐛 ChatSession delete cascade:** deleting a chat session left its `refers_to` graph edge dangling forever (only a full notebook delete swept it). `ChatSession.delete()` now sweeps edges first, mirroring the Source/Note cascade pattern.
  - **⚡ Podcast submit fails fast offline:** offline + a cloud TTS/LLM in the episode/speaker profile previously hung the generation job against an unreachable provider for up to 30 minutes. The submit endpoint now checks network state and rejects immediately with the offending models named; all-local profiles are never blocked.
  - **🎨 Source-chat provider badge gets data:** source chat has routed through the smart router since v0.8.66, but never captured the decision — the local/cloud badge ChatPanel renders stayed permanently blank. The node now threads `selection_out`, the SSE stream emits a `selected_provider` event, and the hook merge-stashes it alongside the offline pill data.
  - **🎨 Gmail "digest queued" indicator:** the Settings card now shows an amber notice when a due digest was deferred offline (reads the new `pending_digest` status field).
  - **✨ Podcast reliability + capability pass:**
    - **Retry fidelity:** the user's per-episode instructions (`briefing_suffix`) are now stored on the episode and replayed on retry — retries previously regenerated with the base briefing only, silently changing the output.
    - **Regenerate completed episodes:** the retry endpoint accepts terminal states (failed OR completed), and EpisodeCard gains a confirm-gated "Regenerate" button (NotebookLM-style "make it again"). In-flight episodes stay blocked.
    - **Language support unlocked:** `EpisodeProfile.language` (BCP 47) existed and `create_podcast()` accepts `language=`, but the two were never connected — episodes always generated in English. Now passed through.
    - **Content token budget at submit:** oversized notebook selections previously blew the outline LLM's context window mid-job after minutes of waiting; now rejected at submit with the token count and guidance (`ONP_PODCAST_MAX_CONTENT_TOKENS`, default 100k, 0 disables).
    - **Typed submit errors no longer 500:** the service's broad exception handler converted the offline gate's ConfigurationError (and the new budget InvalidInputError) into generic 500s — they now bubble to the global handlers with their actionable messages.
    - **Selected-profile guard:** defensive fail-fast if the chosen episode/speaker profile is dropped by the worker's resolution sweep (previously a cryptic podcast-creator validation error).
    - **Hygiene:** `get_job_detail()` logs status-lookup failures instead of silently reporting "unknown"; audio responses set media type by extension (was hardcoded `audio/mpeg`); deleting/retrying an episode now also removes its empty UUID output directory (slow disk-fill); CLAUDE.md no longer claims a silent-audio TTS fallback that was never implemented.
  - **✨ Podcast staged generation — progress, cancel, and outline review (the three "deferred" items, unlocked without forking anything):** podcast-creator exports its compiled LangGraph with four named stage nodes, so the worker now streams the graph instead of calling the `create_podcast()` black box (`commands/podcast_staged.py`).
    - **Per-stage progress:** the worker writes `generation_stage` to the episode as nodes complete (outline → transcript → audio → combining); the episode card's stage indicator is now driven by this authoritative signal instead of the v0.7.33 field-presence heuristic (which couldn't move until the end). Timeout errors now name the stage that hung.
    - **Cancel button:** `POST /podcasts/episodes/{id}/cancel` sets a flag the worker polls every ~5s; the in-flight graph task is cancelled, the episode is marked failed with a clear "cancelled by user" message, and the UI shows a Cancel button on in-progress episodes.
    - **Outline review before audio (beats NotebookLM, which has no edit step):** a "Review outline before generating audio" checkbox in the generate dialog stops generation after the outline; the episode card offers an editor (segment titles, descriptions, short/medium/long sizing) and "Approve & generate audio" resumes from the transcript node with the edited outline via a new `resume_podcast` command. New endpoints: `PUT .../outline`, `POST .../approve-outline`. i18n'd across all 10 locales.
    - **Upgrade guard:** the suite pins podcast-creator's node names — a library upgrade that renames them fails tests loudly instead of stages silently going dark.
    - **🐛 BUG FIX (caught by live smoke test) — staged fields were silently dropped by the database:** the `episode` table is SCHEMAFULL, and `generation_stage` / `cancel_requested` / `briefing_suffix` had no DEFINE FIELD — SurrealDB discarded them on every save, leaving stage tracking, the Cancel button, and retry-with-instructions dead against a real database (unit tests mock the DB, so only the live run surfaced it). Migration 22 adds the fields; a new schema-parity test pins every PodcastEpisode model field to a migration DEFINE FIELD so the next field added without a migration fails CI (`tests/test_v0_8_68_episode_schema_parity.py`). Second half of the same bug class: `ObjectModel._prepare_save_data` drops None values unless the field is declared nullable, so the workers' stage-clear on completion was a no-op and finished episodes stayed stuck on "combining_audio" — `generation_stage` is now in PodcastEpisode's `nullable_fields`. Verified end-to-end by the live smoke test: outline → awaiting_review → edit → approve → transcript → audio → combine → playable MP3.
  - **✨ Prompt optimizer — train transformation prompts with Microsoft SkillOpt (MIT):** every transformation card gains an "Optimize" action that trains the prompt against real sources from a notebook of your choice (NotebookLM has nothing like it).
    - **How it works:** the prompt becomes a SkillOpt "skill document"; each round runs the prompt over example sources with the target model, an LLM judge scores outputs 0–1 against your plain-English criteria, the optimizer proposes bounded edits, and only edits that improve a held-out validation split are kept. The result is shown side-by-side and applied only when you click Apply.
    - **Privacy-first:** runs entirely against OpenAI-compatible endpoints, so the local llama.cpp sidecar (or Ollama) covers both roles with zero data leaving the machine; the offline gate rejects cloud-model runs while offline before any tokens burn. Non-OpenAI-compatible providers are rejected with an actionable message.
    - **Plumbing:** `open_notebook/prompt_optimizer/` (adapter + runner + vendored base config), `optimize_prompt` surreal-commands job (timeout `ONP_PROMPT_OPT_TIMEOUT_SEC`, default 30 min), `POST /transformations/{id}/optimize` (501 when skillopt isn't installed — optional-dependency policy mirrors crawl4ai), OptimizePromptDialog with configure → running → review phases. i18n'd across all 10 locales.
    - **🐛 BUG FIX — skillopt wheel shadowed the repo's `scripts/`:** skillopt 0.1.0 installs a top-level `scripts` package into site-packages (for its console entry points); under PEP 420 a regular package beats a namespace package, which broke `from scripts.benchmark_models import ...` in 13 tests. `scripts/__init__.py` added so the repo's package wins.
    - **Upgrade guard:** tests pin `ReflACTTrainer(cfg, adapter)`'s signature and the flattened config key names (`target_model`, `optimizer_model`, `edit_budget`, …) so a skillopt upgrade that breaks the integration fails loudly (`tests/test_v0_8_68_prompt_optimizer.py`).
    - **🐛 BUG FIX (caught live) — submit 500'd:** `from __future__ import annotations` in the command module turned the handler's type hints into strings LangChain's RunnableLambda input schema couldn't resolve ("optimize_prompt_command_input is not fully defined"). Removed; a regression test now force-resolves every registered command's input schema so the next module with the future import fails CI, not production.
    - **🐛 BUG FIX (caught live) — training aborted with "Unable to determine train_size":** `BaseDataLoader.get_train_size()` returns None and its mere existence blocks the trainer's `train_items` fallback; our dataloader now overrides it. Pinned against skillopt's actual `_resolve_train_size`.
    - **🐛 BUG FIX (caught live) — skillopt wheel ships no prompt templates:** the 0.1.0 wheel omits every `skillopt/prompts/*.md` file, so the reflection stage crashed with "Prompt 'analyst_success' not found" (and the aggregate stage's `merge_*` loads were next). All 21 upstream templates are now vendored (MIT, attribution README) and `ensure_skillopt_prompts()` backfills missing ones into the installed package at run start — never overwriting, so a fixed upstream wheel automatically wins.
  - **🐛 BUG FIX — "This page couldn't load" at launch:** pywebview navigates exactly once, so when that single request raced the Next.js server's startup the user got a static error page with no recovery. Two-sided fix: the launcher's frontend gate now follows the `/` redirect to the real page and requires 3 back-to-back successes (one lucky probe of a just-bound socket no longer opens the window), and a load-retry watchdog re-issues the navigation if the page hasn't loaded after a grace period (`desktop/tests/test_v0_8_68_launch_race.py`, 7 tests).
  - **✨ Welcome splash + python-driven handoff:** the main window now opens on a polished inline welcome page (animated gradient, glass card, breathing logo, rotating status, "everything runs on your Mac" footer) that paints instantly with zero network dependencies and is shown for at least 3 seconds. A **python handoff controller** decides when to navigate to the app: unlike an in-page no-cors probe (which cannot see HTTP status — Next 16 standalone briefly serves its not-found page with status 200 for valid routes while route manifests lazy-load, so a JS probe read "ready" and navigated onto an error page, seen live), it verifies real status + body (`next-error-h1` check) twice in a row, and a failed handoff puts the splash back and retries — the error page can never be the resting state. The loaded handler confirms a genuine app page via an in-page sentinel (`window.__next_f` present, title not 404) because `get_current_url()` is None for inline pages and reports the target URL even for failed loads (`desktop/splash.py`, `desktop/window.py`, 14 tests).
  - **🛠 Tests:** `test_network_state.py`, `test_offline_mode_setting.py`, `test_offline_gate.py`, `test_provisioning_fallback.py`, `test_chat_offline_fallback_plumbing.py`, `test_network_status_endpoint.py`, `test_web_search_offline.py`, `test_digest_offline_deferral.py`, `test_v0_8_68_source_chat_offline_fallback.py`, `test_v0_8_68_model_id_refresh.py`, `test_v0_8_68_chat_session_delete_cascade.py`, `test_v0_8_68_podcast_offline_gate.py`; frontend NetworkStatusBadge + offline-pill cases. conftest pins the network probe "online" so the suite is deterministic on airgapped machines.

- **⚡ v0.8.67w — Database HNSW Vector Indexing, Citation Mapping, and Email Single-Flighting**
  - **⚡ HNSW Vector Search:** Defined SurrealDB HNSW indexes on `source_embedding`, `source_insight`, and `note` tables to optimize vector search from brute-force scans to indexed searches. Refactored `memory_recall.py` to use f-string interpolated `<|K|>` HNSW operators.
  - **🎨 Source-Chat Citation Mapping:** Mapped ephemeral streaming message IDs to canonical database IDs upon stream completion in `useSourceChat.ts` to fix placeholder citation pill rendering.
  - **🎨 Pending Model Overrides:** Implemented pending model override state for new source-chat sessions in `useSourceChat.ts` (matching the notebook-chat pattern) so the selected model isn't discarded before a session exists.
  - **⚡ Cache Pruning:** Cleared stashed message-scoped query caches on session switch in both chat hooks to bound memory usage.
  - **🐛 Gmail Copy Isolation:** Modified `GmailIntegration.get()` to return copies (`.model_copy()`) of the cached singleton, preventing mutations from poisoning the global cache.
  - **🐛 Email Send Single-Flighting:** Refactored `_send_digest_now` to reload config and re-verify send conditions under `_SEND_LOCK` to serialize concurrent scheduled and manual sends.


- **✨ v0.8.67u — Integrate local crawl4ai web scraping engine for URL Ingestion**
  - **✨ URL Ingestion:** Added support for `crawl4ai` as an optional local URL processing engine, enabling dynamic JavaScript execution and anti-bot evasion using Playwright locally.
  - **🛡 Graceful Fallback:** Integrated fallback mechanism so that if `crawl4ai` or its browser binaries are not installed, the tool and graphs fall back to standard `content_core` scrapers automatically without failing the ingest process.
  - **🐳 Docker Support:** Updated `Dockerfile` and `Dockerfile.single` runtime stages to automatically install the Playwright Chromium browser binary and all required OS dependencies, providing out-of-the-box local scraping capabilities in containerized deployments.
  - **⚡ Tiktoken Backtracking Fix:** Resolved a catastrophic regex backtracking issue in the tiktoken tokenizer when given long repeating character streams without spaces (e.g. `"x" * 500_000`), which was causing smart routing tests to hang or run extremely slowly. Sped up the unit test suite by over 16x (from 7m 14s to 26s).

- **✨ v0.8.67s — Add enhanced Ralph Loop script and fix integration test loop mismatch**
  - **🛠 Ralph Loop:** Added `scripts/ralph.sh` implementing an autonomous development loop supporting Claude Code (`claude`), cursor, and `opencode` with git persistence, circuit breakers, and automatic task tracking via `prd.json`.
  - **🐛 Integration Test:** Fixed a loop mismatch error in `test_recall_recent_memory_against_real_surrealdb` by removing the explicit `@pytest.mark.asyncio` decorator and using the function-scoped `clean_namespace` fixture. Also resolved a schema validation error by providing dummy embeddings for test records.

- **🐛 v0.8.67r — Add agentic capabilities and fix python runtime suffix in verification**
  - **H7 Bug Fix:** Fix python runtime verification in `desktop/build/fetch_runtimes.py` on Windows by checking for `.tar.gz` unconditionally (since it was updated to be downloaded as a tarball).
  - **✨ Agentic Capabilities:** Added secure local code execution via the `opencode_run` MCP tool and enabled autonomous web search result source ingestion via `add_web_source_to_notebook`.

- **🎨 v0.8.67q — Self-heal DB-repair banner + frontend bug fixes**
  - **DB-repair banner (the missing UI signal for v0.8.67l):** when the launcher
    flags live-query corruption, source processing is stuck but nothing told the
    user — the self-heal only triggered if they happened to restart. New
    `GET /api/system/db-repair-needed` reads the launcher's `.needs_db_repair`
    flag; a polling hook (`use-db-repair-status`) drives a non-dismissible
    `DbRepairBanner` in `AppShell` telling the user to quit & reopen (where the
    backup-first auto-repair runs). The banner self-clears after the repair.
  - **🐛 Markdown editor ignored dark mode:** `markdown-editor.tsx` imported
    `useTheme` from `next-themes`, whose provider is NOT initialized in this app
    (we use a Zustand theme store) — so `resolvedTheme` was undefined and the
    editor always rendered light (white editor inside a dark dialog). Now reads
    the real theme store's `effectiveTheme`.
  - **🐛 Topics list keyed by array index** (`SourceDetailContent.tsx`) → keyed by
    the topic value so React reconciles correctly.
  - **Audit note:** a frontend review also *cleared* two previously-suspected bugs
    — the source-chat citation popover key (uses a stable `kind`+index, correct)
    and the model-override "lost without a session" concern (the override is sent
    on the same call that auto-creates the session). No change needed for either.
  - **Tests:** `tests/test_v0_8_67q_db_repair_endpoint.py` (flag absent/present +
    never-500 on unreadable home). Frontend typecheck clean.

- **🐛 v0.8.67p — Whisper STT: pre-download the model the shim actually uses (no first-use stall)**
  - **Bug:** `_phase_download_models` pre-fetched a **whisper.cpp `ggml-base.en.bin`**,
    but the STT shim uses **faster-whisper** (CTranslate2) and `app.py` passed the
    bare name `"base.en"` — so faster-whisper ignored the pre-download and fetched
    its *own* model from HuggingFace silently on first voice use. If that download
    was slow/stalled, Whisper stayed "unhealthy" and the port never bound (no error,
    just a hang).
  - **Fix:** `ensure_stt_model` now downloads the real faster-whisper CTranslate2
    model (`Systran/faster-whisper-base.en`: config.json, model.bin, tokenizer.json,
    vocabulary.txt) into `STT/faster-whisper-base.en/` during the gated download
    phase (with progress). `app.py` points the shim at that local directory **only
    when every required file is present** — otherwise it falls back to the bare
    `"base.en"` HF download (an incomplete dir would break the shim, so this is
    regression-proof: worst case is the prior behavior).
  - **Tests:** `test_model_downloads.py` (fetches faster-whisper not ggml; returns
    None if any file fails so the launcher falls back).
  - Note: builds *into* the next release; not hot-fixed today to avoid another
    venv rebuild.

- **🐛 v0.8.67o — Auto-export now actually fires (first export ~10 min after boot)**
  - **Bug (self-review of v0.8.67m):** the scheduled export thread slept the FULL
    interval (24h) BEFORE its first export. A desktop app is usually quit within a
    day, so most sessions produced **no backup at all** — the data-protection
    feature rarely triggered. Now the first export runs shortly after boot
    (default 10 min, `ONP_AUTO_EXPORT_FIRST_DELAY_SECS`), then every interval, so a
    session longer than ~10 min always leaves at least one recoverable backup.

- **🛠 v0.8.67m — Scheduled DB auto-export + remembered window size**
  - **Scheduled auto-export (`desktop/launcher.py`):** a background thread exports
    the running SurrealDB to `~/onp-backups/auto-export-*.surql` on an interval
    (default every 24h, keeping the newest 7), so a corruption or accidental delete
    is always recoverable without you thinking about it. Sleeps the interval first
    (no boot I/O), prunes old exports, and tolerates failures (logs + retries).
    Tunable via `ONP_AUTO_EXPORT_HOURS` (0 disables) and `ONP_AUTO_EXPORT_KEEP`.
  - **Remember window size (`desktop/window.py` + `desktop/window_state.py`):** the
    main window now reopens at the size you last left it (follow-up to v0.8.67j's
    screen-aware default), clamped to the current screen so a size saved on a
    larger monitor can't strand the window off-screen. Stored as a small JSON file;
    a missing/corrupt file just falls back to the screen-aware default.
  - **Tests:** `test_auto_export.py` (retention pruning, keep-floor, safety);
    `test_window_state.py` (clamp floor/screen-cap, load/save roundtrip, corrupt-file
    fallback).

- **🔒 v0.8.67l — Self-healing DB live-query corruption + memory-pressure n_ctx backoff**
  - **Auto-repair (`desktop/db_repair.py` + `desktop/launcher.py`):** the recurring
    "source processing bricked" failure came from SurrealDB live-query state
    corrupting after an unclean shutdown (SIGKILL / force-quit / power loss),
    crashing the worker with *"The key being inserted already exists"* — fixable
    only by running `scripts/repair_desktop_db.sh` by hand. Now a daemon watcher
    detects that crash in `worker.log` (only content appended THIS boot, so a
    stale already-repaired crash can't re-trigger) and sets a one-shot flag; on
    the NEXT launch, BEFORE SurrealDB starts (clean slate), the launcher runs the
    same backup-first export→move→reimport automatically. Abort-safe (restores
    the original dir if the import fails) and one-shot (the flag clears after a
    single attempt, so a non-fixing repair can never loop). `ONP_DISABLE_DB_AUTOREPAIR`
    opts out.
  - **Memory-pressure backoff (`desktop/launcher.py`):** the v0.8.67i RAM-aware
    context default is now also stepped DOWN when AVAILABLE memory (vm_stat) can't
    hold the chosen tier's KV cache + ~5 GiB headroom — avoids launching the chat
    sidecar into a swap storm when the machine is already memory-saturated. No-op
    on a healthy machine (the total-RAM tier is unchanged).
  - **Tests:** `desktop/tests/test_db_repair.py` (signature detection, one-shot flag
    lifecycle, abort-safe guard returns); pressure-backoff cases in
    `test_launcher_adaptive_nctx.py`; `conftest.py` isolates tests from the real
    data dir.

- **🛠 v0.8.67k — Build & CI hardening (gate backend tests · fix dmg · stable-codesign opt-in)**
  - **Gate the backend suite (`Makefile` `build-mac-test`):** the build precondition
    ran only `desktop/tests/`, so a regression in `api/` or `open_notebook/` could
    ship in a build with zero coverage (the v0.8.67i chat-stream fix wasn't gated).
    Now also runs `uv run pytest tests/ --ignore=tests/integration` before a build.
  - **Fix flaky `.dmg` step (`desktop/build/post_build_mac.sh`):** detaches any stale
    mounted ONP image before `hdiutil create`, eliminating the
    `hdiutil: create failed - Resource busy` that aborted the build at the dmg step
    even when the `.app` was complete.
  - **Opt-in stable codesigning (`Makefile` + `scripts/create-signing-identity.sh`):**
    ad-hoc signing gives the app a new identity each rebuild, so macOS resets its TCC
    (Files & Folders) grants every time — the cause of the iCloud/Desktop scandir
    boot-wedge. `ONP_CODESIGN_IDENTITY` (default `-`, unchanged) lets you re-sign with
    a stable self-signed identity created once via the new script. Default build
    behavior is untouched.

- **🎨 v0.8.67j — Main window opens larger (screen-aware) instead of a fixed 1280×800**
  - **Gap:** the desktop window always opened at a hardcoded 1280×800, which felt
    cramped on large displays — the three-pane layout and chat composer had little
    room.
  - **Fix (`desktop/window.py`):** new pure `_fit_window_size()` sizes the window to
    ~90% of the usable screen, floored at the previous 1280×800 (so it never opens
    smaller than before) and capped to a fixed 1600×1000 fallback when the screen
    can't be measured. `_preferred_window_size()` reads the macOS main-screen
    *visible* frame (excludes menu bar + Dock) via AppKit — which pywebview's cocoa
    backend already depends on — and degrades gracefully on any failure. The window
    stays freely resizable (pywebview default).
  - **Tests:** `desktop/tests/test_window_size.py` (scale fraction, floor-wins on
    small screens, unmeasurable-screen fallback, never-smaller-than-floor across
    7 common resolutions).

- **🐛 v0.8.67i — Large source contexts no longer fail chat (RAM-aware n_ctx) + clear overflow message**
  - **Bug:** selecting all sources in a notebook (e.g. 26 sources ≈ 72K tokens)
    made `/chat/stream` fail with a bare *"Chat stream failed unexpectedly."* The
    root cause was two-fold: (1) the launcher hardcoded the chat-LLM context
    ceiling to `ONP_CHAT_LLM_CTX_MAX=32768`, so even on a 64 GB Mac whose model
    (Hermes-3, 131072 native) could hold the prompt, llama.cpp returned HTTP 400
    `context_length_exceeded`; (2) that overflow raised `ExternalServiceError`
    which the stream handler had no `except` for, so it hit the generic catch-all
    and the user got no hint to recover.
  - **Fix 1 — RAM-aware default (`desktop/launcher.py`):** new
    `Supervisor._default_ctx_max()` scales the context ceiling to total unified
    memory on Apple Silicon (≥56 GiB → 98304, ≥40 → 65536, ≥28 → 49152, else the
    historical 32768; non-darwin and sysconf-failure both keep 32768). So a
    capable Mac now chats over large source selections with no env var. An
    explicit `ONP_CHAT_LLM_CTX_MAX` (or `ONP_CHAT_LLM_CTX`) still wins.
  - **Fix 2 — actionable error (`api/routers/chat.py`):** `_stream_chat_events`
    now catches `ExternalServiceError`/`NetworkError` and surfaces the crafted
    `classify_error()` message (*"Content too large for the selected model. Try
    using a smaller selection or a model with a larger context window."*, local-
    model-loading, unreachable-server) instead of the opaque generic failure.
    These are app-crafted strings, not raw provider text — same safe-to-echo
    trust as the existing `ConfigurationError` leg, so no v0.7.184 info-leak
    regression.
  - **Tests:** `desktop/tests/test_launcher_adaptive_nctx.py` (RAM tiers, non-darwin
    floor, sysconf fallbacks, explicit-override precedence); two new cases in
    `tests/test_chat_stream.py` (overflow + network messages surface verbatim,
    generic text absent).

- **🎨 v0.8.67h — Pin the local chat model with `ONP_CHAT_LLM_GGUF`**
  - **Gap:** the launcher loads the chat GGUF via `pick_chat_llm_file`'s heuristic
    scorer, independent of the model selected in the UI — so a user who picked
    Qwen3.5-9B could find Hermes-3 loaded instead (chat works, but "what I picked
    isn't what loads"). The full DB-driven resolution is a riskier boot-time
    re-architecture (deferred).
  - **Fix (`desktop/auto_register/assigner.py`):** `pick_chat_llm_file` now honors
    `ONP_CHAT_LLM_GGUF` (a filename, with/without `.gguf`, case-insensitive) and
    returns that file if present, else falls through to the scorer so the sidecar
    always spawns. Safe, env-gated, no behavior change when unset.
    Tests: `desktop/tests/test_assigner_chat_pin.py` (4 passed).

- **🛠 v0.8.67g — SurrealDB shutdown grace is env-tunable + a one-command DB-repair tool**
  - **Why:** an unclean SurrealDB shutdown (SIGKILL / force-quit / power loss)
    leaves persisted live-query bookkeeping that collides when the next worker
    runs `db.live("command")` → "key already exists" → source processing bricked
    (the outage that needed a full DB re-import to fix this session).
  - **Prevention (`desktop/launcher.py`):** `stop_all`'s teardown grace before the
    SIGKILL fallback is now env-tunable — 5 s → **8 s** default (`ONP_SHUTDOWN_GRACE_SECS`)
    — so SurrealDB reliably flushes its RocksDB + live-query state on a big/busy DB.
  - **Recovery (`scripts/repair_desktop_db.sh`):** safe, backup-first script that
    exports the DB, copies it physically, then re-imports into a fresh `surreal_data`
    (clears the bad live-query state, preserves every notebook/source/note/chat).
    Aborts if the app is still running; never deletes the old data.

- **🛠 v0.8.67f — Boot can't hang on a stalling model directory (found live)**
  - **Bug:** `pick_chat_llm_file` runs `os.scandir(gguf_dir)` on the launch's main
    thread. When the model dir stalled (iCloud-evicted / TCC-gated `~/Desktop`, a
    sleeping external drive), the underlying `open()` blocked UNINTERRUPTIBLY and
    hung the ENTIRE app launch (`sample` of the wedged PID: main thread in
    `os.scandir → open$NOCANCEL`).
  - **Fix (`desktop/app.py`):** `_scan_chat_llm_with_timeout` runs the scan in a
    daemon thread and gives up after **`ONP_MODEL_SCAN_TIMEOUT`** (default 20 s) —
    the app boots (local chat degraded, with a clear warning) instead of hanging.
    Tests: `desktop/tests/test_app_model_scan_timeout.py` (4 passed).

- **🛠 v0.8.67e — `make build-mac-install` quits the running app before replacing it (found live)**
  - **Bug:** the install target ran `rm -rf "/Applications/Open Notebook Plus.app"`
    with the app still running. Deleting a running bundle orphaned its SurrealDB /
    uvicorn / llama.cpp sidecars and left **zombie Next.js frontend servers on stale
    ports**, so the app's webview later showed **"This page couldn't load"** (it was
    pointed at a dead port). It also caused a stuck `/readyz 503` from a half-deleted
    zombie API on a re-install.
  - **Fix (`Makefile`):** `build-mac-install` now `osascript quit`s the app, waits
    for it to exit, then force-kills any stragglers (app, surreal, llama.cpp, worker)
    **before** the `rm -rf` + `cp -R`. Installs land on a clean slate.

- **🛠 v0.8.67d — Harden the remaining startup gates (API `/readyz`, frontend) — completes v0.8.67b**
  - **Gap:** v0.8.67b hardened only SurrealDB's `_wait_tcp` gate. The API `/readyz`
    (`_wait_http`, 180 s) and frontend (120 s) gates stayed hardcoded — and the boot
    right after the v0.8.67c reinstall aborted at exactly the `/readyz` gate (the
    post-update venv rebuild made the API's cold import of langchain/langgraph
    exceed 180 s). Every future app update's first launch could hit the same abort.
  - **Fix (`desktop/launcher.py`):** both gates now use the env-tunable
    `_startup_timeout` helper — `/readyz` 180 s → **300 s** (`ONP_API_READY_TIMEOUT`),
    frontend 120 s → **180 s** (`ONP_FRONTEND_READY_TIMEOUT`). `_wait_http` already
    fail-fasts via `proc.poll()` on a real crash, so the higher ceilings only ever
    wait on a slow-but-alive cold start. Launcher suite 38 passed.

- **🛠 v0.8.67c — CRITICAL: local chat/embed models ran on CPU (0 GPU layers) → chatbot never answered (found live)**
  - **Bug (the actual root cause of "no results in the chatbot"):** the launcher
    spawned the chat & embedding `llama_cpp.server` sidecars with **no
    `--n_gpu_layers`**, so llama-cpp-python defaulted to 0 and ran the ENTIRE model
    on CPU. On Apple Silicon (M1 Max) an 8B chat model on CPU is so slow it never
    returns a completion within the chat timeout — and the health badge (a
    `/v1/models` ping) reported "healthy" while real inference was dead. Verified
    live: 0/33 layers on CPU → `/v1/chat/completions` **HTTP 000 after 90 s**;
    `--n_gpu_layers -1` → **33/33 layers on Metal → a correct answer in 1.7 s**.
  - **Fix (`desktop/launcher.py`):** new `_n_gpu_layers()` helper; both the chat
    (`ONP_CHAT_LLM_N_GPU_LAYERS`) and embed (`ONP_EMBED_N_GPU_LAYERS`) spawns now
    pass `--n_gpu_layers`, defaulting to **-1 (all layers) on macOS** (Metal +
    unified memory makes full offload free) and **0 (CPU) on other OSes** so a
    low-VRAM CUDA build can't OOM; both env-overridable without a rebuild.
  - **Tests:** `desktop/tests/test_launcher_gpu_layers.py` (10) pin the
    platform-default + env-override contract; full launcher suite 38 passed.

- **🛠 v0.8.67b — Launcher: a slow core service no longer aborts the whole app on startup (found live)**
  - **Bug (diagnosed from a live "no results in the chatbot" report):** right after the
    v0.8.67 reinstall, the first launch re-extracted the Python runtime + rebuilt the
    venv (the normal post-update step). That disk I/O delayed SurrealDB's port bind past
    the launcher's hard **30 s** `_wait_tcp` gate, and because SurrealDB is a *core*
    service the gate raised `TimeoutError` and aborted the ENTIRE supervisor
    (`EARLY-INIT FAILURE: tcp 127.0.0.1:53039 never came up within 30s`, `launcher.py
    start_all → _wait_tcp`) → no API, no model sidecars, every sidecar badge red, and the
    chat request failing with `NetworkError: Could not reach the AI model server`.
  - **Fix (`desktop/launcher.py`):** new env-tunable `_startup_timeout(env_key, default)`;
    SurrealDB gate 30 s → **90 s** (`ONP_SURREAL_TCP_TIMEOUT`), chat-sidecar readiness
    60 s → **90 s** (`ONP_SIDECAR_TCP_TIMEOUT`, already log-and-proceed). Safe because
    `_wait_tcp`/`_wait_http` already early-exit via `proc.poll()` the instant a child
    actually dies, so the larger ceiling only ever waits on a slow-but-alive start
    (post-update I/O, or a cold mmap of a 14B–30B GGUF). A non-positive/unparseable env
    override falls back to the default, so the gate can never be made *more* fragile.
  - **Tests:** `desktop/tests/test_launcher_startup_timeout.py` (12 passed) pin the
    parse/fallback contract; existing `desktop/tests/test_launcher.py` (16) still green.

- **✅ v0.8.67 — Accuracy & flow assessment: IMPLEMENTED (2026-05-31)**
  A read-only investigation of the accuracy (answer/recall/search correctness) and
  flow (responsiveness/UX) hot paths surfaced 7 grounded, code-verified items. All
  actionable ones are now FIXED with regression tests (backend suite 1692 passed,
  frontend 195 passed). Shipped in the v0.8.67 desktop rebuild — pyproject keeps
  the upstream 1.8.5 version; this fork tracks its own v0.8.NN cadence in this
  changelog/commits only, so no pyproject bump. Status:
  - **A1** — vector_search relevance floor 0.2 → 0.3 (env `ONP_VECTOR_MIN_SCORE`,
    clamped [0,1]) + `SearchRequest.minimum_score` default 0.2 → 0.3. Commits
    `f3e5094` (BROKEN — a silently-failed Edit left the helper undefined and
    corrupted `vector_search`), repaired in `7590f86` + `f0117c8`. Tests:
    `tests/test_v0_8_67_vector_min_score.py` (11 passed).
  - **A2** — episodes-only semantic recall no longer downgrades to recency. `6fe5111`.
  - **A3** — `Note.get_context("short")` token-budgeted (~160 tok) + ` […]` marker
    instead of a 100-char hard cut. `8031583`, `9f80c93`.
  - **A4** — `ContextItem` token count counts only text fields, not `str(dict)`. `9cf156e`.
  - **F5** — chat auto-scroll `behavior:'auto'` + only when near bottom (no token
    churn, no yank while reading up). `e36f7c0`.
  - **F6** — DEFERRED (cold-start base-URL cache; needs live-app validation).
  - **F7** — NON-ISSUE (real `useSettings` already inherits global
    `refetchOnWindowFocus:false`; the flagged line is the intentional observability hook).

  Original backlog detail (problem statements), highest value × confidence first:
  - **A1 — `vector_search` default `minimum_score=0.2` too permissive
    (`open_notebook/domain/notebook.py:1221`).** The codebase's own memory layer
    uses `_MIN_SCORE=0.30` and its comment calls 0.0–0.3 "unrelated". Source/note
    semantic search (and `ask.py:275`, which uses the default) lets 0.2 through,
    surfacing near-random context. **Fix:** raise default to ~0.3–0.4. `/search/ask`
    already takes a per-request override, so only the unset default changes.
    Effort S / risk low — but **validate against a real corpus** that wanted hits
    aren't dropped. HIGH accuracy impact.
  - **A2 — episodes-only semantic recall fall-through
    (`open_notebook/utils/memory_recall.py` ~L421,432).** The "is the semantic
    result empty?" check tests `facts`/`preferences` but NOT `episodes` (same
    omission class as the MEM-4 fix already shipped in `_count_memory_rows`), so an
    episodes-only store silently downgrades to recency. **Fix:** add
    `and not result.get("episodes")` to both conditions. Effort S / risk low.
  - **A3 — `Note.get_context("short")` hard-cuts at 100 CHARS
    (`notebook.py:1140`, `self.content[:100]`).** Notes in "short" mode are
    truncated mid-word with no `[…]` marker → the LLM treats a fragment as the
    whole note. **Fix:** token-based budget (~150 tok) + ellipsis. (Source's
    "short" is fine — it returns insights, not truncated text.) Effort S / risk low.
  - **A4 — `ContextItem` token count uses `str(self.content)` on a dict
    (`context_builder.py:34`).** Sources/insights are dicts, so the count includes
    `{`/`}`/key/quote overhead the prompt never sees → the budgeter OVER-counts and
    UNDER-includes content. **Fix:** count only the text fields. Effort M /
    risk medium (changes budget estimates — needs test coverage). Source-chat path.
  - **F5 — chat auto-scroll churn (`ChatPanel.tsx:146-147`).** The effect depends
    on `[messages]`, which changes on EVERY streamed token → 50+ stacked
    `scrollIntoView({behavior:'smooth'})` per reply (jank), and it force-scrolls
    even when the user has scrolled up to read. **Fix:** `behavior:'auto'` + only
    autoscroll when already near bottom (copy the distance-from-bottom pattern in
    `sources/page.tsx`). Effort S / risk low. HIGH flow impact.
  - **F6 — cold-start blocks on async base-URL discovery (`client.ts:60-64` +
    `config.ts`).** First API call awaits `/config`; if the API sidecar is still
    starting that's a 1–3s stall before the UI is interactive. **Fix:** cache the
    resolved URL in `sessionStorage`, use it immediately next launch, refetch in
    background. (The shipped F-3 latch fix made this recoverable; this makes it
    fast.) Effort S / risk low.
  - **F7 — `refetchOnWindowFocus: true` override on stable data
    (`use-settings.ts:25`).** Settings refetch on every tab-return is needless
    churn (health/local-models overrides are legitimately volatile — leave those).
    Effort S / risk low. LOW impact.
  - **Verified already-good (no action):** memory recall `_MIN_SCORE=0.30` + caps +
    timeouts + recency fallback; `token_count` tiktoken fallback; the streaming
    race-guard; DB pool warmup; ConnectionGuard recovery; the `['sources']`
    invalidation (already scoped via the `_isSourcesListQuery` predicate, not a
    wildcard). No reranking exists (raw cosine) — a real future ENHANCEMENT
    (L effort), not a bug.

- **🔒 v0.8.66o — Gmail persistence was fully broken (found while live-validating repo_update)**
  - **Bug (Critical-class, pre-existing):** while validating the v0.8.66 H2
    `repo_update` change against a **live SurrealDB 2.1.0**, I found Gmail
    settings/tokens never persisted to the row `get()` reads — TWO id-form bugs
    (same class as the H3 MCP fix the audit caught but didn't catch here):
    1. **`get()`** bound `SINGLETON_ID` as a **string** in `SELECT * FROM ONLY
       $rid`; SurrealDB treats a bound string as a string value, so it returned
       `[]` every time → the account always looked unconfigured.
    2. **`save()`** passed the **bare** `"singleton"` to `repo_upsert`, which runs
       `UPSERT {id} MERGE` → `singleton` was parsed as a TABLE, writing a new
       orphan `singleton:<random>` row on every save instead of updating the
       singleton.
    Net: Gmail connect/disconnect/settings were inert at the storage layer (and
    the C2 "disconnect doesn't clear" fix was moot until this). Likely latent +
    unnoticed because every gmail unit test mocks `repo_query`.
  - **Fix (`open_notebook/domain/gmail.py`):** `get()` binds
    `ensure_record_id(SINGLETON_ID)`; `save()` passes the full `SINGLETON_ID`.
  - **Verified against a live SurrealDB 2.1.0:** real `GmailIntegration`
    save→get now round-trips (email/enabled/token persist), disconnect actually
    clears, and 2 saves → 1 idempotent row. Also confirmed `repo_update`'s
    `UPDATE $rid MERGE $data` (the H2 change) is correct against the live DB with
    string id, RecordID, and bare id, MERGE preserving other fields.
  - **Tests:** new `tests/test_v0_8_66_gmail_persist.py` pins the id-form
    (RecordID get + full-id save) at the mocked boundary. Also confirmed `repo_upsert`
    has no other callers, so the bug was isolated to gmail.

- **📋 v0.8.66 — Audit remediation: explicitly DEFERRED items (with rationale)**
  The v0.8.66 sweep fixed both Criticals, all seven Highs, and ~38 Medium/Low
  items. The following are deferred — each is a large refactor of a hot path
  and/or needs validation this environment can't provide (live SurrealDB, live
  streaming metrics, or a running UI). They are real but lower-priority, and
  shipping them blind would risk regressions the test suite can't catch:
  - **M-B1** (pure-ASGI GZip/metrics middleware): observability-only — H1 already
    fixed the user-facing streaming buffering; the residual is streaming-metric
    accuracy. Needs live streaming-metrics validation.
  - **M-B2** (`/chat/execute` session lock): the lock scope is INTENTIONAL
    (per-session serialization prevents checkpoint corruption); the
    "wait_for-doesn't-stop-the-sidecar" gap needs a sidecar abort-signal feature;
    A-4 already bounds the tool-loop generation calls the stream runs through.
  - **D-4** (SurrealDB HNSW `<|K|>` KNN for source/insight/note search): a
    performance optimization (not a correctness bug) requiring a new index
    migration + query rewrites that must be validated against a live SurrealDB;
    high regression risk to semantic search without that.
  - **F-1** (source-chat citation popover re-key), **F-2** (TanStack per-message
    cache GC), **F-4** (source-chat `pendingModelOverride`), **F-7** (consolidate
    the two theme systems): frontend changes that need live-UI validation +
    hook state-management work; the three lowest-risk frontend Lows (F-3/F-5/F-6)
    were done and tested.
  - **I-2 / I-3** (runtime SHA-256 pinning, Dockerfile reproducibility): low value
    for the native, non-Docker desktop distribution; I-2 needs upstream
    per-runtime checksums.

- **🎨 v0.8.66n — Audit frontend Lows (F-3, F-5, F-6)**
  - **F-3 (`frontend/src/lib/config.ts`):** a failed runtime-config fetch latched
    `configPromise` to a rejected promise, pinning EVERY future
    `getConfig()`/`getApiUrl()` to that rejection until ConnectionGuard manually
    reset — the *expected* case on desktop launch (UI up before the API sidecar).
    `startConfigFetch()` now self-clears the latch on failure so the next call
    re-fetches. (+regression test)
  - **F-5 (`frontend/src/components/source/ChatPanel.tsx`):** "Re-ask allowing
    cloud" is hidden while a stream is in flight (`!isStreaming`), so it can't
    fire a second send that aborts the in-flight one.
  - **F-6 (`frontend/src/components/common/ConnectionGuard.tsx`):** the global
    "R"-to-retry shortcut now ignores Cmd/Ctrl/Alt + key-repeat + typing in
    inputs, so it stops hijacking Cmd/Ctrl+R reload (matches the CommandPalette
    convention).
  - **Verified:** `tsc --noEmit` clean; full vitest suite (187 → 188 tests) green.

- **🔒 v0.8.66m — (Audit S-4) Env-gated rate limiter**
  - **Gap (Medium):** no endpoint had rate limiting (auth brute-force,
    download/discover cost-amplification); the `RateLimitError`/429 handler
    existed but nothing raised it.
  - **Fix (`api/rate_limit.py` + `api/main.py`):** a lightweight per-IP sliding-
    window `RateLimitMiddleware`, registered just inside CORS so it runs BEFORE
    PasswordAuth. **DEFAULT OFF** (`ONP_RATE_LIMIT_PER_MIN` unset/0) → zero change
    to the single-user local-first desktop path; set `ONP_RATE_LIMIT_PER_MIN=N`
    on the exposed/Docker/multi-user path to cap requests/IP/60s (health + version
    + config probes exempt; 429 carries `Retry-After`).
  - **Tests:** new `tests/test_v0_8_66_rate_limit.py` (off-by-default, limits
    excess, health exempt, parse guard).

- **🔒 v0.8.66l — (Audit S-3/A-5) Fence untrusted tool output against prompt injection**
  - **Bug (Medium):** MCP-server + web-search tool results were fed back into the
    conversation VERBATIM. That content is attacker-influenceable (a fetched page,
    a search result, a malicious MCP server), so embedded "ignore previous
    instructions" / role-change / system text could hijack the turn — and poison
    long-term memory via the fire-and-forget extractor. (Recalled memory was
    already hardened in v0.8.47; this closes the inbound live-tool gap.)
  - **Fix (`open_notebook/graphs/chat.py`):** `_fence_untrusted_tool_output`
    wraps each tool result in a `[BEGIN/END UNTRUSTED TOOL OUTPUT]` fence with a
    directive to treat it as DATA only, and escapes any forged end-delimiter so a
    result can't break out of the fence. The citation-popover capture still shows
    the raw result (only the MODEL sees the fenced version).
  - **Tests:** new `tests/test_v0_8_66_tool_output_fence.py`; updated the
    tool-timeout assertions for the fenced content.

- **🤖 v0.8.66k — Audit AI routing/streaming batch (A-M1, A-4, A-1, M-B5)**
  - **A-M1 (`open_notebook/graphs/source_chat.py`):** source-chat's no-override
    path now routes through `provision_langchain_chat_model`, so the highest-PII
    surface (raw source text) gets the SAME smart-router + fail-closed privacy
    gate as notebook chat (it previously called `provision_langchain_model`
    directly, bypassing both). An explicit model pick still goes direct.
  - **A-4 (`open_notebook/graphs/chat.py`):** each `model.ainvoke` in the tool
    loop is now bounded by `ONP_CHAT_MODEL_TIMEOUT_SEC` (default 300s). The
    v0.8.35e per-tool-call timeout bounded tool execution but not generation; on
    /chat/stream (no outer route timeout) a wedged sidecar could hang forever.
  - **A-1 (`api/routers/chat.py`):** the streaming `<think>`-stripping
    accumulator is reset on each `on_chat_model_start`, so a tool-using turn's
    multiple `ainvoke`s no longer concatenate — an unclosed `<think>` from the
    tool-deciding call could otherwise swallow the final answer's tokens.
  - **M-B5:** on a client disconnect, if the turn already COMPLETED
    (`final_result` captured) the fire-and-forget memory extraction still runs,
    so a checkpoint-committed turn isn't left unextracted. Skipped for partial
    turns so they never pollute memory.

- **🛠 v0.8.66j — Audit infra batch (I-INFRA-1, I-1, I-4; I-2/I-3 deferred)**
  - **I-INFRA-1 (`open_notebook/database/repository.py`):** `repo_query` now
    transparently retries ONCE on a likely idle-reaped pooled connection
    (SurrealDB closes idle WebSockets; the first query after an idle stretch
    hard-failed). RESTRICTED to read-only `SELECT` queries so a write whose
    socket died after the server applied it can't be double-executed.
  - **I-1 (`Dockerfile`):** `ENV MAKEFLAGS="-j$(nproc)"` set the LITERAL
    `-j$(nproc)` (ENV doesn't command-substitute) → parallel build was a no-op.
    Now a fixed `-j4`.
  - **I-4:** shipped `examples/docker.env.example` so the `docker-compose-single`
    / `-dev` examples (which `env_file: ./docker.env`) aren't dead on arrival.
  - **Deferred (with rationale):** I-2 (runtime SHA-256 pinning — needs upstream
    per-runtime checksums; the bundled artifacts already ship inside the
    code-signed app) and I-3 (Dockerfile reproducibility) — low value for the
    native, non-Docker desktop distribution.

- **🐛 v0.8.66i — Audit email batch (E-3, E-4, E-5, E-6)**
  - **E-3 (`open_notebook/domain/gmail.py`):** `GmailIntegration.get()` returned the
    SHARED cached instance; callers (disconnect/forget/settings/send) mutate it
    before `save()`, aliasing those mutations into concurrent readers within the
    TTL window. `get()` now returns a `model_copy()`.
  - **E-4 (`api/routers/gmail.py`):** the digest send is now serialized by a single
    `_SEND_LOCK` (the scheduler tick and a manual `/send-test`, or overlapping
    ticks, could interleave into duplicate emails / a race on `last_sent_at`).
  - **E-5:** the Gmail send-API error no longer echoes the response body
    (`r.text[:200]`) to the client (it can reflect request context); it's logged
    and a generic `HTTP <code>` message is returned (mirrors v0.8.24).
  - **E-6:** `_oauth_states` gets a hard 256-entry cap as a backstop to the
    existing TTL purge.

- **🧠 v0.8.66h — Audit memory batch (MEM-2, MEM-4; MEM-3 moot via C1)**
  - **MEM-4 (`open_notebook/utils/memory_recall.py`):** `_count_memory_rows` now
    also counts `memory_episode`, so auto-mode's recency-vs-semantic decision and
    the "no matches" short-circuit no longer discard episodes-only semantic hits
    (the v0.8.49 episode-recall regression).
  - **MEM-2 (`desktop/memory/surreal_store.py`):** prune now orders by recency
    PRIMARY + `confidence` as the tie-breaker, so the persisted confidence
    (v0.8.55) finally influences eviction among same-age rows.
  - **MEM-3:** moot — resolved by C1's `infer=False` (mem0 no longer does the
    infer-time per-table dedup that the per-table `LIMIT` affected).

- **🧹 v0.8.66g — Audit Mediums/Lows batch 4 (data integrity: D-1, D-3, D-5, D-6)**
  - **D-1 (`open_notebook/domain/notebook.py`):** removed the dead
    `DELETE note_embedding` statements (bulk + single note delete). `note_embedding`
    is a **phantom table** — no migration defines it; a note's embedding is a column
    on the `note` row, removed when the row is deleted. The statements were no-ops
    and the comments misled maintainers.
  - **D-5:** the bulk note-delete now deletes the note **rows before** the artifact
    edges, so a partial failure can't strand searchable orphan note rows (any
    leftover edges are swept by the notebook-level `DELETE artifact WHERE out=…`).
  - **D-3:** `ChatSession.relate_to_notebook/_to_source` are now **idempotent** —
    a retried session-create previously RELATE'd a second `refers_to` edge each
    time (RELATE isn't upsert, and `dedup_edges` doesn't sweep `refers_to`). Now
    returns the existing edge if present (mirrors the reference/artifact path).
  - **D-6:** hoisted the RecordID→str `id` coercion from Source to the
    `ObjectModel` base, so all 8 models coerce a DB-sourced id uniformly instead
    of relying on incidental upstream `parse_record_ids`.
  - **Tests:** updated `test_v0_7_133` for the 2-statement rows-first delete.

- **🔒 v0.8.66f — Audit Mediums/Lows batch 3 (backend security/reliability)**
  - **MCP-1 (`open_notebook/mcp/client.py`):** every MCP RPC is now bounded by
    `ONP_MCP_RPC_TIMEOUT_SEC` (default 30s). A hung server previously pinned
    discovery + `/api/mcp/{id}/test` up to the transport's ~300s SSE read timeout.
  - **MCP-4:** optional auth headers (`ONP_MCP_AUTH_HEADER="Name: value"`, or
    `MCPClient(headers=…)`) so protected streamable-http servers are usable.
  - **A-6/A-7 (`open_notebook/ai/router.py`, `provision.py`):** `pick_provider`
    now reserves headroom for the actual reply reservation (max_tokens=8192) +
    a system/tool-schema margin instead of a flat 1000, so a near-full prompt
    routes to cloud instead of overflowing the local sidecar (llama.cpp 400).
    Env-tunable via `ONP_LOCAL_REPLY_HEADROOM_TOKENS`.
  - **S-5 (`open_notebook/utils/encryption.py`):** `decrypt_value` no longer
    embeds `str(e)` in the raised `ValueError` (it surfaced in API responses and
    could leak Fernet/cryptography internals); detail is logged, message is generic.
  - **M-B3 (`api/credentials_service.py`):** `discover_with_config` re-validates
    `base_url` (SSRF) before any outbound discovery request.
  - **M-B4:** `test_credential` matches HTTP status codes on word boundaries
    (`\b401\b`) so a coincidental substring can't misclassify the result.
  - **A-2 (`api/routers/chat.py`):** the streaming path now surfaces the
    actionable `ConfigurationError` message (incl. the fail-closed privacy-gate
    block, with a `privacy_blocked` flag) instead of a generic
    "failed unexpectedly".
  - **Tests:** new `test_v0_8_66_mcp_client_timeout.py`, `test_v0_8_66_router_headroom.py`.

- **🔒 v0.8.66e — (Audit S-1) Validate GGUF download `repo_id`**
  - **Bug (Medium):** `POST /api/local-models/download` interpolated the
    user-supplied `repo_id` straight into the HuggingFace URL
    (`https://huggingface.co/{repo_id}/resolve/main/{filename}`) with no
    validation, while `filename` was already guarded. The host is pinned to
    huggingface.co (so host-smuggling is weak), but path-traversal / query /
    fragment / `@` sequences could be smuggled into the path.
  - **Fix (`api/routers/local_models.py`):** require `repo_id` to match the HF
    `namespace/name` shape (`[A-Za-z0-9][A-Za-z0-9._-]*` × 2, exactly one
    slash) — defense-in-depth matching the existing filename guard.
  - **Tests:** new `tests/test_v0_8_66_gguf_repo_id_validation.py` (9 malicious
    repo_ids → 400; a valid one → 200).

- **✨ v0.8.66d — (Audit MCP-2) Bind chat tools from ALL enabled MCP servers**
  - **Bug (Medium):** `_resolve_chat_tools` only ever built a client for
    `servers[0]`, so every enabled MCP server after the first (by registry
    `priority`) was silently ignored — the multi-server Settings UI was a de-facto
    single-server selector.
  - **Fix (`open_notebook/graphs/chat.py`):** iterate all enabled (non-excluded)
    servers, discover + wrap each one's tools, and concatenate. On a tool-name
    collision across servers the first/higher-priority server wins and the dup is
    logged. Per-server discovery stays TTL-cached. Test hooks (`force_servers`,
    `force_tools_full`, `force_tool_names`) preserved.
  - **Tests:** new `tests/test_v0_8_66_mcp_multiserver.py` (tools from both
    servers bind; collision deduped to the first). Existing MCP suite green.

- **🐛 v0.8.66c — Audit Mediums batch 2 (chat tool-loop robustness + memory leak)**
  - **MCP-3 (`open_notebook/graphs/chat.py`):** `ONP_MCP_TOOL_TIMEOUT_SEC` was
    parsed UNGUARDED inside the per-tool-call loop — a malformed value raised
    `ValueError` that crashed the whole batch (misattributed to the tool), and
    `0`/negative gave an instant timeout. Now parsed ONCE via a guarded+clamped
    `_mcp_tool_timeout_sec()` (blank/garbage/≤0 → default 30s).
  - **A-3 (`chat.py`):** the tool-loop iteration cap was hardcoded to 4 with no
    override, though the v0.8.56 truncation notice tells users to "raise the
    cap." Added `ONP_AGENT_MAX_ITERATIONS` via guarded `_agent_max_iterations()`
    (explicit caller arg still wins; blank/garbage/<1 → 4).
  - **MEM-1 (`desktop/memory/writer.py`):** the batched-extraction
    `_SESSION_BUFFERS` map leaked — a threshold flush left an empty-list key
    behind forever, and abandoned sub-threshold sessions were never evicted. Now
    the key is deleted after a flush, and the map is bounded
    (`_MAX_BUFFERED_SESSIONS=512`, oldest-evicted past the cap).
  - **Tests:** new `tests/test_v0_8_66_chat_env_knobs.py` (parametrized guards);
    `test_memory_batching.py` gains MEM-1 leak-bound tests + an updated post-flush
    assertion.

- **🐛 v0.8.66b — Audit Mediums/Lows batch 1 (data/email/infra hygiene)**
  - **E-2 (`open_notebook/domain/gmail.py`):** `_fernet`/`_dec` logged exceptions
    with printf `%s` placeholders, but loguru uses `{}`-style formatting — so the
    exception detail was silently dropped (defeating the v0.8.28 logging fix).
    Changed both to `{}`.
  - **D-2 (`migrations/5_down.surrealql`):** `REMOVE TABLE … SCHEMAFULL` is invalid
    SurrealQL (SCHEMAFULL is a DEFINE-time modifier), so the migration-5 rollback
    failed to parse. Now a plain `REMOVE TABLE IF EXISTS transformation;`.
  - **D-M1 (`repository.py:repo_create`):** unconditionally stamped
    `created = now`, clobbering a caller-supplied value on reimport/restore. Now
    `data.setdefault("created", now)` — normal creates still auto-stamp. +2 tests.
  - **I-M1 (`Makefile:build-mac-test`):** `pytest … | tail -3` made the recipe's
    exit status `tail`'s (always 0), so a failing suite could not fail the build.
    Dropped the pipe so pytest's non-zero exit aborts `build-mac`. **Follow-on:**
    this surfaced 3 pre-existing `test_launcher` failures (the supervisor's
    v0.8.38 sidecar-log drainer reads `proc.stderr`, but the tests' Popen mock
    used `spec=subprocess.Popen`, which blocks instance attrs) — fixed the mocks
    so the now-enforced build gate is green (desktop suite 332 passed).

- **🔒 v0.8.66 — (Audit H7) Fix Windows-only first-launch crash: python runtime mislabeled `.zip`**
  - **Bug (High, Windows-only):** python-build-standalone's `install_only` artifact
    is a gzip **tarball** on every platform, but the bundle saved/named the Windows
    one `python-windows-x86_64.zip`. `bootstrap.extract_python_runtime` dispatches on
    the suffix → called `zipfile.ZipFile()` on gzip-tar bytes → `BadZipFile` → the
    venv was never provisioned and the app died on first launch. macOS/Linux unaffected.
  - **Fix:** always use `.tar.gz` for the python artifact name in all three sites —
    `desktop/build/fetch_runtimes.py`, `desktop/app.py:_bundled_python_tarball`, and
    `desktop/build/pyinstaller.spec`. `extract_python_runtime` already routes
    `.tar.gz` to `tarfile`.
  - **Tests:** 4 new in `desktop/tests/test_bootstrap.py` — bundled name is `.tar.gz`
    on win32; a Windows `.tar.gz` extracts via tarfile to `python.exe`; and gzip-tar
    bytes named `.zip` provably raise `BadZipFile` (documents the root cause).

- **🔒 v0.8.66 — (Audit H6) Fix connection-pool deadlock: cap-waiter never woken on broken release**
  - **Bug (High):** at capacity, an acquirer parks on `await _pool.get()`, woken only
    by a non-broken release's `put_nowait`. If every checked-out connection released
    **broken** (the multi-connection-poisoning scenario v0.8.65g addresses — a chat-
    stream disconnect / SurrealDB hiccup), the broken path closed the conn + decremented
    `_pool_total` but never enqueued anything, so the parked acquirer hung forever
    despite the now-free capacity (the "chatbot wedged until restart" class). The runtime
    `db_connection` acquire has no timeout, so the hang was unbounded.
  - **Fix (`open_notebook/database/repository.py`):** a broken release now enqueues a
    `_SLOT_FREED` sentinel **iff** a getter is parked (so no stray sentinel pollutes the
    idle queue when nobody waits); `_acquire` loops, treating the sentinel as "a slot
    freed — reserve and create a new connection." No reliance on `wait_for`/cancellation
    semantics.
  - **Tests:** new `tests/test_v0_8_66_pool_deadlock.py` (2): a broken release wakes a
    parked acquirer (was an infinite hang); a broken release with no waiter leaves no
    stray sentinel. Full pool suite still green.

- **🔒 v0.8.66 — (Audit H2/H3/H4 + repo_update) Harden the MCP registry router**
  - **H2 (SurrealQL injection):** `PATCH /api/mcp/{server_id}` passed the raw path id to
    `repo_update`, which f-stringed it into `UPDATE {id} MERGE $data`; SurrealDB executes
    multiple `;`-separated statements, so `mcp_server:x; DELETE notebook; --` ran an
    injected `DELETE`. **Fix:** the router coerces the id via `ensure_record_id`, and
    `repo_update` now binds it as `$rid` (parameterized) — killing the injection class for
    *every* caller of the codebase's sole raw-interpolation primitive.
  - **H3 (RecordID-vs-string no-op):** `DELETE /api/mcp/{id}` and `POST /api/mcp/{id}/test`
    bound a plain string to `id = $id`; a RecordID never equals a string, so Delete was a
    silent no-op (false success toast, row survived) and Test 404'd real servers. **Fix:**
    bind `ensure_record_id(server_id)`.
  - **H4 (SSRF):** `POST /api/mcp` stored an arbitrary URL (later fetched by /test and the
    chat tool loop every turn) with no validation. **Fix:** reuse the existing
    `validate_url` SSRF check (blocks link-local/cloud-metadata + bad schemes, allows
    localhost/private IPs) on create and defensively before /test's outbound fetch.
  - **Tests:** new `tests/test_v0_8_66_mcp_hardening.py` (8): repo_update parameterizes the
    id; PATCH/DELETE/test pass a RecordID; malformed id → 400; link-local URL → 400;
    loopback URL → 201. Existing MCP integration suite still green.

- **🔒 v0.8.66 — (Audit H1) Stop GZip middleware buffering the token streams**
  - **Bug (High):** the global `GZipMiddleware` only exempts `text/event-stream`, but the
    token streams are `application/x-ndjson` (`/chat/stream`) and `text/plain`
    (`/search/ask`, source-chat `/messages`), so it compressed them per-chunk
    (compresslevel=9, no `Z_SYNC_FLUSH`), holding most token chunks back until a gzip frame
    flushed — silently negating the real-time streaming UX for every gzip-capable client
    (every browser + httpx) and delaying `is_disconnected()`.
  - **Fix (`api/main.py`):** `SelectiveGZipMiddleware` bypasses GZip entirely for the
    streaming paths (prefix match on `/api/chat/stream`, `/api/search/ask`; POST to
    `…/messages`), while retaining GZip for the large JSON CRUD responses it was added for.
  - **Tests:** new `tests/test_v0_8_66_gzip_streaming.py` (4): streaming endpoints are NOT
    `Content-Encoding: gzip`; large JSON still is; path-matcher matrix.

- **🔒 v0.8.66 — (Audit C2) Fix Gmail disconnect/forget not actually clearing tokens**
  - **Bug (Critical):** `GmailIntegration.save()` stripped every None value from
    the payload before `repo_upsert` (`UPSERT … MERGE $data`). Because SurrealDB's
    MERGE only overwrites keys *present* in the payload and preserves omitted ones,
    `disconnect()` and `forget_credentials()` — which set the tokens (and, for
    forget, the OAuth client id/secret) to None then call save() — were **DB-level
    no-ops**. The stale encrypted `refresh_token` survived in the row, so the
    account stayed effectively connected and "forgotten" credentials lingered on
    disk. A security/privacy defect: a user who clicks Disconnect still has a live
    refresh token persisted.
  - **Fix (`open_notebook/domain/gmail.py`):** the six credential/token keys
    (`client_id_enc`, `client_secret_enc`, `access_token_enc`,
    `refresh_token_enc`, `token_expires_at`, `email_address`) are now ALWAYS
    written — even when None — so MERGE nulls them. The remaining config fields
    (`enabled`/`frequency`/`include_*`/`last_sent_at`) keep the None-skip so a
    partial save can't wipe them.
  - **Tests:** new `tests/test_v0_8_66_gmail_clear.py` (3 tests): disconnect-save
    force-writes all six keys as None; connected-save still encrypts+writes them;
    a non-credential None field (`last_sent_at`) is still omitted.

- **🔒 v0.8.66 — (Audit C1) Fix silently-inert memory subsystem: mem0↔store key mismatch**
  - **Bug (Critical):** the BrainPulse memory subsystem persisted **empty** rows —
    every stored fact/preference/episode had `text=""` and `scope="user"` — so
    recall surfaced nothing. Two compounding causes:
    1. `desktop/memory/writer.py` called `mem_client.add(messages=text, …)` with
       mem0's **default `infer=True`**, so mem0 re-ran its OWN extraction +
       update-decision LLM over our already-curated text (a second pair of local
       round-trips per fact, plus nondeterministic mutation) and stored the result
       under the payload key **`data`**.
    2. `desktop/memory/surreal_store.py:insert` read `payload["text"]` (never set
       by mem0 → `""`) and `payload["metadata"]["scope"]` (mem0 **flattens**
       metadata to the payload top level → the nested dict never existed → always
       `"user"`). Routing worked only because `_table` read the top-level `kind`.
  - **Why every test passed anyway:** the unit suite mocked the mem0→store
    boundary, so the contract mismatch was invisible — the classic "all green,
    feature inert" failure the production audit was built to catch.
  - **Fix:** writer now passes `infer=False` + a proper `messages=[{role,content}]`
    list (stores our text verbatim, no extra LLM round-trips); `insert` reads
    `payload.get("data") or payload.get("text", "")` for the text and the
    **top-level** `scope`/`confidence` (with the old nested shape kept as a
    back-compat fallback), and preserves the descriptive metadata for recall
    filters. Full chain now consistent: writer → mem0 `data` → store `text` column
    → `recall_recent_memory` `SELECT text`.
  - **🐛 Folds in audit H5:** `created_at` is now written as a native tz-aware
    `datetime` instead of an ISO string. Migration 15 defines these tables
    **SCHEMAFULL** with `created_at TYPE datetime DEFAULT time::now()`; the
    surrealdb client CBOR-tags only real `datetime` objects, so an ISO *string*
    is rejected by SurrealDB v2's strict type check → the `CREATE` hard-fails and
    the writer's broad `except` silently drops the fact. A native datetime
    serializes correctly.
  - **Tests:** new `test_insert_reads_real_mem0_flat_payload` (feeds the exact flat
    payload mem0 emits; asserts `text`/`scope`/`confidence` land non-empty +
    `created_at` is a `datetime`) and `test_insert_still_accepts_legacy_text_and_nested_metadata`
    (back-compat). Made the store test file collectable in the mem0-less dev venv
    (guarded `_register` import). 38 memory unit tests green.

- **🧩 v0.8.65i — Make local-model auto-registration resilient (so local models are selectable in chat)**
  - **Context:** the project chat already has a model selector (the gear button
    by the input → `ModelSelector`) that lists every registered `type:language`
    model with its provider — including local Ollama + bundled llama.cpp models,
    which are auto-registered at launch (`register_ollama_models` registers every
    Ollama model). The user couldn't pick a local model because **the pool-
    poisoning bug (v0.8.65g) made the auto-register's first `GET /api/models`
    fail, and the code `return`ed — skipping ALL registration**, leaving the
    selector empty.
  - **Improvement:** `desktop/auto_register/__init__.py` now **retries** that
    initial `/api/models` fetch (5×, 1s backoff) before giving up, so a transient
    startup hiccup (API not warm, a one-off DB/pool blip) can't skip every local
    model. Combined with the v0.8.65g pool fix, local models register reliably
    and appear in the chat selector after a restart.
  - **Tests:** new `test_auto_register_retries_models_fetch_then_registers`; also
    **fixed a pre-existing stale test** (`test_auto_register_is_idempotent`,
    which drifted 2→3 POSTs when v0.8.36 added Osaurus auto-register and was
    masked by `build-mac-test | tail -3`). Desktop suite now 324 passed (3
    remaining failures are pre-existing `test_launcher` supervisor-drift, flagged
    for a separate follow-up).

- **🫥 v0.8.65h — Stop reasoning models leaking raw `<think>` while streaming**
  - **Bug:** `/chat/stream` yielded raw token chunks WITHOUT stripping `<think>`
    blocks, so reasoning models (Qwen3, Qwen3.5, DeepSeek-R1) flashed their raw
    `<think>…reasoning…</think>` at the user during streaming — only replaced by
    the cleaned answer at the `done` event. (Notebook chat streams, so it was
    affected.) `clean_thinking_content` only ran on the FINAL message.
  - **Fix (`api/routers/chat.py`):** new `_visible_streamed_text` re-derives the
    visible (non-think) prefix from the full accumulated stream each chunk and
    emits only the delta — removing complete `<think>…</think>` blocks,
    suppressing an as-yet-unclosed block, and withholding a trailing partial
    `<think>` prefix split across chunks. Non-reasoning models stream identically
    to before (no tags → same per-chunk delta).
  - **Test:** `tests/test_v0_8_65h_stream_thinking.py` (9 — complete/unclosed/
    multi-block/case-insensitive, chunk-split open + close tags, normal-model
    passthrough). Full backend suite 1592 passed.

- **🐛 v0.8.65g — Fix chatbot "models stopped working" (pool poisoning) + chat Copy/Edit + launcher_prefs**
  - **The chat-blocker (root cause):** `open_notebook/database/repository.py`
    `db_connection` caught `except Exception` — but `asyncio.CancelledError` is a
    `BaseException`, not an `Exception`. So when a chat-stream query was cancelled
    (client disconnect, `wait_for` timeout, route-handler cancel), the connection
    was returned to the pool **still holding a pending in-flight request**. The
    next acquirer's query then collided with the stale response →
    `KeyError(<uuid>)` deep in the SurrealDB driver (`async_ws.py:_send`) → the
    chat's model-record fetch (`domain.base.get`) failed, and the **poisoned
    connection lived on in the pool**, so the chatbot stayed broken until restart
    ("models don't work"). Fix: `except BaseException` → a cancelled query marks
    the connection broken (closed + dropped), never reused. Test:
    `tests/test_db_pool.py::test_cancelled_query_marks_connection_broken`.
  - **`/launcher-prefs` 500 (secondary):** the bundled app raised
    `ModuleNotFoundError: desktop.launcher_prefs` (the module was missing from the
    PyInstaller spec). Bundled it (`pyinstaller.spec` datas) + made the router
    degrade to empty prefs on `ImportError` instead of 500.
  - **Chat Copy/Edit (UI):** human messages in "Chat with Notebook" had no
    actions. New `MessageCopyEditActions` adds **Copy** (to clipboard, for reuse)
    + **Edit** (loads the message back into the chat input to tweak + resend) on
    human messages, and an **Edit** affordance on AI messages (which already had
    Copy via `MessageActions`). Frontend +4 tests (187 total).
  - **Reasoning-model blank-reply fix:** testing local models surfaced that
    Qwen3 (a `<think>…</think>` reasoning model) returned an EMPTY chat answer —
    `clean_thinking_content` stripped the think block and, when the model spent
    its budget thinking, nothing was left. Now it falls back to the reasoning
    text (and strips an unclosed `<think>` cut off mid-thought) so the chatbot
    never renders a blank/raw-tag reply. +3 tests in `tests/test_utils.py`.
  - **Models verified:** representative local Ollama models were driven through
    the real chat tool loop (`bind_mcp_and_run_tool_loop`); llama3.2, gemma3:4b,
    llama3.1:8b produced clean answers. The pool fix is model-agnostic (DB layer,
    not per-model). Large 14B models exceeded the test harness's 200s/160-token
    cap (a test artifact, not an app limit — the app's chat timeout is ~300s).

- **🏷️ v0.8.65f — Rename display name to "Open notebook+" (app only; repo unchanged)**
  - **Display-name only** (identity/filesystem unchanged): `.app` filename
    (`Open Notebook Plus.app`), data dir (`~/.open-notebook-plus`), bundle
    identifier (`com.antman1526.open-notebook-plus`), and the `open_notebook`
    Python package all stay — so no data loss / no path breakage.
  - **Changed:** macOS app label via `CFBundleName`/`CFBundleDisplayName`
    (`pyinstaller.spec`) + the mic-permission string; the desktop window title
    (`window.py`); all frontend UI strings incl. the locale `appName`/
    `loginTitle`/`apiTitle`/`apiDesc`/`docLink` across 10 languages (`Open
    Notebook Plus` and the bare product name `Open Notebook` → `Open notebook+`);
    README product name. Upstream `lfnovo/open-notebook` references untouched.
  - **GitHub repo unchanged:** stays `Antman1526/open-notebook-Plus` (an interim
    rename to `open-notebook` was reverted on request).
  - **Gotcha fixed:** `+` is a regex metacharacter — `CitationPill.test.tsx`'s
    `getByText(/…notebook+…/)` had to be escaped (`\+`). Frontend 183/183 green.

- **🛠️ v0.8.65e — Fix desktop app "Unable to Connect to API Server" (symlinked-bundle patch failure)**
  - **Symptom:** the freshly-built `.dmg` showed *"Unable to Connect to API Server
    — API config endpoint returned status 500"* even though the API was healthy.
  - **Root cause:** PyInstaller 6.x's macOS BUNDLE step relocates the Next.js
    frontend to `Contents/Resources/frontend` (real files) and leaves
    `Contents/Frameworks/frontend/{server.js,.next,package.json,public}` as
    symlinks INTO Resources. The launcher passes the Frameworks path to
    `next_rewrites_patcher`, which copied it with `copytree(symlinks=True)` —
    reproducing the symlinks in `~/.open-notebook-plus/frontend-runtime` where
    they **dangle** (they point `../../Resources` relative to the new location).
    The patcher then found no `server.js`/`.next` manifests, couldn't replace the
    build-time-baked `localhost:5055` with the launcher's dynamic API port, and
    the frontend proxied `/api/*` to a dead `:5055` → `ECONNREFUSED` →
    `/api/config` 500.
  - **Fix (`desktop/next_rewrites_patcher.py`):** `patch_rewrites_for_api_port`
    now detects a symlinked `server.js` and operates on the **resolved real dir**
    (`Resources/frontend`, which has all real files incl `node_modules`). No-op
    for the non-symlinked dev/Windows case.
  - **Test:** `desktop/tests/test_next_rewrites_patcher.py` — reproduces the
    symlinked-bundle shape and asserts the runtime copy gets a REAL `server.js`
    and all 3 rewrite targets get the dynamic port (not `5055`); plus the
    real-dir no-op path.
  - *Deferred note:* `make build-mac-test` pipes pytest through `| tail -3`, so it
    never gates on failures (4 pre-existing desktop-test failures slip through).
    Flagged for a follow-up; not changed here to avoid scope creep on the app fix.

- **🩹 v0.8.65d — Decouple web_search from MCP/DB failures (found via an end-to-end test run)**
  - **Bug:** in `bind_mcp_and_run_tool_loop`, MCP tool resolution (which hits
    SurrealDB via `list_enabled_servers → repo_query`), the native `web_search`
    binding, and `model.bind_tools` shared ONE try/except. So a DB error during
    MCP server lookup would silently drop `web_search` too — even though
    web_search is DB-independent (v0.8.64). Latent in normal operation (DB is up)
    but a real robustness gap.
  - **Fix:** split into three independent steps, each fail-soft on its own — an
    MCP-resolve failure no longer disables web search, and a web_search build
    failure no longer disables MCP tools; a `bind_tools` failure still degrades
    both (the model can't call any tool). Logged at DEBUG per the silent-except
    convention.
  - **End-to-end verification:** drove the real tool loop with a local LLM
    (Ollama `llama3.1:8b`) bound to the real `web_search` tool against live
    Serper — the model called `web_search`, got real results, and produced a
    URL-cited answer ("Python 3.14"). Confirms the full LLM → web_search →
    provider → cited-answer path works, including on a local model.
  - **Test:** `tests/test_v0_8_64_web_search.py::test_loop_binds_web_search_even_when_mcp_resolve_fails`
    (web_search still binds when `_resolve_chat_tools` raises) → file now 35 tests.

- **📄 v0.8.65c — Docs + deploy: private localhost SearXNG for web search**
  - **Problem:** v0.8.65 confirmed live that every public SearXNG mirror blocks the
    JSON API (403/418/429), so the keyless SearXNG path only works against a
    self-hosted instance. This ships that instance + the guide.
  - **Deploy (`deploy/searxng-private/`):** ready-to-run `docker-compose.yml` (binds
    `127.0.0.1:8889` only) + `searxng/settings.yml` with `formats: [html, json]`
    (the actual fix — enables the JSON API) and `limiter: false` (safe for a
    localhost-bound instance). `secret_key` is a **placeholder** with an
    `openssl rand -hex 32` instruction — no real secret committed. `docker compose
    config` validated.
  - **Guide (`docs/5-CONFIGURATION/private-searxng-web-search.md`):** adapted to ONP —
    primary path is the native `web_search` tool (`SEARXNG_BASE_URL` in `.env`),
    with the Kindly Web Search MCP + Claude Code / Cursor / Antigravity stdio
    sections kept for other tools. Linked from the Configuration index.
  - **Doc gap closed:** `onp-env-reference.md` now documents the
    `SERPER_API_KEY` / `TAVILY_API_KEY` / `SEARXNG_BASE_URL` / `ONP_WEB_SEARCH_*`
    vars (previously only in `.env.example`).

- **🩹 v0.8.65b — Web-search audit fixes: total-budget guard + tool-calling hint**
  - **Latency guard (audit fix):** the failover chain could block a chat turn for
    up to ~70s if SearXNG instances *hang* (vs the fast 429s) — and a slow early
    instance could starve a fast later one, since the chat loop's per-tool-call
    timeout (30s) would hard-kill it mid-attempt. `run_web_search` now enforces a
    total wall-clock budget (`ONP_WEB_SEARCH_TOTAL_BUDGET_SEC`, default 25s, under
    the 30s loop cap) and passes `min(per-attempt timeout, remaining budget)` as a
    PER-REQUEST timeout, so the chain self-bounds gracefully and still reaches a
    fast instance. Live-verified: auto→Serper 1.6s; forced-SearXNG fails across
    all 5 instances in 2.9s.
  - **Deferred `d` follow-up — capability hint:** the `McpToolPicker` web_search
    row now carries a static help line ("Web search needs a chat model that
    supports tool calling — most cloud models do; many small local models do
    not"), so a user whose local model silently can't tool-call understands why
    search isn't firing (it isn't a config bug). i18n via `defaultValue`.
  - **Test:** +3 (total-budget parse/clamp + per-request-timeout wiring backend;
    +2 frontend hint show/hide). `tests/test_v0_8_64_web_search.py` → 33;
    `McpToolPicker.test.tsx` → 13.

- **🔁 v0.8.65 — Web-search failover chain + `web_search` visible in the chat MCP picker**
  - **Failover (`open_notebook/tools/web_search.py`):** `run_web_search` now walks
    an ordered *chain* of attempts instead of a single provider. `SEARXNG_BASE_URL`
    accepts a comma/space-separated list of instances; an attempt that errors
    (429/403/timeout/connection) falls through to the next — SearXNG URL-by-URL,
    then across providers (Serper→Tavily→SearXNG) on the auto path. The happy
    path still stops at the first provider (no extra API spend), and a paid
    provider returning a *legitimate* empty 2xx is accepted rather than cascading
    to the next paid provider (protects limited Tavily quota). A stale
    `ONP_WEB_SEARCH_PROVIDER` override is still ignored → falls back to auto.
    Provider keys are never logged (only provider name + instance URL + error).
  - **Real-world note:** live-tested 5 public SearXNG instances — *all* reject the
    JSON API (429/403/418), confirming the well-known "public mirrors block
    `format=json`" reality. The chain degrades gracefully to empty; use a
    self-hosted SearXNG (or rely on Serper/Tavily) for keyless search. `.env.example`
    documents the multi-URL syntax + the caveat.
  - **Picker UI (deferred `d` from v0.8.64):** the built-in `web_search` tool now
    shows as a synthetic, toggleable row in the chat `McpToolPicker` (with the
    active provider label), so users can SEE it's on and untick it for a turn —
    backed by a new `GET /api/mcp/web-search` (`{enabled, provider, tool_name}`,
    provider is a label, never a key) + `useWebSearchStatus()`. The picker now
    renders even with zero MCP servers when web search is on, and counts it in
    the `N/total tools` chip. `disabled_mcp_servers` already excludes `web_search`
    (v0.8.64), so the toggle needed no chat-loop change.
  - **Test:** +7 backend failover/chain tests + a `/api/mcp/web-search` endpoint
    test (`tests/test_v0_8_64_web_search.py`, 31 total); +6 frontend picker tests
    (`McpToolPicker.test.tsx`, 11 total). Live-verified the SearXNG failover walks
    all 5 instances in order.

- **🔍 v0.8.64 — Native env-keyed web search for chat (Serper / Tavily / SearXNG)**
  - **Why:** the only web-search path in this fork was a user-stood-up MCP
    server (the curated SearXNG/Crawl4AI recommendations). Users coming from
    upstream tools expect to drop a provider API key into `.env` and get search.
    `SERPER_API_KEY` / `TAVILY_API_KEY` / `SEARXNG_BASE_URL` were referenced
    nowhere in the codebase — so they did nothing.
  - **Feature:** new `open_notebook/tools/web_search.py` exposes a built-in
    `web_search` tool bound into the chat tool loop. Provider auto-selected from
    whichever key/URL is set (precedence Serper > Tavily > SearXNG, override via
    `ONP_WEB_SEARCH_PROVIDER`). Results render as the same citation pills as MCP
    tool results (shared `{index,name,args,text,blocks}` capture shape).
  - **Opt-in / default-off:** the tool only exists when a provider is
    configured — no key ⇒ tool never bound ⇒ **zero behaviour change**. Also
    respects the per-request MCP picker (disable `web_search` to turn it off for
    a turn). Covers both chat surfaces (source_chat reuses the shared loop).
  - **Safety:** all I/O via `httpx.AsyncClient` (no event-loop block);
    best-effort (any provider error logs at WARNING and returns no results — the
    turn never crashes); the API key is sent only to the provider endpoint and
    **never logged** (provider name + error text only). Defensive parsing tolerates
    provider JSON-shape changes. Tunables `ONP_WEB_SEARCH_MAX_RESULTS` (1-20) /
    `ONP_WEB_SEARCH_TIMEOUT_SEC` (1-60).
  - **Prompt nudge (so the LLM actually uses it):** `prompts/chat/system.jinja`
    + `prompts/source_chat/system.jinja` now tell the model the built-in
    `web_search` tool may exist and to call it for current/open-web questions,
    and the citation section was generalised from "MCP tool" to "external tool"
    so `web_search` results get the same `[mcp:N]` pill (they share one
    per-turn capture counter). source_chat previously named no tools at all
    despite binding them — fixed. Verified both templates still render. The
    section heading was renamed "MCP TOOL CITATIONS" → "EXTERNAL TOOL
    CITATIONS"; `tests/test_phase4_citation_rendering.py` updated to assert the
    new heading + that `web_search` is named in the prompt.
  - **Test-isolation fix (caught by the new keys):** adding real keys to `.env`
    made conftest's `load_dotenv` enable `web_search` for the whole suite,
    flipping v0.8.56's "no outcome when no tools bound" assertion (the tool was
    now bound). `tests/conftest.py` gained an autouse fixture stripping all six
    web-search env vars per test, so the suite is deterministic regardless of
    the developer's `.env` and never makes accidental live web calls; web-search
    tests opt in explicitly.
  - **Test:** `tests/test_v0_8_64_web_search.py` (23 tests — provider detection +
    precedence + override, per-provider request/parse with mocked httpx, blank/
    no-key/error/malformed → empty, formatting, tool-builder capture, and loop
    integration: bound when key set / absent without key / absent when disabled
    by the picker). Live-verified against the real Serper + Tavily APIs (both
    return parsed results); public SearXNG `priv.au` rate-limited the JSON API
    (429 → graceful empty, as designed). `.env.example` documents the new block.

- **🎨 v0.8.63 — Interactive privacy redaction-review sheet + cloud-consent bypass**
  - The On-device privacy badge is now **clickable** → a review popover that
    lists the detected category labels + an explanation, and (in notebook chat)
    offers a **"Re-ask allowing cloud"** action: explicit user consent that
    re-sends the question with the fail-closed gate bypassed for that one turn.
  - **Backend bypass (least-privilege):** new `ExecuteChatRequest.bypass_privacy_gate`
    (**default False** — gate stays active), threaded request → chat state →
    `ThreadState` → `provision_langchain_chat_model(privacy_gate_bypass=…)`,
    which then skips the gate + classifier for that turn and **logs the bypass**
    for auditability. Mirrors the v0.8.42 `disabled_mcp_servers` per-request
    plumbing. Set True ONLY by the deliberate "Re-ask allowing cloud" action.
  - **Frontend:** `ChatMessagePrivacyBadge` became a Popover (categories +
    explanation + optional re-ask button); `ChatPanel` gained `onReaskAllowCloud`
    (finds the preceding user question and re-sends it); `ChatColumn` wires it to
    `useNotebookChat.sendMessage(text, undefined, /*bypass*/ true)`. Source chat
    omits the handler → review-only popover there.
  - **Test:** `tests/test_v0_8_63_privacy_bypass.py` (default-False contract +
    cross-layer source guards) and the rewritten `ChatMessagePrivacyBadge.test.tsx`
    (popover, categories, re-ask present/calls/absent). Backend privacy/gate
    suite + chat/source/ChatColumn vitest re-run green.

- **🎨 v0.8.62 — Agent-FSM "needs input"/"truncated" chip in the chat UI (Phase 5.3c UI)**
  - Surfaces the v0.8.60 `agent_state`: a `❓ Needs your input` chip (when the
    model declared `clarify` — it paused to ask the user) or `✂ Truncated`
    chip (hit the tool-iteration cap) next to the AI message, via
    `ChatMessageAgentStateBadge` reading the cached `done`-event state. Renders
    nothing for `complete`/null → no chrome when `ONP_AGENT_FSM` is off
    (default). Test: `ChatMessageAgentStateBadge.test.tsx` (4 cases).

- **🎨 v0.8.61 — "On-device" privacy badge in the chat UI (Phase 5.2c frontend)**
  - The visible payoff of the v0.8.58 backend: a small `🛡 On-device` chip
    renders next to an AI message when the fail-closed privacy gate kept that
    turn on the local model — so the user SEES that their sensitive content
    was protected instead of silently sent to cloud. The tooltip lists the
    detected category labels (e.g. "email, person_name") — labels only, never
    the matched values.
  - New `frontend/src/components/chat/ChatMessagePrivacyBadge.tsx`, mirroring
    the v0.8.35c provider badge: reads `privacy_gated`/`privacy_categories`
    from the same TanStack Query cache entry that `useNotebookChat` stashes on
    the `/chat/stream` `done` event; mounted next to the provider badge in
    `ChatPanel`. The `chat.ts` stream/response types + the done-event caching
    were extended for `privacy_gated`/`privacy_categories`/`agent_state`.
    Renders nothing unless `privacy_gated === true` → zero change to existing
    messages.
  - **Test:** `ChatMessagePrivacyBadge.test.tsx` (4 cases) green; the full
    chat/source component suite (40 tests) re-runs green.
  - **Follow-up:** an interactive redaction-review sheet (edit/approve before
    a cloud resend) and surfacing `agent_state="clarify"` in the UI are the
    remaining 5.2c/5.3c UI polish.

- **✨ v0.8.60 — Agent-FSM in the chat tool loop (Phase 5.3c-full)**
  - Completes the agent-FSM thread. When `ONP_AGENT_FSM` is on, the chat MCP
    tool loop (`bind_mcp_and_run_tool_loop`) tells the model it MAY end its
    turn with `<state>complete</state>` / `<state>clarify</state>`, then
    classifies the terminal state from the final message and surfaces it as
    `agent_state` on `ExecuteChatResponse` + the `/chat/stream` `done` event.
    The valuable signal is **clarify** — a model that pauses to ask the user a
    question is now visible to the client as `agent_state="clarify"` instead
    of being indistinguishable from a finished answer; `truncated` reuses the
    v0.8.56 cap detection.
  - **Deliberately lightweight + low-risk.** The chat loop is a tool-calling
    loop (not a plan-execute agent), so we do NOT drive/redesign the loop —
    it still terminates exactly as before (model stops calling tools, or the
    `max_iterations` backstop). The FSM only adds a gated prompt hint + a
    terminal-state *classification* (`agent_fsm.parse_state`, tolerant → a
    missing/garbled tag falls back to complete/truncated). A full plan-execute
    agent (todo plan + anti-hallucinated-done) would be a separate graph, not a
    retrofit of chat.
  - **Safety:** **default `ONP_AGENT_FSM` off → zero change** (no `<state>`
    injection, `agent_state` None, response shape stable). The terminal-state
    plumbing reuses the proven v0.8.1/v0.8.58 path; a new `agent_state_out`
    out-param keeps `bind_mcp_and_run_tool_loop`'s signature back-compatible
    (source_chat caller unaffected).
  - **Test:** `tests/test_v0_8_60_agent_fsm_tool_loop.py` — clarify/complete
    classification from `<state>` tags, complete when no tag, truncated on
    cap, and FSM-off → no injection + untouched out-param. Env doc:
    `ONP_AGENT_FSM` now also affects the chat tool loop.

- **🔒 v0.8.59 — Reuse the chat sidecar as the PII classifier (Phase 5.2b-2)**
  - The v0.8.57 model-backed PII layer needed a separately-configured
    `ONP_PRIVACY_CLASSIFIER_URL`. Now setting it to `auto` (aliases `sidecar`
    / `chat-sidecar` / `local`) resolves to the running local chat sidecar
    (`OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL`, same OpenAI-compatible
    `/chat/completions` shape) — so the model PII layer works without
    provisioning a second model. Explicit opt-in (we never silently point at
    the sidecar); resolves to no-classifier if the sidecar URL is also unset.
  - **Test:** `tests/test_privacy_classifier.py` — explicit-URL passthrough,
    `auto`/`sidecar`/… → sidecar URL, `auto` without a sidecar → None, unset →
    None. Env doc updated.

- **🔒 v0.8.58 — Surface the privacy-gate decision in the chat response (5.2c backend)**
  - When the gate reroutes a turn cloud→local for privacy, the client now
    learns WHY: `ExecuteChatResponse` (and the `/chat/stream` `done` event)
    gain `privacy_gated: bool` + `privacy_categories: [str]`. Previously the
    reroute showed only as `selected_provider="local"` (indistinguishable from
    ordinary size/health routing) plus a server-side log/metric.
  - **Plumbing** mirrors the v0.8.1 `selected_provider` path exactly:
    `apply_privacy_gate` gained `findings_out` (populated with the acted-on
    category LABELS); `provision.py` copies them into `selection_out`
    (`privacy_gated`/`privacy_categories`); the chat-graph node returns them in
    `ThreadState`; the router reads `result.get(...)` into the response (both
    `/chat/execute` and the stream `done` event, incl. the Pydantic-state
    fallback).
  - **Safety:** exposes only category LABELS (e.g. `email`, `person_name`) —
    **never the matched secret values** — so the response/logs can't re-leak
    what the gate caught. None when the gate didn't act → no behavior change.
    This is the backend foundation for the 5.2c redaction-review UI (frontend
    follows).
  - **Test:** `tests/test_v0_8_58_privacy_response.py` (response-model shape +
    cross-layer wiring + label-only safety guards) and new `findings_out` cases
    in `tests/test_privacy_classifier.py` (populated on act/block, empty on
    passthrough, model categories included).

- **🔒 v0.8.57 — Model-backed PII layer for the privacy gate (Phase 5.2b-1)**
  - Starts the "big" privacy-classifier item. The v0.8.51 gate catches
    *structured* secrets (keys, SSNs, cards, emails) via regex; this adds an
    OPTIONAL model layer for *unstructured* PII — names, postal addresses,
    health/financial details in prose — that regex can't match.
  - **Reframed** from the design doc's "bundle a ~2.8 GB GGUF" to a **pluggable
    local OpenAI-compatible endpoint** (`ONP_PRIVACY_CLASSIFIER_URL`): BYO model
    or reuse the chat sidecar. Leaner, no giant bundle, fits the local-first
    ethos. New `open_notebook/ai/privacy_classifier.py:classify_via_model_async`
    POSTs the text with a PII-classification prompt and tolerantly parses a
    JSON category array.
  - **Architecture:** the classifier call is **async** (`httpx.AsyncClient`) and
    lives in `provision.py` — NOT inside the sync `apply_privacy_gate` — so it
    can't block the event loop (the sync-I/O-in-async bug family). The gate
    gained an `extra_findings` param; findings are **UNIONed** with the regex
    floor, so the model layer can only ever catch MORE — it never weakens the
    fail-closed regex guarantee. The classifier runs only when the gate is on
    AND the turn is cloud-bound; otherwise zero cost.
  - **Safety:** **default `ONP_PRIVACY_CLASSIFIER_URL` unset → regex-only,
    exactly the v0.8.51 behaviour.** Best-effort: any failure (unconfigured,
    endpoint down, malformed response, timeout) → `[]`, never blocks chat.
    Bounded by `ONP_PRIVACY_CLASSIFIER_TIMEOUT_SEC` (default 5s).
  - **Test:** `tests/test_privacy_classifier.py` — tolerant category parsing
    (clean/prose/fenced/normalized/invalid), `classify_via_model_async`
    (unconfigured/empty/parsed/error/no-choices, all httpx-mocked), and gate
    integration (model finding reroutes cloud→local even when regex-clean;
    regex+model union). Existing v0.8.51 gate tests unchanged (no URL → regex).
  - **Next:** 5.2b-2 (wire a default classifier endpoint in the launcher) and
    5.2c (redaction-review UI). See the design doc.

- **🔭 v0.8.56 — Surface chat tool-loop truncation (Phase 5.3c observability slice)**
  - The chat MCP tool loop (`bind_mcp_and_run_tool_loop`) stops either when the
    model emits no more tool calls (natural completion) OR when it hits the
    `max_iterations=4` backstop. The latter means the model still wanted to
    call tools but was **cut off** — the tool budget, not the model, limited
    the turn, so the answer is likely incomplete. This was **completely
    silent**.
  - **Fix:** after the loop (only when MCP tools were actually bound), classify
    the terminal state and emit `onp_agent_tool_loop_outcomes_total{outcome}`
    (`complete` | `truncated`) plus a `WARNING` log on truncation. A rising
    `truncated` ratio tells operators the iteration cap / `ONP_MCP_TOOL_TIMEOUT_SEC`
    may be too tight. Uses the agent-FSM (v0.8.52) terminal vocabulary.
  - **Safety:** pure observation — **no behavior change, no gate, no signature
    change**; the loop terminates exactly as before. The full FSM loop *driver*
    (model-declared `<state>` + anti-hallucinated-done) remains the staged
    5.3c-full.
  - **Test:** `tests/test_v0_8_56_tool_loop_outcome.py` — truncated when the
    loop hits the cap with pending tool calls; complete on natural stop;
    complete when tools are bound but unused; no outcome recorded when no tools
    are bound. (Mirrors the v0.8.35e mock harness.)

- **✨ v0.8.55 — Confidence-aware memory (Phase 5.1c)**
  - The extract prompt already asks the model for a `confidence` (0.0-1.0) on
    each fact/preference, but it was **ignored**: `apply_tool_call`
    (desktop/memory/writer.py) never read it and `surreal_store.insert` always
    persisted `1.0`. Now we use it:
    - **Floor:** candidates below `ONP_MEMORY_CONFIDENCE_FLOOR` are dropped
      before the write (filters out the model's own low-confidence /
      speculative "facts"). A missing or garbled score is treated as `1.0` —
      we never drop a fact just because the model omitted the number.
    - **Persist:** the real score is carried in metadata and written through
      to the `confidence` column (`surreal_store.insert` now reads it from
      metadata, mirroring how `scope` is carried), enabling confidence-based
      retention/recall ranking in future.
  - **Safety:** **default floor `0.0` → keep everything (unchanged).** Invalid
    / out-of-range floor values fall back to `0.0`. mem0 already performs its
    own ADD/UPDATE/NOOP dedup on write, so this slice focuses on the scoring
    half of 5.1c; confidence-weighted eviction in `prune` is a future
    refinement (today's retention is recency-based, v0.8.50).
  - **Test:** `tests/test_memory_confidence.py` — coerce/clamp, floor parsing,
    default-keeps-all, floor-drops-low, missing-score-not-dropped,
    metadata persistence, and `surreal_store.insert` reading confidence from
    metadata (+ defaulting to 1.0 when absent). Writer/store/retention/batching
    clusters re-run green.

- **✨ v0.8.54 — Batched memory extraction (Phase 5.1b)**
  - The memory writer (`desktop/memory/writer.py`) ran one extractor LLM call
    **per chat turn**. With `ONP_MEMORY_BATCH_TURNS=N>1` the worker now buffers
    turns per session and runs ONE extraction over the combined transcript
    every N turns — collapsing O(turns) extraction calls to O(turns/N) and
    giving the model whole-conversation context (better cross-turn facts,
    fewer near-duplicate writes for the v0.8.50 retention ceiling to prune).
  - **Mechanics:** a process-local, lock-guarded per-session buffer in the
    long-lived worker; `extract_turn` buffers and flushes a combined
    `render_extract_user_batch(turns)` extraction at the threshold;
    `summarize_session` calls the new `flush_session_buffer` FIRST so the
    session's tail isn't stranded below the threshold when the conversation
    ends. The extract→parse→apply→prune body was refactored into a shared
    `_extract_and_apply` so the single-turn and batched paths can't drift.
  - **Safety:** **default `ONP_MEMORY_BATCH_TURNS=1` → byte-for-byte the prior
    per-turn behaviour** (the buffer code path is never entered). Batching is
    opt-in. A worker restart drops any un-flushed tail — acceptable for
    best-effort memory and only relevant when batching is enabled. Invalid /
    `<1` values fall back to 1 so a typo can't disable extraction.
  - **Test:** `tests/test_memory_batching.py` — buffering until threshold, the
    combined transcript carrying all turns, session-end flush draining the
    buffer, per-session isolation, facts written on flush, empty-flush no-op,
    env parsing, and the unchanged default path. Existing writer/retention
    tests re-run green against the refactor.
  - **Note:** this batches the *trigger*; the multi-turn extraction *prompt
    quality* is an operator's opt-in concern (the shared
    `EXTRACT_TURN_SYSTEM_PROMPT` is unchanged). Scoring/dedup of candidates is
    the staged Phase 5.1c.

- **✨ v0.8.53 — Agent-FSM completion gate on the ask graph (Phase 5.3b)**
  - Gives the v0.8.52 FSM core its first real consumer. The `ask` graph
    (open_notebook/graphs/ask.py) fans out the strategy's searches and then
    synthesizes a final answer. When **none** of the searches return grounded
    content, asking the LLM to "synthesize" means writing from an empty
    context — exactly where weak local models confidently hallucinate.
  - **Fix:** when `ONP_AGENT_FSM` is on, `write_final_answer` now checks for
    grounded answers first; if there are none it declares `AgentState.CLARIFY`
    and returns a "refine your question / add sources" message **without
    calling the synthesis LLM**, rather than emitting an ungrounded answer.
    Otherwise it synthesizes as before and tags `agent_state="complete"`.
  - **Streaming-safe:** `api/routers/search.py` captures `final_answer` from
    this node's `on_chain_end` terminal event (its documented "fallback for
    clients that ignore deltas"), so the clarify message is delivered even
    though that path streams no token deltas. No new graph node, no edge
    changes.
  - **Scope:** the `ask` DAG doesn't loop, so this adopts the FSM's *state
    vocabulary* (CLARIFY/COMPLETE); the FSM's loop *driver* + backstop is for
    the chat tool loop (5.3c). Default **off** via `ONP_AGENT_FSM` → unchanged
    behaviour; aliases `1`/`true`/`yes`.
  - **Test:** `tests/test_agent_fsm_ask_gate.py` — 5 cases (clarify on
    empty/whitespace-only answers with the LLM NOT called; synthesis +
    complete on grounded answers; flag-off synthesizes regardless with no
    tag; flag parsing). Docs: `ONP_AGENT_FSM` in the env reference + design
    doc 5.3b status.

- **✨ v0.8.52 — Agent-loop state-machine core (Phase 5.3a)**
  - **What:** new pure module `open_notebook/graphs/agent_fsm.py` — the
    dependency-free core of the agent reliability FSM. Weak local models tend
    to claim "done" with work still open, or loop without progress; the FSM
    gives an explicit lifecycle (`TODO → WORKING → COMPLETE`, with `CLARIFY`
    for user input and `FAILED` terminal) and enforces two guarantees:
    - **Anti-hallucinated-done:** a declared `COMPLETE` with any open todo
      item is downgraded to `WORKING` — completion is only honored when every
      todo is satisfied.
    - **Backstop:** a `max_steps` ceiling force-terminates so a model that
      never declares completion can't loop forever.
  - **API:** `AgentState`, `can_transition`/`is_terminal`, `parse_state` (a
    tolerant parser for a model-declared `<state>…</state>` tag or `STATE: …`
    line, last-wins), `TodoItem`/`completion_satisfied`, and a pure
    `AgentLoop.advance(declared, todos)` driver (no I/O).
  - **Scope:** this is the CORE only (5.3a). Wiring it into the `ask` graph and
    the chat MCP tool loop behind `ONP_AGENT_FSM` (default off) is 5.3b/c —
    the `ask` graph (a fixed strategy→fan-out→synthesize DAG) and the tool
    loop are the wiring targets; see the design doc. Shipping the tested core
    first keeps each step independently reviewable.
  - **Test:** `tests/test_agent_fsm.py` — 27 cases (transition table incl.
    illegal/terminal, parser tag/line/last-wins/invalid/empty, completion
    validation, and the driver: default-to-working, honor/downgrade COMPLETE,
    backstop→COMPLETE/FAILED, terminal idempotence, illegal-declared fallback,
    CLARIFY round-trip).

- **🔒 v0.8.51 — Fail-closed privacy gate before cloud routing (Phase 5.2a)**
  - **What:** the smart router can route a chat turn to a CLOUD provider when
    the local sidecar is unhealthy or the content is too big for the local
    context window — shipping the turn's text (which may hold pasted API keys,
    SSNs, card numbers, …) off-device. New `open_notebook/ai/privacy_gate.py`
    runs a fast, high-confidence **structured-secret** detector over the
    outbound content and, when enabled and the router chose cloud, **fails
    closed**: reroutes the turn to the local model so the secret never leaves
    the machine — or, if no local model is configured, blocks the request
    (clear HTTP 422) rather than leaking.
  - **Detector** (`detect_sensitive`): emails, US SSNs, AWS/GitHub/OpenAI/
    Google/Slack tokens, PEM private-key blocks, `secret=`/`password:`-style
    assignments, and Luhn-validated credit-card numbers (Luhn check rejects
    arbitrary 16-digit IDs/order numbers to keep false positives low). Returns
    category labels so logs + the routing reason say WHAT was caught.
  - **Seam:** one wrapped call (`apply_privacy_gate`) at the `pick_provider`
    call site in `provision.py:provision_langchain_chat_model`, before the log
    + `selected_provider` labeling so both reflect the gated decision.
  - **Config / safety:** `ONP_PRIVACY_GATE` — **default off** (zero change to
    routing). On → `on`/`1`/`true`/`yes`/`local`. Observability:
    `onp_privacy_gate_redirects_total{outcome="local"|"blocked"}` (a rising
    counter is a security-relevant signal).
  - **Scope (honest):** gates the **auto-route cloud-fallback** path only;
    catches *structured* secrets — unstructured PII (names/addresses in prose)
    needs the local classifier model planned for Phase 5.2b. Gating
    explicit-cloud-default turns is a documented follow-up.
  - **Test:** `tests/test_privacy_gate.py` — 33 cases (every detector category,
    clean text, Luhn accept/reject, dedup/sort; gate off/on, cloud+sensitive→
    local, cloud+clean→passthrough, already-local→passthrough, no-local→blocks,
    env-driven mode). Docs: `ONP_PRIVACY_GATE` in the env reference + design doc
    updated.

- **📐 v0.8.50 — Phase 5 design doc + memory retention ceiling (Phase 5.1a)**
  - **Design:** new `docs/7-DEVELOPMENT/phase-5-advanced-memory.md` — a full
    design + phased implementation plan for the three deferred Phase-5
    capabilities (distill-and-score memory pipeline, fail-closed privacy
    filter before cloud fallback, agent-loop state machine), each mapped to
    concrete files/seams in this codebase, sequenced by payoff/risk, and
    broken into independently shippable `v0.8.NN` sub-phases.
  - **Implemented Phase 5.1a — closes Finding #3 (unbounded memory growth).**
    The `memory_fact`/`memory_preference`/`memory_episode` tables grew without
    bound: recall caps RESULTS, never ROWS, and the semantic path does an O(N)
    full-table cosine scan every turn. Added a per-table recency ceiling:
    - `SurrealMemoryStore.prune(keep_per_table)` + `.count(table)`
      (desktop/memory/surreal_store.py). `prune` keeps the newest N rows by
      `created_at` and deletes the rest, using a deliberate select-then-delete
      shape (`SELECT id, created_at … ORDER BY created_at DESC` → slice in
      Python → `DELETE … WHERE id IN $ids`) that sidesteps the v0.8.19/v0.8.30
      "missing order idiom" trap and batches large eviction lists (1000/stmt).
    - `desktop/memory/writer.py:prune_memories()` — best-effort wrapper
      (never raises; a retention failure can't break a memory write). Invoked
      at **session end** in `summarize_session` (always — the natural,
      infrequent boundary) and **per turn** in `extract_turn` behind a cheap
      high-water `count()` gate (`keep × 1.5`), so a user who never deletes
      sessions still stays bounded while the common path pays nothing.
    - Config: `ONP_MEMORY_KEEP_PER_TABLE` (default 500).
  - **Safety:** purely additive to the write path; pruning is best-effort and
    recency-based (no semantic loss of recent memories). Also made
    `surreal_store.py`'s mem0 `VectorStoreBase` import defensive (mirrors
    `client.py`) so the store's pure logic is unit-testable in the mem0-less
    dev/test venv — zero production change (the worker venv ships mem0).
  - **Test:** `tests/test_memory_retention.py` — 17 cases (prune keeps
    newest/deletes rest, no-op under ceiling, query-shape guard, large-list
    batching, count; writer wrapper no-op/high-water/never-raises; env
    parsing).
  - **Docs:** `ONP_MEMORY_KEEP_PER_TABLE` + the v0.8.49
    `ONP_MEMORY_RECALL_EPISODES` flag documented in
    `docs/5-CONFIGURATION/onp-env-reference.md`.
  - **Staged (designed, not yet built):** Phase 5.1b batch buffering, 5.1c
    scoring/dedup, 5.2 privacy filter, 5.3 agent-loop FSM — see the design doc.

- **✨ v0.8.49 — Wire episode recall (the missing read half of v0.7.70)**
  - The memory layer has three tables — `memory_fact`, `memory_preference`,
    `memory_episode` (routed by metadata `kind` in
    `desktop/memory/surreal_store.py`). Facts and preferences are recalled
    into every chat / source-chat system prompt. **Episodes never were.**
    `summarize_session` (v0.7.70) fires an ~800-token LLM call on every
    session delete to distill the conversation into one `memory_episode`
    row — but a codebase-wide search confirmed NOTHING ever read those
    rows back (recall only queried fact + preference; the Gmail digest
    reads the unrelated podcast `episode` table). The whole-conversation
    memory layer was write-only dead data.
  - **Fix:** wired the read path. `recall_recent_memory` and
    `recall_relevant_memory` (open_notebook/utils/memory_recall.py) now
    also pull `memory_episode` — recency via
    `SELECT text, created_at … ORDER BY created_at DESC` and semantic via
    the same `vector::similarity::cosine` idiom, both following the
    v0.8.30 "order-idiom" projection rule. `render_memory_block` renders a
    sanitized (v0.8.47) `## Earlier conversation summaries` section,
    ordered LAST (coarsest / least authoritative). The recall dict gains
    an `episodes` key (always present for shape stability).
  - **Scope/safety:** purely additive — the write side (`summarize_session`)
    is untouched; this only makes the already-written episodes usable.
    Capped tight at `_MAX_EPISODES = 2` (summaries are long) and gated by
    `ONP_MEMORY_RECALL_EPISODES` — **default ON** for parity with
    facts/preferences (already recalled by default in this single-user
    local app), set `=0`/`false`/`off` to suppress (privacy: stop old
    conversations resurfacing; or reclaim ~1k chars of prompt budget on a
    tiny local model).
  - **Test:** 7 new cases in `tests/test_memory_recall.py` (episode query
    fires + follows the hardened idiom; disable flag suppresses it;
    flag-parsing; render section + ordering; only-episodes renders;
    episodes are sanitized). Updated the v0.8.19/v0.8.30 query-shape test
    for the now-3-query recency path.
  - **Deferred (still Phase 5):** unbounded `memory_*` table growth +
    full-table cosine scan — needs a retention/distillation pass against
    the separate `MEMORY_SURREAL_URL` store, not cleanly testable here.

- **🐛 v0.8.48 — Notebook delete leaked cascade-deleted sessions' checkpoints**
  - Deleting a notebook cascade-deletes its linked `chat_session` rows
    (v0.7.61), but — unlike the single-session delete path, which cleans
    LangGraph checkpoints since v0.7.171 — it never removed those
    sessions' checkpoint THREADS. The blobs leaked **permanently**: the
    `checkpoint_prune` background task uses per-thread retention
    (`ROW_NUMBER() … PARTITION BY thread_id`, keep newest 50), so it only
    trims old snapshots *within* an over-retention thread — an orphaned
    sub-50-checkpoint thread belonging to a deleted session is never
    reached. A user who creates and deletes notebooks accumulates dead
    checkpoint/writes rows in `checkpoints.sqlite` forever. (Surfaced
    while auditing delete-cascade completeness after the v0.8.46d
    session-delete fix.)
  - **Fix (layering-aware):** `Notebook.delete()`
    (open_notebook/domain/notebook.py) now returns the stringified
    cascade-deleted session ids as `deleted_chat_session_ids`. The
    domain layer must not import the chat graph/checkpointer, so the
    notebooks API router (api/routers/notebooks.py) owns the cleanup: a
    new best-effort `_cleanup_checkpoint_threads()` helper calls the same
    `chat_graph.checkpointer.delete_thread` the chat router uses, one
    thread per id, each isolated in its own try/except so one failure
    can't abort the rest or fail the (already-committed) delete.
  - **Test:** `tests/test_v0_8_48_notebook_delete_checkpoint_cleanup.py`
    — behavioral tests for the helper (delete_thread called per session;
    best-effort continuation past a failing thread without raising;
    no-op on empty list / on a checkpointer lacking `delete_thread`)
    plus a two-halves source guard pinning the `deleted_chat_session_ids`
    contract between the domain return and the router read. (The domain
    delete itself is a live-SurrealDB integration path covered by the
    integration suite.)

- **🔒 v0.8.47 — Harden recalled memory against stored prompt injection**
  - `render_memory_block` (open_notebook/utils/memory_recall.py)
    interpolated each recalled fact/preference **verbatim** into the
    chat SYSTEM prompt (`f"- {p['text']}"`). Memory rows are
    auto-extracted by the mem0 WRITE path (v0.7.68) from chat turns —
    **including turns where the user pasted untrusted external content**
    (PDFs, web pages, emails — ONP's core research workflow). A planted
    "fact" containing newlines + a forged `## SYSTEM` header (or
    "ignore prior instructions …") would render on its own lines and
    fabricate a brand-new prompt section that persists across sessions:
    a textbook stored-prompt-injection vector.
  - **Fix:** new `_sanitize_memory_text()` collapses ALL whitespace
    (newlines, tabs, control chars) in each fact to a single space and
    caps length at 600 chars before interpolation. Flattening to one
    line is the core mitigation — the text can no longer start a fresh
    line to forge block-level markdown; whatever survives stays inside
    its `- ` bullet, clearly framed as untrusted "facts learned about
    the user" data. Bullets that sanitize to empty (and now-empty
    sections) are dropped. Deliberately does NOT strip a leading
    `-`/`*` run (would mangle legit facts like "-5°C preferred"; the
    enclosing bullet already prevents a leading `#` from forming a
    heading).
  - **Test:** 8 new cases in `tests/test_memory_recall.py` — sanitizer
    unit tests (flatten newlines/tabs/CR, length cap, empty handling)
    plus end-to-end render assertions that a forged `## SYSTEM` section
    never becomes a standalone heading line and empty bullets/sections
    are dropped.
  - **Audit (memory_recall.py), findings dispositioned:**
    - *Count-query "missing order idiom" (claimed):* **false positive** —
      `SELECT VALUE count() FROM memory_fact GROUP ALL` has no `ORDER
      BY`, so the v0.8.19/v0.8.30 idiom-bug family cannot apply; the
      aggregate returns `[N]` and the code reads `rows[0]` correctly.
    - *Metrics async-safety (claimed):* **false positive** —
      `record_memory_fallthrough` is a `prometheus_client` Counter
      `.inc()`, a synchronous thread-safe non-blocking in-memory op;
      safe to call from async without `await`, and already import-guarded.
    - *Unbounded memory-table growth:* **real, deferred** — the recall
      side caps result COUNT (15 facts / 10 prefs) but the WRITE side
      grows the tables without bound, and the semantic path does a
      full-table cosine scan. This is the Phase-5 "distill-and-score
      memory pipeline" design item, not an inline fix.

- **🐛 v0.8.46d — Session delete 500'd on EVERY call since v0.8.43 (regression)**
  - `DELETE /chat/sessions/{id}` raised `TypeError` → 500 for every
    session delete from v0.8.43 onward. Root cause: the v0.8.43
    `replace_all` that appended
    `disabled_mcp_servers=getattr(session, "disabled_mcp_servers", None)`
    after each `model_override=getattr(session, "model_override", None),`
    in `api/routers/chat.py` was too broad — it ALSO matched the
    fire-and-forget `_fire_memory_summarize_session(...)` call inside
    `delete_session`. That helper's signature only accepts
    `chat_session_id` + `model_override`, so the injected kwarg failed
    at call-binding time, and the handler's `except Exception` re-wrapped
    it as a 500. The four *intended* insertions (inside the
    `ChatSessionResponse` / `ChatSessionWithMessagesResponse`
    constructions) and the two `_fire_memory_extract_turn` calls were
    unaffected.
  - **Fix:** removed the stray kwarg from the summarizer call (the
    session-end summarizer has no use for the per-conversation MCP
    picks) and left a `# v0.8.46d` comment explaining the `replace_all`
    over-match so it isn't reintroduced.
  - **Test:** new `tests/test_v0_8_46d_delete_session_summarize_kwargs.py`
    — (1) a signature contract asserting `_fire_memory_summarize_session`
    does NOT accept `disabled_mcp_servers`, and (2) a behavioral test
    that drives the **real** `delete_session` handler (DB + checkpoint
    deps mocked, summarizer NOT mocked so the call-binding is actually
    exercised) and asserts it returns `SuccessResponse` instead of
    raising. The pre-existing v0.7.171 delete tests are source-text
    guards that never invoke the handler, which is why they couldn't
    catch a call-binding error — this one closes that gap.

- **🐛 v0.8.46c — Fix 10 full-suite test failures the curated subset masked**
  - Running the **entire** backend suite (`uv run pytest tests/`, not the
    curated per-feature subset used during the v0.8.36→v0.8.46 work)
    surfaced 10 failures invisible to the smaller runs. Two distinct
    root causes:
  - **(A) Event-loop pollution → "Event loop is closed" (9 tests).**
    `tests/test_v0_8_1_selected_provider.py` and
    `tests/test_phase3_smart_routing.py` drove their async-under-test
    via `asyncio.get_event_loop().run_until_complete(coro)`. Under
    `asyncio_mode = "auto"` (pytest-asyncio 1.x), an earlier async
    test in the full collection leaves the process-wide "current"
    event loop **closed**; these sync driver tests then inherited it
    and raised `RuntimeError: Event loop is closed` (with a
    "coroutine ... was never awaited" warning). They passed in
    isolation / the curated subset only because no prior test had
    closed the loop. **Fix:** switched the `_run` helpers (and one
    inline call) to `asyncio.run(coro)`, which creates + closes a
    fresh loop per call — immune to the pollution. The inline
    `new_event_loop()` tests (`TestNCtxEnvVarSync`, `TestHealthCacheTTL`)
    were already immune and untouched.
  - **(B) Drifted mock target (1 test).**
    `tests/test_chat_history_cap.py::test_call_model_invokes_trimming`
    patched `chat.provision_langchain_model`, but since **v0.8.0
    Phase 3 Task 12** the chat node's no-model-override path calls the
    smart-route wrapper `provision_langchain_chat_model` instead —
    which invoked the *real* `model_manager.get_defaults()` → a live
    SurrealDB connect → failure. The test broke at v0.8.0 but was
    never in a curated sweep, so only the full run caught it.
    **Fix:** also patch `chat.provision_langchain_chat_model`.
  - **(C) Network in unit tests (speed/robustness).** The v0.8.37
    disabled-path `get_defaults()` toggle-check made two
    disabled-path tests open a live SurrealDB connection (caught, so
    they "passed" — but ~33s each on a connection timeout in a
    DB-less environment). **Fix:** mock `model_manager.get_defaults`
    with an `AsyncMock` returning a stub (`auto_route_enabled=False`)
    in those tests. The 3 disabled-path tests now run in 3.9s total.
  - **No production code changed** — all three are test-only fixes
    (fragile loop driver, stale mock target, missing DB mock). The
    app behavior was correct; the tests had drifted from it and the
    curated subset never exercised the failing ordering.
  - **Lesson:** a green curated subset can hide both test-isolation
    bugs (loop pollution only manifests at full-collection scale) and
    mock-drift (a call site moves to a wrapper the test doesn't
    patch). Periodic full-suite runs are the only thing that catches
    these. (Also fixed two `react-hooks/exhaustive-deps` warnings —
    see v0.8.46b below.)

- **🐛 v0.8.46b — Stale-closure deps in the two chat-send callbacks**
  - The full-suite lint pass flagged `react-hooks/exhaustive-deps` on
    `sendMessage` in both `useNotebookChat` and `useSourceChat`: each
    reads `disabledMcpServers` (v0.8.42/v0.8.44 per-turn MCP picks)
    inside the `useCallback` body but omitted it from the deps array.
    Real stale-closure bug: toggling a server then sending — with no
    other dependency changing — would capture the *previous* disable
    list, so the backend saw the picks from before the last toggle.
  - **Fix:** added `disabledMcpServers` to both deps arrays. Verified
    by ESLint (warnings cleared) + the existing hook/chat tests.

- **🐛 v0.8.46 — Mount the MCP tool picker in chat UI (feature was unreachable)**
  - **Bug:** v0.8.42→v0.8.44b built the per-conversation MCP tool
    picker end-to-end — `<McpToolPicker>` component (with its own 5
    tests), the `disabledMcpServers` + `toggleDisabledMcpServer` state
    on both `useNotebookChat` and `useSourceChat`, the request/state/
    session plumbing, and the persistence migration — **but never
    mounted `<McpToolPicker>` in any actual chat UI.** `ChatPanel`
    (the shared notebook + source chat surface) neither imported it
    nor accepted the toggle props, so the entire feature chain was
    dead code from the user's perspective: no way to open the picker,
    so `disabled_mcp_servers` was always empty in practice.
  - **Fix:**
    - `ChatPanel` gains optional `disabledMcpServers?: string[]` +
      `onToggleMcpServer?: (name) => void` props and renders
      `<McpToolPicker>` on the model-selector row (the picker self-
      hides when there are no enabled MCP servers, so the row
      gracefully collapses to just the model selector for users
      without MCP configured).
    - `ChatColumn` (notebook chat) forwards
      `chat.disabledMcpServers` + `chat.toggleDisabledMcpServer`.
    - `sources/[id]/page.tsx` (source chat) forwards the same from
      `useSourceChat`.
  - **Tests:** `frontend/.../ChatPanel.mcp-picker.test.tsx` — 3 new
    tests as a permanent regression guard for exactly this class of
    gap: picker renders + receives the forwarded disable list/handler
    when `onToggleMcpServer` is provided; picker is absent when the
    prop is omitted; empty array forwarded when `disabledMcpServers`
    is undefined. (Stubs ChatPanel's heavy child tree + the JSDOM
    `scrollIntoView` gap.) Frontend suite: 166/166 across 30 files.
  - **Lesson:** plumbing + component + tests can all be green while
    the feature is still unreachable if nothing wires the component
    into a rendered tree. The new test asserts the *mount*, not just
    the component in isolation.

- **✨ v0.8.39d — Persistent download jobs across API restart**
  - **What:** The GGUF downloader's job registry was in-memory, so an
    API restart mid-download lost the job record — the user had to
    rediscover which model was downloading. v0.8.39d makes interrupted
    downloads survive a restart by reconciling against on-disk
    sidecars, so the Local Models page proactively shows a "Resume"
    button for them. (v0.8.39e already handled the actual resume from
    the `.part` byte offset on the next click; v0.8.39d adds the
    *visibility* so the user doesn't have to remember.)
  - **Mechanism (`open_notebook/local_models/downloader.py`):**
    - Each in-flight download now writes a tiny `{filename}.part.meta`
      JSON sidecar `{job_id, repo_id, filename, bytes_total}` alongside
      the `.part`. The `repo_id` is the key bit — a bare `.part`
      filename tells us the model name but not which HF repo to resume
      from. Written at stream start, refreshed once `bytes_total` is
      known, removed on successful completion (best-effort; a sidecar
      write/remove failure never breaks the download).
    - New `reconcile_jobs(dest_dir)` scans `*.part.meta` sidecars and,
      for any whose (repo_id, filename) isn't already a live job,
      reconstructs a `DownloadJob` with `status="cancelled"` +
      `resume_from_bytes`=current `.part` size. Reusing the existing
      `cancelled` terminal status means the frontend's existing Resume
      affordance (v0.8.39e) lights up with zero new enum handling.
      Prunes orphan sidecars (no surviving `.part`) and corrupt
      sidecars. Idempotent — safe to call on every list request.
  - **API (`api/routers/local_models.py`):** new
    `GET /local-models/downloads` reconciles + returns all known jobs.
    (`GET /downloads/{job_id}` docstring updated to point at it for
    post-restart repopulation.)
  - **Frontend (`DownloadPanel.tsx`):** on mount, queries
    `/local-models/downloads` and seeds `jobByKey` (without clobbering
    any actively-polled job) so a recommendation card whose download
    was interrupted shows "Resume" immediately. Refetch-on-focus
    surfaces newly-reconciled jobs when returning to the tab.
  - **Tests:** `tests/test_v0_8_39d_persistent_jobs.py` — 8 backend
    tests: reconcile rebuilds from sidecar+`.part`, cancelled status +
    resume offset, idempotency, orphan-sidecar prune, corrupt-sidecar
    prune (no crash), skip-live-job, missing-dir, and the list
    endpoint. Plus 1 frontend test (URL-routed mock → Resume button
    seeded from a reconciled job). Downloader regression: 35/35.
    Frontend: 163/163 across 29 files.

- **🐛 v0.8.45 — Log silent metric-increment excepts in studio.py**
  - The bug-hunt sweep (recurring-pattern checklist from CLAUDE.md)
    surfaced 4 best-effort metric blocks in `api/routers/studio.py`
    (`_record_outcome`, two `record_studio_outline_parse_failure`
    sites, `record_studio_single_note_fallback`) using bare
    `except Exception: pass` with NO logging — the last stragglers of
    the v0.8.27→v0.8.35f silent-except family.
  - Fixed: each now `logger.debug(...)` the swallowed exception so a
    broken metrics path is discoverable. DEBUG (not WARNING) because
    these are genuinely best-effort observability increments that must
    never mask the user's actual response — but total silence made
    "why aren't my Studio metrics moving?" undebuggable. The line-770
    `except Exception: _record_outcome("failed"); raise` site was
    already correct (re-raises) and left untouched.
  - The rest of the hunt (sync submit_command in async, LangGraph
    state-shape variance, SSE is_disconnected gaps, reader cancel
    ordering, edge-table direction, delete cascades, str(payload)
    overcount, blocking I/O on the loop) came back **clean** — no new
    instances. Reported explicitly per the standing-workflow
    requirement.

- **✨ v0.8.44b — Source-chat session persistence for MCP picks**
  - **What:** v0.8.44 made source-chat MCP picks per-request (hook-local).
    v0.8.44b persists them on the session row so source-chat picks
    survive page reloads + session switches — full parity with
    notebook chat's v0.8.43. No new migration: source-chat sessions
    share the `chat_session` table, so migration 20 (v0.8.43) already
    provisions the `disabled_mcp_servers` column.
  - **Backend (`api/routers/source_chat.py`):**
    - `UpdateSourceChatSessionRequest` + `SourceChatSessionResponse`
      (and the `WithMessages` subclass) gain the `disabled_mcp_servers`
      field.
    - The update handler switches to `model_dump(exclude_unset=True)`
      semantics so a PATCH that only carries `disabled_mcp_servers`
      doesn't clobber title/model_override (and vice-versa) — mirrors
      the notebook-chat v0.8.43 handler.
    - All 4 response-construction sites (create / list / get / update)
      now echo the persisted picks via `getattr(session, ...)` /
      `session_data.get(...)` (the list path uses `SELECT *` so the
      field is present).
    - **Precedence rule:** the send-message handler resolves the
      effective per-turn disable list — request body wins; a null
      body falls back to the session's persisted picks; an explicit
      `[]` is preserved as "no disables this turn" (`is not None`).
      Resolved in the handler (where `session` is loaded) and passed
      to the SSE generator, which only receives `session_id`.
  - **Frontend:**
    - `UpdateSourceChatSessionRequest` type extended (the session
      type already inherited `disabled_mcp_servers` from
      `BaseChatSession`).
    - `useSourceChat`: hydrates `disabledMcpServers` from the session
      on load (gated on `!updateSessionMutation.isPending` to avoid
      the v0.8.43b clobber race), and `toggleDisabledMcpServer`
      PATCHes via a mutation ref (same forward-ref TDZ-safe pattern
      as the notebook-chat v0.8.43b fix).
  - **Tests:** `tests/test_v0_8_44b_source_chat_persistence.py` — 4
    tests: update-request exclude_unset semantics, response schema
    field exposure, per-request field coexistence, and a precedence-
    rule reproduction (request-wins / null-fallback / empty-list-
    preserved / both-null). Existing source-chat regression
    (`test_source_chat_context_caps`, `test_source_chat_history_cap`)
    + the v0.8.44 suite all pass. Frontend: 162/162 across 29 files.

- **✨ v0.8.44 — Source-chat MCP picker parity**
  - **What:** The v0.8.42/v0.8.43 MCP server disable picker shipped on
    notebook chat only. Source chat (the per-source focused chat surface
    that uses the same `bind_mcp_and_run_tool_loop` helper) was a feature
    gap: users could untick SearXNG on a notebook chat but the same toggle
    didn't exist on a source-focused conversation. v0.8.44 closes the gap.
  - **Backend:**
    - `open_notebook/graphs/source_chat.py:SourceChatState` gains the
      `disabled_mcp_servers: Optional[list[str]]` field (parallel to
      v0.8.42's `ThreadState`).
    - The source-chat node now passes `state.get("disabled_mcp_servers")`
      into `bind_mcp_and_run_tool_loop(exclude_server_names=...)` —
      same filtering semantics as notebook chat (case-insensitive name
      match, blank-string + None handling already covered by the
      v0.8.42 resolver).
    - `api/routers/source_chat.py:SendMessageRequest` accepts the new
      `disabled_mcp_servers` field; the streaming handler forwards it
      to `stream_source_chat_response` (signature updated to accept the
      kwarg) which writes it onto `state_values`.
  - **Frontend:**
    - `SendMessageRequest` (source-chat type) extended with
      `disabled_mcp_servers?: string[]`.
    - `useSourceChat` exposes `disabledMcpServers` state +
      `toggleDisabledMcpServer` callback. The send path passes
      `disabled_mcp_servers: disabledMcpServers.length > 0 ?
      disabledMcpServers : undefined` to the API client.
    - The existing `<McpToolPicker>` component (v0.8.42) is reusable
      as-is — UI consumers can drop it into source-chat surfaces with
      the same prop shape.
  - **Persistence parity (deferred to v0.8.44b):** The v0.8.43 work
    persists notebook-chat picks on the `chat_session` row. Source-chat
    sessions use the same `chat_session` table so the `disabled_mcp_servers`
    column already exists for them — the only missing piece is the
    source-chat router writing through to it on session PATCH and the
    `useSourceChat` hydration effect mirror. Tracked separately.
  - **Tests:**
    - `tests/test_v0_8_44_source_chat_mcp_disable.py` — 3 backend
      tests: `SendMessageRequest` schema (absent / null / empty list /
      list), `SourceChatState` TypedDict declares the field (regression
      guard against accidental rollback), `stream_source_chat_response`
      signature accepts the kwarg with default None (back-compat for
      any pre-v0.8.44 caller).
    - Combined v0.8.42 + v0.8.43 + v0.8.44 MCP tests: 17/17 pass in
      ~1.2s. Frontend: 162/162 across 29 files. Existing v0.8.42 case-
      insensitive filter + empty-list no-op tests cover the source-chat
      path implicitly through the shared `_resolve_chat_tools` helper.

- **🐛 v0.8.43b — Two v0.8.43 audit fixes (forward-ref + hydration race)**
  - **Audit finding A — useCallback forward-reference (MEDIUM).**
    `toggleDisabledMcpServer` (declared near the top of `useNotebookChat`)
    referenced `updateSessionMutation` which is hoisted later in the
    same function body. JS temporal-dead-zone forbids const references
    in deps arrays declared earlier than the const itself. Fix: assign
    the live mutation object into a ref (`updateSessionMutationRef`) via
    a useEffect placed AFTER the mutation declaration; the callback
    dereferences `.current` at call time so the stale-closure bug
    (mutation hot-swap) is avoided AND the deps array stays clean.
    Pattern matches `abortControllerRef` / `inFlightSendsRef` already
    used elsewhere in the hook.
  - **Audit finding B — hydration race (MEDIUM).**
    Pre-v0.8.43b, a rapid double-toggle could lose the user's second
    click. Sequence: toggle #1 → setDisabledMcpServers; PATCH fires;
    onSuccess invalidates session query; refetch returns the post-#1
    server value; user toggles #2 while refetch in flight →
    setDisabledMcpServers updates; refetch lands → hydration useEffect
    fires → overwrites local state with stale-by-one server value.
    Fix: gate hydration on `!updateSessionMutation.isPending` so the
    optimistic state survives until the user's last write lands. Also
    moved the hydration useEffect AFTER the mutation declaration (same
    TDZ rule as fix A) and added `currentSession?.disabled_mcp_servers`
    + `updateSessionMutation.isPending` to the deps array so the effect
    correctly re-runs on either signal change.
  - **Audit also confirmed clean** (no action needed):
    - Migration 20 `TYPE option<array<string>>` matches the pattern in
      migrations 1, 12, 14 — valid SurrealDB syntax.
    - `/chat/execute` precedence rule correctly uses `is not None` so
      an empty list (`[]`) is treated as "explicit clear" not "fall back
      to session value".
    - `ChatSession` domain-model `nullable_fields` membership +
      `Optional[list[str]]` Pydantic field correctly serializes the new
      field on save / round-trips it on load via the existing
      `_prepare_save_data` helper.

- **✨ v0.8.43 — Persistent per-conversation MCP picks**
  - **What:** v0.8.42 made tool selection per-request (hook-local
    state). v0.8.43 persists the picks on the `chat_session` row so
    the user's "load only what I need" choices stick across page
    reloads, browser tabs, and session navigation. Click "untick
    Crawl4AI" once → it stays unticked for every future turn in
    that session until ticked back.
  - **Backend (migration 20):**
    - `open_notebook/database/migrations/20.surrealql` —
      `DEFINE FIELD disabled_mcp_servers ON chat_session
      TYPE option<array<string>> DEFAULT NONE`. Existing rows stay
      NULL ⇒ behavior unchanged for any chat created before this
      migration.
    - `20_down.surrealql` — `REMOVE FIELD` for a clean rollback.
  - **Backend (`open_notebook/domain/notebook.py:ChatSession`):**
    - New `disabled_mcp_servers: Optional[list[str]] = None` field.
      Added to `nullable_fields` so the SurrealDB serializer treats
      NULL correctly across the create/save round-trip.
  - **Backend (`api/routers/chat.py`):**
    - `UpdateSessionRequest` accepts `disabled_mcp_servers` with
      `exclude_unset=True` semantics — omitting it on PATCH does
      NOT clear the persisted value (so the existing v0.7.x rename-
      session flow keeps working).
    - `ChatSessionResponse` surfaces the field via `getattr(..., None)`
      so pre-migration rows return null safely without raising.
    - `/chat/execute` and `/chat/stream` precedence: per-request
      body wins (v0.8.42 semantic); falls back to the session's
      persisted picks if the body omits the field. Pre-v0.8.43,
      the request body was the only signal.
  - **Frontend (`useNotebookChat`):**
    - On session load (`currentSessionId` change), hydrate
      `disabledMcpServers` state from `session.disabled_mcp_servers`.
    - `toggleDisabledMcpServer` now PATCHes the session via
      `updateSessionMutation` after updating local state — best-
      effort, the optimistic UI toggle doesn't block on the
      network round-trip.
  - **Type sync (`api.ts`):** `BaseChatSession.disabled_mcp_servers`
    + `UpdateNotebookChatSessionRequest.disabled_mcp_servers` added.
  - **Tests:**
    - `tests/test_v0_8_43_persistent_mcp_picks.py` — 5 backend
      tests: UpdateSessionRequest exclude_unset behavior (absent /
      null / empty list / non-empty); ChatSessionResponse field
      exposure; ChatSession domain model field + nullable_fields
      membership; ExecuteChatRequest v0.8.42 field unaffected;
      migration files exist + reference the right field name
      (cheap sanity check against typos that would propagate to
      every install).
  - **Combined with v0.8.42** — A user can now: open a notebook
    chat, untick SearXNG in the picker, send a turn, close the
    tab, come back tomorrow, see SearXNG is still unticked.
    Toggle it back on → stored. The XDA-Developers / Pi-harness
    "load only what I need" pattern is now a first-class affordance.

- **🐛 v0.8.42b — Two HIGH-severity audit fixes against v0.8.39e / v0.8.40b**
  - External post-v0.8.42 audit caught two real bugs:
    - **`Supervisor.hot_swap_chat` n_ctx rollback**
      (`desktop/launcher.py`): on respawn failure, `chat_llm_path`
      was restored to the pre-swap value but `chat_llm_n_ctx`
      kept the newly-resolved value. Next retry saw a mismatched
      (path, n_ctx) pair. Now we snapshot `old_n_ctx` alongside
      `old_path` and restore BOTH on the failure path. Test:
      `test_hot_swap_chat_restores_n_ctx_on_restart_failure` —
      runs `hot_swap_chat` against a stub `restart_sidecar` that
      returns failure, asserts both attributes are back to their
      pre-call values.
    - **Downloader Content-Range mismatch detection**
      (`open_notebook/local_models/downloader.py`): v0.8.39e's
      Range-resume path checked for a `200` response (server
      doesn't support Range → would corrupt the append by
      duplicating leading bytes). It did NOT verify that a `206`
      response's `Content-Range` start matches the requested
      offset. A broken / malicious mirror could return 206 with
      `bytes 0-1499/3000` to a `bytes=1500-` request and silently
      corrupt the file. Now we parse `Content-Range`, compare
      the start byte to `resume_from`, and fail with a readable
      error BEFORE opening the .part file. Tests: one for the
      mismatch case (asserts `failed` + .part untouched), one
      regression check for the matching happy path.
  - **Audit also confirmed clean**:
    - `PasswordAuthMiddleware` excluded_paths uses exact `in` match
      (`api/auth.py:79`); `/api/system/env-refresh/foo` cannot
      bypass auth.
    - `cancel_job` flag mutation is CPython GIL-safe for the
      read-modify scenario in the stream loop (documented).
    - `_resolve_chat_tools` exclude-list normalization correctly
      handles `None`, `[]`, missing `name`, and blank-string
      entries (already covered by v0.8.42 tests).
    - `recommendations.py` URLs all valid.

- **✨ v0.8.42 — Per-request MCP server disable list (XDA "load only what I need" pattern)**
  - **What:** Implements the deeper XDA Developers lesson from the
    article evaluation in v0.8.41: "load only 5-6 tools you actually
    need; dynamically unload unused tools from context." A new
    `<McpToolPicker>` widget sits above the notebook chat input,
    listing every registry-enabled MCP server with a checkbox.
    Unchecking a server adds its name to the chat request's
    `disabled_mcp_servers` field; the chat graph skips that server's
    tools for the turn. Persistent registry state untouched —
    purely a per-turn affordance.
  - **Backend (`open_notebook/graphs/chat.py`):**
    - `_resolve_chat_tools()` gets a new `exclude_server_names: list[str]
      | None = None` kwarg. Names are normalised (case-insensitive,
      `strip()`-trimmed) on both sides so frontend typos don't
      silently fail to filter. Empty list and None are both
      "no-disables" sentinels — defends against an accidental
      "exclude=[]" wiping all tools.
    - `bind_mcp_and_run_tool_loop()` threads the kwarg through to the
      resolver. `call_model_with_messages` reads
      `state.disabled_mcp_servers` from `ThreadState`.
    - New `disabled_mcp_servers: Optional[list[str]]` field on
      `ThreadState`.
  - **Backend (`api/routers/chat.py`):**
    - `ExecuteChatRequest` gets the optional `disabled_mcp_servers:
      List[str]` field. Default null = all servers visible
      (back-compat for existing clients). The router writes it onto
      `state_values["disabled_mcp_servers"]` for both `/chat/execute`
      and `/chat/stream`.
  - **Frontend (`SendNotebookChatMessageRequest`,
    `useNotebookChat`):**
    - Type extended with `disabled_mcp_servers?: string[]`.
    - Hook owns the `disabledMcpServers: string[]` state +
      `toggleDisabledMcpServer(name)` helper. State is hook-local
      (not persisted to the chat_session row — sticky-per-conversation
      memory is deferred to v0.8.42b which would need a schema
      migration).
  - **Frontend (`components/chat/McpToolPicker.tsx`):**
    - New compact Popover widget. Trigger chip shows "N/T tools"
      so the user sees state at a glance. Picker hides
      registry-disabled servers (those can't bind tools anyway —
      handled by the v0.8.0 admin Settings → MCP page). Checkbox
      state reflects case-insensitive name match against the
      disabled array.
  - **Tests:**
    - `tests/test_v0_8_42_mcp_disable_filter.py` — 6 backend tests:
      filter excludes matching servers, case-insensitive matching
      (3 variants), empty/None is no-op (regression guard against
      accidental wipe), blank-string entries ignored,
      `bind_mcp_and_run_tool_loop` forwards the kwarg, and
      `ExecuteChatRequest` schema accepts absent/null/empty/non-empty.
    - `frontend/.../McpToolPicker.test.tsx` — 5 frontend tests:
      empty registry → hidden; N/T trigger count math; registry-
      disabled hidden; case-insensitive disabled-array match;
      onToggle propagation. Frontend suite: 162/162 across 29 files.
    - Full v0.8.x backend sweep: TBD — all v0.8.42 unit tests
      green (6/6); existing v0.8.35e (per-tool timeout) +
      v0.8.35f (silent-except log) + v0.8.1 plumbing remain.

- **✨ v0.8.39e — GGUF download cancel + resume**
  - **What:** The v0.8.39b downloader gets two features one tier of
    "real-world UX" away: in-flight Cancel and automatic Resume from a
    partial `.part` file. A 7 GB Qwen download interrupted by an
    accidental window close, a flaky wifi blip, or a user closing the
    laptop lid no longer means starting over from byte 0.
  - **Backend (`open_notebook/local_models/downloader.py`):**
    - `DownloadJob` gets `cancelled: bool`, `resume_from_bytes: int`,
      and a new `"cancelled"` terminal status. `cancel_job(job_id)`
      sets the flag; the stream loop checks it on every 1 MiB chunk
      boundary and tears down cleanly, leaving the `.part` file on
      disk.
    - `start_download` detects a pre-existing `.part` file at the
      target name; if non-empty, seeds `resume_from_bytes` from the
      stat'd size. The stream loop sends a `Range: bytes=N-` header
      and opens the `.part` in `ab` (append-binary) mode.
    - `_stream_download` uses `Content-Range` (not `Content-Length`)
      to derive `bytes_total` on a Range request, and **fails clearly
      when the server returns 200 to a Range request** — a mirror
      that ignored Range would send the FULL file from byte 0,
      append-mode would duplicate the leading bytes and corrupt the
      GGUF. Detect-and-fail rather than silently corrupt.
  - **Backend endpoint (`api/routers/local_models.py`):**
    - `POST /api/local-models/downloads/{job_id}/cancel`. 200 +
      `{ok, detail}` on happy path; 404 for unknown job_id; 409
      Conflict when the job is already in a terminal state (so the
      client doesn't retry-loop on an already-done job).
  - **Frontend (`DownloadPanel.tsx`):**
    - Cancel button on every in-flight card (loader spinner). New
      "Cancelled. Click Resume to continue from where it stopped"
      copy on cards in the cancelled state. The Download/Retry/Resume
      button label adapts to the prior job's status — "Resume" when
      a `.part` exists from a cancelled prior run.
  - **Tests:**
    - `tests/test_v0_8_39e_cancel_resume.py` — 10 backend tests:
      `cancel_job` unknown/in-flight/already-terminal; stream-loop
      aborts on cancel; resume seeds `resume_from_bytes` from
      existing `.part`; Range header sent; `Content-Range` derives
      `bytes_total`; 200-to-Range-request corruption guard fails
      with a readable error (and verifies the `.part` file is
      untouched); endpoint 200/404/409.
    - Existing v0.8.39b + v0.8.40c test stubs updated to accept the
      new `headers=None` kwarg `_stream_download` now passes; their
      semantics are unchanged. Full v0.8.x backend sweep:
      **153/153** in ~5m11s.
    - Frontend: +1 case on `DownloadPanel.test.tsx` (in-flight
      Cancel POSTs `/cancel`); 28 files / **157/157** tests.

- **✨ v0.8.40d — Hot-swap n_ctx synced into the running API process**
  - **Closes the v0.8.40b limitation:** after a successful chat-GGUF
    swap, the launcher now PUSHES the new `OPEN_NOTEBOOK_LOCAL_N_CTX`
    into the running API's environment via a new auth-gated endpoint
    so the smart router (`provision.py`) sees the right native
    context window on the very next chat turn. Pre-v0.8.40d this
    required an app relaunch — documented in v0.8.40b's CHANGELOG
    as a known limitation, closed here.
  - **New API endpoint (`api/routers/system.py`):**
    `POST /api/system/env-refresh` body `{vars: {KEY: VALUE}}`.
    Bypasses the password middleware (the launcher doesn't have the
    user-facing password); auth via the same
    `OPEN_NOTEBOOK_LAUNCHER_CONTROL_TOKEN` the launcher uses for its
    control-plane calls, constant-time compared via
    `secrets.compare_digest`. **Strict whitelist** of allowed env
    var names (just `OPEN_NOTEBOOK_LOCAL_N_CTX` for now) — defense
    against a compromised process arbitrarily mutating `PATH`,
    `PYTHONPATH`, etc. Mixed payloads return both `updated` and
    `rejected` lists so the launcher can submit best-effort without
    pre-filtering.
  - **Launcher (`desktop/launcher.py`):** `Supervisor._push_env_to_api`
    helper + a `try/except` around the call in `hot_swap_chat`
    (best-effort — a push failure logs a WARNING but does NOT undo
    the successful sidecar swap; user is no worse off than v0.8.40b).
    Token + API port lifted from `session_env` (already populated by
    v0.8.40).
  - **Tests (`tests/test_v0_8_40d_env_refresh.py`):** 7 endpoint
    cases — 503 when no token configured; 401 missing/malformed/
    mismatched header; 200 whitelist mutation actually changes
    `os.environ`; non-whitelisted key rejected without touching
    `os.environ`; mixed payload returns both lists. Confirms `PATH`
    is never mutable through this endpoint (defense check).

- **✨ v0.8.41 — Curated MCP server recommendations (XDA Developers picks)**
  - **What:** Settings → MCP page now shows a "Recommended MCP servers"
    panel above the manual add-form. Three curated, locally-runnable
    servers — SearXNG (web search, replaces paid search subs),
    Crawl4AI (web→markdown, replaces Firecrawl), Playwright MCP
    (browser automation). Each card has install instructions
    (upstream link), pre-filled default URL, and a one-click Connect
    button that registers the server via the existing v0.8.0
    `POST /api/mcp` endpoint.
  - **Selection rationale:** Inspired by the XDA Developers article
    on local-LLM MCP stacks. We skipped picks we already cover
    server-side (Mem0 — v0.7.68/70 memory writer + recall; Qdrant
    — SurrealDB native vector search; sentence-transformers — our
    llama-cpp embed sidecar) and picks that don't fit our research-
    assistant use case (Context7 — code-doc lookup; our users
    research, they don't code).
  - **Backend (`open_notebook/mcp/recommendations.py`):** New module
    with the `RECOMMENDATIONS` table. Schema parity with
    `local_models/downloader.py:RECOMMENDATIONS` (v0.8.39b) so the
    frontend pattern is the same: `{id, label, description,
    default_url, install_url, tags, replaces}`. Three entries; first
    one tagged "recommended" for the default UI prominence.
  - **Backend (`api/routers/mcp.py`):** New `GET /api/mcp/recommendations`
    endpoint. Same `{recommendations: [...]}` envelope as the GGUF
    endpoint.
  - **Frontend (`/settings/mcp/RecommendationsPanel.tsx`):**
    Card-per-recommendation grid with tag badges (search / scraping
    / browser / recommended / "Replaces X"), install-instructions
    link, Connect button. De-duplicates against the existing-servers
    query: case-insensitive name match OR exact URL match → button
    flips to a disabled "Connected" state with the check icon.
    Toast feedback on connect success/failure. Manual add-form
    below stays for any server not in the curated list.
  - **Tests:**
    - `tests/test_v0_8_41_mcp_recommendations.py` — 5 backend
      tests: required-field shape, unique IDs, localhost-only
      URLs (defense against accidentally curating a remote SaaS),
      "recommended" tag presence, endpoint smoke.
    - `frontend/.../RecommendationsPanel.test.tsx` — 5 frontend
      tests: render with tags + Replaces badge, Connect calls
      create-server mutation with defaults, name-match dedupe,
      URL-match dedupe, failure → error toast.
    - Existing `mcp/page.test.tsx` extended to stub out the new
      panel (avoids needing a QueryClientProvider just for the
      panel's useQuery). All 156/156 frontend tests across 28
      files pass.

- **🐛 v0.8.40c — Skip redownload when GGUF already exists locally**
  - **Bug:** `open_notebook/local_models/downloader.py:start_download`
    deduplicated only IN-FLIGHT jobs, not COMPLETED ones. A user who
    triggered Download → completion → came back days later and
    clicked Download again would re-download the same multi-GB GGUF
    for no benefit. Surfaced by the post-v0.8.40b audit.
  - **Fix:** Pre-check `dest_dir/filename` size at start_download
    time; if non-empty, return a synthetic `status="completed"`
    job immediately without firing the HTTP stream or registering
    in the dedupe table (so a subsequent re-trigger after file
    deletion produces a fresh real download). Zero-byte files are
    NOT treated as "already downloaded" — those are failed-prior-
    download artifacts the v0.8.39 inventory already filters.
  - **Audit also surfaced** (no action): three lower-severity items
    that I evaluated and documented as not-actionable — race on
    `_procs[-1]` read in `_try_spawn` is GIL-protected per the
    docstring; `Path.replace()` on Windows is atomic since Python
    3.3 (the agent flagged this as a bug but the claim was a false
    alarm — `pathlib.Path.replace()` uses `os.replace()` which is
    cross-platform); `_check_auth` token race during ControlServer
    shutdown is a spurious 401 at worst, not a security hole.
  - **Test:** `tests/test_v0_8_40c_downloader_skip_existing.py` — 3
    cases: skip when file exists (zero HTTP fired); subsequent
    download after deletion runs real flow with a fresh job_id;
    zero-byte file is NOT skipped. Existing 12 v0.8.39b tests
    unaffected.

- **✨ v0.8.40b — Hot-swap chat GGUF without app restart**
  - **What:** The Settings → Local Models inventory page (v0.8.39)
    now sports a "Set as active chat model" button on each row. Click
    → the chat sidecar is killed and respawned with the new GGUF —
    no app relaunch, no terminal. Closes the last big foundational
    UX gap from the Phase-1 audit (#5 — "no model swapping during
    the session").
  - **Launcher (`desktop/launcher.py:Supervisor.hot_swap_chat`):**
    Validates the new path (exists + `.gguf` + lives under
    `cfg.model_dir`), updates `chat_llm_path`, re-resolves
    `chat_llm_n_ctx` from the new GGUF's metadata, then delegates
    to the v0.8.40 `restart_sidecar("chat")` so the existing
    SIGTERM→SIGKILL→respawn flow is reused. Rolls back the path on
    respawn failure so subsequent attempts don't compound. Returns
    `(ok, detail)` like other control-plane callbacks.
  - **Launcher control plane (`desktop/launcher_control.py`):**
    `do_POST` refactored from single-route to a small dispatch
    table `ROUTE_MAP` so adding new operations no longer requires
    duplicating auth/body/length/error-handling boilerplate. New
    `/hot_swap_chat` route expects `{path: str}`. Response shape
    echoes the request field (`kind` for restart, `path` for
    swap) so HTTP callers can correlate without inspecting body
    contents. The original `/restart_sidecar` route is regression-
    tested in v0.8.40b to confirm the refactor didn't break it.
  - **API endpoint (`api/routers/local_models.py`):** New
    `POST /api/local-models/set-active` body `{path}`. Defense-in-
    depth validation at the edge — exists + `.gguf` + resolved
    parent inside the configured `model_dir` — before forwarding
    to the launcher's own checks. 60s read timeout (large GGUFs
    can take time to mmap on a cold disk). Maps launcher 5xx→502
    and 4xx→400 with detail surfaced.
  - **Frontend (`/settings/local-models/page.tsx`):** "Set as
    active chat model" button on every inventory card with
    `Power` icon + `Loader2` spinner during the swap. `activatingPath`
    state so the user sees which card is in flight if they click
    rapidly. Toast success/failure with the launcher's `detail`.
    Invalidates the local-models-health query so badges can flip
    red briefly as the new sidecar mmaps the GGUF and back to
    green once it binds.
  - **Tests:**
    - `tests/test_v0_8_40b_hot_swap.py` — 13 backend tests across
      two layers. ControlServer `/hot_swap_chat` route (6 cases:
      auth, missing-field, happy-path callback dispatch, failure
      → 400, no callback → 503, regression check for the
      original `/restart_sidecar` route after the dispatcher
      refactor). Endpoint `POST /local-models/set-active`
      (7 cases: missing path, nonexistent, non-`.gguf`, outside
      `model_dir` path-traversal block, no control URL → 503,
      real-launcher round-trip with a happy-path callback +
      resolved-path verification, launcher rejection → 400 with
      detail).
    - `frontend/.../page.test.tsx` — extended from 3 → 5 cases:
      Set Active POSTs to `/set-active` and toasts success;
      failure → error toast with detail. Mocks `sonner` to
      capture toasts; DownloadPanel stubbed out to avoid
      coupling.
    - Backend suite: **29/29** combined v0.8.40 + v0.8.40b in
      ~12s. Frontend: **151/151** across 27 files.
  - **Known limitation (documented):** The hot-swap updates
    `chat_llm_path` + `chat_llm_n_ctx` on the launcher side but
    does NOT push the new n_ctx into the API subprocess's
    `OPEN_NOTEBOOK_LOCAL_N_CTX` env var (subprocess env is fixed
    at spawn time). If the new GGUF has a SMALLER native context
    than the old, the v0.8.0 smart router may still route prompts
    that fit the old context to local, and the new sidecar will
    reject them with 400 context_length_exceeded for that edge.
    Common case (same family / same quant) is unaffected. A
    follow-on (deferred v0.8.40c) could add a control-plane
    endpoint that the launcher calls to push env-var updates to
    the running API process.

- **✨ v0.8.40 — Launcher↔API control plane + in-place sidecar restart (Phase 4c of Osaurus plan)**
  - **What:** Closes the v0.8.38b deferred item. The frontend's
    `SidecarLogPopover` (v0.8.38) now sports a "Restart" button that
    actually restarts the relevant sidecar — chat, embed, whisper,
    piper, memory — without quitting the app. Pre-v0.8.40 the
    popover surfaced the crash cause but the only recovery action
    was "quit and relaunch."
  - **New IPC infrastructure (`desktop/launcher_control.py`):**
    - Tiny `ThreadingHTTPServer` running inside the launcher
      process. Binds 127.0.0.1 only on an OS-assigned random port.
      Stdlib only — no aiohttp/uvicorn dragged into the launcher's
      sync world.
    - Random 32-byte bearer token (`secrets.token_urlsafe(32)`)
      generated per session. `secrets.compare_digest` so a timing
      attack on the token isn't possible from a chatty local
      neighbor.
    - `/health` (unauth) for liveness probes. `/restart_sidecar`
      (auth + POST) dispatches into a registered callback.
    - Callback registry pattern (`register_callback(name, fn)`) so
      the Supervisor can wire in `restart_sidecar` (and, in
      v0.8.39c, `hot_swap_chat`) without import cycles.
  - **Launcher integration (`desktop/launcher.py`):**
    - Per-kind Popen tracking (`_sidecar_procs: dict[str, Popen]`)
      + spawn args (`_sidecar_spawn_args`) populated automatically
      via `_try_spawn`'s post-spawn hook. No per-spawn-function
      changes required.
    - New `restart_sidecar(kind) → (ok, detail)` method. SIGTERM
      → wait → SIGKILL the process group (same pattern as
      `stop_all`), drop from `_procs`, re-invoke `_spawn_<kind>`
      via the original `_try_spawn` path so progress events fire
      consistently.
    - `start_all` stands up the ControlServer BEFORE `session_env`
      is built and exports `OPEN_NOTEBOOK_LAUNCHER_CONTROL_URL` +
      `OPEN_NOTEBOOK_LAUNCHER_CONTROL_TOKEN` to the API subprocess.
      Best-effort: a port-bind failure logs a warning and leaves
      the URL empty — the API's restart endpoint returns 503 with
      a clear message rather than crashing.
    - `stop_all` tears down the ControlServer first so in-flight
      requests fail-fast with connect-refused, before the
      subprocess teardown could leave them hanging.
  - **API endpoint (`api/routers/local_models.py`):**
    - `POST /api/healthz/sidecars/{kind}/restart`. Reuses the same
      kind allowlist as the v0.8.38 log endpoint (path-traversal
      safe). Reads `OPEN_NOTEBOOK_LAUNCHER_CONTROL_URL/TOKEN` from
      env; httpx-POSTs to the launcher with the bearer token;
      maps launcher 5xx → 502 and 4xx → 400 with the detail
      surfaced. No control URL → 503 with the friendly "running
      outside launcher" hint.
    - 15s read timeout — long enough for a real GGUF mmap on a
      slow disk, short enough that a hung launcher doesn't tie
      up the API request slot.
  - **Frontend (`SidecarLogPopover.tsx`):**
    - "Restart" button replaces the v0.8.38 "quit and relaunch"
      footer copy. Loader2 spinner during the mutation; toast
      success/error with the launcher's detail string.
    - On success, invalidates the log + local-models-health
      queries so the badge dot can flip green and the popover
      refetches the new sidecar's stderr tail.
  - **Tests:**
    - `tests/test_v0_8_40_launcher_control.py` — 16 backend tests
      across two layers. ControlServer layer (11): bind+health,
      OS-assigned port, missing/mismatched token → 401, callback
      dispatch on happy path, failure → 400, callback exception →
      500 + readable error, missing kind → 400, no callback → 503,
      unknown path → 404, idempotent stop. Endpoint layer (5):
      unknown kind → 404, no control URL → 503, real-launcher
      happy-path proxy round-trip (stands up a ControlServer
      in-test), connect-refused → 502, launcher-rejected → 400
      with detail.
    - `frontend/.../SidecarLogPopover.test.tsx` — extended from 8
      → 10 cases: Restart POSTs to the right endpoint + toasts on
      success; failure → error toast with detail. Mocks `sonner`
      to capture toasts.
  - **Out of scope this iteration (v0.8.40b/c):** Hot-swap chat
    GGUF (`POST /local-models/set-active`) — needs the same
    control plane but with a `hot_swap_chat` callback that updates
    `chat_llm_path` + re-resolves `chat_llm_n_ctx` before
    respawning. Foundation is in place; one more callback + a
    matching API endpoint will land it.

- **✨ v0.8.39b — HuggingFace GGUF downloader (Phase 4b of Osaurus plan)**
  - **What:** Adds curated HuggingFace GGUF recommendations with
    one-click download to the Settings → Local Models page. The
    foundation v0.8.39 (Phase 4a) shipped READ-only inventory; this
    closes the second half: zero-friction model acquisition straight
    from the UI. The user goes from "fresh install" → "downloaded
    Qwen 2.5 7B" → "talking to a local model" in three clicks, no
    terminal, no Finder.
  - **Backend (`open_notebook/local_models/downloader.py`):** New
    module. `RECOMMENDATIONS` table — 3 curated entries (Qwen 2.5
    7B for the default user, Qwen 2.5 3B for low-RAM machines,
    Nomic Embed for the embeddings slot). Each entry has stable
    React key, label, description, repo_id+filename, approx size,
    capability tags, context length. `start_download(repo_id,
    filename, dest_dir)` spawns an `asyncio.create_task` that
    streams via `httpx.AsyncClient` into `{filename}.part`, writes
    bytes_downloaded as it goes, then atomic-renames to the final
    `{filename}` on success — same atomic pattern the v0.8.38 tail
    drainer uses so `enumerate_models` never sees a partial file.
    Deduplicates in-flight (repo_id, filename) requests via a
    lazy-init `asyncio.Lock` so two tabs hitting Download at the
    same time can't corrupt the .part file. Surfaces HTTP / network
    / disk errors as `job.status="failed"` + readable
    `job.error` — the background task never raises.
  - **Backend (`api/routers/local_models.py`):** Three new endpoints.
    `GET /local-models/recommendations` returns the curated list.
    `POST /local-models/download` body `{repo_id, filename}` →
    `{job_id, status, ...}`; defense-in-depth validation rejects
    missing fields, path-traversal filenames (`..`, `/`, `\\`),
    and non-`.gguf` extensions. `GET /local-models/downloads/{job_id}`
    polls progress (404 for unknown job_id).
  - **Frontend (`frontend/src/app/(dashboard)/settings/local-models/DownloadPanel.tsx`):**
    Card-per-recommendation grid above the inventory list. Each
    card shows label, description, tag badges (chat/tools/small/
    recommended/embedding), size hint, context window, repo path.
    Click "Download" → POST `/local-models/download` → in-flight
    state with `<Progress>` bar + percentage + status line.
    Polling loop (1s interval while queued/downloading) updates
    the bar; on completion invalidates the inventory query so the
    new model appears in the table below without manual refresh.
    Failed downloads surface the error inline with a Retry button.
    All i18n via `defaultValue`, no locale-parity churn.
  - **Page integration:** DownloadPanel rendered whenever the model
    dir is reachable — including the empty-inventory state. That's
    the most useful place for it: brand-new install, nothing yet,
    here are some good first picks.
  - **Tests:**
    - `tests/test_v0_8_39b_downloader.py` — 12 backend tests:
      RECOMMENDATIONS shape + unique IDs + URL composition + happy-
      path download (mocked httpx with chunked aiter_bytes) +
      atomic-rename verification + dedupe on in-flight + HTTP
      error → failed job (no raise) + 4 endpoint validation cases +
      404 on unknown job_id + recommendations-endpoint smoke.
    - `frontend/.../DownloadPanel.test.tsx` — 3 tests: cards render,
      Download POSTs + shows in-flight progress, completed state
      renders. Frontend suite: 147/147 across 27 files; backend
      adds 12 → 100/100 in the v0.8.x phase sweep.
  - **Deferred to v0.8.39d (persistent jobs):** Job state is in-
    memory; a mid-download API restart loses the progress tracker
    (the `.part` file stays on disk for manual cleanup; re-trigger
    starts over). Multi-user/multi-tenant deployments should
    persist to SurrealDB via `surreal_commands`. Standalone scope.
  - **Deferred to v0.8.39e (cancel + resume):** No cancel button
    today; the underlying httpx stream isn't cancellable cleanly.
    HuggingFace serves Range so resume is feasible. Both fit the
    same UI iteration; tracked together.

- **✨ v0.8.39 — Local GGUF inventory page (Phase 4a of Osaurus plan)**
  - **What:** New `Settings → Local Models` page lists every GGUF in
    the configured model directory with metadata: architecture
    (qwen2, llama, phi3, gemma…), parameter count (7B, 13B…),
    quantization (Q4_K_M, Q5_K_M…), native context length, file
    size. Empty state guides the user to drop a GGUF file in (with
    a HuggingFace link to a curated starting point). Closes one of
    the biggest UX gaps surfaced by the Phase-1 audit: pre-v0.8.39
    users had to know the exact path to drop GGUFs into, and could
    only see what was registered by guessing names in Settings →
    API Keys.
  - **Backend (`open_notebook/local_models/`):**
    - New module. `gguf_metadata.py` — `parse_gguf_metadata(path)`
      uses the optional `gguf` library when installed
      (authoritative `general.architecture` + `<arch>.context_length`),
      falls back to filename heuristics (quant from a longest-match
      table of llama.cpp quant schemes, params via regex, arch via
      family-name substring). `os.stat` for size always works.
    - `inventory.py` — `enumerate_models(model_dir)` non-recursive
      scan. Filters non-`.gguf`, dotfiles, `.tmp`/`.part`/zero-byte
      stubs. Returns sorted `LocalModelInfo` list. Defensive: empty
      list on missing/unreadable dir; never raises.
  - **Backend (`api/routers/local_models.py`):**
    - New `GET /api/local-models/inventory` endpoint. Resolves
      model dir from env precedence (OPEN_NOTEBOOK_MODEL_DIR →
      OPEN_NOTEBOOK_MODEL_DIR_DEFAULT → POSIX default
      ~/Desktop/AI_Models matching `desktop/config.py`). Returns
      `{model_dir, available, models[]}` with `available: false`
      for missing dirs (frontend renders a friendly state). Sync
      filesystem stat pushed to `asyncio.to_thread` so a slow disk
      doesn't stall the event loop.
  - **Frontend (`frontend/src/app/(dashboard)/settings/local-models/page.tsx`):**
    - New page. Card-per-model layout matching the existing
      `api-keys` page rhythm (`max-w-4xl`, `space-y-8`, secondary
      `Badge`s for quant + arch). `Refresh` button. Three top-
      level states: error, dir-missing, empty-list, populated.
      Uses TanStack Query with `refetchOnWindowFocus: true` so
      Finder-drag-and-drop new files show up on tab return.
    - All i18n strings use `defaultValue` fallback so no locale
      parity churn.
  - **Tests:**
    - `tests/test_v0_8_39_local_models_inventory.py` — 24 backend
      tests covering parse_quant (7 cases), parse_param_count_b
      (7 cases), parse_gguf_metadata filename-fallback + missing-
      file paths, enumerate_models filter/empty/missing/metadata/
      sort, and the inventory endpoint (env precedence, missing
      dir, listing).
    - `frontend/src/app/(dashboard)/settings/local-models/page.test.tsx`
      — 3 smoke tests for the three top-level UI states. Frontend
      suite now 26 files / 144 tests, all green; locale parity
      preserved (no new keys).
  - **Deferred to v0.8.39b (HuggingFace downloader):** One-click
    download via `huggingface_hub` with progress polling. Requires
    background-task infrastructure beyond inline FastAPI — the
    existing `surreal_commands` async-job pattern is the right
    home but adding a command requires touching the desktop
    bundle's worker registry. Standalone scope.
  - **Deferred to v0.8.39c (hot-swap):** `POST /local-models/set-active`
    that signals the launcher to re-spawn the chat sidecar with a
    different GGUF. Depends on the bidirectional launcher↔API IPC
    that v0.8.38b also needs — implementing them together makes
    more sense than each separately.

- **✨🐛 v0.8.38 — Sidecar lifecycle visibility (Phase 3 of Osaurus plan)**
  - **Pain solved:** Pre-v0.8.38, when a local sidecar (chat / embed /
    whisper / piper / memory) crashed at launch — bad GGUF path,
    OOM, port collision, CUDA/Metal error — the frontend just
    rendered a red badge with no signal. Users had to enable
    `debug_mode`, relaunch the app, and hunt log files in
    `~/.open-notebook-plus/logs/` to diagnose. Now: click the red
    badge → popover with the last ~50 stderr lines + a one-line
    user-friendly hint ("Model file not found", "Out of memory",
    "Port already in use", etc).
  - **Launcher (`desktop/launcher.py`):**
    - `_spawn()` now sets `stderr=subprocess.PIPE` in non-debug
      mode (was DEVNULL) so the new tail drainer can read it. stdout
      stays DEVNULL — only stderr matters for crash diagnostics.
    - New `_start_tail_drainer()` keeps a
      `collections.deque(maxlen=50)` per sidecar and atomically
      rewrites `{log_dir}/{name}.tail` on each new line. Secret
      redaction (`--pass=…`, encryption keys) mirrors the v0.7.58
      debug-mode drainer. Drain thread is daemon + joined-with-
      timeout in `stop_all` just like the existing one.
    - Exports `OPEN_NOTEBOOK_LAUNCHER_LOG_DIR` in `session_env` so
      the API process knows where to read the tail files. Default
      `~/.open-notebook-plus/logs`.
  - **Backend (`open_notebook/utils/error_classifier.py`):**
    - New `classify_sidecar_error(tail_text)` function. Plain
      first-match-wins substring scan over `_SIDECAR_PATTERNS`
      mapping known stderr signatures to user-friendly hints. Pure
      function, zero I/O. Patterns ordered narrowest-first so
      specific errors (model load, GGUF) win over generic ones
      (segfault, killed).
  - **Backend (`api/routers/local_models.py`):**
    - New `GET /healthz/sidecars/{kind}/log` endpoint. `kind` is
      validated against a fixed allowlist (chat / embed / whisper
      / piper / memory) BEFORE composing the filename — path-
      traversal-safe. Returns `{kind, log, hint, available}` with
      `available: false` when no log dir is set (API running
      outside launcher) or the tail file doesn't exist (sidecar
      never spawned). Defensive 8 KiB cap on response size.
      Sync `read_bytes` pushed to `asyncio.to_thread` so a slow
      disk doesn't stall the event loop.
  - **Frontend (`frontend/src/components/chat/`):**
    - New `SidecarLogPopover` component renders a Radix Popover
      with the hint (in an amber callout when present) + the raw
      tail (monospace, scrollable, max-h-64) + a footer noting
      that in-app restart is a future feature. Lazy-fetches on
      open — badges that never get clicked pay no bandwidth.
    - Helper `sidecarKindFromName()` maps credential-name strings
      from `/api/local-models/health` (e.g. "Local GGUF
      (llama.cpp)", "Local Embeddings") to the canonical kind.
    - `LocalModelHealthBadges` wraps the red (unhealthy) status
      dot in the popover; healthy / not_configured / unknown
      dots stay static. Dots get a `ring-offset` + hover ring +
      `role="button"` for accessibility when clickable.
  - **Tests:**
    - `tests/test_v0_8_38_sidecar_log.py` — 20 backend tests:
      11 classifier patterns (case-insensitive, first-match-wins,
      empty input, no-match), 8 endpoint cases (unknown kind 404,
      no log dir, missing file, present file with hint, 8 KiB
      cap, all 5 known kinds, path-traversal block, raw byte
      handling).
    - `frontend/src/components/chat/SidecarLogPopover.test.tsx` —
      8 tests covering all 5 heuristic mappings and 3 popover
      content states (unavailable, log+hint, empty-log).
  - **Deferred to v0.8.38b:** `POST /healthz/sidecars/{kind}/restart`.
    The launcher and API are separate processes; there's no
    bidirectional IPC today. Restart requires a control-plane
    channel (Unix domain socket, named pipe, or sentinel file)
    that's a meaningful build of its own. For now the popover
    surfaces the cause + "quit and relaunch" as the action — a
    huge upgrade over the v0.8.0 zero-signal experience.

- **✨🐛 v0.8.37 — Smart routing UI toggle (Phase 2 of Osaurus plan) + audit fix for v0.8.1 missing API field**
  - **Feature:** Pre-v0.8.37, smart routing was opt-in via the
    `OPEN_NOTEBOOK_AUTO_ROUTE_CHAT=1` env var only — invisible to UI-
    driven users. v0.8.37 adds a "Smart routing" card at the top of
    Settings → API Keys with a master enable toggle and an
    auto/local/cloud provider-preference dropdown. Env var still wins
    when set (back-compat for ops); otherwise the new
    `DefaultModels.auto_route_enabled` and
    `DefaultModels.auto_route_provider_pref` fields drive
    `provision_langchain_chat_model`.
  - **Audit bug fix:** While wiring the new fields through the API
    schema, found that `auto_route_cloud` (added on the domain side
    in v0.8.1 Migration 18) was MISSING from
    `api/models.py:DefaultModelsResponse` — the frontend's
    `useUpdateModelDefaults` PUT body included it but the Pydantic
    schema silently dropped it before the router could persist it.
    The field never actually round-tripped in the v0.8.1 release. Now
    explicitly declared + handled in the GET + PUT paths.
  - **Backend:**
    - `open_notebook/ai/models.py:DefaultModels` — new
      `auto_route_enabled: bool = False` and
      `auto_route_provider_pref: str = "auto"` fields.
    - `open_notebook/ai/provision.py` — `provision_langchain_chat_model`
      now consults DefaultModels when the env var is unset (back-compat
      preserved). Provider preference falls through to
      `pick_provider()` the same way. Defaults-fetch failure safely
      defaults the toggle to OFF.
    - `api/models.py:DefaultModelsResponse` — adds `auto_route_cloud`
      (v0.8.1 audit fix), `auto_route_enabled`,
      `auto_route_provider_pref`.
    - `api/routers/models.py` — GET + PUT both surface and persist
      the three fields. PUT clamps `provider_pref` to the allowed set.
  - **Frontend:**
    - `frontend/src/lib/types/models.ts:ModelDefaults` — typed the
      two new fields.
    - `frontend/src/components/settings/SmartRoutingPanel.tsx` — new
      card with `Checkbox` toggle + `Select` provider-pref dropdown.
      Hint copy explains env-var precedence. Uses i18next
      `defaultValue` fallback so no locale parity churn.
    - `frontend/src/app/(dashboard)/settings/api-keys/page.tsx` —
      panel rendered above `<DefaultModelSelectors>`. Narrowed the
      page's internal `DefaultConfig.key` type from
      `keyof ModelDefaults` to a string-only union so the new boolean
      field doesn't widen the `currentValue` access type.
  - **Tests:**
    - `tests/test_v0_8_37_smart_routing_ui_toggle.py` — 7 backend
      tests covering the 4-way env-vs-field precedence matrix and
      the provider_pref override paths.
    - All existing `test_phase3_smart_routing.py` (15),
      `test_v0_8_1_*` (11), `test_v0_8_35*` (11),
      `test_v0_8_36_osaurus*` (10), and the new v0.8.37 (7) pass —
      54 chat-platform-surface tests green. Frontend: 133/133
      including locale parity.
  - **scripts/verify-chat-platform.sh:** header updated to document
    the UI-toggle path alongside the env-var path.

- **✨ v0.8.36 — Osaurus first-class local provider integration (Phase 1 of Osaurus plan)**
  - **What:** Native macOS users running [Osaurus](https://github.com/osaurus-ai/osaurus)
    (MIT, 5.5k★, MLX-accelerated local AI server on port 1337) now get
    auto-detection + one-click connect from Open Notebook Plus.
    Apple-Silicon throughput is typically 2-4× llama-cpp on the same
    hardware, so this is a meaningful default for Mac users without us
    having to ship or maintain MLX.
  - **Backend:**
    - `desktop/auto_register/osaurus.py` — new module mirroring the
      `register_llamacpp_models` / `register_ollama_models` pattern.
      Probes `http://127.0.0.1:1337/v1/models` (port overridable via
      `OPEN_NOTEBOOK_OSAURUS_PORT`); on 200 creates an
      `openai_compatible` credential named "Osaurus (local MLX)"
      pointed at `/v1` and registers each discovered model. Idempotent
      across re-launches; safe on non-Mac platforms (`ConnectError`
      logged at DEBUG, returns False).
    - `desktop/auto_register/__init__.py` — wired into the existing
      auto-register pipeline alongside Ollama + llama-cpp.
    - `api/routers/credentials.py` — new `POST /credentials/detect-osaurus`
      endpoint. Runs the same probe + register flow on demand for
      users who install Osaurus AFTER launching ONP, no restart needed.
      Returns `{running, port, models_registered, credential_id, detail}`.
  - **Frontend:**
    - `frontend/src/components/settings/OsaurusDetectionBanner.tsx` —
      banner card on Settings → API Keys that renders ONLY when (a)
      no Osaurus credential exists yet AND (b) the backend probe
      reports `running: true`. One-click "Connect" calls
      `/credentials/detect-osaurus`, invalidates the credentials +
      models query keys, toast confirms. Includes a "Learn more"
      link to the Osaurus GitHub. All i18n strings use `defaultValue`
      fallback so no locale parity churn is required.
  - **Tests:**
    - `tests/test_v0_8_36_osaurus_auto_register.py` (10 backend tests)
      covering default/env-override/garbage port; happy-path probe;
      ConnectError fallthrough; non-200 fallthrough; happy-path
      registration; no-op when not running; credential-failure
      bailout; explicit-port kwarg override.
    - `frontend/src/components/settings/OsaurusDetectionBanner.test.tsx`
      (4 frontend tests) covering already-connected (hidden); not
      running (hidden); running + click Connect; toast on success.
    - Backend regression: 30/30 across the v0.8.1, v0.8.35b/c/d/e/f
      and Osaurus suites pass. Frontend regression: 133/133
      including locale parity.

- **🐛 v0.8.35f — log silent MCP-bind failure (audit deferred from v0.8.35e)**
  - The bare `except Exception: mcp_tools = []` at
    `open_notebook/graphs/chat.py:354-356` (in
    `bind_mcp_and_run_tool_loop`) was the last silent-except left over
    from the v0.8.27 → v0.8.33 sweep. Operators debugging "why doesn't
    MCP work on my local model?" had zero signal — the failure
    looked identical to "no MCP server configured."
  - Now logs at DEBUG (matches v0.8.27/v0.8.28/v0.8.33 severity:
    DEBUG for benign/expected — local providers without tool-calling
    support — WARNING for surprises). Same except runs in source-chat
    since v0.8.16 shared the helper, so this lights up diagnostics
    for both chat surfaces with one line. Pure observability, no
    behavior change. Promoted `from loguru import logger` to a
    module-level import (continuing the v0.8.35e import-hygiene
    pass).

- **🐛🔒 v0.8.35e — `bind_mcp_and_run_tool_loop` per-tool-call timeout**
  - **Bug:** `open_notebook/graphs/chat.py:bind_mcp_and_run_tool_loop`
    (the shared MCP tool-execution helper for both `chat.py` and
    `source_chat.py` since v0.8.16) called `await tool.coroutine(**args)`
    with NO timeout. An MCP tool that hangs — slow web fetch, server
    stuck, network black hole, MCP server crashed mid-response — used
    to block the whole chat turn indefinitely. `/chat/execute` was
    bounded by the v0.7.99 outer wrap (`ONP_CHAT_TIMEOUT_SEC`,
    default 300s) so the request eventually 504'd, but
    `/chat/stream` only halts on client disconnect — a hung tool
    froze the user's stream until they reloaded the tab.
  - **CLAUDE.md standing audit ties:** "missing timeouts" is named
    in the recurring-footgun list. The fix matches the per-call
    timeout pattern already used in `api/chat_service.py:_DEFAULT_TIMEOUT`
    and the v0.7.99 chat-wrap in `api/routers/chat.py`.
  - **Fix:** Wrapped `tool.coroutine(**args)` in `asyncio.wait_for`
    with a budget from `ONP_MCP_TOOL_TIMEOUT_SEC` (default 30s,
    parsed same way as `ONP_CHAT_TIMEOUT_SEC`). On timeout, re-raise
    as a plain `Exception("timed out after Ns")` so the existing
    `except Exception as tool_exc` branch converts it into the
    standard `f"Tool {name!r} failed: ..."` ToolMessage — same
    error-feedback channel any other tool failure uses, so the model
    can adapt (apologize, try a different tool, give up) instead of
    the stream freezing forever. 30s default is generous: typical
    MCP web-search/fetch tools complete in 1-5s; a tool taking 30s
    is almost certainly broken. Tightened the chat.py imports
    (added `import asyncio` + `import os` at the top instead of
    inline-importing).
  - **Test:** `tests/test_v0_8_35e_mcp_tool_timeout.py` — 3 tests
    using `pytest.mark.asyncio` + scripted `_ScriptedModel`/
    `_FakeAIMessage` fakes. (1) Fast tool: regression guard — a
    sub-millisecond tool returns its real result, not a timeout
    string. (2) Hanging tool: with `ONP_MCP_TOOL_TIMEOUT_SEC=0.05`
    and a `_hang` tool that `asyncio.sleep(60)`s, the loop completes
    within an outer 5s safety bound and the second-round payload
    contains a ToolMessage mentioning the timeout. (3) Env-unset
    default: a 0.5s tool completes under the 30s default — guards
    against an overly-aggressive default that would false-timeout
    legitimate slow tools. All 3 pass. Regression sweep:
    `test_chat_stream.py` (5), `test_v0_8_1_stream_selected_provider.py`
    (4), `test_v0_8_35_health_cache_single_flight.py` (2),
    `test_v0_8_35d_gmail_single_flight.py` (2), `test_gmail_cache.py`
    (4) — 20/20 unaffected.

- **⚡🐛 v0.8.35d — `GmailIntegration.get()` thundering-herd race (same family as v0.8.35b)**
  - **Bug:** While sweeping the codebase for the same TTL-cache
    anti-pattern fixed in v0.8.35b for `_local_chat_healthy_cached`,
    found the *exact* same race in
    `open_notebook/domain/gmail.py:GmailIntegration.get()`. The
    v0.7.157 cache fixed the SECOND-caller case but the v0.7.157
    comment itself names the FIRST-caller race that wasn't closed —
    "the /settings/api-keys page mounts BOTH the GmailIntegration
    panel AND the GmailSidebarButton, so two concurrent slow queries
    fire on every cold load → 8+ seconds of perceived freeze."
    Both callers raced past the cache check, both fired `repo_query`,
    both wrote the same result. The 30-second TTL hit only worked
    once the first query completed — but the second concurrent
    caller had already started its own query before that point.
  - **Fix:** Same single-flight pattern as v0.8.35b:
    lazily-constructed `asyncio.Lock` (`_CACHE_LOCK` +
    `_get_cache_lock()`) wraps the cache-miss path with a
    double-check on the cache state inside the lock. Cache-HIT
    callers skip the lock entirely so steady-state polls have zero
    extra latency. The query, timeout/exception handling, row
    parsing, Fernet decryption, AND the cache write are now all
    inside the lock so followers always re-read the cache before
    deciding whether to query.
  - **Test:** `tests/test_v0_8_35d_gmail_single_flight.py` — 2 new
    tests with the same shape as the v0.8.35b suite. (1)
    Concurrency: 5 `asyncio.gather`'d cache-miss callers with a
    100ms-slow `repo_query` AsyncMock must result in exactly 1
    query. Pre-fix the assertion fails with `5`. (2) Cache-hit
    non-serialization: with a pre-populated cache, 3 concurrent
    callers must never reach the query. Both pass after the fix.
    Existing 4 `test_gmail_cache.py` regressions and the 7
    `test_gmail_router.py` tests all still pass.

- **✨🎨 v0.8.35c — local/cloud routing badge on AI messages**
  - **Feature:** Small `<ChatMessageProviderBadge>` chip (live in
    `frontend/src/components/chat/ChatMessageProviderBadge.tsx`)
    renders next to AI messages when the smart router decided this
    turn, showing "local" or "cloud" with a tooltip carrying the
    actual model ID. Closes the user-visible loop of v0.8.1: the
    routing decision is now plumbed all the way from `pick_provider()`
    → graph state → /chat/execute & /chat/stream response → TanStack
    cache → chip in the message list.
  - **Plumbing:**
    1. `useNotebookChat.ts` on the `done` SSE event now stashes
       `{ selected_provider, selected_model_id }` in the TanStack
       Query cache keyed by the last AI message ID
       (`['chat', 'selected-provider', messageId]`). Same pattern as
       v0.8.1 Item 3's `mcp_tool_calls` stash.
    2. `ChatPanel.tsx` renders the new badge alongside
       `MessageActions` for AI messages. The badge reads from the
       cache only (no fetch); renders null when no cache entry
       exists (source-chat, pre-v0.8.1 sessions) or when
       `selected_provider` is null (smart routing didn't run / explicit
       model_override).
  - **i18n:** Badge labels and tooltip use i18next's `defaultValue`
    fallback (no locale entries required), keeping the locale-parity
    test (`src/lib/locales/index.test.ts`) green without 7×4 = 28
    locale rows of translation churn.
  - **Tests:** new
    `frontend/src/components/chat/ChatMessageProviderBadge.test.tsx`
    covers 5 cases — local pick, cloud pick, no cache entry (null
    render), explicit-null cache entry (null render), and the
    model-id-missing edge case (tooltip falls back to the
    provider-only string, no `{{model}}` interpolation marker leaks).
    Full frontend suite: 129/129 passing including locale parity
    (23 files, ~44s). TypeScript `tsc --noEmit` and ESLint on the
    touched files both clean.

- **⚡🐛 v0.8.35b — `_local_chat_healthy_cached` thundering-herd race**
  - **Bug:** `open_notebook/ai/provision.py:_local_chat_healthy_cached`
    had no single-flight guard. When N concurrent chat requests (e.g.
    user with multiple tabs open) hit a TTL boundary at the same time,
    each coroutine independently entered the cache-miss branch and
    called `await asyncio.to_thread(probe_all_local_models, ...)` in
    parallel. The probe itself takes up to ~9s (httpx structured
    timeout) so N concurrent callers = N × 9s of duplicate work on
    the local sidecar every 30s TTL window. The existing
    `TestHealthCacheTTL` regression only drove the helper
    sequentially, so the race was invisible to the test suite.
  - **Fix:** Added a lazily-constructed `asyncio.Lock` and reworked
    the function into a fast cache-hit path (no lock) + a single-
    flight slow path (`async with`). The first cache-miss caller
    acquires the lock, probes, and writes the cache; subsequent
    callers wait, re-check the cache under the lock, and return the
    leader's result without probing. Cache-hit callers never touch
    the lock so steady-state latency is unchanged.
  - **Test:** `tests/test_v0_8_35_health_cache_single_flight.py` —
    new file with two cases. (1) Concurrency test: 5 cache-miss
    callers in `asyncio.gather` with a 100ms slow-probe stub must
    result in exactly 1 probe call. Pre-fix the assertion fails with
    `5 probes`. (2) Cache-hit non-serialization: with a pre-populated
    cache, 3 concurrent callers must never reach the probe. Both
    pass after the fix; the existing `TestHealthCacheTTL` and v0.8.1
    plumbing tests are unaffected.

- **✨🐛 v0.8.35 — `/chat/stream` now surfaces `selected_provider` + Pydantic-fallback bug fix**
  - **Feature:** The streaming endpoint's `done` NDJSON event now
    carries `selected_provider` ("local"/"cloud"/null) and
    `selected_model_id`, matching the `/chat/execute` response shape
    added in the original v0.8.1 work. SSE clients (frontend
    `useNotebookChat`) no longer need a follow-up GET to discover
    which side served the turn. Fields are ALWAYS present in the
    wire payload (null when smart routing didn't run), keeping the
    shape stable for destructuring consumers.
  - **Bug fix (closely related, found during the streaming audit):**
    in `api/routers/chat.py` `_stream_chat_events()`, the
    `on_chain_end` capture's Pydantic-state fallback synthesised
    `final_result = {"messages": msgs, "mcp_tool_calls": mcp_calls_raw}`
    — which silently dropped `selected_provider` and
    `selected_model_id` whenever LangGraph emitted a Pydantic-typed
    state instead of a dict. The dict path was OK; the Pydantic path
    erased the routing decision. The synthetic dict now includes all
    four keys, with the same dict-vs-attribute dual-read guard the
    rest of the file uses.
  - **Frontend type sync:** `frontend/src/lib/api/chat.ts` —
    `ChatStreamEvent` `done` variant and `sendMessage` return type
    updated to declare the new fields, so TS consumers compile
    cleanly. Existing destructure of `event.messages` keeps working
    unchanged.
  - **Tests:** new `tests/test_v0_8_1_stream_selected_provider.py`
    covers four cases — dict output local, dict output cloud, dict
    output without the keys (wire-shape stability check), AND the
    Pydantic-state regression that motivated the bug fix. All four
    pass alongside the existing 5 `tests/test_chat_stream.py` cases
    and the 7 `tests/test_v0_8_1_selected_provider.py` cases (16
    total in the chat-platform surface).

- **🐛 v0.8.34 — useAsk hook missing BUFFER_MAX cap (parity with useSourceChat)**
  - `frontend/src/lib/hooks/use-ask.ts` accumulates the SSE stream
    chunks into `buffer` and splits on `\n`. The companion hook
    `useSourceChat.ts` got a 4 MiB BUFFER_MAX cap in v0.7.49 to
    defend against a stream that never emits a newline (server
    bug, transport corruption) and would otherwise grow `buffer`
    unbounded. `use-ask.ts` was missed in that pass.
  - Low severity in practice — the ask endpoint is server-
    controlled, so the threat is server-bug-induced rather than
    adversarial. But defense-in-depth says match the pattern;
    a long-running browser tab with a hung ask stream now fails
    fast instead of OOMing.

- **🐛 v0.8.33 — GmailIntegration.get() silent-except trailing cleanup**
  - `open_notebook/domain/gmail.py:202` was the last unlogged
    `except Exception: return cls()` in the silent-swallow family
    (v0.8.19 + v0.8.27 + v0.8.28 + v0.8.29 + v0.8.33 = 8 sites
    total this session). The TimeoutError branch right above logs
    WARNING but the broad-Exception branch was silent.
  - Impact: when a transient DB error (connection drop, auth fail,
    schema mismatch) hits the cache-miss path of `GmailIntegration.get()`,
    the UI silently displayed "Connect Gmail" as if the user had never
    configured the integration — no signal in launcher.log to
    correlate. Recoverable on next successful poll, but confusing
    and indistinguishable from genuine unconfigured state.
  - Fix: log WARNING on non-timeout DB errors, mirroring the
    TimeoutError handler immediately above for symmetry.

- **🧪 v0.8.32 — memory_recall integration test (v0.8.30 lesson operationalized)**
  - v0.8.30 documented that the v0.8.19 fix was incomplete because
    the unit tests only mocked `repo_query` — SurrealDB's real query
    parser was never exercised. The lesson: any SurrealQL change
    needs at least one integration-style test against a real query
    parser.
  - This commit ships that test as `tests/integration/test_memory_recall.py`.
    Gated by `SURREAL_INTEGRATION=1` (same machinery as
    `test_notebook_lifecycle.py`); mints a throwaway namespace, runs
    the full migration set, exercises `recall_recent_memory()` against
    real `memory_fact` / `memory_preference` rows, then REMOVE
    NAMESPACE on teardown.
  - **Coverage:**
    - `test_recall_recent_memory_against_real_surrealdb`: inserts 2
      facts + 2 preferences with distinct `created_at`, asserts the
      response has the right shape AND ordering (newest first).
      Would have failed against the v0.8.18 / v0.8.19 state with the
      parser error `Missing order idiom`.
    - `test_safe_select_query_shape_does_not_raise`: smallest possible
      regression guard — runs each query against an empty table and
      asserts no WARNING-level exception fires. Catches a future
      refactor that drops `created_at` from the projection or
      reintroduces `SELECT VALUE` even when no rows exist.
  - Both tests correctly skip when `SURREAL_INTEGRATION` is unset
    (verified locally: `2 skipped in 0.06s`). Running them requires
    a live SurrealDB at `ws://localhost:8000/rpc` and the env var
    set, matching the existing integration suite UX.
  - **Why this matters going forward:** the existing integration
    job in CI now has a memory-recall regression guard. If a future
    SurrealDB version (or schema change) re-introduces the "Missing
    order idiom" error, CI fails immediately instead of the bug
    being silently shipped and surfaced by a user months later
    (the v0.7.71 → v0.8.29 story).

- **🐛 v0.8.31 — mcp.py router violated the v0.7.135 HTTPException re-raise convention**
  - The v0.8.30 follow-up sweep (run full test suite to catch silent
    regressions) found the v0.7.135 AST meta test failing:
    ```
    mcp.py:53: create_mcp_server() try/except at line 53 converts any
    Exception to HTTPException(500) but lacks an `except HTTPException:
    raise` clause earlier — typed 4xx/5xx exceptions raised inside the
    try will be clobbered to 500.
    ```
    Today `repo_create` doesn't raise `HTTPException` so the bare
    `raise` at the end of the generic branch still propagates it
    correctly, but the v0.7.135 convention guards against future
    refactors of the repo layer adding typed HTTP errors.
  - Fix: add `except HTTPException: raise` before the generic
    `except Exception:` branch.
  - Suite: 33 meta tests pass.

- **🐛 v0.8.30 CRITICAL — v0.8.19 memory recall fix was incomplete; STILL returning empty**
  - **Discovered while running the full test suite this session** — a
    long-tail validation that the cumulative fixes don't regress
    surfaced `tests/test_chat_history_cap.py::test_call_model_invokes_trimming`
    against a live SurrealDB. The warning log (added in v0.8.19's
    severity bump) showed:
    ```
    Parse error: Missing order idiom `created_at` in statement selection
     | SELECT text FROM memory_preference ORDER BY created_at DESC LIMIT $limit
    ```
    SurrealDB rejects this query even though v0.8.19 already dropped
    the `VALUE` projection.
  - **What v0.8.19 missed:** the "Missing order idiom" error is not
    just about `VALUE`. SurrealDB requires the `ORDER BY` field to
    ALSO be present in the projection — period. `SELECT text FROM x
    ORDER BY created_at DESC` fails for the same reason
    `SELECT VALUE text FROM x ORDER BY created_at DESC` failed.
    v0.8.19's severity-bumped WARNING log fired on every chat turn
    across v0.8.19 → v0.8.29, but I only noticed it now when the
    test surfaced the underlying exception.
  - **Net effect:** memory recall has been silently returning empty
    every chat turn from v0.7.71 (original bug) through v0.8.29
    (incomplete fix) — that's **a full year of chat turns where
    memory was claimed to work but never recalled a single fact**.
    The user-visible symptom (chat assistant doesn't remember
    previous facts) was attributed to LLM behavior, not infrastructure.
  - **The v0.8.19 fix-blameworthy aspect:** I described v0.8.19 as
    closing the bug but didn't actually validate the fix against a
    live SurrealDB. The unit tests only mocked `repo_query` so the
    SurrealDB-level rejection was never exercised. Lesson: any
    SurrealQL change needs at least one integration-style test that
    talks to the real query parser. Adding this as a follow-up note
    on the v0.8.30 ticket.
  - **Fix:** add `created_at` to the SELECT projection in both
    queries:
    ```sql
    SELECT text, created_at FROM memory_fact
      ORDER BY created_at DESC LIMIT $limit
    ```
    `_coerce_text` already extracts only the `text` field from dicts,
    so consumers are unchanged.
  - **Test:** extended the existing
    `test_recall_recent_memory_uses_select_text_not_select_value`
    in `tests/test_memory_recall.py` with an additional assertion
    that `created_at` appears IN the projection (before `FROM`).
    A future refactor that drops the field would fail the test.
  - Suite: 28 memory_recall tests pass (27 existing + the
    extended assertion), zero regressions.

- **🐛 v0.8.29 — `_check_provider_has_credential` silently masked DB errors (final loose end of the silent-except sweep)**
  - `api/routers/models.py:116-123` had `except Exception: pass`
    followed by `return False` — same silent-swallow anti-pattern as
    v0.8.19/v0.8.27/v0.8.28 but at a smaller blast radius: the
    `/providers` status endpoint's `has_cred or has_env` fallback
    (line 418) covers the env-var path, so a DB blip only
    misreports providers configured exclusively via DB credentials,
    until the next poll succeeds.
  - **Cross-file anti-pattern sweep summary** for this session
    (v0.8.19 → v0.8.29) — the silent-except family was the most
    productive: **7 sites closed**, all with the same shape
    (`except Exception` → return sentinel → no log). The fix
    pattern is also consistent: classify schema-error/genuine-bug
    vs. table-missing/expected-bootstrap, log accordingly.
  - **Sweep audit yield trend:** v0.8.19 (memory recall, CRITICAL) →
    v0.8.27 (digest, MEDIUM) → v0.8.28 (4 sites, MEDIUM-LOW) →
    v0.8.29 (1 site, LOW). The anti-pattern grep is hitting
    diminishing returns. With no remaining "real" sites of this
    shape in `api/` / `open_notebook/` / `commands/`, the silent-
    except family is closed for this session.
  - Fix: emit `logger.debug(...)` naming the provider and the error.
    DEBUG (not WARNING) because the endpoint is polled by the Settings
    UI and a sustained DB outage would spam launcher.log on every
    poll. DEBUG keeps the diagnostic available without the noise.
  - **Test:** 1 new in `tests/test_models_api.py`:
    `test_v0829_check_provider_has_credential_logs_debug_on_db_error`
    mocks `Credential.get_by_provider` to raise; asserts the
    function still returns False (correct fallback) AND a DEBUG line
    naming the provider is emitted.
  - Suite: 13 model API tests pass (12 existing + 1 new), zero
    regressions.

- **🐛🔒 v0.8.28 — silent-except sweep closing 4 remaining sites with no logging**
  - After v0.8.19 (memory_recall) and v0.8.27 (digest) closed the two
    highest-impact instances of `except Exception: return <sentinel>`
    with no log, this iteration's grep across `api/`, `open_notebook/`,
    and `commands/` surfaced **four more** sites with the same shape:
    - `open_notebook/domain/gmail.py:_fernet` — **silent at security
      boundary**. When `OPEN_NOTEBOOK_ENCRYPTION_KEY` is set but
      `Fernet(fkey)` raises (cryptography library bug, binary-garbage
      env var), pre-v0.8.28 returned None silently. The downstream
      `_enc` then raised `"OPEN_NOTEBOOK_ENCRYPTION_KEY not set"` —
      **misleading the operator** about the real root cause. Fix:
      WARNING with explicit "the downstream RuntimeError saying the
      key is unset is misleading — real cause is here".
    - `open_notebook/domain/gmail.py:_dec` — split the over-broad
      `except (InvalidToken, Exception)`. `InvalidToken` is the
      canonical key-rotation case and stays quiet (otherwise every
      legacy unencrypted row would WARN on read). Anything else
      (binary garbage, cryptography lib bug, OOM) → WARNING so
      Gmail integration doesn't silently disappear.
    - `open_notebook/database/async_migrate.py:get_all_versions` —
      classify like v0.8.19/v0.8.27: `"Table missing"` → DEBUG
      (bootstrap case; fresh installs would otherwise spam WARN on
      every startup); anything else → WARNING with the consequence
      explicit ("treating as version 0, which may cause already-
      applied migrations to re-run").
    - `open_notebook/utils/chunking.py:detect_content_type_from_extension` —
      Path/.suffix is normally infallible but exotic input (embedded
      null byte, etc.) could raise. DEBUG only because the fallback
      to heuristic detection is correct — but a recurring failure
      should be diagnosable.
  - **Pattern observation:** the silent-swallow anti-pattern has now
    surfaced 6 times in this session (v0.8.19 memory_recall, v0.8.27
    digest, and the 4 here in v0.8.28). The grep `except Exception`
    + sentinel return is a high-yield audit query — anywhere a
    function returns `[]`/`None`/`False`/`{}`/`""` after an exception
    with no log is a hiding place where downstream symptoms become
    impossible to triage.
  - **Tests** (7 new in `tests/test_v0_8_28_silent_swallow_sweep.py`):
    - `test_v0828_fernet_logs_warning_on_construction_failure`:
      mocks `Fernet` to raise; asserts WARNING with the "misleading"
      verbiage.
    - `test_v0828_fernet_silent_when_key_unset`: confirms the
      intentional no-key path stays quiet.
    - `test_v0828_dec_quiet_on_invalid_token`: ensures
      `InvalidToken` does NOT log (key-rotation case).
    - `test_v0828_dec_warns_on_unexpected_exception`: ensures
      non-`InvalidToken` errors DO log WARNING.
    - `test_v0828_get_all_versions_debug_on_table_missing`: fresh
      install stays DEBUG (no startup WARN noise).
    - `test_v0828_get_all_versions_warns_on_other_errors`:
      connection drops WARN with "re-run" verbiage.
    - `test_v0828_detect_content_type_logs_debug_on_exception`:
      DEBUG only for the low-impact fallback.
  - Suite: 53 tests pass across gmail/chunking/digest/v0.8.28
    (46 existing + 7 new), zero regressions.

- **🐛 v0.8.27 — digest builder silently swallowed query errors (same shape as v0.8.19)**
  - `open_notebook/digest/__init__.py:_safe_query` did
    `except Exception: return []` with **no log** — identical to the
    v0.8.19 memory_recall bug that hid a production-broken SurrealDB
    query for 50+ releases. If any of the digest's six queries fails
    (schema mismatch, syntax issue, driver bump, the same `SELECT VALUE`
    + `ORDER BY` class of error that v0.8.19 fixed elsewhere), the
    digest silently omits that section — and the user just sees
    "no activity in the digest window" instead of an alarm.
  - **Why this hid:** the digest scheduler runs every 5 minutes
    (`open_notebook/digest/scheduler.py:_TICK_INTERVAL_SEC=300`). A
    silent-swallow on a broken query produces no log and the user-
    visible symptom is just "fewer items in the digest email" — which
    looks indistinguishable from a quiet week. The audit pattern that
    surfaced this is the same as v0.8.19: any `except Exception:`
    that returns a sentinel without logging is a hiding place.
  - **Fix:** classify the exception:
    - `"Table missing"` / `"table does not exist"` → DEBUG (benign
      fresh-install case — `memory_fact` may not exist before the
      first memory write; DEBUG so launcher.log isn't spammed every
      5-minute tick).
    - `"Parse error"` / `"Missing order idiom"` / `"Idiom missing"` /
      `"unexpected token"` → WARNING tagged `SCHEMA ERROR` so a
      future SurrealDB upgrade that breaks one of the digest queries
      surfaces loudly in launcher.log.
    - Everything else → WARNING. Better to err on visibility — an
      unknown error suppressing a section is still a bug.
    Same substring lists as v0.8.19's `_safe_select` in
    `open_notebook/utils/memory_recall.py` so a future SurrealDB
    upgrade only needs one place updated.
  - **Tests:** 3 new in `tests/test_digest_builder.py`:
    - `test_v0827_safe_query_logs_warning_on_schema_error`:
      injects a "Parse error: Missing order idiom" RuntimeError;
      asserts a WARNING is emitted with `SCHEMA ERROR` token.
      Uses a loguru sink (loguru bypasses stdlib `caplog`).
    - `test_v0827_safe_query_stays_debug_on_table_missing`:
      injects "Table missing"; asserts DEBUG only, no
      WARNING/ERROR (otherwise launcher.log spams every 5 min).
    - `test_v0827_safe_query_logs_warning_on_unknown_error`:
      injects "ConnectionDropped: WS frame 0x4F2C"; asserts
      WARNING fires (visibility wins on unknown errors).
  - Suite: 9 digest tests pass (6 existing + 3 new), zero regressions.

- **🐛⚡ v0.8.26 — transformation + prompt graphs were missing per-node LLM timeouts**
  - `open_notebook/graphs/transformation.py:121` and
    `open_notebook/graphs/prompt.py:40` both called
    `chain.ainvoke(payload)` with **no timeout**. v0.7.138 added per-node
    timeouts to `ask.py` (`_ask_invoke()` wraps `asyncio.wait_for`)
    but the same sweep missed transformation + prompt — both have the
    identical bug shape: a wedged local LLM mid-generation pins the
    caller indefinitely.
  - **Why this matters more for transformation**: it's invoked from
    `source_graph` (`open_notebook/graphs/source.py:180`), which runs
    inside the `process_source` surreal-commands worker.
    `commands/source_commands.py` configures retry with
    `max_attempts=15`, `wait_max=120s`. A single wedged transformation
    could keep the worker slot unavailable for **roughly half an hour**
    before surreal_commands gave up, backing up the entire ingest
    queue. The `/transformations/execute` endpoint has an outer
    timeout (v0.7.95), but the graph-internal call from `source_graph`
    does not — that's the gap.
  - **Why prompt graph too**: notes router (`api/routers/notes.py:89`)
    invokes the prompt graph for title generation. Same wedge =
    same indefinite hang.
  - Fix: introduce `_transform_node_timeout_sec()` in
    `transformation.py` reading `ONP_TRANSFORM_NODE_TIMEOUT_SEC`
    (default 180s — more generous than ask.py's 120s because
    transformations run over capped source content, not just a query).
    Wrap both `chain.ainvoke` calls in `asyncio.wait_for` and raise
    `ExternalServiceError` on timeout. The prompt graph imports the
    shared helper so both files use one env knob.
  - **Tests** (8 new in `tests/test_v0_8_26_graph_node_timeouts.py`):
    - `test_v0826_timeout_default_is_180_seconds`
    - `test_v0826_timeout_respects_env_var`
    - `test_v0826_timeout_falls_back_on_garbage_value`
    - `test_v0826_timeout_falls_back_on_negative_value`
    - `test_v0826_transformation_graph_times_out` — substitutes a
      chain whose ainvoke sleeps 5s vs. a 0.1s timeout, asserts
      ExternalServiceError fires with the env-knob name in the message
    - `test_v0826_prompt_graph_times_out` — same shape, prompt graph
    - `test_v0826_transformation_uses_wait_for` — source-text pin
    - `test_v0826_prompt_uses_wait_for` — source-text pin
  - Suite: 24 graph + transformation tests pass (16 existing + 8 new).

- **🔒 v0.8.25 — onp theme endpoint leaked exception detail on save failure (fourth & final sweep site)**
  - `api/routers/onp.py:97` in `POST /onp/theme` had:
    ```python
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    ```
    `new_cfg.save(path)` can raise:
    - `OSError` / `PermissionError` carrying the resolved config-file
      path under the user's home directory
    - `JSONEncodeError` if a dataclass field is corrupted
    - Various internal errors from `Config.save`
    `str(exc)` for OS errors typically reads like
    `"[Errno 13] Permission denied: '/Users/operator/.open-notebook-plus/config.toml'"`
    — leaking the operator's home directory layout into the response.
  - **Systematic sweep** done this iteration: grep'd all 30+ `detail=str(e)`
    / `detail=str(exc)` sites across `api/`, triaged each:
    - All 400-status sites were `except InvalidInputError` or
      `except ValueError` paths with user-controlled messages — safe.
    - `studio.py:540` (`except InvalidInputError`) and
      `launcher_prefs.py:82` (`except ValueError`) — safe.
    - File-I/O paths in `exports.py`, `filesystem.py`, `launcher_prefs.py`
      include operator-controlled paths + OS error text — borderline,
      deferred (low severity for single-user local-deploy).
    - **`onp.py:97` was the only remaining broad-Exception 500 leak.**
    Sweep concludes the v0.7.177 → v0.8.22 → v0.8.24 → v0.8.25 chain.
  - Fix: emit `f"Theme save failed ({type(exc).__name__}). Check
    launcher.log for details."` and log the full traceback via
    `logger.exception`.
  - **Test:** 1 new in `tests/test_onp_router.py`:
    - `test_v0825_post_theme_sanitizes_save_failure_detail`: injects
      an `OSError("[Errno 13] Permission denied: '/Users/operator/...';
      INTERNAL_TRACE: SurrealDB ws://127.0.0.1:8765")`; asserts none
      of the operator path, errno, or internal URL leaks into the
      response detail AND that the type name IS present for triage.
  - Suite: 7 onp tests pass (6 existing + 1 new), zero regressions.

- **🔒 v0.8.24 — gmail router leaked raw exception strings in two paths**
  - `api/routers/gmail.py:280` in the OAuth callback echoed
    `f"Token exchange error: {exc}"` into the HTML response page
    rendered in the user's browser. Google's token-exchange errors
    can include the OAuth `client_id`, the `redirect_uri`, and
    (in error-response bodies) hint fragments of the `client_secret`
    — operator config that shouldn't leak into the browser tab.
  - `api/routers/gmail.py:340` in `POST /onp/gmail/send-test`
    raised `HTTPException(status_code=500, detail=str(exc))`.
    The `_send_digest_now` exception can carry:
    - `build_digest_html` errors with SurrealDB internals
    - Network errors carrying request URLs + `Authorization`-header
      fragments via traceback formatting libraries
    - Email addresses from `g.email_address`
  - **Third sweep** in this audit chain: v0.7.177 swept
    `podcast_service.py`, v0.8.22 caught `credentials_service.py`,
    and v0.8.24 closes `gmail.py`. The pattern is consistent:
    auth-gated endpoints that built error responses with `str(exc)`
    were missed because their exception surfaces felt "operator-only"
    — but the v0.7.177 contract is that raw exception text NEVER
    leaves the process via response bodies regardless of the auth
    layer (reverse proxies, browser dev tools, screenshots).
  - Fix: both sites now emit `f"... ({type(exc).__name__}). Check
    launcher.log for details"`. Full detail stays in
    `log.exception()` for ops triage.
  - **Tests:** 2 new regression tests in `tests/test_gmail_router.py`:
    - `test_v0824_send_test_endpoint_sanitizes_exception_detail`:
      mocks `_send_digest_now` to raise with a payload containing
      `Bearer ya29.SECRET_TOKEN_DO_NOT_LEAK`, a SurrealDB WS frame
      ref, and an email address; asserts none leak and the type
      name IS present for triage.
    - `test_v0824_oauth_callback_sanitizes_token_exchange_error`:
      mocks the httpx.AsyncClient `post` to raise with a payload
      containing the full `client_id`, a `client_secret` hint, and
      the `redirect_uri`; asserts none leak into the HTML page body.
  - Suite: 5 gmail tests pass (3 existing + 2 new), zero regressions.
  - **Deferred — out of scope this turn:**
    - `_oauth_states` is module-level mutable state (`api/routers/gmail.py:58`).
      With multi-worker deploys, state set in worker A doesn't reach
      worker B → spurious "OAuth state mismatch" CSRF errors. Safe
      for the desktop default (workers=1), real bug for any
      production deploy. Worth a v0.8.25+ pass with a Redis or
      cookie-signed-state design decision.

- **🔒 v0.8.23 SECURITY — sources download helpers had the sibling-prefix path-traversal bug**
  - `api/routers/sources.py` `_resolve_source_file` (called by
    `GET /sources/{id}/download`) and `_is_source_file_available`
    (called by `GET /sources/{id}` to set the response's
    `file_available` field) both did:
    ```python
    safe_root = os.path.realpath(UPLOADS_FOLDER)
    resolved_path = os.path.realpath(file_path)
    if not resolved_path.startswith(safe_root):
        ...
    ```
    The `startswith(safe_root)` check has **no trailing separator**. If
    `UPLOADS_FOLDER = "/var/uploads"`, then a tampered or stale
    `source.asset.file_path` of `"/var/uploadsbypass/etc-passwd"`
    passes the check — `"/var/uploadsbypass/...".startswith("/var/uploads")`
    is `True`. The download endpoint then serves the file via
    `FileResponse`, and the source detail response misreports
    `file_available: true` for it.
  - **Same bug family** as: v0.6.31 (model_manager), v0.6.34
    (Source.delete), v0.7.2 (podcasts.py `_resolve_audio_path`). The
    v0.7.2 fix introduced the `Path.is_relative_to()` idiom for the
    podcasts router but the sources router helpers were missed. The
    other two callsites in this same file (`generate_unique_filename`
    line 109, `create_source` line 467) DO add `+ os.sep` and are
    safe — only the download helpers had the unprotected form.
  - **Attack vector severity (local-deploy):** requires a tampered
    `source.asset.file_path` value. Sources for that:
    - A future API endpoint or migration that writes raw file paths
      from user input without scoping to `UPLOADS_FOLDER`
    - Manual DB row edits by an attacker who got SurrealDB write
      access via another vector
    - A bug in `parse_source_form_data` future evolution
    The fix removes the class of bug — `is_relative_to()` cannot be
    defeated by sibling-prefix tricks.
  - Fix: switch both helpers from `os.path.realpath` + `startswith`
    to `Path(...).resolve()` + `is_relative_to()`. Matches the
    v0.7.2 podcasts.py idiom. Also tolerates `OSError`/`ValueError`
    on malformed paths (e.g. embedded null bytes) by treating them
    as "not found" rather than crashing the endpoint.
  - **Tests:** 6 new in `tests/test_sources_path_containment_v0823.py`:
    - `test_is_source_file_available_rejects_sibling_prefix` — the
      headline attack scenario: builds `<tmp>/uploads/` and
      `<tmp>/uploadsbypass/secret.txt`, asserts the helper returns
      False for the bypass path.
    - `test_is_source_file_available_accepts_legitimate_path` —
      sanity: the fix doesn't over-reject genuine paths.
    - `test_is_source_file_available_tolerates_malformed_path` —
      embedded null byte → False, not 500.
    - `test_resolve_source_file_rejects_sibling_prefix` — async
      version: asserts 403 (not 200 + wrong file bytes).
    - `test_resolve_source_file_serves_legitimate_path` — sanity.
    - `test_no_startswith_path_check_in_source_helpers` —
      AST-based contract test that pins the `is_relative_to`
      idiom in both helper bodies. Uses `ast.unparse` for exact
      function-body extraction rather than the brittle "\\n\\n"
      heuristic. Catches a future refactor that "simplifies"
      the fix back into the bug.
  - Suite: 22 sources-related tests pass (16 existing + 6 new),
    zero regressions.

- **🔒 v0.8.22 — credentials migration was leaking raw exception strings into the response**
  - Both `migrate_from_provider_config` and `migrate_from_env`
    (`api/credentials_service.py:864, :954`) wrote
    `errors.append(f"{provider}/{name}: {e}")` into the API response.
    The raw exception `e` can carry:
    - **SurrealDB driver internals**: WS frames, partial RecordIDs,
      connection-pool diagnostics, internal SurrealQL fragments
      (e.g. the `repo_query` text from line 932 / 842).
    - **Fernet ciphertext fragments**: if `Credential.save()` raises
      mid-encryption, the partial base64 string can land in
      `str(e)`.
    - **api_key prefixes**: a Pydantic validation error on a malformed
      `api_key` includes the offending value in the message.
  - Same family as the v0.7.177 sweep that sanitized
    `podcast_service.py`. The credentials_service was missed in
    that pass. Authenticated, yes — but the v0.7.177 contract is
    that raw exception text NEVER leaves the process via response
    payloads, regardless of the auth layer (reverse proxies log
    response bodies, browser dev tools show them, screenshots get
    shared).
  - Fix: emit `f"{provider}/{name}: {type(e).__name__}"` instead.
    Operator gets enough to correlate with the log line; the full
    detail is already preserved in `logger.error(..., exc_info=True)`
    that runs alongside the append.
  - **Tests:** 2 new regression tests in `tests/test_credentials_api.py`:
    - `test_migrate_from_env_sanitizes_exception_in_response` —
      injects a `RuntimeError("INTERNAL: api_key=sk-VERYSECRET; WS frame=…; encrypted=gAAAAAB…")`
      into the env migration path; asserts none of the secret
      tokens appear in `response.json()["errors"]` AND that the
      type name `"RuntimeError"` is present for triage.
    - `test_migrate_from_provider_config_sanitizes_exception` —
      same shape for the ProviderConfig migration path, injecting
      a `RuntimeError` containing `"SELECT * FROM credential api_key=sk-LEAKME-123"`
      and asserting neither the SQL fragment nor the key leak.

- **🐛 v0.8.21 — chat-hook refetch was clobbering optimistic state on rapid sends**
  - Both `useSourceChat` and `useNotebookChat` had:
    ```ts
    useEffect(() => {
      if (currentSession?.messages) setMessages(currentSession.messages)
    }, [currentSession])
    ```
    Every refetch of `currentSession` overwrote local messages — including
    the in-flight optimistic user bubble and streaming AI placeholder for
    a second rapid send. The race:
    1. User sends msg #1 → stream completes → `await refetchCurrentSession()` starts.
    2. User rapidly sends msg #2 → `setMessages(prev => [...prev, user_2_optimistic])`,
       then (as tokens arrive) `ai_2_streaming_placeholder`.
    3. The refetch from step 1 returns with only `[user_1, ai_1]` (msg #2
       not yet persisted) → useEffect fires → `setMessages([user_1, ai_1])` →
       user_2 + ai_2_streaming **WIPED from the UI**.
    4. Subsequent token deltas for msg #2 try to map onto `streamingAiId_2`,
       but that ID is no longer in local state → no-op → user only sees
       msg #2's response after its OWN stream-complete refetch.
  - **Why this hid:** requires rapid user input (within the ~100-500ms
    refetch window). Normal pacing never triggers it. Single-user
    local-deploy testing tends to be deliberate. Surfaced by an audit
    pass on the SSE consumer surfaces this session.
  - Fix: introduce `inFlightSendsRef = useRef(0)`, incremented at the
    start of `sendMessage` (BEFORE any await) and decremented in
    `finally{}` (AFTER the awaited refetch). The useEffect now skips
    its overwrite when the counter is > 0.
  - **Why a counter, not a boolean:** msg #1's `finally{}` runs while
    msg #2 is still in flight. A boolean would get cleared by msg #1's
    exit, reopening the race for msg #2's lifecycle. The counter stays
    > 0 until BOTH have settled. The `Math.max(0, ... - 1)` defends
    against future double-decrement bugs.
  - **Trade-off:** cross-tab edits made during a send don't appear in
    local state until the send finishes. Acceptable for the single-user
    local-deploy target.
  - **Test:** `frontend/src/lib/hooks/chat-race-guard.test.ts` —
    source-text contract assertion (counter declared, `=== 0` guard
    present in useEffect, `+= 1` increment present in sendMessage,
    `Math.max(0, … - 1)` decrement present in finally). Catches a
    refactor that drops or weakens the guard. Behavioral integration
    test deferred — existing test scaffolding only covers single-call
    hooks; a streaming-send simulation would need a sizable mock
    harness out of scope for this fix.

- **🐛⚡ v0.8.20 CRITICAL — sync httpx probe was blocking the FastAPI event loop**
  - The v0.8.0 health-probe surface (`open_notebook/health/local_models.py`)
    drives `httpx.Client.get()` **synchronously** with a structured 9s
    budget (connect=2.0, read=5.0, write=2.0, pool=2.0). Two production
    code paths called it from inside `async def` functions:
    1. `api/routers/local_models.py:62` — `/api/local-models/health`
       (the endpoint the frontend's sidebar badge polls every 30s).
    2. `open_notebook/ai/provision.py:155` — `_local_chat_healthy_cached()`,
       invoked on every chat turn that hits the v0.8.0 smart router.
  - In both, a wedged or slow sidecar pinned the entire FastAPI event
    loop for up to 9s — every concurrent request stalled: chat SSE
    streams froze, status polls timed out, the launcher's own status
    poll hung, the very 30s frontend poll that triggered the freeze
    came back to find the UI unresponsive. Combined with the v0.8.0
    "sequential probe" design, a single hung local model could cascade
    into a multi-second app-wide freeze every poll cycle.
  - **Why this hid for 20 patch releases:** with a healthy local
    sidecar the probe returns in 5-50ms — well under any timeout that
    would show up as user-visible jank. The bug only bit when a sidecar
    actually died mid-session (process crash, OOM kill, port collision).
    `tests/test_phase1_local_model_health.py` mocked the probe with a
    sync `lambda` (returns instantly) so the test harness gave no
    signal. This is the **16th** silently-shipped production-broken
    bug found by audit this session and the second event-loop-blocking
    bug after v0.7.55's `submit_command` fix (the same pattern shape).
  - Fix:
    1. **`_local_chat_healthy_cached` → `async def`**: wrap the inner
       `probe_all_local_models(creds)` call in `await asyncio.to_thread`
       so the blocking httpx call lands on the default executor. The
       single caller in `provision_langchain_chat_model` now awaits
       it before passing the result to the sync `pick_provider`. Cache
       semantics unchanged.
    2. **`/api/local-models/health` endpoint**: same `asyncio.to_thread`
       wrap. The launcher's sync caller at `desktop/app.py:787` keeps
       calling `probe_all_local_models` directly — it's already off
       the FastAPI event loop.
    3. Sync `probe_all_local_models` itself stays sync (launcher needs
       a synchronous entry point; no point in async-rewriting it).
  - **Test updates:**
    - Existing monkeypatches in `tests/test_phase3_smart_routing.py`
      (8 sites) and `tests/test_v0_8_1_selected_provider.py` (2 sites)
      switched from sync `lambda: True/False` to `AsyncMock(return_value=...)`
      so `await _local_chat_healthy_cached()` still resolves correctly.
    - `TestHealthCacheTTL.test_health_cache_respects_ttl` now drives
      the helper on a fresh event loop so the awaited form works.
    - 3 new regression tests in `tests/test_phase1_local_model_health.py`:
      - `test_local_chat_healthy_cached_is_awaitable` — asserts
        `inspect.iscoroutinefunction(...)` so a future sync regression
        fails loudly at import time.
      - `test_local_models_health_endpoint_yields_event_loop` —
        live `TestClient` call with a 0.5s blocking stub probe;
        proves the endpoint still returns the right shape even when
        the probe is slow.
      - `test_local_models_health_uses_to_thread` — AST/text check
        that the router source literally contains
        `asyncio.to_thread(probe_all_local_models`. Catches a future
        refactor that drops the wrap even when the runtime stubs are
        fast enough to mask the bug.

- **🐛 v0.8.19 CRITICAL — memory recall has been silently broken for many releases**
  - `recall_recent_memory()` (used on every chat turn since v0.7.71)
    ran `SELECT VALUE text FROM memory_fact ORDER BY created_at DESC`,
    which SurrealDB rejects with **"Missing order idiom in statement
    selection"** — the `VALUE` projection requires the `ORDER BY`
    field to also be projected. `_safe_select` swallowed the parse
    error at `DEBUG` level. Net effect: **memory recall returned
    empty every chat turn in production.** Users saw memory writes
    succeed and assumed recall worked; in fact no fact was ever
    surfaced to the chat system prompt. This is the **15th**
    silently-shipped production-broken bug found by audit this
    session and the longest-lived (introduced in v0.7.71).
  - Fix in two parts:
    1. Drop `VALUE` from both queries — return list of `{text: ...}`
       dicts. Downstream `_coerce_text` already handles dict shape
       per its v0.7.71 docstring, so consumers are robust.
    2. **Bump silent-swallow severity:** `_safe_select` now classifies
       SurrealDB schema/parse errors (substrings "Parse error",
       "Missing order idiom", "Idiom missing", "unexpected token")
       as `WARNING`. "Table missing" stays at `DEBUG` (genuine
       fresh-install case). Pre-v0.8.19 this exact bug was invisible
       in launcher.log unless someone enabled debug logging.
  - 3 new tests in `tests/test_memory_recall.py`: pins the SQL shape
    against regression, asserts schema errors log at WARNING, asserts
    table-missing stays at DEBUG. Suite: 25 → 28, all passing.

- **🐛📚 v0.8.17 — SearXNG/Wikipedia (free + unlimited MCP) + source-chat SSE wiring fix (CRITICAL)**
  - **Doc add:** the v0.8.15 page recommended Brave (2k/mo) and Tavily
    (1k/mo) as "free" but both are bounded + require signup. Added two
    truly-free + no-key options at the top:
      - **SearXNG** (self-hosted Docker meta-search; aggregates Google,
        Bing, DDG, Brave, Wikipedia, etc.) — unlimited, no signup.
      - **Wikipedia MCP** — no Docker, no key, encyclopedic coverage.
    Plus a decision matrix and explicit guidance that
    `Wikipedia + Fetch` covers ~80% of use cases with zero setup
    friction.
  - **CRITICAL fix:** v0.8.16 wired the source-chat graph to surface
    `mcp_tool_calls` in state, but the source-chat **SSE never emitted
    the event**. The chat graph executed MCP tools, captures populated,
    state had them — and the frontend never received them. Source-chat
    citation pill popovers always showed the v0.8.10 placeholder
    fallback even when notebook chat's pills had real payloads. This
    is the **second** instance of the v0.8.0-Phase-2 "feature wired to
    dead path" failure mode (cf. v0.8.3, v0.8.4, v0.8.7).
  - Fix: `api/routers/source_chat.py` captures `mcp_tool_calls` in the
    `on_chain_end` branch and emits `{"type": "mcp_tool_calls",
    "calls": ...}` SSE event after the canonical `ai_message` event.
    `frontend/src/lib/hooks/useSourceChat.ts` adds a parser branch
    that stashes the calls in TanStack Query cache keyed by
    `streamingAiId` — same pipeline as `useNotebookChat`.
  - Phase 2 backend: 23/23 still pass. Frontend: 114/114, tsc clean.

- **✨ v0.8.16 — Source-chat MCP integration (closes last MCP-deferred item)**
  - Pre-v0.8.16 `source_chat.py`'s `call_model_with_source_context`
    called `model.ainvoke(payload)` directly with no MCP binding. Any
    MCP server registered via Settings → MCP Servers was invisible to
    source chat — operators got MCP only on notebook chat. The v0.8.2
    docs and citation-pill UI implied parity that didn't exist.
  - Fix: extracted the v0.8.9 in-node tool execution loop from
    `chat.py:call_model_with_messages` into a reusable
    `bind_mcp_and_run_tool_loop(model, payload)` helper. Both graphs
    now call it. `SourceChatState` gained `mcp_tool_calls: Optional[list]`
    so the source-chat router can surface captures alongside the AI
    message (same v0.8.1 Item 3 pill-popover pipeline). Full chain
    works end-to-end on source chat now: v0.8.10 dynamic discovery,
    v0.8.11 StructuredTool schemas, v0.8.12 cache, v0.8.13 multi-block
    content.
  - 2 new tests pin the helper extraction works in isolation and the
    SourceChatState shape includes the new field. Phase 2 suite:
    21 → 23, all passing.

- **📚 v0.8.15 — Free MCP servers for internet search (user-facing doc)**
  - `docs/3-USER-GUIDE/free-mcp-servers-web-search.md` — three
    practical free options with setup steps: Brave Search
    (Anthropic-official, 2000 free queries/mo with a free API key),
    Fetch (Anthropic-official, no key, URL-only), and Tavily
    (community, 1000/mo free, LLM-optimized output). Includes a
    "pair them" recipe using the v0.8.1 Item 5 priority arrows so
    the model can search then fetch. Verification steps reference
    the v0.8.10 dynamic discovery, v0.8.9 in-node tool loop, and
    v0.8.13 citation-pill payloads so operators can confirm the
    full chain end-to-end.

- **🐛 v0.8.14 audit fix — launcher_prefs API exception narrowness**
  - `GET`/`PUT /api/launcher-prefs` only caught `ValueError`.
    `PermissionError` (file owned by another user / chmod 600 by
    something else) and `OSError` (read-only DMG, filesystem full,
    cross-device rename failure) would surface as 500 instead of an
    actionable 400. Added narrow `(PermissionError, OSError)` branch
    on both handlers — same 400 contract, different detail message
    pointing at the underlying fs cause.
  - Existing 4 launcher_prefs API tests still pass unchanged (the new
    branches are additive).

- **🐛 v0.8.13 — MCP non-text content blocks (images, embedded resources)**
  (closes the v0.8.10 deferred Item #1)
  - `MCPClient.call_tool` pre-v0.8.13 only returned the FIRST content
    block from the MCP server's response and only handled
    `TextContent`. `ImageContent` worked accidentally
    (`getattr(.data)` returned the base64) but lost its mime type.
    `EmbeddedResource` returned `None` because the resource's `text`/`blob`
    live one level deeper on `.resource`. Multi-block responses
    (e.g. `[text, image]` or `[text, attached_pdf]`) silently dropped
    everything after the first block.
  - Fix: walk ALL content blocks and surface them with type
    preserved. New return shape:
    `{ok, text: "<all readable text concatenated>", blocks:
    [{type, ...per-type fields}]}`. `text` stays at the top level
    so the v0.8.11 closure (`result.get("text")`) keeps working
    without change. The chat-graph closure also stashes `blocks`
    into `mcp_captures` so v0.9 frontend work can render image
    thumbnails / resource links in the pill popover.
  - For images, the inline text fallback gives the LLM a placeholder
    line like `[image: image/png, ~768 bytes]` so it at least knows
    something arrived even if it can't see the bytes.
  - Future-proof: unknown content types are surfaced as
    `{"type": "unknown", "repr": ...}` rather than dropped, so the
    next MCP content-block class surfaces immediately instead of
    silently failing.
  - 2 new tests pin the multi-block round-trip and the empty-result
    case. Phase 2 suite: 19 → 21, all passing.

- **🐛 v0.8.12 audit fixes — JSON Schema nullable types + per-turn MCP discovery cache**
  - **Fix 1 (nullable JSON Schema):** v0.8.11's
    `_json_schema_to_pydantic_model` did `type_map.get(spec.get("type"))`,
    which returned None when type was a list (real-world MCP servers
    use `"type": ["string", "null"]` for nullable fields). The None
    propagated to Pydantic and broke field construction. Now: list
    types are resolved to the first non-null primary + an optional
    nullability flag; required-but-nullable fields are correctly
    marked optional on the Pydantic model; JSON Schema `default`
    values propagate to Pydantic `Field(default, ...)`.
  - **Fix 2 (perf, real bug):** v0.8.11's `_resolve_chat_tools` called
    `MCPClient.list_tools_full()` on EVERY chat turn — an MCP
    handshake + session.initialize + list round-trip per turn
    (~50–500ms depending on server). Added a 30s TTL cache keyed by
    server URL, mirroring the v0.8.0 Phase 1 local-model health
    probe pattern. Negative results are cached too so a flaky/down
    MCP server doesn't add discovery latency to every chat turn
    until removed.
  - **Fix 3 (test pollution from #2):** the test file already had
    one test that intentionally poisons `http://x` with an empty
    discovery result; the v0.8.12 cache made that poison persist
    across tests, breaking the v0.8.9 tool-loop assertion that
    runs later. Added autouse fixture clearing the cache before
    and after every test in `test_phase2_mcp_integration.py`.
  - 3 new tests (nullable shape + positive-cache + negative-cache).
    Phase 2 suite: 16 → 19 cases, all passing.

- **✨ v0.8.11 — Tool-calling docs + StructuredTool args_schema for MCP**
  (closes the v0.8.10 deferred list)
  - `docs/4-AI-PROVIDERS/local-models-tool-calling.md` — compatibility
    matrix for every GGUF in `scripts/download_models.sh`, split into
    ✅ Supported / ⚠️ Inconsistent / ❌ Not supported. Documents the
    silent no-MCP degradation (chat works, but `bind_tools` is a
    no-op because the model wasn't fine-tuned for tool calls) and
    points operators at the right model when they have MCP servers
    registered.
  - `MCPClient.list_tools_full()` — new method returning `name +
    description + input_schema` per tool (not just names). The
    `list_tool_names` shim stays for backward compat.
  - `_resolve_chat_tools` now uses `list_tools_full` and builds a
    `StructuredTool` per discovered tool with a Pydantic `args_schema`
    derived from the server's JSON Schema via a small
    `_json_schema_to_pydantic_model` converter. `bind_tools` now sends
    the LLM the REAL arg names + types (`{name: "search",
    parameters: {query: string, limit: integer}}`) instead of the
    no-schema fallback (`{name: "search", parameters: {input: string}}`).
    Pre-v0.8.11 the LLM had to guess server arg names; now they're
    in the function-call schema.
  - The chat-graph in-node loop still uses direct
    `tool.coroutine(**args)` dispatch (v0.8.10) — works equally well
    with StructuredTool and avoids any LangChain schema-validation
    edge cases on the runtime path.
  - 1 new test pins the StructuredTool + Pydantic args_schema shape;
    Phase 2 suite 15 → 16 cases all passing.

- **🐛 v0.8.10 CRITICAL — MCP tool names hardcoded; gbrain docs promise broken integration**
  - `_resolve_chat_tools` bound `mcp_search` → `client.call_tool("web_search", ...)`
    and `mcp_fetch` → `client.call_tool("fetch_url", ...)`. Most MCP
    servers don't expose those exact names — gbrain (the integration
    documented in v0.8.2 Item B) exposes `search`/`think`/`find_trajectory`.
    Registering gbrain per the docs would have produced
    `tool not found` errors every turn. v0.8.2 Item B was a promise
    we couldn't keep.
  - Fix: discover the server's tools via `client.list_tool_names()`
    and wrap each as `mcp_<remote_name>`. Fail-soft on discovery
    failure (empty list, no tools bound, chat continues without MCP).
    `force_tool_names` test hook lets units bypass the network call.
  - Subsidiary fix: chat-graph tool loop (v0.8.9) was going through
    `Tool.ainvoke(args)` which requires an `args_schema` — without
    one, LangChain bound the dict to a single `input` kwarg and the
    closure received empty args. Captures populated but with `args={}`.
    Switched to direct `tool.coroutine(**args)` dispatch — bypasses
    the schema confusion entirely.
  - 3 new tests: discovery returns `mcp_<remote_name>` correctly;
    gbrain's real tool names (`search`, `think`, `find_trajectory`)
    bind correctly; fail-soft when discovery raises. Phase 2 suite
    13 → 15 cases, all passing.

- **🐛 v0.8.9 CRITICAL — chat graph never executed MCP tool calls (the WHOLE Phase 2/3 MCP story was broken)**
  - Audit follow-on. The chat graph is `START → agent → END` (single
    `StateGraph` node, `agent_state.add_node("agent",
    call_model_with_messages)`). `model.bind_tools(mcp_tools)` makes
    the tools VISIBLE to the LLM (schemas in the system prompt) but
    **nothing in the graph actually executes any `tool_calls` the LLM
    emits.** No `ToolNode`. No conditional edge dispatching to a tool
    executor. The `mcp_captures` accumulator from v0.8.1 Item 3 stayed
    empty forever; the `[mcp:N]` markers in the LLM's text were pure
    hallucination (system prompt told it to emit them when it "called"
    a tool, but no call ever happened); the citation pill popovers
    always showed the v0.8.1 Item 3 placeholder fallback.
  - Fix: in-node tool execution loop in `call_model_with_messages`.
    After the first `model.ainvoke(payload)`, if the AI message has
    `tool_calls`, iterate them, await each matching tool's coroutine
    (which fires the v0.8.1 captures path), feed the results back as
    `ToolMessage`s, and re-invoke the model. Loop up to
    `MAX_TOOL_ITERATIONS=4` (runaway safety against models that
    spin on tool calls). Keeps the graph topology unchanged — no
    separate ToolNode — so `/chat/execute`'s message-list extraction
    keeps working.
  - 2 new tests in `tests/test_phase2_mcp_integration.py` pin: (a)
    end-to-end execution loop (model emits tool_call → tool fires →
    ToolMessage fed back → second model invocation → captures
    populated → final answer returned), (b) runaway bound holds at
    ≤5 total invocations even if the model never stops emitting
    tool_calls.

- **🐛 v0.8.8 — `launcher_prefs` whitelist leaked on read paths**
  - Audit follow-on. v0.8.6 Item D enforced the 5-key whitelist on
    WRITES via `update_prefs` but `get_prefs` returned all file keys
    verbatim and `merge_with_env` wrote all file keys into the env
    dict. A `launcher.env` with `MY_SECRET=foo` (from a pre-whitelist
    history, a manual edit, or a future release that drops a key from
    the whitelist) leaked them through `GET /api/launcher-prefs` AND
    into `os.environ` at launcher startup.
  - Fix: both READ paths now filter to `ALLOWED_KEYS`. Defense in
    depth so the whitelist holds even if one layer misbehaves.
  - Also: `merge_with_env` silently swallowed `ValueError` on
    malformed files (operator edited one line wrong → all prefs
    reverted with no indication). Now logs a `WARNING` to launcher.log
    with the parse error and a recovery hint, still non-fatal so a
    broken `launcher.env` can't block startup.
  - 3 new tests in `desktop/tests/test_launcher_prefs.py`. Suite grew
    8 → 11 cases, all passing.

- **🐛 v0.8.7 CRITICAL — propagate launcher's auto-detected n_ctx into `OPEN_NOTEBOOK_LOCAL_N_CTX`** (closes the last v0.8.5 follow-on)
  - The launcher's `_spawn_llamacpp_chat` resolves the chat-LLM n_ctx
    (env override → GGUF metadata autodetect → `ctx_max` cap), but that
    resolution lived INSIDE the spawn function — too late to propagate
    into `session_env`. So even after v0.8.5's precedence-chain fix,
    operators with high-capacity GGUFs and no explicit env override
    still saw the router default to 32768 instead of, e.g., Hermes-3's
    native 131k. Router conservatively over-routed to cloud for
    33k–131k prompts when local could have handled them.
  - Fix: extracted resolution into `Supervisor._resolve_chat_llm_n_ctx()`,
    called from `start_all()` BEFORE `session_env` is built, cached on
    `self.chat_llm_n_ctx`. `session_env` exports
    `OPEN_NOTEBOOK_LOCAL_N_CTX=str(self.chat_llm_n_ctx)`; v0.8.5
    precedence still honors an explicit operator override.
    `_spawn_llamacpp_chat` now reads `self.chat_llm_n_ctx` for its
    `--n_ctx` argv — single source of truth.
  - 2 new tests pin propagation + in-memory/env single-source-of-truth.
    Full launcher suite (31 tests) passes; v0.7.206 n_ctx tests
    unchanged.

- **✨ v0.8.6 Item D — Settings UI for launcher env vars**
  - `desktop/launcher_prefs.py` (new) — read/write
    `~/.open-notebook-plus/launcher.env` with a strict key whitelist
    so accidental secrets can't land in it. `Supervisor.start_all`
    merges the file values into `os.environ` (env wins) at the top
    of the start sequence so every downstream reader sees them.
  - `api/routers/launcher_prefs.py` (new) — `GET`/`PUT`
    `/api/launcher-prefs`, admin-auth-protected.
  - `frontend/src/app/(dashboard)/settings/launcher-prefs/page.tsx`
    (new) — form with four fields covering speculative-decoding
    knobs (`OPEN_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH`,
    `OPEN_NOTEBOOK_LOCAL_DRAFT_N_PREDICT`) and n_ctx tuning
    (`ONP_CHAT_LLM_CTX`, `ONP_CHAT_LLM_CTX_MAX`). Saves show a
    "restart launcher to apply" banner since env is only read at
    startup. Sidebar nav entry added under Manage. Full i18n
    across 10 locales.

---

## v0.8.5 — 2026-05-25 — Smart-router production fixes + speculative decoding

- **v0.8.5** Sync anchor for `test_version_matches_changelog`; pins
  `desktop/__init__.py:__version__` to the headline release tag for this
  rollup. See sections below for per-bullet detail.

This release ships everything in the v0.8.1 → v0.8.5 lines as a single tag.
The headline is that the v0.8.0 Phase 3 smart router (local-vs-cloud per
chat turn) was **broken in production in four different ways** — every
one of them silently shipped, and every one of them is fixed now:

1. **v0.8.1 #2** — `cloud_model_id` fell back to `DefaultModels.default_chat_model`,
   which often pointed at a local model → cloud branch silently masqueraded
   as local. Fixed by adding the dedicated `auto_route_cloud` field (migration 18).
2. **v0.8.3** — speculative decoding (v0.8.2 Item A) was wired into
   `LlamaCppProvider`, which had been deprecated as a spawn path since v0.7.193.
   Operators following the v0.8.2 docs got zero speedup. Fixed by moving the
   wiring into `Supervisor._spawn_llamacpp_chat` (the LIVE spawn path).
4. **v0.8.4** — `OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL` was read by the router's
   health probe but never set in production. `_local_chat_healthy_cached()`
   always returned `False`, so the "prefer local when healthy" branch was
   unreachable. Fixed by exporting it in `session_env`.
5. **v0.8.5** — `OPEN_NOTEBOOK_LOCAL_N_CTX` (router) and `ONP_CHAT_LLM_CTX`
   (launcher) were two names for the same concept with no cross-talk.
   Low-RAM operators setting `ONP_CHAT_LLM_CTX=8192` got their sidecar
   bound at 8k but the router still assumed 32k headroom →
   `400 context_length_exceeded`. Fixed by reading either env var with a
   precedence chain.

Plus three smaller items that round out the local-models story:

- **v0.8.1 Item 1** — `selected_provider` end-to-end on `/chat/execute` so
  verify scripts can auto-assert routing.
- **v0.8.1 Item 3** — MCP tool-call payloads stashed in TanStack Query cache;
  citation pills now show real search/fetch results instead of placeholder.
- **v0.8.1 Items 4 + 5** — `useInsight` hook for insight pills (was wrongly
  fetching via `useSource`); priority-based MCP server ordering with ▲/▼
  reorder UI in Settings.
- **v0.8.2 Items A/B/C** — `--model_draft` + `--n_predict_draft` flags for
  llama.cpp speculative decoding (now correctly wired to the live spawn
  path); gbrain MCP integration docs at `docs/3-USER-GUIDE/integrating-gbrain-mcp.md`.

### Backend test counts after v0.8.5
- Phase 1-4 backend: 60/60 passing.
- Launcher + provider: 42/42 passing.
- Phase 3 routing alone: 22/22 (was 18 in v0.8.0).
- Frontend: 110/110 passing.

### v0.8.5 commit chain (all on `desktop-app`)

`b95cc5a` v0.8.5: router/launcher n_ctx desync — read either env var
`c110aaf` v0.8.4 CRITICAL: export OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL in session_env
`dcdd673` v0.8.3 CRITICAL: dual llama.cpp spawn + Item A on the wrong path
`ef49ddb` v0.8.2 item C + audit: --n_predict_draft knob; messageId propagation OK
`a22bc56` v0.8.2 items A+B: speculative-decoding env var + gbrain MCP integration docs
`aba5f88` v0.8.1 item 3: MCP tool-call payloads end-to-end (Phase 4 closeout)
`704331e` v0.8.1 item 4 fix: insight popover matches SourceInsightResponse shape
`6706a6b` v0.8.1 item 5: MCP server priority ordering + reorder UI
`8c256ad` v0.8.1 item 4: CitationPill InsightPopoverContent uses useInsight
`0b700df` v0.8.1 item 1: surface selected_provider end-to-end in /chat/execute
`b81306c` v0.8.1 item 2: cloud_model_id fallback fix (migration 18)

### Per-bullet detail follows.

- **🐛 v0.8.5 — Router/launcher n_ctx desync (low-RAM operators saw context_length_exceeded)**
  - Audit follow-on to v0.8.4. The router read
    `OPEN_NOTEBOOK_LOCAL_N_CTX` (default 32768) while the launcher
    reads `ONP_CHAT_LLM_CTX` (also default 32768) — same concept, two
    names. Operators running `ONP_CHAT_LLM_CTX=8192` for low-RAM
    mode got their sidecar bound at 8k context, but the router
    still assumed 32k headroom, so prompts of 9k–32k tokens were
    routed to local and the sidecar returned 400
    `context_length_exceeded`. Default-config operators were
    unaffected (both defaults match), but the bug bit any user
    overriding the launcher knob.
  - Fix: `open_notebook/ai/provision.py` now reads either env var
    with a precedence chain — `OPEN_NOTEBOOK_LOCAL_N_CTX` wins
    (explicit router knob), `ONP_CHAT_LLM_CTX` is the v0.8.5
    fallback, `32768` is the final default. Malformed value falls
    back to 32768 rather than crashing the chat turn
    (mirrors v0.7.206's launcher-side guard).
  - 4 new tests in `tests/test_phase3_smart_routing.py::TestNCtxEnvVarSync`
    pin the precedence chain + malformed-value handling.
  - Known follow-on for v0.8.6 (deferred): the launcher's
    `_spawn_llamacpp_chat` auto-detects n_ctx from GGUF metadata
    (e.g. Hermes-3 native 131k, capped at `ONP_CHAT_LLM_CTX_MAX`).
    The router still defaults to 32768 if neither env var is set,
    so operators with high-capacity GGUFs and high
    `ONP_CHAT_LLM_CTX_MAX` ceilings under-route to cloud. Closing
    this needs the launcher to propagate its resolved n_ctx through
    env, which requires moving the n_ctx resolution before
    `session_env` is built.

- **🐛 v0.8.4 — CRITICAL: smart router's local branch was dead on arrival**
  - `open_notebook/ai/provision.py:_local_chat_healthy_cached` reads
    `OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL` to know where to probe the
    llama.cpp sidecar. Nothing in production code set that env var
    (audited: only test files monkeypatched it; the launcher set
    `MEMORY_CHAT_LLM_URL` at the same site but not this one). So the
    probe always returned `False`, `pick_provider(local_chat_healthy=
    False)` always took the cloud branch, and v0.8.0 Phase 3's
    "prefer local when healthy" code path was effectively dead — every
    routed turn went to cloud regardless of model size, sidecar state,
    or n_ctx headroom.
  - Fix: `desktop/launcher.py` now exports
    `OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL=http://127.0.0.1:{chat_llm_port}/v1`
    in `session_env` (right next to the existing `MEMORY_CHAT_LLM_URL`
    line, same source of truth). Test
    `test_supervisor_writes_session_env` extended to pin the new key
    AND assert it matches the memory URL so a future edit can't desync
    the two.
  - Full regression: 60/60 pass (launcher + provider + phase-3 router).

- **🐛 v0.8.3 — Dual llama.cpp spawn + draft-model wiring fix (CRITICAL)**
  - **Bug 1 (resource waste):** `desktop/app.py:_phase_select_provider`
    called `LlamaCppProvider.start()`, which spawned a `llama_cpp.server`
    subprocess (~4 GB RAM, 10–30 s cold mmap) on a dynamic port. Since
    v0.7.193 wired `auto_register` to prefer `sv.chat_llm_port` over the
    `OPENAI_COMPATIBLE_BASE_URL` env var, **nothing routed traffic to
    that spawn** — it was a 4 GB / 30 s waste per launch, with the
    actual chat sidecar still spun up separately by
    `Supervisor._spawn_llamacpp_chat`. Fixed by removing the `.start()`
    call. The `LlamaCppProvider` import is dropped from
    `_phase_select_provider`; the class continues to expose its
    discovery helpers (`is_available`, `pick_default_model`,
    `list_models`) for any non-desktop callsite that wants them.
  - **Bug 2 (Item A on the wrong path):** v0.8.2 Item A wired
    `OPEN_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH` /
    `OPEN_NOTEBOOK_LOCAL_DRAFT_N_PREDICT` into `LlamaCppProvider.start()`
    — i.e. the dead path. Operators following the v0.8.2 docs were
    setting the env vars correctly and seeing **no speedup**. Fixed by
    moving the wiring to `Supervisor._spawn_llamacpp_chat`
    (`desktop/launcher.py`). Env var names preserved — existing operators
    get speculative decoding for the first time without touching their
    `.env`.
  - **Guards preserved on the new path**: missing/sub-1MB GGUF skipped
    silently (no crash); malformed `n_predict` env logged and dropped;
    `n_predict` without a draft model also dropped (would otherwise make
    `llama_cpp.server` reject the argv).
  - 7 new tests in `desktop/tests/test_v0_8_3_dual_spawn_fix.py` pin
    every branch on the LIVE spawn. Full launcher + provider suite:
    42/42 passing (35 pre-existing + 7 new).

- **✨ v0.8.2 Item A — llama.cpp speculative decoding via `--model_draft`**
  - `desktop/providers/llamacpp.py`: `LlamaCppProvider.__init__` accepts
    `draft_model_path: Path | None`; when set, the spawned argv gets
    `--model_draft <abs path>` so llama-cpp-python uses the draft model
    for speculative sampling (typical 1.5–2× decode speedup, no quality
    loss when draft + target share a tokenizer family, e.g. Llama-3.2-1B
    drafting for Hermes-3-Llama-3.1-8B).
  - `desktop/app.py:_phase_select_provider` reads
    `OPEN_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH` (absolute path to the GGUF)
    and wires it through. Empty/unset = current behavior, byte-for-byte.
    Missing or sub-1MB draft path is skipped silently (matches the
    main-model `MIN_GGUF_BYTES` guard) so a stale env var can't crash
    the sidecar — the operator just doesn't get the speedup.
  - 4 new unit tests in `desktop/tests/test_llamacpp_provider.py`
    cover: omits flag when unset (backward compat), appends flag when
    valid, skips missing path, skips sub-1MB LFS-pointer-shaped path.
    Suite grew 14 → 18 cases, all passing.

- **✨ v0.8.2 Item C — `--n_predict_draft` tuning knob**
  - `LlamaCppProvider.__init__` accepts `draft_n_predict: int | None`;
    when set together with `draft_model_path`, the spawned argv gets
    `--n_predict_draft <N>` so operators can tune draft tokens per
    verification pass (llama_cpp.server default is 8). Higher values
    speed throughput when the draft and target models agree often
    (same tokenizer family); lower values waste less work on
    disagreement-heavy pairs.
  - `desktop/app.py:_phase_select_provider` reads
    `OPEN_NOTEBOOK_LOCAL_DRAFT_N_PREDICT`; non-int or <=0 falls back
    to None so llama_cpp.server uses its built-in default. A stray
    value without a configured `OPEN_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH`
    is dropped silently — pairing-only flag, won't appear in argv
    without the draft model that gives it meaning.
  - 2 new unit tests pin both branches (both-set appends correctly,
    n_predict-without-draft-path drops). Suite grew 18 → 20 cases,
    all passing.

- **🔍 v0.8.2 audit — messageId propagation across chat surfaces (no bug)**
  - Verified the v0.8.1 Item 3 deferral language is correct: notebook
    `ChatColumn` already renders messages through `ChatPanel`
    (`frontend/src/app/(dashboard)/notebooks/components/ChatColumn.tsx:6,87`),
    so the messageId prop + cache-stash pipeline reaches notebook
    chat for free. Source chat's `source_chat.py` graph does NOT
    bind MCP tools — the LLM cannot emit `[mcp:N]` markers in the
    source-chat path at all, so the missing cache stash there is
    a non-issue (the placeholder pill would only render on stray
    user-pasted tokens, which is benign).

- **📚 v0.8.2 Item B — gbrain MCP integration docs**
  - `docs/3-USER-GUIDE/integrating-gbrain-mcp.md`: three-step setup
    guide for registering [gbrain](https://github.com/garrytan/gbrain)
    as an MCP source so the chat model can call its hybrid retrieval +
    knowledge graph from any notebook. Covers the no-code v0.8.0
    Settings → MCP Servers path, when-to-use-which decision table, and
    four common troubleshooting modes (system prompt missing, blank
    pill popovers, smart-routing flips to cloud, gbrain down).

- **✨ v0.8.1 Item 3 — MCP tool-call payloads in citation pill popovers (Phase 4 closeout)**
  - Chat-graph `_resolve_chat_tools()` accepts a `captures` accumulator;
    each `mcp_search`/`mcp_fetch` closure appends `{index, name, args, text}`
    on completion (text truncated to 4000 chars). `ThreadState` carries the
    list; `call_model_with_messages` resets per turn.
  - `ExecuteChatResponse.mcp_tool_calls` exposes the list on /chat/execute.
    /chat/stream emits a final `{"type":"mcp_tool_calls"}` NDJSON event
    just before stream-close.
  - Frontend `useNotebookChat` stashes the payload in TanStack Query cache
    keyed by last-AI-message id. `CitationPill` MCP popover reads it and
    renders tool name + args (compact JSON) + result excerpt.
    The v0.8.0 placeholder text is now an honest "no payload" fallback for
    old sessions.
  - i18n: 4 new/updated keys across 10 locales.
  - 3 new backend unit tests in `tests/test_phase2_mcp_integration.py`.
  - 2 new frontend tests in `CitationPill.test.tsx`.
  - `messageId` prop-drilled through `AIMessageContent` → `CitationPill`
    (approach a); only 2 component levels, no context needed.

- **✨ v0.8.1 Item 5 — MCP server priority ordering**
  - `mcp_server.priority` (migration 19, default 100); `list_enabled_servers()`
    sorts by priority then created for deterministic order. Chat graph
    `_resolve_chat_tools()` still picks `servers[0]` — now meaningfully
    "highest-priority enabled server" instead of "whichever was inserted first".
  - `PATCH /api/mcp/{server_id}` accepts `{priority?, enabled?}` for partial
    updates; 400 on empty body.
  - Settings/MCP page adds ▲/▼ icon buttons per row (no drag-and-drop dep);
    full i18n across 10 locales. Two new vitest cases cover the mutation call
    + the first/last-row disabled state.

- **🐛 v0.8.1 Item 4 — citation pill insight popover uses the right hook**
  - `frontend/src/components/chat/CitationPill.tsx` — `InsightPopoverContent`
    was wired to `useSource(id)` on the assumption that insight IDs were source
    records. The chat system prompt actually instructs the LLM to emit
    `[insight:source_insight:xxx]` markers, so the source GET 404'd silently
    and the popover always fell back to the italic placeholder. Now uses the
    existing `useInsight(id)` hook so the popover shows the insight title.
    Test added; CitationPill suite grew 7 → 8 cases.

- **🐛 v0.8.1 Item 2 — cloud_model_id fallback fix**
  - `DefaultModels.auto_route_cloud` + migration 18 — dedicated slot for the
    smart router's cloud model. Stops the v0.8.0 bug where the router silently
    routed oversized prompts to a local model when the operator's
    `default_chat_model` happened to point at a local sidecar and
    `OPEN_NOTEBOOK_CLOUD_CHAT_MODEL_ID` was unset. Three new unit tests cover
    env-var override, field fallback, and the "no cloud configured" path.

- ✨ **Chat routing introspection** (`api/routers/chat.py`,
  `open_notebook/ai/provision.py`, `open_notebook/graphs/chat.py`):
  `ExecuteChatResponse` now carries `selected_provider` ("local"/"cloud"/null)
  and `selected_model_id`. The chat-graph node captures the
  `pick_provider()` decision via a new `selection_out` dict on
  `provision_langchain_chat_model()` and threads it through `ThreadState`
  so /chat/execute can return it. Closes the v0.8.0 introspection gap that
  forced `scripts/verify-chat-platform.sh` Steps 4+5 into manual-eyeball
  mode — they now assert `.selected_provider == "local" | "cloud"`
  programmatically. New test file
  `tests/test_v0_8_1_selected_provider.py` covers the Pydantic shape and
  the local/cloud/disabled plumbing paths.

---

## v0.8.0 — 2026-05-25 — Local-first MCP-enabled chat platform

v0.8.0 ships the full local-first MCP-enabled chat platform across four phases
(Tasks 1-17). Active health probing detects live local-model sidecar status and
surfaces it as colored sidebar badges so the user always knows whether their GGUF
is reachable. An MCP server registry backed by SurrealDB lets operators plug in
any Model Context Protocol server; the chat LangGraph graph wires enabled servers
as tool-callable surfaces for every turn, and a Settings UI covers add/test/delete.
Smart local-vs-cloud routing (`pick_provider()`, opt-in via `OPEN_NOTEBOOK_AUTO_ROUTE_CHAT`)
selects the local sidecar when it is healthy and the turn fits within its context window,
falling back to the configured cloud model on overflow or sidecar failure — no manual
switching required. Every claim derived from MCP tool results or notebook documents
is tagged with a citation marker (`[mcp:N]`, `[source:ID]`, etc.) and rendered as an
interactive pill in the chat panel. Regression tests across all four phases, a
Phase 5 end-to-end verification script, and a version-sync bump to v0.8.0 complete
the release.

- **v0.8.0** ✨ Phase 5 closeout — E2E verification script + version release.
  `scripts/verify-chat-platform.sh` five-step smoke test: local-model health,
  MCP enabled-server count, MCP citation marker assertion ([mcp:1]), and
  manual log-check steps for local-vs-cloud routing (Steps 4+5 are manual
  eyeball checks — ExecuteChatResponse does not expose a model field; tracked
  as v0.8.1 item). Version bumped from 0.7.212 → 0.8.0 across
  `desktop/__init__.py`. pyproject.toml `version = "1.8.5"` is upstream
  lfnovo/open-notebook's version and intentionally left unchanged.

- **✨ Phase 4 — Task 14: frontend citation pills (v0.8.0)**
  - `frontend/src/components/chat/CitationPill.tsx` + `frontend/src/lib/utils/citations.ts` —
    splits chat assistant text on `[mcp:N]` / `[source:ID]` / `[note:ID]` /
    `[insight:ID]` markers and renders each as an interactive Radix popover pill.
    For source/note/insight, hover lazy-fetches the record via existing hooks.
    For mcp, the popover currently shows the marker label only — full tool-call
    payload requires a chat-stream contract change (deferred to v0.8.1).
    Full i18n across 10 locales.

- **✨ Phase 3 — Smart local-vs-cloud routing (v0.8.0 pre-release)**
  Pure function deciding which AI provider (local sidecar vs cloud) to use
  for a chat turn, based on health status, context window headroom, and user
  preference. Foundational for Task 12 (wiring into provision_langchain_model).
  
  - `open_notebook/ai/router.py` — `pick_provider()` pure function routing by
    health + n_ctx headroom; honors user overrides (cloud/local/auto). 5 unit
    tests cover all branches: auto-mode (healthy+fits, oversized, unhealthy),
    forced overrides (both directions), error cases, and fallbacks (Phase 3 Task 11).
  - `open_notebook/ai/provision.py` + `open_notebook/graphs/chat.py` —
    `provision_langchain_chat_model()` wrapper invokes `pick_provider()` when
    `OPEN_NOTEBOOK_AUTO_ROUTE_CHAT` is truthy (`1`/`true`/`yes`/`on`). Routing
    is **opt-in** — default behavior (env unset) is byte-equivalent to the
    existing `provision_langchain_model(content, None, "chat")` call.
    Health is TTL-cached for 30 s so the router adds no probe latency per turn.
    `chat.py:call_model_with_messages` now uses the smart wrapper when no
    per-request `model_id` override is set; explicit overrides bypass routing
    unchanged.

    Env knobs for operators:
    - `OPEN_NOTEBOOK_AUTO_ROUTE_CHAT` — enable smart routing (default: off)
    - `OPEN_NOTEBOOK_LOCAL_CHAT_MODEL_ID` — SurrealDB model ID for local sidecar
    - `OPEN_NOTEBOOK_CLOUD_CHAT_MODEL_ID` — SurrealDB model ID for cloud chat
      (falls back to `DefaultModels.default_chat_model` when unset — see v0.8.1 note)
    - `OPEN_NOTEBOOK_LOCAL_N_CTX` — local context window size (default: `32768`)
    - `OPEN_NOTEBOOK_CHAT_PROVIDER` — force routing: `auto` | `local` | `cloud`
      (default: `auto`)
    - `OPEN_NOTEBOOK_LOCAL_CHAT_BASE_URL` — sidecar URL for the health probe;
      desktop bootstrap writes this on launch

    v0.8.1 note: if `OPEN_NOTEBOOK_CLOUD_CHAT_MODEL_ID` is unset and
    `DefaultModels.default_chat_model` points to a local model, the router's
    cloud branch will select that local model ID. A dedicated `auto_route_cloud`
    field in DefaultModels (with migration) will fix this in v0.8.1.
    (Phase 3 Task 12, Phase 3 closeout)

- **✨ Phase 1 — Local-model health verification (v0.8.0 pre-release)**
  Foundational probe system for active sidecar health checks, surfaced at
  startup via launcher.log and on-demand via /api/local-models/health + badge
  UI component. Five commits:
  
  1. `open_notebook/health/local_models.py` — Active probe module providing
     `probe_local_model` and `probe_all_local_models` entry points. Detects
     unallocated sidecars (port 0) and hits OpenAI-compatible `/v1/models`
     endpoint on each sidecar with 5s read timeout + 9s total timeout. Returns
     HealthResult dict with status, detail, latency.
  
  2. `/api/local-models/health` — Async aggregation endpoint returning
     `{overall: string, models: [HealthResult]}`. Excluded from password auth
     so splash-screen can poll before user logs in. Fetches local credential
     config from SurrealDB, calls probe_all, normalizes overall status to
     healthy/degraded/down based on model states.
  
  3. `useLocalModelsHealth()` hook + `LocalModelHealthBadges` sidebar
     component. Hook polls /api/local-models/health every 30s with stale-time
     caching; component renders colored dot badges (green/yellow/red) per model.
     Full i18n across 10 locales (en, fr, de, es, it, ja, ko, pt, ru, zh). WCAG-AA
     contrast on badge colors (no red < 5:1, green >= 4.5:1).
  
  4. Desktop `desktop/app.py:_phase_auto_register` startup probe. After the
     existing auto_register block, probes each running sidecar (chat + embed) and
     logs results to launcher.log as `phase1.health {name}: {status} ({detail}, Xms)`.
     Non-fatal catch so stuck probes don't block UI launch.

- **✨ Phase 2 — Task 6: MCP client wrapper (v0.8.0)**
  - `open_notebook/mcp/client.py` — Generic streamable-http MCP client wrapper.
    Provides MCPClient dataclass wrapping mcp.client.session and streamablehttp_client,
    exposing `await list_tool_names()` and `await call_tool(name, arguments)` for
    chat graph integration. Each call opens a fresh session per MCP's design.
    Added `mcp>=1.0.0` dependency to pyproject.toml.

- **✨ Phase 2 — Task 7: MCP server DB registry (v0.8.0)**
  - `open_notebook/mcp/registry.py` + migration 17 — DB-backed MCP server registry;
    chat graph reads enabled servers per-turn. `list_enabled_servers()` queries the
    `mcp_server` SurrealDB table (schemaful, name UNIQUE) and filters for enabled
    servers only, so operators can toggle a server off without deleting it. Includes
    rollback migration 17_down for development/testing.

- **✨ Phase 2 — Task 8: chat-graph mcp_search/mcp_fetch tools (v0.8.0)**
  - `open_notebook/graphs/chat.py` — `_resolve_chat_tools()` exposes `mcp_search` + `mcp_fetch` when an MCP server is enabled; bound to LLM with graceful fallback for providers without tool support (Phase 2 Task 8)

- **✨ Phase 2 — Task 9: /api/mcp CRUD endpoints (v0.8.0)**
  - `api/routers/mcp.py` — /api/mcp CRUD endpoints (list, create, delete, test); admin auth via PasswordAuthMiddleware; 409 on duplicate names (Phase 2 Task 9)

- **✨ Phase 2 — Task 10: MCP Servers Settings page (v0.8.0, Phase 2 closeout)**
  - frontend Settings/MCP page — list/add/test/delete MCP servers; sidebar nav link; full i18n across 10 locales (Phase 2 Task 10, Phase 2 closeout)

- **✨ Phase 4 — Task 13: citation markers in chat system prompt (v0.8.0)**
  - `prompts/chat/system.jinja` — capabilities block flags `mcp_search`/`mcp_fetch`
    as live-info tools; new "MCP TOOL CITATIONS" section instructs the LLM to
    emit `[mcp:N]` markers after sentences whose claims came from MCP results,
    with N being the 1-based turn-local call index. Example block models the
    expected output. The frontend pill renderer (Task 14) reads these markers.

- **✨ Phase 4 — Task 15: citation pipeline regression guard (v0.8.0, Phase 4 closeout)**
  - `tests/test_phase4_citation_rendering.py` — 8 backend tests that render the
    chat system prompt and run the citation regex over it. Catches accidental
    removal of the MCP block, regex drift between frontend and backend, and
    deletion of the frontend splitter/pill modules. Pairs with the 20 vitest
    cases shipped in Task 14 to cover the full pipeline.

- **v0.7.212** 🐛 **Bootstrap partial-extraction recovery +
  mem0 backend-down short-circuit + wizard SSE thread leak.**
  Three follow-ups from the v0.7.210 deep-audit deferred list
  (MED #5, LOW #9, LOW #10).

  1. **`desktop/bootstrap.py` — partial-extraction recovery.**
     `extract_python_runtime` short-circuited as soon as
     `<runtime>/python/bin/python3` existed on disk — but a
     tarball extraction interrupted mid-write (Force Quit, disk
     full, Time Machine partial restore) leaves the binary AND
     a partial set of stdlib `.so` files. The interpreter can't
     even `import sys`, and the next `python -m venv` step
     failed with a cryptic error that forced manual
     `rm -rf ~/.open-notebook-plus/python-runtime`. Now: probe
     with `python -c "import sys, encodings; print(sys.version)"`
     and `shutil.rmtree` + re-extract on any non-zero exit. The
     `_interpreter_is_healthy` helper has its own bounded 5-s
     timeout so a hung/non-responsive interpreter doesn't block
     launcher startup forever.

  2. **`desktop/memory/writer.py` — backend-down short-circuit.**
     `apply_tool_call` previously caught all exceptions but
     didn't signal the driver. A turn with 5 facts could spend
     ~5 minutes pinned on dead 60s retries when the memory shim
     was down (mem0's underlying httpx default read timeout).
     Now: a new `_MemoryBackendUnreachable` sentinel is raised
     for connection-class exceptions
     (`ConnectError`, `ConnectionRefusedError`, `ReadTimeout`,
     `OSError`, etc.); the `extract_turn` driver catches it and
     aborts the remaining tool calls for THIS turn. Next turn
     tries again — the shim may be back up. Logical errors
     (ValueError on bad payload, mem0 internal assertions) still
     fall through to the v0.5.10 soft-fail path so a single bad
     fact doesn't poison the rest of the turn.

  3. **`desktop/first_run/server.py` — wizard SSE thread leak.**
     `progress_stream` started a daemon reader thread that
     called `progress_bus.subscribe(timeout=120.0)`. When the
     user closed the wizard window mid-stream, the writer loop
     broke but the reader sat blocked for the full 120 s
     timeout before terminating. Cumulative — every cancelled
     wizard run leaked one daemon thread + one asyncio.Queue.
     Added a `threading.Event()` cancel signal set on writer
     exit (normal OR exception OR ConnectionResetError); reader
     checks between subscribe iterations.

  Tests at `tests/test_v0_7_212_audit_followup.py`: 7 new — AST
  pins on each fix plus runtime tests for the interpreter health
  probe (rc=0 / rc≠0 / missing-file) and the mem0 circuit
  breaker (raises on ConnectionRefusedError; swallows ValueError;
  driver aborts subsequent calls). Two pre-existing tests
  updated: `test_bootstrap.py::test_extract_python_runtime_skips`
  now monkeypatches the v0.7.212 health probe to True (the
  partial-extraction-recovery path is exercised by the new
  tests), and `test_client.py::test_build_memory_client...`
  asserts `openai_base_url` instead of `base_url` (the v0.7.207
  field-name swap).

  Backend tests: **1094/1094** (was 1087; +7 new).
  Desktop + memory tests: **299/299** (2 pre-existing updated).

- **v0.7.211** 🐛 **Worker concurrency explicit + missing-GGUF
  warnings + AsyncSqliteSaver shutdown.** Three follow-ups from
  the v0.7.210 deep-audit deferred list.

  1. **Worker `--max-tasks` flag explicit in launcher spawn.**
     `surreal-commands.cli.worker` already defaults to 5
     concurrent tasks (verified via `--help`), but the v0.7.210
     audit flagged "single worker, head-of-line blocking"
     because the spawn line had no concurrency arg. The default
     IS 5 today, but a future surreal-commands release could
     change it without us noticing. Pin the intent:
     `--max-tasks 5` (tunable via `ONP_WORKER_MAX_TASKS`,
     clamped to 1-32). No behaviour change today; defensive
     against silent regression.

  2. **Missing-GGUF startup warnings via ProgressBus.**
     Pre-v0.7.211 path: `pick_chat_llm_file` returned None when
     the user had deleted/moved their chat GGUF, the supervisor
     silently skipped llama-cpp startup, auto_register saw no
     chat credential, the user opened the app to find their
     local chat model gone with no explanation. Same for the
     embedding GGUF (`nomic-embed-text-v1.5.f16.gguf`). Now the
     launcher publishes:
       - `provider.llamacpp / warning / "No chat GGUF found in
         <dir>. Local chat will be disabled until you download
         a model..."`
       - `provider.embedding / warning / "No embedding GGUF
         found at <path>. Vector search will be disabled..."`
     Visible in `~/.open-notebook-plus/logs/progress.jsonl` and
     by future frontend toast plumbing.

  3. **AsyncSqliteSaver connection close on FastAPI shutdown.**
     `chat.py` and `source_chat.py` lazily open an
     `aiosqlite.Connection` and hold it for the process lifetime
     via the LangGraph AsyncSqliteSaver. Nothing closed it on
     shutdown — harmless on POSIX, but on Windows the SQLite
     file stayed locked between launcher restarts, and the FD
     count crept up on long-running .app sessions. Added
     `close_async_graph()` / `close_async_source_chat_graph()`
     idempotent helpers; wired into the API lifespan teardown
     right after the SurrealDB pool drain.

  Tests at `tests/test_v0_7_211_audit_followup.py`: 7 new —
  AST pins on all three fixes + a runtime smoke that the close
  helper is safe on a never-built graph (because most shutdowns
  happen on a process where the chat path was never exercised).

  Backend tests: **1087/1087** (was 1080; +7 new).
  Desktop tests: **254/254**.

- **v0.7.210** 🐛 **Version sync + frontend version badge + /api/
  version endpoint + periodic stale-command reaper.** Deep audit
  by background agent + user-explicit ask to "make the startup
  window for each new rebuild show what's running".

  1. **`desktop/__init__.py` — synced `__version__` from "0.1.0"
     to v0.7.210.** Was set when the project started, never
     touched. Every log line, the smoke test, any About dialog
     all reported v0.1.0 against a v0.7.x build. Locked in step
     with the CHANGELOG via a new `test_version_matches_changelog`
     smoke test that fails the build on drift.

  2. **`api/main.py` — new `GET /api/version` endpoint.** Returns
     `{"version": "0.7.210", "name": "Open Notebook Plus"}`.
     Excluded from auth so the launch splash / login page can
     hit it before the user enters credentials. Useful for
     support diagnostics (`curl localhost:5055/api/version`)
     without grepping logs.

  3. **`desktop/window.py` — inject `window.ONP_VERSION` into the
     main webview window** alongside the existing theme / memory /
     voice globals. Frontend can render it in the sidebar footer.

  4. **`frontend/src/components/layout/AppSidebar.tsx` — version
     badge in the sidebar footer** (`v0.7.210`, tiny mono font,
     muted color). Hidden when the sidebar is collapsed. Falls
     back to `—` on the server render to avoid hydration warning;
     populated client-side from `window.ONP_VERSION` (which
     desktop/window.py sets).

  5. **`api/main.py` — periodic stale-command reaper.** The
     startup reaper at lifespan start only catches rows orphaned
     by the LAST shutdown. If the worker dies mid-day while the
     API stays up (OOM / llama.cpp crash), the orphaned rows
     linger as "running" forever and the frontend polls them
     until the next full API restart. Added a 5-minute
     background loop that runs the same query. Cancelled cleanly
     on shutdown via `_track_task` + the existing shutdown
     teardown.

  Tests at `tests/test_v0_7_210_version_and_reaper.py`: new — AST
  pins on the sidebar badge + /api/version excluded_paths + the
  reaper loop shape. Existing `desktop/tests/test_smoke.py`
  updated to the new sync-with-CHANGELOG assertion.

  Backend tests: **1073/1073** (unchanged — version reaper test
  in v0.7.210 file).
  Desktop tests: **253/253** (smoke test now asserts
  CHANGELOG match instead of literal "0.1.0").

- **v0.7.209** 🐛 **Audit sweep: 5 fixes across memory shim,
  source pipeline, auth middleware, CORS.** Fresh audit on
  uncovered paths (source upload edges, auth middleware, SSRF
  guards, CORS) plus a follow-up to v0.7.207's whisper/piper
  /v1/models pattern.

  1. **memory_shim missing `/models` endpoint.** Same v0.7.207-
     class fix — connection_tester probes `{base_url}/models`
     and the Memory (local) credential test 404'd. Memory's
     base_url is registered without /v1 (per
     `auto_register/memory.py:41`), so this endpoint sits at
     `/models` (no /v1 prefix), distinguishing it from the
     whisper/piper shims.

  2. **HIGH — `content_process` ignored user ContentSettings.**
     `open_notebook/graphs/source.py:34-51` constructed a fresh
     `ContentSettings(...)` with hardcoded literals every time,
     silently overriding the singleton record the user toggled
     in Settings. The auto_delete_files toggle, processing-
     engine choices for documents and URLs, YouTube language
     preferences — all ignored. Now loads
     `ContentSettings.get_instance()` with a hardcoded fallback
     only on a DB load failure.

  3. **MED — orphan "Processing..." source rows on permanent
     extract failure.** `commands/source_commands.py`'s
     `except ValueError` branch caught the corrupted-PDF /
     unreadable-file failures from content-core but left the
     API-created placeholder source row in the DB forever. User
     saw a phantom source they couldn't make sense of. Added
     three-way safe cleanup: title still "Processing..." AND
     full_text empty AND delete wrapped in try/except so a
     cleanup failure doesn't mask the original ValueError.

  4. **MED — `PasswordAuthMiddleware` class-default
     `excluded_paths` omitted health probes.** main.py's
     production call-site passes the full list explicitly so
     production is fine, but any future re-wiring or test
     fixture instantiating `PasswordAuthMiddleware(app)`
     without the kwarg returned 401 on /livez, /readyz,
     /healthz/deep, /metrics. Footgun. Default now mirrors the
     production list.

  5. **MED — CORS `allow_credentials=True` with wildcard
     origins.** The combination is silently dropped by browsers
     per the Fetch spec; the response then asserted both
     `Access-Control-Allow-Origin: *` AND
     `Access-Control-Allow-Credentials: true` which Chromium /
     Firefox refuse. Now `allow_credentials = not
     CORS_IS_DEFAULT_WILDCARD` — honest contract when wildcard
     is in effect; users who explicitly set CORS_ORIGINS to a
     concrete origin keep the credentialed-CORS behaviour they
     configured for.

  Tests at `tests/test_v0_7_209_audit_sweep.py`: 6 new — AST
  pin on memory_shim `/models`, runtime smoke that hits it via
  TestClient, AST pin on the ContentSettings load, AST pin on
  the orphan-cleanup three-way check, runtime test on the auth-
  middleware default excluded_paths, AST pin on the CORS
  honesty check.

  Backend tests: **1073/1073** (was 1067; +6 new).
  Desktop tests: **253/253**.

- **v0.7.208** 🐛 **Two fixes from end-to-end source-upload +
  local-model audit:** the `embed` Form default + orphan
  credential pruning.

  1. **`/api/sources` upload form `embed` default flipped from
     `"false"` to `"true"`.** Surface: I verified end-to-end the
     local embed server is healthy (probed `/v1/embeddings`
     directly — returns valid 768-dim vectors). I uploaded an
     800-word text via curl; the source completed with
     `embedded=false`, `embedded_chunks=0`, status="completed".
     Looked successful but was invisible to vector search. Worker
     log confirmed: `process_source_command: Embed: False —
     Created 0 insights, embedding skipped`.

     Root cause: backend `Form("false")` default on the upload
     endpoint. Meanwhile the frontend's `AddSourceDialog.tsx:136`
     resolves `embed = settings?.default_embedding_option ===
     'always' || === 'ask'` — both of which are TRUE for the
     user-facing default (`"ask"`). So through the UI, every
     upload embeds; through curl / scripts, nothing embedded.
     Surprising asymmetry; flipped the backend default to match
     user expectation. Explicit `-F embed=false` still honoured
     for the rare ingest-only flow.

  2. **Orphan `llama.cpp (local)` credential pruning at launcher
     startup.** v0.7.194 stopped the duplicate-credential
     creation going forward, but pre-existing installs still
     carried an orphan row (modern name with 0 models linked,
     plus the legacy `Local GGUF (llama.cpp)` row with all the
     model links). User saw a permanently-broken credential
     they couldn't make sense of in Settings → API Keys.

     Added `_prune_orphan_legacy_credentials` to `auto_register.
     _do_register`, runs right after the initial credentials
     fetch. Conservatively safe — requires ALL THREE constraints:
       (a) candidate name matches `llama.cpp (local)`
       (b) legacy `Local GGUF (llama.cpp)` row ALSO exists
       (c) candidate has ZERO models linked

     Logging is verbose (each KEEP / DELETE / SKIP is an INFO
     line in launcher.log) so an operator can audit what got
     pruned in any given launch. The orphan is gone on first
     launch with v0.7.208; subsequent launches are no-ops.

  Tests at `tests/test_v0_7_208_orphan_prune_and_embed_default.py`:
  5 new —
    - AST pin on the `embed: str = Form("true")` change.
    - Behavioural runtime tests on the prune helper with a mocked
      httpx client: deletes-when-safe, skips-without-legacy,
      skips-with-linked-models, skips-on-fetch-failure.

  Backend tests: **1067/1067** (was 1062; +5 new).
  Desktop tests: **253/253**.

- **v0.7.207** 🐛 **Local-model health audit: three credentials
  failing despite their processes being alive.** User asked to
  verify file uploads + local models work end-to-end. Hit live
  API + ran `POST /api/credentials/{id}/test` against every
  local credential:

  | Credential | Result before v0.7.207 |
  |---|---|
  | Local GGUF (llama.cpp) — chat | ✅ Connected |
  | Local Embeddings (llama.cpp) | ✅ Connected |
  | Whisper (local) | ❌ Server returned status 404 |
  | Piper (local) | ❌ Server returned status 404 |
  | Memory (local) | ❌ Cannot connect to server |

  Source upload itself was fully working — confirmed via
  `POST /api/sources` (tiny .txt file → got back complete
  SourceResponse with full_text extracted).

  **3 bugs found + fixed:**

  1. **Memory shim crashed at startup** with:
     ```
     TypeError: BaseEmbedderConfig.__init__() got an unexpected
     keyword argument 'base_url'
     ```
     `desktop/memory/client.py` passed `base_url` to mem0's
     embedder + LLM configs. mem0 uses `openai_base_url` —
     verified at `mem0/configs/embeddings/base.py:23` and
     `mem0/llms/openai.py:50`. Fix: swap field name on both
     config blocks. memory_shim now boots cleanly and the
     Memory (local) credential test goes green.

  2. **Whisper credential test 404'd** because the shim only
     exposed `GET /health` + `POST /v1/audio/transcriptions`,
     but `connection_tester.py:_test_openai_compatible_connection`
     probes `GET /v1/models` (the standard OpenAI-compatible
     discovery endpoint). Added a `/v1/models` route returning
     `{"object": "list", "data": [{"id": "whisper-base-en", ...}]}`.

  3. **Piper credential test 404'd** for the same reason. Added
     the same `/v1/models` route — each loaded voice surfaces as
     its own model entry, so users can see what's available
     before testing TTS.

  Tests at `tests/test_v0_7_207_local_models_health.py`: 5 new
  — AST pin on the mem0 field-name swap, AST pins on both
  shim routes, plus runtime smoke tests that build the FastAPI
  app and actually hit `/v1/models` to catch broken handlers.

  Backend tests: **1062/1062** (was 1057; +5 new).
  Desktop tests: **253/253**.

- **v0.7.206** 🐛 **Local chat failing — n_ctx default bumped +
  GGUF context-length auto-detection.** User report: "Local
  models are failing in the chat." Investigated llamacpp_chat.log
  and api.log; the chat server returned `400` with:
  `"This model's maximum context length is 16384 tokens. However,
  you requested 21016 tokens..."`

  **Root cause:** the launcher hardcoded `n_ctx=16384` for the
  llama_cpp chat server. That default was set when gemma-2-9b /
  codellama-13b were common (8k/16k native contexts), but the
  current install base runs Hermes-3 (131k native), Qwen2.5
  (32k-128k), Llama-3.2 (131k) — all artificially capped at 16k.
  A user with 2-3 selected sources easily exceeded 21k tokens
  (system prompt + history + sources), tripping the cap.

  **Fix:**
    1. **Default `n_ctx` bumped 16384 → 32768.** Doubles KV-cache
       RAM for an 8B model (~2 GB → ~4 GB) — acceptable trade-off
       on any local-AI-capable machine. Gives 11k of headroom
       over the v0.7.205-era failure case.
    2. **Auto-detect from GGUF metadata.** When `ONP_CHAT_LLM_CTX`
       is NOT explicitly set, the launcher now reads the GGUF
       file's `<arch>.context_length` field (e.g.
       `llama.context_length=131072` for Hermes-3) via the
       `gguf` Python library (a transitive dep of
       llama-cpp-python). Capped at `ONP_CHAT_LLM_CTX_MAX`
       (default 32768) for RAM safety. Capable users with 64GB+
       Mac Studios can set `ONP_CHAT_LLM_CTX_MAX=65536` or
       directly `ONP_CHAT_LLM_CTX=65536` to use more of the
       model's native window.
    3. **Defensive failure mode.** `_detect_gguf_context_length`
       returns the fallback on any error (missing `gguf`
       library, corrupt file, unrecognised quant); the
       launcher MUST NOT block startup on metadata-parse
       failures.

  Test housekeeping: v0.7.8 launcher tests pinned the old 16384
  default. Updated to pin the new 32768 default with comments
  documenting the v0.7.206 rationale.

  Tests at `tests/test_v0_7_206_local_chat_context_window.py`:
  5 new — AST pin on the 32768 default, AST + runtime pin on
  the `_detect_gguf_context_length` helper existence + the
  "never raises" contract (missing `gguf` library, corrupt
  file), AST pin on the explicit-env-var override branch.

  Backend tests: **1057/1057** (was 1052; +5 new).
  Desktop tests: **253/253** (3 v0.7.8 launcher tests updated
  for the new default).

- **v0.7.205** 🐛🔥 **CRITICAL: `PORT` env-var leak caused the
  frontend window to show the API's "Not Found" JSON instead of
  the UI.** Discovered while testing v0.7.204 — the .app opened,
  pywebview's window rendered `{"detail":"Not Found"}` on a dark
  background instead of the Next.js dashboard.

  **Root cause:** `desktop/launcher.py:242` set `"PORT": str(
  frontend_port)` in `self.session_env`, which every spawned
  child inherits via `_spawn(env=self.session_env)`. uvicorn —
  used by `llama_cpp.server` (the embed/chat servers) — reads
  `PORT` from env via pydantic_settings and treats it as
  AUTHORITATIVE, overriding the `--port <X>` CLI arg.

  Concrete failure mode from the user's launch:
    - api_port=60432, frontend_port=60433, embed_port=60434
    - API spawned on 60432 ✓ (uvicorn CLI args take precedence
      via the `__main__` argparse path used here)
    - Next.js (`node server.js`) bound `*:60433` ✓
    - `llama_cpp.server --port 60434` was spawned but bound
      `127.0.0.1:60433` because `PORT=60433` from inherited env
      overrode the `--port 60434` CLI arg in pydantic_settings'
      priority order.
    - macOS routes 127.0.0.1 connections to the most-specific
      listener, so `http://127.0.0.1:60433/` (the URL the
      webview opened) was served by the embed server's FastAPI
      root handler — returning `{"detail":"Not Found"}` instead
      of the Next.js UI.

  **Fix:**
    1. Remove `"PORT": str(frontend_port)` from `session_env`.
    2. Add `extra_env: dict[str, str] | None = None` kwarg to
       `_spawn()`; if passed, merge on top of `session_env` for
       that child only.
    3. `_spawn_next` now passes `extra_env={"PORT": str(port)}`
       so the env override scope is narrowed to the Next.js
       child.

  Diagnosis evidence preserved:
    `ps -p 69622 -o command=` showed
    `python -m llama_cpp.server ... --port 60434`
    but `lsof -iTCP -sTCP:LISTEN` showed PID 69622 listening on
    `127.0.0.1:60433`. The discrepancy is the entire signature
    of the bug.

  Tests at `tests/test_v0_7_205_port_env_leak.py`: 3 new — AST
  pins on the absence of `PORT` in session_env, the new
  `extra_env` parameter shape on `_spawn`, and the explicit
  `extra_env={"PORT": str(port)}` in `_spawn_next`.

  Backend tests: **1052/1052** (was 1049; +3 new).
  Desktop tests: **253/253**.
  TypeScript strict-mode compile: clean.

- **v0.7.204** 🐛 **Cosmetic / low-priority closeout.** Five
  small fixes consolidating the items the user explicitly
  flagged as "remaining but not blocking" — pulling them all
  into one batch so the deferred list is finally empty.

  1. **Backend — `find_free_ports` socket-release race
     mitigation.** Added `SO_REUSEADDR=1` on probe sockets so a
     child binding the same port after the probe socket closes
     isn't blocked by a stray TIME_WAIT on the host. Also added
     a bounded re-probe loop (`_MAX_REPROBE_ATTEMPTS = 5`) that
     re-runs allocation if any OS allocator quirk returns
     duplicates within a single batch. Doesn't eliminate the
     race (a true fix requires socket-FD handoff via subprocess,
     which would invade every spawn helper) but defeats the
     common manifestations.

  2. **Frontend — `search/page.tsx` auto-trigger useEffect
     deps narrowed via ref pattern.** `handleSearch` /
     `handleAsk` are recreated each render with deps
     `searchQuery`, `searchType`, `askQuestion`, `modelDefaults`,
     `customModels` — so the auto-trigger effect's dep array
     listed them as well and re-ran every time the user typed.
     Correctness was preserved by the `hasAutoTriggeredRef`
     guard, but the brittleness was real. Stashed both
     handlers in refs that update via a separate effect; the
     auto-trigger effect now depends ONLY on the URL-driven
     inputs (`urlQuery`, `urlMode`, `modelsLoading`,
     `modelDefaults?.default_chat_model`).

  3. **Backend — `podcast_service.get_episode` error
     classification.** Was a bare `try/except Exception: raise
     HTTPException(404, "Episode not found")` — every failure
     (DB connection drop, mid-query timeout, decryption error)
     became a synthetic 404 with a misleading message. An
     operator looking at logs saw the real backend issue but
     the API client got the same 404 it would for a stale ID,
     so debugging took 10× longer. Restructured: a None return
     from `PodcastEpisode.get` (the actual "not found" path)
     raises `NotFoundError`; everything else propagates as its
     real type and hits the global classifier with the right
     HTTP code (500 for DB, 502 for upstream, etc.).

  4. **Backend — `command_service.submit_command_job` typed-
     exception passthrough.** Outer `except Exception: raise`
     re-raised untyped exceptions that the FastAPI framework
     rendered as "Internal Server Error" with no detail. Now
     wraps untyped subclasses as `OpenNotebookError` so the
     global classifier emits a structured 500 with a useful
     message. Typed exceptions (`ValueError`,
     `asyncio.TimeoutError`, `OpenNotebookError` subclasses) pass
     through unchanged via an `isinstance` guard.

  5. **Backend — `notes.py` title `[:80]` magic number
     parameterized.** The auto-title fallback (used when the LLM
     title-generation prompt times out) sliced `first_line[:80]`
     — CJK content's first 80 chars can be 240+ bytes and the
     sidebar column has plenty of room. Made it tunable via
     `ONP_NOTE_TITLE_FALLBACK_LEN` env, clamped to 20-500 so a
     misconfigured value can't break note creation entirely.
     Default stays at 80 for backwards compat.

  Test housekeeping: v0.7.177's regression test was pinning the
  pre-v0.7.204 `try/except` shape in `get_episode`. Updated to
  pin the NotFoundError raise instead, with comments documenting
  the v0.7.204 restructure so a future contributor knows why the
  needles changed.

  Tests at `tests/test_v0_7_204_cosmetic_closeout.py`: 6 new —
  AST pins on SO_REUSEADDR + dedupe + ref-pattern + NotFoundError
  raise + typed-exception wrap + title len env, plus a runtime
  smoke test on `find_free_ports(5)` returning 5 distinct
  ephemeral ports.

  Backend tests: **1049/1049** (was 1043; +6 new).
  Frontend tests: **72/72**.
  TypeScript strict-mode compile: clean.

- **v0.7.203** 🌐 **Studio page full i18n extraction (the big
  deferred item from v0.7.196).** ~30 user-visible strings on the
  Studio page were hardcoded English; non-English users — the
  only ONP install base that ACTUALLY exercises the i18n system —
  saw English on a primary entry point. Audit HIGH #1 from the
  v0.7.196 frontend visual audit, carried through 7 versions of
  deferred lists because the diff size (30 strings × 10 locales =
  ~300 string changes) deserved its own dedicated commit.

  Fix shape:

  1. **New `studio.*` namespace in all 10 locale files.** en-US +
     zh-CN + zh-TW + pt-BR + ja-JP + it-IT + fr-FR + ru-RU +
     bn-IN + es-ES each got the full ~30-key block. Translations
     are functional rather than literary — terminology consistent
     across the namespace and follows each locale's existing
     conventions for tech UI ("notebook" → "笔记本" / "Notebook" /
     "ノートブック" / etc.).

  2. **`studio/page.tsx` rewritten to route every visible string
     through `t('studio.*')`.** Page title, subtitle, both step
     headings, both mode-tile titles + descriptions, both profile-
     select labels + placeholders, title input label + placeholder,
     all 4 generate-button states, all 5 toast title/description
     strings, the dropzone aria-label + visible text, the file-
     remove aria-label, and the file-rejection toast.

  3. **`isAllowed()` refactored.** Was returning pre-formatted
     English `"unsupported type .pdf"` / `"file is 60 MB; cap is
     50 MB"` rejection reasons. Now returns a typed `{key, params}`
     object that the caller interpolates via `t()` so the user sees
     the message in their language.

  Tests at `tests/test_v0_7_203_studio_i18n.py`: 4 new — AST pin
  on every locale having the full `studio.*` key set, AST pin on
  no remaining hardcoded English in the Studio render tree,
  AST pin on the load-bearing `t('studio.X')` calls, AST pin on
  the `RejectionReason` union shape. Frontend's existing locale-
  parity vitest enforces structural sameness across the 10
  locale files for the new keys.

  Backend tests: **1043/1043** (was 1039; +4 new).
  Frontend tests: **72/72** (the locale-parity assertion that
  blocked v0.7.199 stays green with the 30 new keys).
  TypeScript strict-mode compile: clean.

- **v0.7.202** 🐛 **Audit LOW-tier closeout: discovery timeouts,
  iso() sweep, healthz defaults, NoteEditor toast, source-polling
  cap.** Five small fixes consolidating the deferred LOW items
  from the v0.7.201 backend audit + frontend polish.

  1. **Backend — `credentials_service` discovery branches drop
     per-call `timeout=` kwargs.** httpx merges `client.get(...,
     timeout=X)` by REPLACING the client-level `httpx.Timeout(
     connect=5, read=30, write=10, pool=5)` with a single X-second
     budget for all four. Partially undid the v0.7.187 structured-
     timeout fix on the Ollama + openai_compatible + Azure +
     Google + standard-OpenAI branches. Dropped 5 callsites; the
     client-level `_DISCOVERY_HTTP_TIMEOUT` now owns the contract.

  2. **Backend — `command_service.list_command_jobs` iso() sweep.**
     Was emitting `str(row.get("created"))` which renders as
     `surrealdb.DateTime(...)` repr in some driver versions —
     breaks Safari `new Date()` on the /commands admin listing.
     Replaced with `iso(row.get("created"))` per the v0.7.181-183
     convention.

  3. **Backend — `/healthz/deep` defensive `checks[...]` defaults.**
     `must_have_ok = checks["database"]["ok"] and checks[
     "migrations"]["ok"]` indexed both keys without verifying
     either was populated. If a probe path raised before the
     assignment, the route 500'd with a stack-trace body instead
     of returning the structured 503 health payload operators
     expect. Added `checks["database"] = {"ok": False, "status":
     "unknown"}` / `checks["migrations"] = {"ok": False}` defaults
     at the top of the function.

  4. **Frontend — NoteEditorDialog toast on missing notebookId.**
     `if (!notebookId) { console.error; return }` silently
     swallowed the Save click. User watched the dialog freeze
     with no feedback. Added a toast inside the branch so the
     failure is at least visible. (In normal flows this branch is
     unreachable; safety net for a misconfigured parent.)

  5. **Frontend — `useSourceStatus` cumulative-poll cap.** Was
     2-second polling forever while `status ∈ {new, queued,
     running}`. A worker stuck in 'running' (common after the
     v0.7.172 reaper window) pinged the API every 2 s for the
     life of the page, wasting requests and battery on the
     desktop app. After ~15 min (450 ticks) fall back to a 30 s
     background pulse so the UI still notices if the worker
     eventually wakes up, without burning network.

  Tests at `tests/test_v0_7_202_audit_lows.py`: 5 new — AST pins
  on each fix.

  Backend tests: **1039/1039** (was 1034; +5 new).
  Frontend tests: **72/72**.
  TypeScript strict-mode compile: clean.

- **v0.7.201** 🐛 **Backend services + notes router + frontend
  follow-ups from the fresh audit.** Five backend findings and two
  frontend polish items from a sweep of areas not yet covered by
  v0.7.177-200.

  1. **Backend (HIGH) — `credentials_service.test_credential`
     str(e) leak.** Same class as v0.7.177/184. The fallback
     `Error: {truncated}` returned `str(e)[:100]` to the API client.
     Esperanto / SDK exceptions can embed endpoint URLs, partial
     API keys, SurrealDB driver frames. Now logs the full exception
     at `warning` level and returns a generic
     `Connection test failed. Check that the {provider} endpoint
     is reachable and the credentials are valid.` to the client.

  2. **Backend (MED) — `podcast_service.generate_podcast` content
     fallback wrote literal "None".** `notebook = await
     Notebook.get(notebook_id)` was not guarded against `None`;
     the fallback `str(notebook)` produced the string "None" as
     the podcast's content, generation proceeded with empty
     source material and produced a nonsensical episode. Now
     raises `NotFoundError(f"Notebook {notebook_id} not found")`
     before touching the reference; the inner-`except` lets the
     typed exception bubble to the global classifier.

  3. **Backend (MED) — `/readyz` leaked migration `str(exc)`.**
     Migration exceptions can embed `.surql` file paths,
     SurrealDB driver frames, DB DSN fragments. Returning that
     inside the public health-probe JSON body is the kind of
     info-leak that defeats hardening at the proxy layer.
     Sanitized to a generic placeholder `"migrations check
     failed"`; full exception still goes to `logger.warning`.

  4. **Backend (MED) — `notes.py` NotFoundError sweep.** Five
     bare `HTTPException(status_code=404, detail="Note/Notebook
     not found")` callsites bypassed the v0.7.179-183 global
     classifier. Bulk-swapped to `raise NotFoundError(...)`.
     Added `except NotFoundError: raise` handlers to the two
     functions that didn't already have them (`list_notes`,
     `create_note`) — without those, `NotFoundError` would have
     been caught by the generic `except Exception` and collapsed
     to 500.

  5. **Frontend (LOW) — SetupBanner upstream-repo URL.** Docs
     link in the encryption-required banner pointed at
     `lfnovo/open-notebook`; now points at
     `Antman1526/open-notebook-Plus`. Plus users land on Plus
     docs that match their build.

  6. **Frontend (LOW) — MarkdownEditor hardcoded light mode.**
     `data-color-mode="light"` was set unconditionally; in dark
     mode the editor rendered white-on-dark inside the parent
     dialog. Now reads `useTheme().resolvedTheme` and sets the
     data-attribute accordingly. SSR fallback is "light"
     (MDEditor is ssr:false anyway).

  Tests at `tests/test_v0_7_201_audit_sweep.py`: 7 new — AST
  pins on each of the 5 backend fixes, plus pins on the
  SetupBanner Plus-fork URL and the MarkdownEditor theme hook.

  Backend tests: **1034/1034** (was 1027; +7 new).
  Frontend tests: **72/72**.
  TypeScript strict-mode compile: clean.

- **v0.7.200** 🐛 **Search/Ask exception classifier pass-through +
  proper ask-stream cancellation + standards-compliant disconnect
  status + React 19 deprecation cleanup.** Four discrete deferred
  items from v0.7.199.

  1. **Backend (HIGH) — `/search` swallowed typed exceptions.**
     `api/routers/search.py` caught `InvalidInputError` and
     `DatabaseOperationError` and collapsed them into bare
     `HTTPException(400/500, "Search failed")`, defeating the
     v0.7.179-183 global-classifier middleware sweep. Users saw
     the literal "Search failed" placeholder instead of e.g.
     "Database connection lost — please retry". Fix: combined
     handler `except (NotFoundError, InvalidInputError,
     DatabaseOperationError): raise` lets each bubble to the
     classifier middleware in `api/main.py`.

  2. **Backend (HIGH) — `stream_ask_response` didn't cancel
     in-flight LLM call on disconnect.** Previously was a simple
     `async for event in ask_graph.astream_events(...)` with
     `if is_disconnected(): return` in the body. The `return`
     only fires at the iterator's NEXT `await` boundary, which
     for `write_final_answer` is AFTER the 30-60 s synthesis LLM
     call completes. Local LLM kept tokenising tokens nobody
     read — wasted GPU + battery on every cancelled Ask. Fix:
     drive iterator manually with `asyncio.ensure_future(
     event_iter.__anext__())`, poll `is_disconnected()` every
     200ms while the task is pending, `next_task.cancel()` on
     disconnect. Cancellation propagates into the in-flight LLM
     call (modern async clients honour it). Mirrors the
     v0.7.184 chat.py pattern.

  3. **Backend (LOW) — `/search/ask/simple` raised
     non-standard HTTP 499.** 499 is nginx-only and rendered as
     "Unknown status" in FastAPI logs, Sentry, OTel exporters —
     operators couldn't graph cancellation rates. Swapped to
     standard 503 with a descriptive detail string.

  4. **Frontend (LOW) — React 19 `onKeyPress` deprecation.**
     `search/page.tsx:341`, `SessionManager.tsx:132,181` were
     the last three callsites. React 19 silently no-ops the
     handler. Swapped to `onKeyDown` (the modern, fully-
     supported equivalent — same event shape).

  Tests at `tests/test_v0_7_200_search_typed_exceptions.py`:
  4 new — AST pin on the combined typed-exception handler in
  search.py, AST pin on the `__anext__` task wrapping + cancel
  flow, AST pin on no remaining 499 status, AST pin on no
  remaining `onKeyPress` in search/SessionManager.

  Backend tests: **1027/1027** (was 1023; +4 new).
  Frontend tests: **72/72**.
  TypeScript strict-mode compile: clean.

- **v0.7.199** 🐛 **Search/Ask Pydantic state-shape +
  use-search.ts error leak + zod schema i18n.** Three discrete bugs
  from a fresh audit of the Setup Wizard and Search/Ask flows.

  1. **Backend (MED-becomes-HIGH on first hit) — `/search/ask` SSE
     handler drops events on Pydantic state shapes.**
     `api/routers/search.py:stream_ask_response` had
     `if not isinstance(output, dict): continue` — LangGraph's
     state-shape variance means a node may return either a dict OR
     a Pydantic model depending on its return annotation. Every
     subsequent strategy/answer/final_answer event was silently
     dropped; the user saw a blank streaming response even though
     the graph had completed normally. Same fix v0.7.55 already
     applied to `/search/ask/simple`: a getattr-fallback `_get()`
     helper that tries dict access first, falls back to
     `getattr(output, key, None)`.

  2. **Frontend (HIGH) — `use-search.ts` still passes the bare-key
     variant through `t()`.** Missed in the v0.7.196 hook-layer
     sweep because this file is a one-off, not in the
     use-credentials / use-podcasts / use-notes / use-models
     cluster. Pattern was `description: t(getApiErrorKey(error.
     message))` — for unmapped errors, `getApiErrorKey` returns the
     raw backend message string, `t()` returns it verbatim, leaking
     axios "Network Error" / FastAPI stack-trace fragments into
     the toast. Swap to `getApiErrorMessage(error, t, 'apiErrors.
     genericError')`.

  3. **Frontend (MED) — three zod schemas had hardcoded English
     (or no) validation messages.** `TransformationEditorDialog`
     (3 fields), `NoteEditorDialog` (1), `CreateNotebookDialog`
     (1) all used `z.string().min(1, 'Name is required')` or worse
     `z.string().min(1)` (no message at all — falls back to zod's
     default "String must contain at least 1 character(s)").
     Non-English users saw English text in field errors. Converted
     all three to the factory pattern that the Episode/Speaker
     profile dialogs already use: `makeXSchema(t) => z.object({...
     name: z.string().min(1, t('common.nameRequired'))...})`.

     Added 4 new `common.*` keys to all 10 locale files:
     `titleRequired`, `contentRequired`, `promptRequired`,
     `openMenu`. Locale-parity test enforces all 10 stay in sync;
     the v0.7.198 `aria-label="Open menu"` hardcode in
     `SourceDetailContent.tsx` upgraded to `t('common.openMenu')`.

  Tests at `tests/test_v0_7_199_search_ask_state_shape.py`:
  3 new — AST pin on the search.py getattr-fallback, AST pin on
  use-search.ts using the translating helper, AST pin on all
  three zod factories being present.

  Backend tests: **1023/1023** (was 1020; +3 new).
  Frontend tests: **72/72** (locale-parity test now passes
  across all 10 locales with the new keys).
  TypeScript strict-mode compile: clean.

- **v0.7.198** 🐛 **Chat-LLM readiness gate + accessibility +
  theme-token consistency.** Continuation of the deferred work
  filtered to fixes-the-app or improves-perf items.

  1. **Backend (HIGH) — wait for llamacpp_chat before spawning
     memory_retriever.** llama-cpp typically takes 10–30 s to mmap a
     multi-GB GGUF and bind. Previously `_spawn_memory_retriever`
     started immediately after the `_try_spawn` for chat — mem0's
     startup validation hit a closed port and the memory child
     exited rc=1 silently (production DEVNULL trap from v0.7.195).
     The user saw "Memory (local)" → Cannot connect to server in
     the credentials UI on every cold launch.

     Fix: `_wait_tcp("127.0.0.1", chat_llm_port, timeout=60.0,
     proc=self._procs[-1])` between the two spawns. 60-s timeout
     accommodates cold-cache mmap on slow SSDs. `proc=` lets us
     short-circuit if the child crashed (e.g., GGUF corrupt) instead
     of waiting the full minute. On timeout we LOG warning and
     proceed — better degraded than a frozen UI.

     Also extends the v0.7.197 conditional-stash invariant to the
     chat port: `self.chat_llm_port = chat_llm_port if chat_alive
     else 0`. Without it, an install without a chat GGUF would still
     have auto_register create a chat credential pointing at a port
     nothing is listening on.

  2. **Frontend (MED) — icon-only button aria-labels.** Three sites
     where screen readers announced "button" with no purpose: the
     `MoreVertical` dropdown trigger in `SourceDetailContent.tsx`,
     the insight-delete `Trash2` button in the same file, and the
     credential-delete button on the API-keys settings page. Added
     `aria-label`; the SR users now hear "Open menu" / "Delete".

  3. **Frontend (MED) — hardcoded red colour tokens swept to
     theme tokens.** 28 occurrences across 14 files were using
     `text-red-600`, `text-red-600 hover:text-red-700`,
     `text-red-600 focus:text-red-600`, `text-red-600
     dark:text-red-400`, or `bg-red-600 hover:bg-red-700` — all
     converted to `text-destructive` / `bg-destructive` (with
     `/90` for hover variants). Form-validation messages, danger
     buttons, error banners, and confirm-dialog destructive actions
     now match the active theme (light / dark / the 9 custom ONP
     palettes) instead of staying vivid red.

  4. **Frontend (LOW) — LoginForm password show/hide toggle.**
     Standard affordance was missing — users couldn't verify what
     they typed (especially fat-finger touch). Added `Eye` /
     `EyeOff` toggle button with `aria-pressed` state and
     focus-visible ring. Toggled state is per-render only; cleared
     on remount.

  Tests at `tests/test_v0_7_198_launcher_readiness.py`: 3 new —
  AST pins on `chat_alive` precondition, the `_wait_tcp` call with
  `timeout=60.0`, the log.warning fallback path, and the
  conditional `chat_llm_port` stash.

  Backend tests: **1020/1020** (was 1017; +3 new).
  Desktop tests: **253/253** (full launcher suite unchanged).
  Frontend tests: **72/72** (no regression on the 28-file colour
  sweep + 3 aria-label additions + LoginForm changes).
  TypeScript strict-mode compile: clean.

- **v0.7.197** 🐛 **Local-model orchestration deferred items +
  touch-device sidebar fix.** Working through the v0.7.196 deferred
  list, filtered to items that actually fix the application or
  improve performance.

  Backend (4 items from the background "Local model audit"):

  1. **`mcp` + `fastmcp` pinned in `desktop/requirements.txt`** —
     same class as v0.7.195. The `openchronicle_shim.py` does
     `from mcp.client.session import ClientSession` /
     `from mcp.client.streamable_http import streamablehttp_client`,
     but `mcp` was only present in the lockfile as a transitive of
     `fastmcp`, which was itself NOT in `requirements.txt`. Next
     lockfile regen would drop both, the openchronicle bridge
     would crash silently at startup (same DEVNULL trap as v0.7.195).

  2. **`embed_port` / `whisper_port` / `piper_port` stash now
     conditional on the spawn actually producing a server.** Before:
     `_spawn_llamacpp_embed` early-returned when `nomic_embed_path`
     was None / missing, BUT `self.embed_port = embed_port` still
     ran unconditionally. auto_register then registered `Local
     Embeddings (llama.cpp)` against a port nothing was listening
     on, and `_spawn_memory_retriever` started the memory child
     with `--embed-url http://127.0.0.1:<dead_port>/v1`. First
     source upload hung because the embed call to the dead port
     silently timed out. Same trap for whisper / piper.

     Fix: mirror the spawn function's preconditions in the stash
     step. If the prerequisite (model file present, voices present)
     is missing, stash 0 instead of the allocated port.

  3. **Embedding GGUFs routed to the embed credential, not chat.**
     `register_llamacpp_models` iterated every GGUF in `model_dir`,
     classified each as `language` or `embedding` via
     `_is_embedding_gguf(name)`, and then linked ALL of them to
     `cred_id` (the chat credential pointing at `chat_llm_port`).
     The chat llama-server does not serve `/v1/embeddings` for the
     embed model file; selecting it as the embedding model from
     the UI returned 404 at runtime.

     Fix: when an embedding GGUF is detected AND the `Local
     Embeddings (llama.cpp)` credential exists in
     `existing_cred_names`, look up its id via `GET /api/credentials`
     and link the model to THAT credential. Fall back to the chat
     credential when no embed credential exists (clean install, no
     nomic file) so the model still appears in dropdowns with a
     known-bad URL the user can fix in the UI.

  4. **`_spawn_openchronicle_bridge` honours `OPENCHRONICLE_MCP_URL`.**
     The shim's argparse default already read the env var (the
     P1-MED-10 audit fix), but `launcher.py:802` hardcoded
     `--mcp-url http://127.0.0.1:8742/mcp` on every spawn — which
     OVERRODE the shim's default. Users on a non-default MCP port
     (the documented use case) couldn't reach their server.

  Frontend (1 MED item from the v0.7.196 frontend audit):

  5. **Touch-device action menus / sidebar expand button now
     visible.** Four sites used `opacity-0 group-hover:opacity-100
     transition-opacity` to hide action chrome until hover. On
     touch devices (iPad, touch laptops, mobile) `:hover` never
     fires, so the buttons were permanently invisible and the
     features unreachable. Added `[@media(hover:none)]:opacity-100`
     to AppSidebar (collapsed-sidebar expand), NotebookCard,
     NotesColumn, SourceCard (action menus).

  Tests at `tests/test_v0_7_197_local_model_orchestration.py`:
  5 new — pins on requirements.txt mcp+fastmcp entries, AST pins
  on launcher.py's `embed_alive`/`whisper_alive`/`piper_alive`
  guards, AST + behavioural pin on the embed-credential routing in
  llamacpp.py, AST pin on the MCP env-var read.

  Backend tests: **1017/1017** (was 1012; +5 new).
  Desktop tests: **253/253**.
  Frontend tests: **72/72**.

- **v0.7.196** 🐛 **Frontend visual audit — error-message leaks +
  i18n-key-as-text bug across 6 hook files.** Discovered while running
  the explicit visual scan request: 27 callsites across `use-models`,
  `use-podcasts`, `use-notes`, `use-credentials` (and 5 more raw-
  message sites in components) were passing `getApiErrorKey(error,
  t('common.error'))` directly as the toast description. That helper
  returns the i18n KEY string (e.g. `"apiErrors.notebookNotFound"`),
  so on any mapped backend error the user saw literal text like
  `apiErrors.notebookNotFound` rendered in the toast instead of the
  translated string.

  Sibling pattern to the v0.7.184 chat-stream sanitization. Audit
  also surfaced 5 raw-`error.message` callsites that leak axios +
  FastAPI stack-trace text to the user on transient errors.

  Fixes applied (HIGH from the audit):
  1. **Hook layer sweep** — 27 callsites in `use-models.ts`,
     `use-podcasts.ts`, `use-notes.ts`, `use-credentials.ts` swapped
     from `getApiErrorKey(error, t('KEY'))` to
     `getApiErrorMessage(error, t, 'KEY')`. `getApiErrorMessage`
     returns the translated string (or the backend's user-friendly
     detail when no mapping exists).
  2. **`use-models.ts:useTestModel`** — was rendering raw
     `String(error)` ("[object Object]" / axios `Error: Network
     Error`) into `testResult.message` shown beside the Test button.
     Now routes via `getApiErrorMessage`.
  3. **`use-models.ts:useAutoAssignCapability`** — was rendering
     raw `error.message` (Python exception strings) in the toast.
     Same fix.
  4. **`SettingsForm.tsx:90`** — load-failed Alert previously
     showed raw `error.message` ("Network Error", "Request failed
     with status code 500"). Now routed.
  5. **`DiscoverModelsDialog.tsx:101-102`** — discovery-error Alert
     previously stored `error.message` / `String(error)` as the
     visible text. Same fix.
  6. **`GeneratePodcastDialog.tsx:928`** — podcast-generation-failed
     toast description was raw `error.message`. Now routed via
     `getApiErrorMessage(error, t, 'common.refreshPage')`.
  7. **`GeneratePodcastDialog.tsx:374-376`** — `toLocaleString(lang.
     startsWith('zh') ? lang : 'en-US')` silently fell back to en-US
     date format for 7 of our 10 supported locales (pt-BR, ja-JP,
     fr-FR, ru-RU, bn-IN, es-ES, it-IT). Replaced with
     `formatDateTime(note.updated, language)` (the v0.7.189 helper).
  8. **`studio/page.tsx`** — both the failure-toast `catch` and the
     inline `mutation.isError` Alert were ad-hoc unwrapping
     `response.data.detail || error.message` (could surface raw
     stack-trace text). Both now route through `getApiErrorMessage`.

  Tests at `frontend/src/lib/utils/error-handler.test.ts`: 7 new —
  contract tests on `getApiErrorMessage` (mapped key → translated
  string; unmapped → backend detail; empty detail → fallback key
  translated; null → safe default), and an AST-level regression
  test that walks all `src/lib/hooks/**/*.ts` looking for the
  `description: getApiErrorKey(` pattern and fails if any are
  re-introduced.

  Deferred (note in the report below):
  - **HIGH #1 from the audit: full Studio i18n extraction.** ~20
    English strings hardcoded on the Studio page. Scope is
    cross-cutting (touches all 10 locale files); the visual scan
    fixed the user-facing error path (which IS now routed through
    the i18n helpers) but the static labels remain English-only.
    Carries over as v0.7.197.
  - **MED #8-#10 from the audit: opacity-0 group-hover patterns,
    mobile sidebar drawer, icon-only buttons without aria-label.**
    All real but discrete polish items, not user-blocking. Carry
    over.

  Frontend tests: **72/72** (was 65 in v0.7.195; +7 new).
  TypeScript strict-mode compile: clean (`npx tsc --noEmit`).
  Backend regression-test smoke: **8/8** (v0.7.194 + v0.7.195
  pins still green).

- **v0.7.195** 🐛 **STT / TTS / Memory shim dependencies bundled —
  Whisper, Piper, and Memory servers now actually start.**
  Long-standing bug, root-caused while verifying local models.

  Symptom (visible via `/credentials/{id}/test`):
    - `Whisper (local)` → "Cannot connect to server."
    - `Piper (local)` → "Cannot connect to server."
    - `Memory (local)` → "Cannot connect to server."

  Root cause: the three shim files (`desktop/desktop_shims/{whisper,
  piper,memory}_shim.py`) have been in the codebase since the v0.4
  local-server feature shipped, but their runtime dependencies
  (`faster-whisper`, `piper-tts`, `mem0ai`) were **never pinned** in
  `desktop/requirements.txt`. Every .app install since v0.4 has
  silently shipped non-functional STT / TTS / Memory servers.

  Why nobody noticed earlier:
    - Production-mode `_spawn()` redirects child stdout/stderr to
      DEVNULL, so a shim crashing at import LEFT NO TRACE in
      launcher.log. The per-server log files (`whisper.log` etc.)
      never got new entries.
    - `progress.jsonl` still marked `supervisor.whisper: done`
      because `_try_spawn` only knows about exceptions from the
      spawn helper, not whether the subprocess actually bound a
      port.
    - `auto_register` happily registered credentials pointing at
      the planned ports.

  Fix: pin all three deps in `desktop/requirements.txt`:
  ```
  faster-whisper>=1.1.0,<2
  piper-tts>=1.2.0,<2
  mem0ai>=0.1.0,<2
  ```
  Lockfile regenerated via `make build-mac-lock`. Same class of
  fix as v0.7.192's `llama-cpp-python[server]` extras and the
  v0.7.141 lockfile-staleness incident.

  Tests at `tests/test_v0_7_195_shim_deps_bundled.py`: 5 new —
  requirements.txt pins (3 deps), lockfile pins (3 deps), forward-
  guards that the shims still import what we pinned.

  Backend: **1012/1012** (was 1007 in v0.7.194; +5 new).
  Combined `tests/ desktop/tests/`: **1265/1265**.

- **v0.7.194** 🐛 **`register_llamacpp_models` recognises the legacy
  `Local GGUF (llama.cpp)` credential name — fixes pre-v0.6.x
  installs that had 10-20+ models orphaned by the v0.6.x rename.**

  Discovered while inspecting `/api/credentials` on a freshly-
  launched v0.7.193 .app: two local-llama-cpp credentials coexisted.

    - `Local GGUF (llama.cpp)` (created 2026-05-14, pre-v0.6.x):
      `base_url=http://127.0.0.1:8080/v1` (hardcoded port from
      v0.5.9 era, broken since), **18 models linked**.
    - `llama.cpp (local)` (created by v0.7.193, current dynamic
      port): 0 models linked, orphan.

  Root cause: somewhere around v0.6.x the canonical credential name
  shifted from `Local GGUF (llama.cpp)` to `llama.cpp (local)`, but
  the rename was never propagated to existing installs. v0.7.193's
  auto-register looked up the new name, didn't find a match, and
  created a fresh credential — the user's existing models stayed
  linked to the broken legacy one pointing at port 8080.

  Fix: at the credential-creation step in
  `desktop/auto_register/llamacpp.py`, check whether
  `Local GGUF (llama.cpp)` already exists in `existing_cred_names`
  (case-insensitive). If yes, target THAT credential — v0.7.193's
  `_ensure_credential` PUT branch then refreshes its `base_url` to
  the current `chat_llm_port` and the pre-existing model links keep
  working. New installs (no legacy credential) get the modern name
  unchanged.

  **End-user note**: if you already had the orphan `llama.cpp
  (local)` credential created by v0.7.193, it'll stay sitting
  harmlessly with 0 models attached. Delete it manually from the
  Settings UI if you want a clean list. Future launches won't
  create new duplicates.

  Tests at `tests/test_v0_7_194_legacy_credential_alias.py`: 3 new
  — AST pin on the legacy-name check, behavioural pin that the PUT
  refreshes the legacy credential's URL (not POST a duplicate),
  behavioural pin that clean installs still get the modern name.

  Backend: **1007/1007** (was 1004 in v0.7.193; +3 new).
  Combined `tests/ desktop/tests/`: **1260/1260**.

- **v0.7.193** 🐛 **Local-server credentials now refresh `base_url`
  on every launch — fixes "chat model configured but broken after
  every restart".** Two related bugs in the auto-register flow.

  **Bug 1: `_phase_auto_register` ignored `sv.chat_llm_port`.**
  `desktop/app.py:_phase_auto_register` resolved `llamacpp_port`
  ONLY by parsing the user's `OPENAI_COMPATIBLE_BASE_URL` env var
  — never by reading the launcher-allocated `sv.chat_llm_port`.
  The other four local servers (whisper, piper, embed, memory)
  read directly from the supervisor; chat-LLM was the lone holdout.
  `desktop/auto_register/llamacpp.py:71-75` would log "skipping
  local-GGUF credential registration: no llama-cpp server port
  supplied (would have created broken creds)" and silently
  not wire up the chat model.

  Fix: priority chain in `app.py` — `sv.chat_llm_port` first (always
  present in desktop mode), then `OPENAI_COMPATIBLE_BASE_URL` env
  override (for users pointing at an external llama.cpp / LM Studio
  instance instead of the bundled one).

  **Bug 2: `_ensure_credential` never updated existing credentials'
  `base_url`.** Even if `auto_register` passed the right port, the
  shared helper at `desktop/auto_register/_http.py` just returned
  the existing credential ID without checking whether the saved URL
  still matched. The desktop launcher's `find_free_ports()` allocates
  dynamic ports each launch, so the credential saved by yesterday's
  launch pointed at port 56918 while today's llama-cpp-python was
  bound to 57204. `/credentials/{credential_id}/test` connected to
  a closed socket; the model dropdown showed it as broken.

  Fix: on the "already exists" branch, compare the saved `base_url`
  against the one the caller passed; PUT the new one when they
  differ. Skipped when the URLs match (saves a round-trip on the
  common case where the port happened to repeat across launches)
  or when the caller didn't pass a base_url (API-key credentials).
  Single fix in the shared helper benefits all 5 local-server
  registrations: llama.cpp (local), Memory retriever, Whisper STT,
  Piper TTS, llama.cpp embedding.

  Tests at `tests/test_v0_7_193_local_model_port_refresh.py`: 4 new —
  app.py reads sv.chat_llm_port pin, behavioural pins on
  `_ensure_credential` (refreshes URL on port change, skips PUT when
  unchanged, skips PUT when no URL provided).

  Backend: **1004/1004** (was 1000 in v0.7.192; +4 new).
  Combined `tests/ desktop/tests/`: **1253/1253**.

- **v0.7.192** 🐛 **Two end-to-end bugs caught by testing the freshly-
  built v0.7.191 .app on macOS arm64.** The kind of bugs unit tests
  can't catch — they only show up when the bundled venv runs against
  real langgraph + real local-LLM-server processes.

  **(1) LangGraph SqliteSaver no longer supports async methods.**
  Newer langgraph (≥ 0.6) split sync vs. async checkpointers;
  `chat_graph.astream_events(...)` and `chat_graph.ainvoke(...)`
  internally call `aget_tuple()`, which the sync `SqliteSaver`
  raises `NotImplementedError` on. End-user symptom: clicking "Send"
  in chat instantly returned **"Chat stream failed unexpectedly."**
  (the v0.7.184 sanitised SSE error event). The full traceback in
  `api.log` pointed at the LangGraph internals.

  Fix:
    - `open_notebook/graphs/chat.py` and
      `open_notebook/graphs/source_chat.py` now expose lazy async-
      graph factories: `get_async_graph()` and
      `get_async_source_chat_graph()`. Each returns an
      `AsyncSqliteSaver`-backed twin of the legacy graph (same
      nodes + topology, different persistence backend), constructed
      on first call.
    - `api/routers/chat.py` and `api/routers/source_chat.py` route
      `ainvoke` / `astream_events` through the async twins; sync
      `get_state(...)` reads (already wrapped in `asyncio.to_thread`)
      keep using the original sync graph.
    - Both savers point at the SAME on-disk SQLite file
      (`LANGGRAPH_CHECKPOINT_FILE`). SQLite's WAL mode (configured
      in `open_notebook.utils.sqlite_checkpoint`) makes concurrent
      reads + writes across independent connections safe; the
      v0.7.32 busy-timeout absorbs rare lock contention.
    - **Why lazy**: `aiosqlite.connect()` captures the current event
      loop at construct time via `asyncio.get_running_loop()`. At
      module import time there's no loop yet, so eager construction
      fails with "no running event loop". The lazy factory uses a
      threading.Lock (loop-agnostic) for the slow-path init and
      caches the result for all subsequent calls.

  **(2) `llama_cpp[server]` extras missing from the bundled venv.**
  `desktop/requirements.txt` pinned `llama-cpp-python>=0.3.16,<0.4`
  without the `[server]` extra. The bundled venv installed
  `llama_cpp.server.__main__` but was missing `starlette-context`,
  `sse-starlette`, `pydantic-settings`, and `PyYAML`. Every
  `python -m llama_cpp.server` spawn died at import time with
  `ModuleNotFoundError: No module named 'starlette_context'`,
  leaving the local llamacpp_embed + llamacpp_chat servers dead.

  End-user symptom: source uploads stuck "Processing" forever
  (visible in the screenshot during testing). Every embed_source
  command failed with `Failed to generate embeddings: All connection
  attempts failed` because the embedding port wasn't bound.

  Fix: `llama-cpp-python[server]>=0.3.16,<0.4` in
  `desktop/requirements.txt`. Lockfile regenerated via
  `make build-mac-lock`. Now includes `starlette-context==0.3.6`,
  `sse-starlette==3.4.2`, `pydantic-settings==2.14.1`, `pyyaml==6.0.3`.

  Tests at `tests/test_v0_7_192_langgraph_async_checkpointer.py`:
  6 new — chat + source_chat module exports lazy factory pins,
  chat + source_chat router import + use pins, requirements.txt
  `[server]` extra pin, lockfile contains-deps pin.
  Plus updated 3 existing tests to monkey-patch the new
  `get_async_graph` lazy factory alongside the legacy `chat_graph`
  attribute (test_chat_execute_timeout, test_chat_stream,
  test_v0_7_174_session_locks).

  Backend: **1000/1000** (was 994 in v0.7.191; +6 new tests, all
  green). Bundled .app needs `make build-mac` to ship these fixes.

- **v0.7.191** 🐛 **Round-9 audit — Frontend LOW closeout: cancel
  control, stable callback identity, scoped invalidation, dead-code
  removal.** Four small but defensible improvements that close the
  round-9 audit completely.

  **(1) `useNotebookChat` exposes `cancelStreaming`.** Parity with
  `useSourceChat` which already had it. UI now has a way to stop
  a runaway local-LLM mid-generation; previously only the unmount
  path aborted, leaving users staring at tokens they didn't want.

  **(2) `useNotebookChat.buildContext` callback has stable identity.**
  Pre-fix the useCallback depended on ARRAY REFERENCES (`sources`,
  `notes`) — but TanStack Query returns a fresh array on every
  refetch even when the row set is identical. So `buildContext`
  identity churned per refetch, retriggering the gated effect, which
  POSTed `/chat/build-context` again per refetch with zero user
  input. Now derives stable string fingerprints (`sourcesKey`,
  `notesKey`, `selectionsKey`) and depends on those.

  **(3) `use-sources` mutations use predicate-scoped invalidation.**
  The pre-fix `invalidateQueries({ queryKey: ['sources'] })` matched
  EVERY source query — including `['sources', sourceId, 'status']`
  polling keys. So every source mutation triggered a status refetch
  for every source the user had open, even completed ones. On a
  notebook with 30+ sources this was a measurable hit. New
  `_isSourcesListQuery` predicate scopes invalidation to LIST keys
  only; per-source status polls keep their independent cadence.

  **(4) ChatColumn dead `if (!sources && !notes)` branch removed.**
  Both `sources` (prop) and `notes` (useNotes default `[]`) are
  ALWAYS truthy arrays — the "unable to load chat" UI was
  unreachable. Removed alongside the orphaned `AlertCircle`
  import. If real load-failure UI is ever needed, branch on
  `useNotes().error` explicitly.

  Tests at `tests/test_v0_7_191_frontend_low_closeout.py`: 4 new —
  cancelStreaming export pin, stable-keys pin, predicate pin,
  dead-code-absent pin.

  Backend: **994/994** (+4). Frontend: **65/65** + tsc clean.
  Combined `tests/ desktop/tests/`: **1247/1247**.

  **Round-9 audit COMPLETE: 8 commits (v0.7.184-v0.7.191), all 24
  findings resolved or formally documented as audited-no-fix.**

- **v0.7.190** 🐛 **Round-9 audit — Backend LOW closeout: GC-anchored
  background tasks + repo_query timeout + UTC timestamp tool.**
  Three small defensible improvements.

  **(1) Module-level `_BACKGROUND_TASKS` set + `_track_task()` in
  `api/main.py`.** Per the asyncio docs, fire-and-forget tasks may
  be GC'd before they finish — the event loop only keeps weak
  references. The lifespan-local var anchor pattern works today,
  but a future refactor that extracts the spawn into a helper
  would silently lose it. Wrapped all 3 lifespan tasks
  (digest_scheduler, checkpoint_prune, gmail_prewarm) in
  `_track_task` as defence-in-depth. Auto-discards on completion
  so the set doesn't leak across many short-lived tasks.

  **(2) `repo_query()` accepts optional `timeout_s` kwarg.** Default
  None preserves the v0.7.120 behaviour. Callers that fan out many
  small queries (ContextBuilder, memory_recall) can pass an
  explicit per-query budget so a single stuck pool connection
  doesn't pin the route handler past its outer timeout. Matches
  the v0.7.52 `wait_for(10s)` pattern already used for pool warmup.

  **(3) `tools.py::get_current_timestamp` returns UTC ISO 8601.**
  Was `datetime.now().strftime("%Y%m%d%H%M%S")` — naive local time,
  no TZ marker. The output lands in LLM prompts that may be replayed
  cross-machine; "20260522113000" was ambiguous (UTC-5 local? UTC+9
  local?). Now `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`
  with explicit T separator + Z marker. Updated the two associated
  tests for the new format.

  Tests at `tests/test_v0_7_190_backend_low_closeout.py`: 6 new —
  _track_task helper pins (3), repo_query signature pins (2),
  timestamp format pin (1).

  Backend: **990/990** (was 994 — minus 4 from this round's
  format-update of test_graphs.py tests + test_v0_7_165 pin).

- **v0.7.189** 🎨 **Round-9 audit — Frontend MED+LOW polish: stale
  invalidation gaps + i18n-aware date formatting.** Three everyday
  papercuts the audit caught.

  **(1) `useUpdateNote` invalidates `QUERY_KEYS.notebooks` on
  success.** `useCreateNote` and `useDeleteNote` already did this
  (v0.7.166); `useUpdateNote` was the missing third side. Sidebar's
  recently-updated sort + per-notebook last-activity timestamp now
  refresh immediately after editing a note instead of staying
  stale until the next window-focus refetch.

  **(2) New `formatDateTime` + `formatDate` helpers in
  `lib/utils/date-locale.ts` — i18n-aware date formatting.** Five
  call sites did raw `new Date(str).toLocaleString()` which honours
  the OS locale, NOT the app's i18n language. Same component
  rendered two different date formats stacked on each other if a
  user picked Chinese in the app while running on an English OS
  (SourceDetailContent line 859 used `getDateLocale(language)`
  for the relative-time line then bare `.toLocaleString()` for
  the absolute-time line right below it). Migrated:
    - `SourceDetailContent.tsx` — `source.created` / `source.updated`
      absolute-time lines.
    - `RebuildEmbeddings.tsx` — `status.started_at` /
      `status.completed_at`.
    - `GmailIntegration.tsx` — `status.last_sent_at`.
  The new helper is `formatDateTime(value, language)`. Null/empty
  input → empty string; malformed date → original string passed
  through (caller decides on a fallback).

  **(3) `useNotebookChat` invalidates session list after stream
  completes.** After `/chat/stream` finished, only the CURRENT
  session was refetched; the session-list query that powers the
  sidebar's "last updated" timestamp stayed stale until the next
  window-focus refetch. Matches the pattern useSourceChat already
  used.

  Tests at `tests/test_v0_7_189_frontend_polish.py`: 6 new —
  useUpdateNote invalidation pin, formatDateTime helper export
  pin, 3 call-site pins, useNotebookChat session-list
  invalidation pin.

  Backend: **994/994** (was 988 in v0.7.188; +6). Frontend:
  **65/65** + tsc clean. Combined `tests/ desktop/tests/`: **1237/1237**.

- **v0.7.188** 🛠️ **Round-9 audit — Desktop reliability: model
  download resume + launcher early-exit on dead child.** Two
  user-visible reliability bugs from the audit, both with
  material everyday impact.

  **(1) Model downloads now resumable (`desktop/model_downloads.py`).**
  Migrated from `urllib.request.urlopen` to `httpx.stream`. The
  audit caught two genuinely painful bugs:
    - `urllib`'s 300s `timeout=` only covered the initial connect;
      a mid-stream stalled socket hung the launcher forever.
      `httpx.stream(... timeout=httpx.Timeout(read=30))` raises
      `ReadTimeout` within 30s on any idle stall.
    - `shutil.copyfileobj(resp, f)` had NO resume capability. A
      5GB chat-LLM GGUF interrupted at 4.8GB silently restarted
      from byte 0 on next launch. Multiply by the 4-6 models the
      first-launch downloader fetches — a single dropped network
      event cost 20-30GB of redundant transfer. The new flow
      sends `Range: bytes=<existing>-` against any `.tmp` partial
      and appends; on the next launch the user picks up exactly
      where the previous one left off. Defends against CDNs that
      ignore Range and return 200 by restarting from byte 0
      (preventing prefix duplication / corruption).
    - The `.tmp` is now PRESERVED on failure (the pre-fix
      `tmp.unlink(missing_ok=True)` actively defeated any resume
      support before it could exist). The partial-validity check
      at the top of `_download_one` (min_bytes / 80% rule) still
      detects + discards corrupted partials.
    - Progress messages throttled to one per 2 seconds so a
      multi-GB stream doesn't flood `progress.jsonl`.

  **(2) Launcher readiness probes early-exit on dead child
  (`desktop/launcher.py`).** `_wait_tcp` and `_wait_http` now
  accept an optional `proc` argument. Between probe attempts
  they call `proc.poll()`; a non-None returncode means the
  child crashed and there is NO chance the port/endpoint will
  come up — raise `RuntimeError(f"child exited rc={returncode}")`
  immediately. Pre-fix, a uvicorn that crashed in 200ms (binary
  missing, port collision, EACCES on the logging dir) left the
  user staring at "Starting…" for the full 180s probe timeout.
  Three call sites in `_main_supervise` wired the latest spawned
  proc via `self._procs[-1]`.

  **(3) `progress.jsonl` rotation — AUDITED, NO FIX NEEDED.**
  The audit suggested adding rotation; it's already there from
  v0.5.10 (`ProgressBus._rotate_if_oversized` at progress.py:45
  rotates to `.old` at 2MB on startup). Custom check+rename
  pattern, not RotatingFileHandler — sidesteps the Windows
  concurrency gotcha. Added a forward-guard test so a future
  contributor doesn't accidentally simplify the rotation away.

  Tests at `tests/test_v0_7_188_desktop_reliability.py`: 8 new —
  proc-argument pins on _wait_tcp/_wait_http, behavioural pin on
  _wait_tcp early-exit timing, httpx-not-urllib pin, Range-header
  construction pin, behavioural pin on tmp-preserved-on-failure,
  behavioural pin on server-ignored-Range restart-from-zero,
  ProgressBus rotation forward-guard.

  Also rewrote `desktop/tests/test_model_downloads.py` to mock
  `httpx.stream` instead of `urllib.request.urlopen` (same
  behavioural semantics; new transport).

  Backend: **988/988** (was 966 in v0.7.187; +22 — that includes
  the test_model_downloads rewrite picking up some prior tests
  that had been collection-only and the v0.7.188 new file).
  Combined `tests/ desktop/tests/`: **1231/1231**.

- **v0.7.187** 🐛 **Round-9 audit — MED-severity correctness tightening
  across three independent surfaces.**

  **(1) `api/routers/config.py` version-check TTL uses
  `time.monotonic()`.** Was `time.time()`. Wall-clock comparisons
  break on laptops: NTP corrections jump the clock, sleep/resume
  freezes it, DST transitions skip an hour. The desktop target is
  *explicitly* a laptop that sleeps. `time.monotonic()` is the
  canonical "elapsed time" comparison. Same fix the rest of the
  codebase (repository.py, metrics.py, app.py, launcher.py)
  already uses.

  **(2) `open_notebook/domain/base.py::ObjectModel.save()` writes
  aware UTC ISO 8601 timestamps.** Was `datetime.now().strftime(
  "%Y-%m-%d %H:%M:%S")` — naive local-time, no TZ marker, non-ISO
  format. Cross-machine sync between the primary install and the
  2-3 testers produced off-by-N-hour ordering; the v0.7.181
  `iso()` helper couldn't reconstruct a TZ that was never stored.
  Now writes `datetime.now(timezone.utc).isoformat()` and
  round-trips existing datetime objects through `.isoformat()`
  too. Companion to the v0.7.181/182 read-side fix.

  **(3) `api/credentials_service.py` httpx.AsyncClient uses a
  shared `_DISCOVERY_HTTP_TIMEOUT`.** Was bare `httpx.AsyncClient()`
  + per-call `timeout=30.0`. The per-call kwarg only bounds the
  request-response phase; TLS handshake and pool-acquire could
  hang indefinitely on a half-broken provider URL. Now sets
  explicit connect/read/write/pool budgets at client construction
  — mirrors the chat_service.py pattern.

  Tests at `tests/test_v0_7_187_med_correctness.py`: 5 new —
  monotonic-clock pin, time.time() forward-guard, aware-UTC
  isoformat pin, timezone import pin, shared-timeout pin.

  Backend: **966/966** (was 961 in v0.7.186; +5 new).
  Combined `tests/ desktop/tests/`: **1223/1223**.

- **v0.7.186** 🐛 **Round-9 audit — Frontend HIGH-severity bugs.**
  Two everyday-UX bugs the audit caught.

  **(1) Sources page hijacked global keyboard navigation.**
  `src/app/(dashboard)/sources/page.tsx` registered a window-level
  `keydown` listener that called `e.preventDefault()` on
  ArrowDown/Up/Home/End/Enter regardless of `e.target`. As long
  as the Sources route was active, EVERY input across the app
  lost arrow-key caret movement — the app search bar, the command
  palette, dialog inputs, contenteditable spans, all of it.
  CommandPalette already uses the correct guard pattern; ported
  it: early-return when the focused element is INPUT/TEXTAREA/
  SELECT/contenteditable.

  **(2) EpisodeCard leaked object-URLs + setState-after-unmount +
  stale-blob-wins on fast switching.** The audio-fetch `useEffect`
  had three subtle race conditions:
    - The cleanup closure captured `revokeUrl` at the time it was
      returned — but `revokeUrl = URL.createObjectURL(blob)`
      happens LATER in the async path. Quick unmount → cleanup
      ran while `revokeUrl` was still undefined → object URL
      created later was NEVER revoked. Memory leak per fast
      unmount.
    - `setAudioSrc/setAudioError` ran after potential unmount
      (no `mounted` guard), triggering React warnings + pinning
      a reference to the unmounted component.
    - No AbortController on the fetch — a slow first request
      could resolve AFTER a faster second request and stomp the
      correct `audioSrc` with the stale blob (user clicking
      between episodes).

  Rewritten with: `cancelled` flag guarding every setState,
  `currentObjectUrl` assigned BEFORE setState so cleanup always
  sees the right value, AbortController wired to `fetch(..., {
  signal })`, and AbortError silently absorbed (it's the expected
  outcome of switching mid-fetch, not a real failure to surface
  via toast).

  Tests at `tests/test_v0_7_186_frontend_high_bugs.py`: 3 new —
  keyboard-guard pin, cancelled-flag pattern pin, AbortError
  handling pin.

  Backend: **961/961** (+3). Frontend: **65/65** + tsc clean.
  Combined `tests/ desktop/tests/`: **1218/1218**.

- **v0.7.185** 🪟 **Round-9 audit — Windows compatibility fixes.**
  Four bugs that silently broke on Windows but worked on macOS /
  Linux — exactly the class of bug that goes unnoticed when the
  primary developer is on macOS and Windows is the secondary
  platform.

  **(1) Centralised home-directory resolver — `desktop/paths.py`.**
  9 sites independently rolled their own `os.environ.get("HOME",
  fallback)` lookup with inconsistent fallbacks. Seven used `"."`
  — CATASTROPHIC on Windows: when the .exe is launched from File
  Explorer, CWD is the .exe directory (typically `C:\Program
  Files\...`, read-only). Logs, PID file, surreal_data, and config
  all silently fail to write. Two used `"~"` (POSIX-only). New
  `user_home()` falls through to `Path.home()` — guaranteed to
  return a writable directory on every supported OS. Migration
  touched bootstrap.py, launcher.py (3 sites), singleton.py,
  __main__.py, providers/llamacpp.py, memory_dashboard/server.py,
  model_manager/server.py. Forward-guard test scans desktop/
  on every CI run.

  **(2) SurrealDB `file://` URI now uses `Path.as_uri()`.**
  `f"file://{data_dir}"` produces `file:///Users/...` on POSIX
  (valid) but `file://C:\\Users\\...` on Windows (NOT valid —
  SurrealDB's URL parser reads `C:` as the host). `data_dir.as_uri()`
  is the idiomatic cross-platform builder: returns
  `file:///C:/Users/...` on Windows, `file:///Users/...` on POSIX.

  **(3) Windows process-tree teardown via `taskkill /F /T /PID`.**
  Previously `os.kill(pid, CTRL_BREAK_EVENT)` — a no-op for a
  windowed PyInstaller .exe because the signal requires a console.
  Grandchildren (next-server forks, etc.) leaked on every shutdown
  — exactly the same bug v0.7.173 fixed for POSIX. `taskkill /F
  /T` is the Windows equivalent of `killpg(SIGKILL)` and works
  without a console. Also added `CREATE_NO_WINDOW` to Popen flags
  so child processes don't pop transient console windows from the
  packaged .app.

  **(4) Filesystem denylist normalises path separators + drive
  letters.** `_DENIED_PREFIXES` had POSIX-shaped entries
  (`"/Windows"`, `"/$Recycle.Bin"`) but the prefix match was
  `.lower().startswith(prefix.lower())`. On Windows,
  `Path.resolve()` returns `C:\\Windows\\System32\\...`;
  `.startswith("/windows")` was False, silently letting the file
  picker browse into protected paths. Fix: normalise backslashes
  to forward slashes, strip leading drive letter, then prefix
  match. POSIX coverage unchanged.

  Tests at `tests/test_v0_7_185_windows_compat.py`: 10 new — 4
  user_home() behaviour pins, AST forward-guard on desktop/ HOME
  fallbacks, as_uri pin, taskkill + CREATE_NO_WINDOW pins, denylist
  normalisation pin, POSIX denylist still-works pin.

  Backend: **958/958** (was 948 in v0.7.184; +10 new).
  Combined `tests/ desktop/tests/`: **1215/1215**.

- **v0.7.184** 🐛 **Round-9 audit — Backend HIGH-severity bugs
  (cascade-delete data leak, dead /chat/stream handler, info leak).**
  Three independent surfaces flagged by a project-wide audit.

  **(1) Notebook.delete() chat-session cascade — DATA INTEGRITY
  bug.** `open_notebook/domain/notebook.py:401` used
  `DELETE $ids` to remove orphaned chat sessions. That isn't
  valid SurrealQL — DELETE wants a table reference or WHERE clause,
  NOT a bare array bound to the verb position. The query silently
  no-op'd (or errored, driver-dependent), so EVERY `chat_session`
  row that ever pointed at a deleted notebook leaked into the
  database across the entire v0.7.61 → v0.7.183 window. Fixed by
  switching to `DELETE chat_session WHERE id IN $ids`.

  **(2) `/chat/stream` had a dead handler + str(e) leak.**
  `api/routers/chat.py:1032-1040` had `except NotFoundError:` that
  shadowed the v0.7.183 bulk-inserted
  `except (NotFoundError, InvalidInputError): raise`. The v0.7.183
  clause was unreachable AND semantically wrong for a streaming
  context (HTTP status has already been emitted; we can't bubble
  to the global 404 handler). Narrowed to
  `except InvalidInputError as e:` + yield-as-event, matching the
  NotFoundError treatment. ALSO sanitised the catch-all
  `except Exception: yield ... str(e)` to a generic message —
  same info-leak class v0.7.168/177 closed for non-streaming routes.

  **(3) Sync source-processing leaked worker error message.**
  `api/routers/sources.py:642` did
  `HTTPException(500, detail=f"Processing failed: {result.error_message}")`.
  Worker errors carry SurrealDB driver frames + partial paths.
  Same info-leak class v0.7.177 closed for podcast_service.
  Sanitised to a generic detail; logger.error preserves the full
  message for ops.

  Plus: updated `tests/test_chat_stream.py::test_stream_emits_error_event_on_graph_exception`
  which had been effectively *asserting the str(e) leak* — now
  asserts the sanitised detail and the absence of raw exception
  text in the response body.

  Tests at `tests/test_v0_7_184_backend_high_bugs.py`: 4 new — AST
  pin on the cascade-delete query shape, dead-handler removal pin,
  generic-catch-all str(e) absence pin (scoped to the catch-all,
  not InvalidInputError which carries safe typed messages),
  source-processing info-leak pin.

  Backend suite: **948/948** (was 948 in v0.7.183, with 1 prior test
  rewritten to assert the leak was closed instead of the leak being
  present).

- **v0.7.183** 🐛🧹🎨 **Final deferred-list completion sweep — closes
  EVERY remaining backlog item from rounds 1-8.** Five independent
  surfaces, one version tag.

  **(1) source_chat.py redundant handler cleanup.** The v0.7.182
  bulk-sweep inserted `except (NotFoundError, InvalidInputError):
  raise` at 7 endpoints — but at 5 of those endpoints an explicit
  `except NotFoundError: raise HTTPException(404, "Source not
  found")` already caught NotFoundError above. The tuple form's
  NotFoundError leg was unreachable. v0.7.183 narrowed those 5 to
  `except InvalidInputError:` only (still routes 400 to the global
  handler via InvalidInputError). At the 2 sites that DON'T have an
  upstream NotFoundError handler (stream_source_chat_response,
  send_message_to_source_chat), the tuple form is preserved with
  an explicit inline comment.

  **(2) NotFoundError reraise — final 10 routers completed.** The
  v0.7.179/v0.7.181/v0.7.182 sweep covered 10 routers; this round
  closes the rest with the same audit-then-apply pattern:
    - `api/routers/context.py` — 1 endpoint fixed (was the only
      remaining function-level wrapper swallowing NotFoundError).
    - `api/routers/chat.py` — 9 reraises inserted (was largest
      remaining surface).
    - `api/routers/search.py` — 4 reraises.
    - `api/routers/embedding.py` — 2 reraises.
    - Audited-no-fix: gmail.py (singleton GmailIntegration.get()
      never raises NotFoundError), exports.py (export_note has no
      outer try wrapper; NotFoundError propagates clean to the
      global handler), commands.py / config.py /
      embedding_rebuild.py (no Model.get(id) calls — the
      `.get(...)` calls in those files are dict accesses).
  **Cumulative: 14 routers Safari-NotFoundError-clean.**

  **(3) iso() helper — final coverage.** v0.7.181/v0.7.182 covered
  10 routers; v0.7.183 swept the rest:
    - `api/routers/transformations.py` — 8 sites
    - `api/podcast_service.py` — 2 sites
    - `api/credentials_service.py` — 2 sites
    - `api/command_service.py` — 2 sites
  Forward-guard test `test_no_unsafe_str_dt_calls_anywhere_in_api`
  now scans the entire `api/` tree on every CI run; ANY future
  `str(X.created)` / `str(X.updated)` will fail loud.

  **(4) Cross-suite test pollution FIXED.** Two namespace
  collisions resolved:
    - `desktop/scripts/` → `desktop/dl_scripts/`. The
      `desktop/scripts/` folder name shadowed the root `scripts/`
      package whenever a desktop shim test inserted `desktop/`
      into sys.path. `from scripts.benchmark_models import ...`
      in `tests/test_v0_7_139.py` resolved to
      `desktop/scripts/benchmark_models` (which doesn't exist) →
      8 ModuleNotFoundError failures.
    - `desktop/tests/__init__.py` removed (was empty). With the
      file present, `desktop/tests/` became a package called
      `tests`, shadowing the root `tests/` directory the same way.
      `from tests.integration.conftest import ...` in
      `tests/test_v0_7_131.py` → 4 more failures.
    - The remaining 5 failures (test_v0_7_139.py
      TestJSONExtraction / TestPodcastSpeakerDetection /
      TestReportRendering) were downstream of the first scripts
      collision and now pass.
    **Combined `pytest tests/ desktop/tests/`: 1201/1201**
    (was 17 failed, 1177 passed in v0.7.182). Tracks as
    "Cross-file test pollution between tests/ and desktop/tests/
    (pre-existing)" — resolved.

  **(5) Frontend visual polish — markdown headers + Advanced page
  padding.**
    - `SourceDetailContent.tsx:600-601` — markdown h1/h2
      `font-bold` → `font-semibold`. Prevents the weight-shift
      when scrolling from the source title (v0.7.180 H1 standard,
      `font-semibold`) into the body content.
    - `(dashboard)/advanced/page.tsx:13` — outer padding `p-6` →
      `px-6 py-10 sm:px-8`. Brings the Advanced page to the
      v0.7.180 dashboard padding standard (Settings, Podcasts,
      Search, Models). No more cramped-to-the-rail feel.

  Tests at `tests/test_v0_7_183_completion_sweep.py`: 7 new — narrow
  handler pin, cumulative `str(X.created)` forward-guard, context.py
  pin, both namespace-collision pins (desktop/scripts/ AND
  desktop/tests/__init__.py), markdown header pin, Advanced page
  padding pin.

  **Final tallies:**
  - Backend `tests/`: **948/948** (was 941; +7 new).
  - Desktop `desktop/tests/`: **253/253** (unchanged).
  - **Combined `tests/ desktop/tests/`: 1201/1201** ← *was 17 failed
    going back many rounds; the pre-existing cross-suite pollution
    is FIXED.*
  - Frontend: **65/65** + tsc clean.

- **v0.7.182** 🐛🍎 **Round-8 sweep — iso() helper extended to 6 more
  routers + NotFoundError sweep continued to 4 more + response-model
  Optional[str] widening across all 8 domain shapes.** Continuation of
  v0.7.181 with broader scope.

  **(1) iso() migration — 6 new routers (20 sites total).** Continuation
  of the v0.7.181 fix. Routers migrated:
    - `api/routers/source_chat.py` — 6 sites (3 endpoints)
    - `api/routers/podcasts.py` — 2 sites
    - `api/routers/models.py` — 4 sites
    - `api/routers/exports.py` — 2 sites (HTML metadata serialisation
      — pre-fix would emit a SPACE-separated datetime into the exported
      file; downstream JS pipelines re-parsing those strings would
      hit the same Safari bug.)
    - `api/routers/embedding_rebuild.py` — 2 sites (rebuild-status
      response.started_at / .completed_at)
    - `api/routers/insights.py` — 4 sites

  Running total: **10 routers** Safari-safe. The remaining ~14 routers
  with no datetime serialization (search, context, commands, config,
  episode_profiles, speaker_profiles, etc.) aren't on the hot path
  for this bug.

  **(2) Response-model `Optional[str]` widening — 6 more shapes.** The
  v0.7.181 widening covered SourceResponse + SourceListResponse only.
  This round propagates the change to NotebookResponse, ModelResponse,
  TransformationResponse, NoteResponse, SourceInsightResponse, and
  CredentialResponse. Caught when the iso() migration in `models.py`
  exposed a latent test-suite failure (`test_create_same_model_name_*`
  → 500) on `ModelResponse.created` being required `str` but the test
  mocks producing None.

  **(3) NotFoundError re-raise — 4 new routers.** Continuation of the
  v0.7.179 + v0.7.181 sweep:
    - `api/routers/studio.py` — 5 reraises (was the largest unhandled
      surface — 28 broad excepts, none with typed reraise)
    - `api/routers/source_chat.py` — 7 reraises (had partial v0.7.108
      coverage; now bulk-applied)
    - `api/routers/episode_profiles.py` — 4 reraises
    - `api/routers/speaker_profiles.py` — 4 reraises

  Running total: **10 routers** with typed re-raise. Remaining: gmail,
  exports, embedding, embedding_rebuild, search, context, commands,
  config, insights, chat (deferred for next round).

  Tests at `tests/test_v0_7_182_sweep_continued.py`: 6 new — per-router
  iso() import pins, str() absence forward-guard, response-model
  Optional[str] cumulative pin, NotFoundError reraise cumulative pin,
  iso() helper contract re-pin.

  Full backend suite: **941/941** (was 935 in v0.7.181; +6 new).
  Frontend: **65/65** + tsc clean (no frontend touches this round).

- **v0.7.181** 🐛🍎 **Safari ISO datetime fix + SourceResponse shape
  reconciliation + NotFoundError sweep continuation (round-7).**
  Three independent surfaces, one version tag.

  **(1) `api/utils/iso.py` — Safari `new Date()` brittleness fix.**
  Python's `str(datetime)` produces a SPACE-separated form
  (`"2026-05-22 10:14:41+00:00"`) that Safari refuses to parse;
  every other browser accepts it silently. Pydantic response
  models declare `created` / `updated` as `str`, so router code
  is on the hook for the conversion — and the natural-looking
  `str(source.created)` is exactly wrong for Safari. v0.7.181
  introduces a None-safe, idempotent `iso(value)` helper at
  `api/utils/iso.py` that returns `.isoformat()` (T separator,
  Safari-safe) for datetimes and passes strings through unchanged.
  Migrated the four highest-traffic routers:

  - `api/routers/sources.py` — 10 sites
  - `api/routers/notebooks.py` — 4 sites
  - `api/routers/notes.py` — 6 sites
  - `api/routers/chat.py` — 8 sites

  Also widened `SourceResponse.created/updated` and
  `SourceListResponse.created/updated` from `str` to
  `Optional[str]` — the pre-v0.7.181 behavior silently returned
  the literal string `"None"` via `str(None)` during async-create
  paths where the row isn't persisted yet. The new `iso(None)`
  returns proper None; Optional[str] is the matching type change.

  **(2) `SourceResponse` / `SourceListResponse` shape
  reconciliation.** The list endpoint reported `insights_count`
  per row; the detail endpoint did not — clicking into a source
  silently dropped the "N transformations" badge from the
  sidebar. v0.7.181 adds `insights_count: int = 0` to
  SourceResponse (default for backward compat with POST/PUT/retry
  constructions), wires a fast `SELECT VALUE count() FROM
  source_insight WHERE source = $source_id` aggregate into the
  detail endpoint, and tightens `processing_info` from bare
  `dict` to `dict[str, Any]` (matching the list endpoint). The
  intentional asymmetries (full_text + notebooks list-only
  omission) are preserved and documented inline in models.py.

  **(3) NotFoundError re-raise sweep continued —
  `api/routers/credentials.py` + `api/routers/transformations.py`.**
  Continuation of the v0.7.179 sweep that covered notebooks /
  podcasts / models. Each got the `except (NotFoundError,
  InvalidInputError): raise` clause inserted before every broad
  Exception handler (12 + 5 endpoints respectively), plus the
  typed imports. Forward-guard test extended.

  Tests at `tests/test_v0_7_181_iso_helper.py`: 8 new — helper
  contract, T-separator invariant, idempotency, per-router
  migration pins, forward-guard against `str(X.created)`
  regressions in the four migrated files.

  Tests at `tests/test_v0_7_181_source_response.py`: 5 new —
  insights_count field pin, processing_info type pin, detail-
  endpoint count query pin, list-endpoint field pin, and a
  YAGNI guard against future "let's add full_text to list for
  symmetry" PRs.

  Full backend suite: **935/935** (was 922 in v0.7.180; +13 net
  new tests, all green). Frontend: **65/65**.

- **v0.7.180** 🎨🧹 **Visual polish + locale cleanup + Zustand
  forward-guard.** Five low-risk surfaces from the deferred backlog.

  **(1) `sidebar-store.ts` partialize.** The Zustand store used
  `persist` middleware with no explicit `partialize`. Today the
  store only has `isCollapsed` so this was a no-op — BUT the moment
  a future contributor adds ephemeral state (`isHovered`,
  `lastClickedAt`, a transition flag), the default behavior would
  silently bleed it into localStorage and across page reloads.
  Codified the persistence boundary as `partialize: (state) => ({
  isCollapsed: state.isCollapsed })`. Matches the pattern auth-store
  already uses per `lib/stores/CLAUDE.md`. Zero behavior change today;
  full forward-guard tomorrow.

  **(2) Orphaned i18n keys removed: `searchPage.chooseAMode` +
  `podcasts.chooseAView`.** v0.7.153 + v0.7.164 removed the
  "CHOOSE A MODE" / "CHOOSE A VIEW" all-caps captions from Search
  and Podcasts (two-tab toggles are self-explanatory) but left the
  unused locale keys behind in all 10 locale files. The vitest
  `Unused Key Detection` test eventually flagged them. Removed
  10 lines × 2 keys = 20 lines from `en-US`, `bn-IN`, `es-ES`,
  `fr-FR`, `it-IT`, `ja-JP`, `pt-BR`, `ru-RU`, `zh-CN`, `zh-TW`.

  **(3) H1 weight standardisation — Advanced + Sources pages.**
  Last two dashboard H1s still on the legacy `text-3xl font-bold`
  pattern. Both promoted to the v0.7.153 standard
  `text-3xl font-semibold tracking-tight`. Every dashboard route
  now reads with consistent weight. Also fixed the inline-edit
  titles in `NotebookHeader.tsx` and `SourceDetailContent.tsx`
  (`text-2xl font-bold` → `text-2xl font-semibold`) so the
  notebook/source detail titles don't outweigh the H1s above them.

  **(4) Hardcoded `text-gray-500/600` → `text-muted-foreground` in
  SourceCard.** 4 spots: source-type indicator + processing message
  + 2 progress labels. Cards now absorb the active theme's muted
  hue instead of pinning a literal gray that's wrong in
  Solarized / Nord / Dracula / Paper / etc.

  **(5) `text-red-500` → `text-destructive` in three error
  surfaces.** `sources/page.tsx:270` (error banner),
  `SourceDetailContent.tsx:451` (load error), and
  `RebuildEmbeddings.tsx:256` (failed-status icon). Same pattern
  v0.7.165 used for ErrorBoundary. Held to the user's "no theme
  color changes" constraint by NOT touching the
  queued/running/completed icon colors (yellow/blue/green) — those
  keep their semantic palette; only the destructive case has a
  canonical theme token.

  Frontend `tsc` clean. **Frontend tests: 65/65** (up from 64 — the
  unused-key test now passes after the locale cleanup; was the
  flagging mechanism that caught `podcasts.chooseAView`).
  **Backend suite: 922/922.**

- **v0.7.179** 🐛 **NotFoundError re-raise sweep across three high-
  traffic routers — fixes wrong-status responses on legitimate 404s
  (MED severity).** Continuation of the v0.7.178 fix to
  `sources.py::create_source_insight`; applies the same pattern
  systematically across notebooks.py, podcasts.py, and models.py.

  Background: `Source.get()` / `Notebook.get()` / `Model.get()` —
  every domain-model fetcher rooted at `ObjectModel.get()` —
  raises `NotFoundError` when the record isn't found, instead of
  returning None (`open_notebook/domain/base.py:183`). The local
  `if not source: raise HTTPException(404)` guards that appear
  throughout the routers are therefore dead code. The real bug:
  the broad `except Exception` handler at the bottom of nearly
  every endpoint intercepts NotFoundError *before* it bubbles to
  the global FastAPI handler at `api/main.py:651`, so what should
  be a 404 surfaces as a generic 500.

  v0.7.179 adds `except (NotFoundError, InvalidInputError): raise`
  before every plain broad-Exception handler in the three biggest
  routers. Endpoint count:

  - `api/routers/notebooks.py` — 7 endpoints fixed (delete-
    preview, get_notebook, update_notebook, add_source_to_notebook,
    remove_source_from_notebook, delete_notebook, plus get_notebooks).
  - `api/routers/podcasts.py` — 6 endpoints fixed.
  - `api/routers/models.py` — 2 endpoints fixed.

  Plus a forward-guard meta-test
  (`test_forward_guard_domain_get_implies_notfounderror_import`)
  that pins: any router file importing a domain model AND calling
  `.get()` AND catching Exception MUST also import `NotFoundError`.
  Stops a future contributor from adding a new endpoint that
  silently swallows 404s.

  Tests at `tests/test_v0_7_179_notfound_sweep.py`: 6 new — per-
  router import + re-raise pins, broad-handler counter sanity
  check, and the forward-guard.

  Wider sweep across the remaining ~12 routers is deferred as a
  follow-on (gmail.py, credentials.py, search.py, transformations.py,
  studio.py, source_chat.py, chat.py, episode_profiles.py,
  speaker_profiles.py, exports.py, embedding.py, embedding_rebuild.py,
  context.py, commands.py, config.py, insights.py, notes.py).

  Full backend suite: **922/922** (was 916 in v0.7.178).

- **v0.7.178** 🐛 **Round-5 deferred sweep: embedding OOM cap,
  NotFoundError re-raise, two more studio str(exc) leaks (MED+LOW
  severity).** Four independent surfaces, one version tag.

  1. **`commands/embedding_commands.py` chunk-count cap.** The
     per-source chunk list had no ceiling — a 500MB plain-text
     upload chunks to ~333k entries, each holding (chunk text +
     768-dim float32 embedding + record dict) simultaneously in
     memory before the bulk INSERT flushes. Worker OOMs.
     v0.7.178 adds `MAX_CHUNKS_PER_SOURCE = 10000` (~50MB peak,
     comfortable headroom for any legitimate document at the
     default 1500-char chunk size). Raised as `ValueError` so
     surreal_commands' `stop_on: [ValueError]` retry config
     does NOT spin the worker in a retry loop blowing up the
     same way each time.

  2. **`api/routers/sources.py::create_source_insight`
     NotFoundError re-raise.** The bare `except Exception` clause
     swallowed `NotFoundError` / `InvalidInputError` from
     `Source.get()` / `Transformation.get()`, returning HTTP 500
     instead of letting the global FastAPI handlers in
     `api/main.py` map them to 404 / 400. The local
     `if not source: raise HTTPException(404)` guards were dead
     code — `Source.get()` raises `NotFoundError` instead of
     returning None
     (`open_notebook/domain/base.py:183`). Added the explicit
     `except (NotFoundError, InvalidInputError): raise` before
     the broad handler. The wider sweep across other routers is
     a separate task (see deferred section).

  3. **`api/routers/studio.py` two more str(exc) leaks.** The
     v0.7.168 + v0.7.177 sweeps missed two `detail=f"...{exc}"`
     raises (notebook-create failure + single-note fallback).
     Sanitized to generic messages; `logger.exception` still
     captures the full traceback for ops.

  4. **Forward-guard test on launcher drain-thread join.** The
     v0.7.58 race fix (join drain threads BEFORE closing log
     files in `stop_all`) is now AST-pinned. A future cleanup
     pass that reorders or drops the join would re-introduce
     'I/O on closed file' tracebacks on every desktop shutdown.

  Tests at `tests/test_v0_7_178_audit_sweep.py`: 5 new — chunk-
  cap constant pin + ValueError class pin, NotFoundError re-raise
  pin, two studio sanitized-detail pins, drain-thread join order
  forward-guard.

  Full backend suite: **916/916** (was 911 in v0.7.177).

- **v0.7.177** 🐛🔒 **Round-4 deferred sweep: podcast_service info-
  leak, cancel_command_job private-API fallback, forward-looking
  migration guard catches 8 & 15 (MED+LOW severity).** Three
  independent surfaces, one version tag.

  1. **podcast_service.py str(e) leak sweep.** The v0.7.168 router
     sweep handled every `api/routers/*.py` file but missed
     `api/podcast_service.py`. Four `HTTPException(detail=f"...
     {str(e)}")` raises were echoing driver internals (SurrealDB
     WS frames, RecordIDs, connection-pool diagnostics) back to
     the client. Sanitized to generic messages; `logger.error`
     still captures the full exception for ops.

  2. **cancel_command_job private-API fallback.** The function
     imported `surreal_commands.core.service.get_command_service`
     — a private module. An upstream rename of `core.service`
     would silently break ALL job cancellation with an ImportError
     swallowed by the broad `except Exception` below. v0.7.177
     wraps the private import in try/ImportError and falls back
     to a direct `repo_query` UPDATE on the `command` table
     (same pattern as the v0.7.172 lifespan reaper).

  3. **Migration idempotency forward-guard.** The new
     `test_every_up_migration_uses_idempotent_defines` meta-test
     caught two more unguarded migrations the v0.7.176 audit
     missed: migration 8 (`model_override` + `command` field
     additions) and migration 15 (all three `memory_*` tables).
     Both fixed with `IF NOT EXISTS` guards. The meta-test now
     serves as a sentinel against future contributors adding
     migration 17+ with the same footgun.

  Tests at `tests/test_v0_7_177_audit_sweep.py`: 5 new — AST pins
  on sanitized podcast_service details, on the try/ImportError
  fallback in cancel_command_job, on `command:` prefix handling,
  and the forward-guard meta-test.

  Full backend suite: **911/911** (was 906 in v0.7.176).

- **v0.7.176** 🐛 **Migrations 12 and 16 are now re-run-safe (MED
  severity).** Round-3 deep-scan item #6.

  Background: `AsyncMigrationManager` records `_sbl_migrations` rows
  so each migration only fires once, but if that table is manually
  rolled back, a backup restore replaces a newer DB with an older
  snapshot, or a disaster-recovery procedure rewinds the version row,
  the next API startup re-runs migrations against a schema that
  already has the tables. In SurrealDB without `IF NOT EXISTS`:

  - `DEFINE FIELD ... ON foo TYPE string;` fails or silently
    overwrites on re-run.
  - `DEFINE INDEX ... ON foo FIELDS ...;` drops + recreates,
    opening a window where queries miss rows.

  Migrations 12 (`credential` table + `idx_credential_provider`
  index) and 16 (`gmail_integration`) were the last two without
  guards — every other migration either uses `IF NOT EXISTS` /
  `OVERWRITE` or is a data-only migration. v0.7.176 adds the
  guards.

  Tests at `tests/test_v0_7_176_migration_idempotency.py`: 5 new —
  every DEFINE in 12.surrealql and 16.surrealql carries a guard,
  marker comments preserved, no regressions in other migrations.

- **v0.7.175** 🐛 **`/sources/{id}/insights` now routes through
  `CommandService.submit_command_job` — adds the missing 10-second
  timeout cap (MED severity).** Round-3 deep-scan item #4.

  Background: every other call site for `submit_command` had already
  been migrated to `CommandService.submit_command_job` (see
  sources.py:520, sources.py:1064), which wraps the sync
  `submit_command` call in `asyncio.wait_for(timeout=10)` and raises
  `ValueError` on timeout. The insight-submission endpoint at
  sources.py:1205 was the lone holdout — a bare
  `asyncio.to_thread(submit_command, ...)` with no timeout cap. On a
  saturated SurrealDB pool / hung WebSocket handshake this could
  block a FastAPI worker slot indefinitely, pinning one worker per
  stuck insight request.

  v0.7.175 routes through `CommandService.submit_command_job` and
  translates the `ValueError` (CommandService's timeout signal) into
  HTTP 503 ("service overloaded, retry shortly") rather than the
  generic 500 — letting clients distinguish transient overload from
  a real server error and back off appropriately.

  Tests at `tests/test_v0_7_175_insights_timeout.py`: 3 new — AST
  pins on `submit_command_job` routing, 503 status code on
  ValueError, and `logger.warning` before the raise (so saturated-
  pool incidents aren't silent).

  Full backend suite: **906/906** (was 898 in v0.7.174).

- **v0.7.174** 🔒 **Per-session lock serializes concurrent `/chat/execute`
  + `/chat/stream` — fixes silently-lost turns (HIGH severity).**
  Last remaining HIGH from the v0.7.169 deep-scan.

  Background: both `/chat/execute` and `/chat/stream` (plus their
  source-chat analogue) followed this pattern:

      current_state = await asyncio.to_thread(chat_graph.get_state, ...)
      state_values["messages"].append(HumanMessage(...))
      result = await chat_graph.ainvoke(state_values, ...)

  Two concurrent requests to the SAME `thread_id` (two open tabs,
  an SSE reconnect racing a fresh POST, an aggressive automated
  client retry) each hit `get_state` independently, each appended
  their own HumanMessage in process memory, each invoked the graph.
  The `add_messages` reducer DID append both new messages — but
  each ainvoke's INPUT state was missing the other's user turn,
  so request B's LLM never saw request A's question and the saved
  checkpoint could end up with one AIMessage overwriting the
  other. Net effect: silently lost turns.

  **Fix:** new `api/utils/session_locks.py` module with a
  `WeakValueDictionary[str, asyncio.Lock]` registry +
  `get_session_lock(session_id)` accessor. Critical sections in
  three endpoints now run under the lock:

  - `api/routers/chat.py:execute_chat` — `async with session_lock:`
    wraps state-read + ainvoke (clean `async with` since the
    region is small).
  - `api/routers/chat.py:_stream_chat_events` — manual
    `acquire()`/`try`/`finally release()` because the critical
    section spans a multi-yield generator body; `async with` would
    require re-indenting the entire astream_events loop.
  - `api/routers/source_chat.py:stream_source_chat_response` —
    same manual pattern as the chat-stream path.

  **WeakValueDictionary** semantics: while a caller holds the
  lock (async-with or local reference), the lock survives. Once
  all holders release and the local refs go out of scope, the
  lock is GC'd and the WeakValueDict entry auto-evicts. No
  manual cleanup, no unbounded growth on a long-running install
  with many distinct session_ids.

  **Why per-session, not global:** the race is per-thread_id.
  Two unrelated notebooks chatting simultaneously shouldn't
  serialize on each other; only the literal "same chat session"
  case needs ordering.

  **GeneratorExit safety:** when FastAPI's StreamingResponse
  closes the generator early (client disconnect), Python's
  GeneratorExit cleanup runs the `finally` block — the lock
  IS released even mid-stream. Confirmed by the early-`return`
  path in the disconnect handler.

  Tests at `tests/test_v0_7_174_session_locks.py`: 7 new —
  same-session-returns-same-lock, different-sessions-get-different-
  locks, **timing-based serialization proof** (two concurrent
  acquirers can't both be in the critical section at once,
  verified via timestamp recording), WeakValueDictionary
  GC-eligibility check, plus AST pins on all three endpoint
  wrap sites.

  Full backend suite: **898/898** (was 891 in v0.7.172-173).

- **v0.7.173** 🐛 **Launcher spawns children into their own process
  group + kills the whole group on shutdown (HIGH severity).** Top
  desktop finding from the v0.7.169 deep-scan.

  Background: `desktop/launcher.py:_spawn` called bare
  `subprocess.Popen` with no process-group setup, and `stop_all`
  used `p.terminate()` — which only signals the immediate child.
  Next.js forks per-request workers (`next-server (v16.2.6)`),
  content-core forks PDF/OCR backends, llama-cpp may fork helpers;
  any grandchild reparented to PID 1 when the immediate child died,
  surviving past .app close. The user has personally seen the
  `next-server` zombies accumulating between launches.

  v0.7.142 `reap_orphans` is a startup sweep, not a shutdown one
  — so closing the .app left zombies until the next launch.

  Fix in `desktop/launcher.py`:

  1. **Spawn-time isolation.** `_spawn` now passes
     `start_new_session=True` on POSIX (makes the child a process-
     group leader) or `creationflags=CREATE_NEW_PROCESS_GROUP` on
     Windows. The whole subtree rooted at the immediate child
     now shares one pgid.
  2. **Shutdown-time pkill.** `stop_all` reversed-iterates the
     procs and calls `os.killpg(pid, signal.SIGTERM)` (POSIX) or
     `os.kill(pid, signal.CTRL_BREAK_EVENT)` (Windows). The pgid
     equals the leader's PID (because we set
     `start_new_session=True`). One signal → entire group dies.
  3. **Fallback chain** preserves the existing v0.7.82 test-mock
     contract: `ProcessLookupError`/`PermissionError`/`OSError`
     are caught, and we fall through to `p.terminate()` so
     `MagicMock(spec=Popen)` in tests still works.

  Tests at `desktop/tests/test_v0_7_173_process_group.py`: 5
  AST-level guards on the spawn-side kwarg, the Windows
  equivalent, the killpg call, the fallback chain, and the
  `signal` import. Existing 15 launcher tests pass unchanged.

  After the next rebuild the orphan-zombie next-server processes
  the user has seen accumulating between launches will get killed
  properly on .app close.

- **v0.7.172** 🐛 **Lifespan-startup stale-command reaper.** Top
  MEDIUM finding from the deep-scan. Fixes the "frontend polls
  forever after worker crash" UX bug.

  Background: if the surreal-commands worker crashed / was OOM-
  killed mid-job, the command row stayed in `new` / `queued` /
  `running` forever. The frontend's `useSourceStatus` polls every
  2 seconds while status is in any of those states — silent
  CPU + DB load forever, with no path to recovery short of
  manual SurrealQL or a Source.delete + recreate.

  On API restart we KNOW the worker isn't still mid-job (the
  launcher's process tree restarts together), so any pre-restart
  row in a non-terminal state is stale.

  Fix in `api/main.py` lifespan (between dedupe and digest
  scheduler):

  ```sql
  UPDATE command
  SET status = 'failed',
      error_message = 'Marked stale on API restart — …',
      updated = time::now()
  WHERE status IN ['new', 'queued', 'running']
    AND updated < (time::now() - 30m)
  RETURN id;
  ```

  The 30-minute updated-time filter is belt-and-suspenders: in
  the unlikely future case of cross-process worker supervision,
  we don't wipe an actually-running job mid-execution. For the
  current desktop-launcher process-tree model this is overkill
  but cheap.

  Reaped count is logged at WARNING level (visible in api.log
  filters) so an install that consistently reaps stale rows
  on every restart shows up as a canary for "the worker is
  unreliable".

  Wrapped in try/except so a SurrealDB hiccup at startup doesn't
  block the API from coming up — the reaper is purely defensive.

  Tests at `tests/test_v0_7_172_stale_command_reaper.py`: 3
  AST-level guards covering the query shape, the non-fatal
  try/except wrapper, and the WARNING-level canary log.

  Combined suite: **891/891 backend** + **253/253 desktop** in
  isolation (was 888/884 / 248/248 in v0.7.171).

- **v0.7.171** 🐛🔒 **LangGraph checkpoint cleanup on session delete —
  fixes unbounded SQLite growth + thread-ID-collision data leak.**
  Top finding from the v0.7.169 deep-scan (HIGH severity).

  Background: `ChatSession.delete()` removed the SurrealDB row but
  never touched the LangGraph SQLite checkpoint store. Two problems
  compounded:

    1. **Unbounded disk growth.** The `checkpoints` + `writes`
       tables in `langgraph.sqlite` kept the full transcript of
       every deleted chat forever, indexed by `thread_id =
       full_session_id`. Chat-heavy users accumulate hundreds of MB
       over months. The v0.7.125 `checkpoint_prune` task keeps N
       newest per thread but does NOT delete orphaned threads.
    2. **Thread-ID-collision leak.** If a `chat_session:` ULID ever
       collided with one used in the past (test harness, manual
       SurrealQL insert, restored backup), the "new" session
       inherited the prior conversation as its history — wrong
       transcript surfaced to a different user or context. Low
       probability but high impact when it hits.

  Fix in both `api/routers/chat.py:delete_session` and
  `api/routers/source_chat.py` analogue:

  ```python
  await session.delete()
  try:
      checkpointer = getattr(chat_graph, "checkpointer", None)
      delete_thread = getattr(checkpointer, "delete_thread", None)
      if delete_thread is not None:
          await asyncio.to_thread(delete_thread, full_session_id)
  except Exception as cleanup_exc:
      logger.warning("…cleanup failed (non-fatal): {}", cleanup_exc)
  ```

  - **`getattr` chain** defends against LangGraph versions that may
    not ship the method (older versions, swapped checkpoint
    backends). Falls through cleanly without crashing the delete.
  - **`asyncio.to_thread`** because SqliteSaver's `delete_thread` is
    sync — same bridging pattern as `get_session_message_count`.
  - **Best-effort try/except** so the cleanup never blocks the
    primary SurrealDB delete (the row is GONE; any orphan
    checkpoint will be caught by the existing prune-loop on its
    next sweep). Logged at WARNING level so a systematic failure
    (LangGraph upgrade broke our private-method assumption) surfaces
    as a canary in api.log filters.
  - **Order matters**: SurrealDB delete runs FIRST. Reversed order
    would risk an orphaned session row pointing at empty history
    on a partial failure.

  Tests at `tests/test_v0_7_171_checkpoint_cleanup.py`: 4 AST-level
  pins — both delete paths invoke `checkpointer.delete_thread`,
  both wrap in try/except + WARNING log, and order (`session.delete()`
  precedes cleanup) is enforced. A future refactor that drops the
  cleanup OR swaps the order fails deterministically at test-collection
  time.

  Full backend suite: **888/888** (was 884 in v0.7.170).

- **v0.7.170** 🐛 **Datetime aware/naive normalization — repository.py +
  gmail._parse_dt.** Both sites previously could produce naive
  datetimes that would TypeError when compared against aware ones.

  - `open_notebook/database/repository.py:repo_update` —
    `datetime.fromisoformat(data["created"])` returns naive when the
    input string has no tz suffix. The adjacent line writes
    `datetime.now(timezone.utc)` (aware), so a row could end up with
    a mixed-aware pair that breaks downstream comparison code.
  - `open_notebook/domain/gmail.py:_parse_dt` — passed naive datetime
    instances through unchanged AND `fromisoformat` could leak naive.
    `needs_refresh` at line 242 then did
    `datetime.now(timezone.utc) >= self.token_expires_at` which
    raised `TypeError: can't compare offset-naive and offset-aware
    datetimes`. The Gmail-token-refresh path could crash silently.

  Both fixes: `if x.tzinfo is None: x = x.replace(tzinfo=timezone.utc)`.
  Matches the convention everywhere else in the codebase that uses
  `timezone.utc` explicitly.

  Tests at `tests/test_v0_7_170_datetime_aware.py`: 9 new — naive
  ISO strings, Z-suffix strings, aware strings, naive datetime
  instances, aware datetime instances, None/empty/unparseable
  fallthrough, plus an end-to-end `needs_refresh` test against a
  naive DB input. AST guard on the repository.py normalization
  ensures a future refactor can't drop it.

  Suite: **884/884** (was 875 in v0.7.169).

- **v0.7.169** ⚡ **Pagination completion — `Notebook.get_chat_sessions`
  + `podcast_commands` unbounded SELECTs.** Two remaining items from
  the v0.7.165 deferred list, both same shape family as v0.7.159 /
  v0.7.163 / v0.7.166.

  **(1) `Notebook.get_chat_sessions()` paginated.**
  `open_notebook/domain/notebook.py:135`. v0.7.161 made the
  per-session LangGraph checkpoint reads concurrent (read-side
  fan-out), but the underlying SurrealQL SELECT that listed every
  chat session attached to the notebook was still unbounded. A
  power user with hundreds of sessions per notebook paid for the
  full table scan + relationship traversal BEFORE the parallel
  checkpoint reads even started.

  Fix: optional `limit` / `offset` parameters with the same
  validation contract as `ObjectModel.get_all` from v0.7.159
  (positive int / non-negative int, `bool` explicitly rejected,
  `InvalidInputError` raised pre-try-block so it propagates to
  HTTP 400 via the global handler instead of getting clobbered
  to 500). Defaults stay `None` for backward compatibility —
  existing callers keep the unbounded behavior.

  `api/routers/chat.py:get_sessions` wires the new parameters
  through with sensible defaults: `limit=Query(100, ge=1, le=1000)`,
  `offset=Query(0, ge=0)`. The right-rail Chat list is bounded
  to 100 newest sessions by default; clients can paginate beyond
  that with `?offset=100`.

  **(2) `commands/podcast_commands.py` LIMIT 1000 on the two
  unbounded `SELECT *` calls.** Both `episode_profile` and
  `speaker_profile` tables are typically small (<20 user-defined
  rows each) but the unbounded shape was the same defensive gap
  v0.7.159 was closing — a script-generated or migration-artifact
  population could blow up the podcast-generate path's memory
  footprint on every job. Now LIMIT 1000 (generous — well above
  any realistic install) with a WARNING log if either limit is
  ever hit. The canary log surfaces in api.log filters so an
  operator who actually grows past 1000 profiles sees the signal
  immediately rather than wondering why profiles are missing
  from podcast generation.

  Tests at `tests/test_v0_7_169_pagination_completion.py`: 7 new
  — AST checks for the router wiring + the podcast LIMIT clauses,
  runtime checks for the validation contract (invalid limit/offset
  → InvalidInputError), and back-compat check that no-args calls
  still produce an unbounded query.

  Full backend suite: **875/875** (was 868 in v0.7.168; +7 new).

- **v0.7.168** 🔒 **HTTPException detail leakage sweep — strip
  `: {str(e)}` from 66 sites across 11 routers.** Carried from
  v0.7.165 deferred list (the biggest remaining code-scan finding).

  Background: the codebase had a recurring pattern of

      except Exception as e:
          logger.error(f"Error fetching X: {str(e)}")
          raise HTTPException(
              status_code=500, detail=f"Error fetching X: {str(e)}"
          )

  …where the **same** raw exception text was both logged AND returned
  in the HTTP response body. The `logger.error()` is correct — the
  api.log is the right place for the full traceback — but echoing
  `str(e)` to the user leaks:
    - SurrealDB driver internals + class names
    - File paths from loguru-formatted exception strings
    - Database connection details on connection errors
    - On rare occasions, API keys (when an upstream provider's
      error message echoes the bad key)

  Plus those raw strings are untranslatable; the frontend's i18n
  layer (`getApiErrorMessage` in `lib/utils/error-handler.ts`) can
  only meaningfully translate a stable prefix, not an arbitrary
  `RuntimeError: <some-driver-thing>`.

  Mechanical sweep with a Python regex against the consistent
  `detail=f"<prefix>: {str(e)}"` pattern. 66 sites fixed across:

    | Router               | Sites |
    |----------------------|-------|
    | sources.py           | 12    |
    | models.py            | 11    |
    | notebooks.py         | 8     |
    | transformations.py   | 8     |
    | chat.py              | 7     |
    | source_chat.py       | 6     |
    | notes.py             | 5     |
    | search.py            | 4     |
    | embedding.py         | 2     |
    | embedding_rebuild.py | 2     |
    | context.py           | 1     |

  After sweep: `detail=f"<prefix>: {str(e)}"` → `detail="<prefix>"`.
  The preceding `logger.error(f"...: {str(e)}")` line is unchanged
  — operators tailing api.log still get the full text; only the
  user-facing response is sanitized.

  **Regression guard** (`tests/test_v0_7_168_no_str_e_leakage.py`):
  parametrized test that asserts EVERY file in `api/routers/`
  contains zero `detail=f"...: {str(e)}"` patterns. 28 tests
  (one per router file + a global aggregate) — a future PR adding
  a new endpoint that reintroduces the pattern fails at
  test-collection time with the offending line(s) printed.

  Migration guidance baked into the test docstring: if a new
  endpoint legitimately needs to surface a richer error message,
  raise a typed `OpenNotebookError` subclass (`NotFoundError`,
  `InvalidInputError`, `RateLimitError`, etc.) — those have
  explicit, safe message contracts and the global exception
  handlers in `api/main.py:567-616` map them to the right HTTP
  status with the right detail format.

  Full backend suite: **868/868** (was 840 in v0.7.166; +28 new
  parametrized tests in this commit).

- **v0.7.167** 🎨 **Visual quick-wins batch.** Three small,
  high-visibility fixes from the v0.7.164 secondary-opportunities
  list. All low-risk; zero behavior change.

  **(1) RebuildEmbeddings — raw `⚠️` emoji → lucide `<AlertTriangle>`.**
  `RebuildEmbeddings.tsx:292` was the only icon-via-Unicode in an
  otherwise lucide-driven UI. Jarring next to the sibling
  `<AlertCircle>` icons in the same component. Now consistent with
  the app-wide icon system.

  **(2) RebuildEmbeddings — stats grid less "marketing-dashboard"-y.**
  Stats numbers were `text-2xl font-bold` next to `text-sm` labels —
  the numbers visually outweighed the surrounding settings UI.
  Toned down to `text-xl font-semibold`. Also made the 4-column grid
  responsive (`grid-cols-2 lg:grid-cols-4`) so on narrow viewports
  the stats don't crowd.

  **(3) ErrorBoundary — raw red palette → theme destructive tokens.**
  `ErrorBoundary.tsx:59-60,62` was the ONE place in the audit using
  raw Tailwind `red-100/red-600/red-900` outside the theme system.
  Meant the error fallback rendered with a jarring hardcoded red
  even when the user picked one of the 8 non-blue themes
  (Solarized, Nord, Paper, Dracula, etc.). Replaced with
  `bg-destructive/10` / `text-destructive` so the error UI absorbs
  the active theme's destructive hue.

  **(4) AppSidebar — separator noise.** 4 nav sections × 1
  `<Separator className="my-3" />` between each = 3 horizontal
  hairlines in the sidebar. Visually busy on what should be a quiet
  rail. The uppercase section labels (COLLECT, PROCESS, CREATE,
  MANAGE) already provide enough delineation; bumped section
  spacing to `mt-6` and dropped the separators entirely. Saves
  ~12px of visible chrome cumulative; reads as a calmer rail.

  Frontend `tsc` clean. No behavior change, no test changes.

- **v0.7.166** 🐛⚡ **Cache invalidation gap + archived-filter
  efficiency + bootstrap test debt.** Three follow-through items
  from the v0.7.165 deferred list.

  **(1) Frontend cache invalidation: sidebar counts stayed stale
  after every source/note mutation.** `GET /notebooks` returns
  `source_count` and `note_count` per row for the sidebar
  (`api/routers/notebooks.py:53-59`), but the source/note
  mutation hooks weren't invalidating `QUERY_KEYS.notebooks` on
  success. Result: after every add/delete the sidebar counters
  showed the old number until the next window-focus refetch —
  visible UX bug.

  Fix: added `queryClient.invalidateQueries({ queryKey:
  QUERY_KEYS.notebooks })` to 5 source mutation hooks
  (`useCreateSource`, `useDeleteSource`, `useFileUpload`,
  `useAddSourcesToNotebook`, `useRemoveSourceFromNotebook`) and
  2 note mutation hooks (`useCreateNote`, `useDeleteNote`). All
  7 onSuccess callbacks now refresh the sidebar count.

  **(2) Notebooks `?archived=` filter moved from Python to
  SurrealQL WHERE clause.** `api/routers/notebooks.py:64-65`
  previously fetched ALL notebook rows (including the per-row
  `source_count` + `note_count` subqueries) then filtered in
  Python with `[nb for nb in result if nb.get("archived") ==
  archived]`. A caller asking for `?archived=false` paid for the
  full archive scan plus the heavy subquery fan-out, then threw
  half the results away.

  Fix: build a conditional `WHERE archived = $archived` clause
  with proper parameter binding (NOT f-string interpolation),
  so SurrealDB skips archived rows server-side. The
  `validated_order_by` f-string interpolation is still safe —
  it's been checked against the allowed-fields + allowed-directions
  allowlists above. Three new tests pin this: `?archived=false`
  binds `$archived=False`, `?archived=true` binds `$archived=True`,
  no `archived` param → no WHERE clause emitted.

  **(3) `desktop/tests/test_bootstrap.py` test debt.** The
  `test_ensure_venv_creates_venv_and_writes_marker` test was
  asserting `len(run_calls) == 2` but v0.7.141 added the
  depcheck suite (6 additional `python -c 'import X'` probes),
  making the count 8. Test wasn't updated alongside v0.7.141 and
  has been silently failing in CI-style full-suite runs ever
  since. Updated to assert `>= 2` for venv-create + uv-install
  (the original contract) and added structural assertions on
  the remaining calls (each must be a `python -c 'import X'`
  probe against the fake venv interpreter).

  Tests: 5 new at `tests/test_v0_7_166_invalidation_and_archived.py`
  + the bootstrap-test fix. Full backend suite: **840/840**
  (was 835). Desktop suite: 248/248 (was 247/248).

  **Verified in isolation:** the 17 "failures" seen when running
  `tests/` + `desktop/tests/` together are pre-existing cross-file
  test pollution (shared module state); each suite passes cleanly
  on its own. Logged as a separate item for future test-infra
  cleanup; NOT caused by v0.7.166 changes.

- **v0.7.165** 🐛 **Production-readiness fixes — LangGraph state-shape
  guards + asyncio task GC risk.** Three top-priority items from the
  2026-05-21 code-improvement scan (round 2).

  **(1) `api/routers/chat.py:632-649` — missing dual-path guard on
  `result.get("messages", [])`** in the non-streaming /chat/execute
  handler. The graph currently returns a TypedDict so this works,
  but the streaming handler in the same file already applies the
  dual dict-vs-Pydantic guard explicitly because past LangGraph
  releases have shipped Pydantic-typed states. The CLAUDE.md
  standing audit calls this out as a recurring footgun
  (v0.7.52/55/56/75/81/95 are all prior fixes for the same pattern).

  Fix: normalize once at the top of the handler:

      result_messages = (
          result.get("messages", []) if isinstance(result, dict)
          else (getattr(result, "messages", None) or [])
      )

  Both downstream iteration sites (response-conversion loop +
  memory-extractor) now read this local — same source of truth,
  guarded once.

  **(2) `open_notebook/graphs/source.py:165-176` — missing dual-path
  guard on `result["output"]`** from `transform_graph.ainvoke`.
  Same shape-variance footgun. Two consecutive uses (`source.
  add_insight(transformation.title, result["output"])` and the
  returned `{"output": result["output"]}`) would both KeyError /
  AttributeError under a Pydantic state. Refactored to a single
  `output_text = result["output"] if isinstance(result, dict)
  else (getattr(result, "output", "") or "")` local.

  **(3) `api/main.py:365` — bare `asyncio.create_task` for gmail
  prewarm.** Python 3.11+ documented foot-gun: the asyncio loop
  only keeps a WEAK reference to created tasks, so a task that
  yields control immediately (our `await GmailIntegration.get()`
  awaits a SurrealDB roundtrip) can be GC'd before it resumes —
  silently dropping the pre-warm. The other two `create_task`
  calls in this lifespan (digest_scheduler, checkpoint_prune)
  already assign to local variables and cancel cleanly on
  shutdown; gmail-prewarm was the outlier.

  Fix: assign to `gmail_prewarm_task = asyncio.create_task(...)`,
  add a shutdown-time `if not gmail_prewarm_task.done(): await
  asyncio.wait_for(..., timeout=2)` (with cancel fallback) so the
  task is held alive AND cleaned up on lifespan tear-down.

  Tests at `tests/test_v0_7_165_state_shape_guards.py`: 5 new
  AST-level regression tests that pin the dual-path normalization
  pattern in both chat.py and source.py (look for
  `isinstance(result, dict)` + `getattr(result, ...)` adjacent to
  the read sites), plus syntax-validity guards on both edited
  files and a check that the gmail-prewarm task is assigned to
  a local variable. AST checks fail deterministically at collection
  time on a future refactor that drops the guard.

  Full backend suite: **835/835** (was 830).

  Remaining scan items deferred to follow-up commits:
    - GET /notebooks unbounded → pagination follow-through
    - get_chat_sessions unbounded → bound the session list itself
    - Frontend cache invalidation: source/note mutations don't
      invalidate notebooks query (sidebar counts stale)
    - HTTPException detail=f"{str(e)}" leakage across 40+ sites
    - `archived` filter in notebooks.py applied in Python after
      full table fetch (should be WHERE clause)
    - datetime aware/naive normalization in repository.py + gmail.py

- **v0.7.164** 🎨 **H1 hierarchy sweep — Notebooks, Transformations,
  Studio, Search pages match the v0.7.153 standard.** Top finding
  from the 2026-05-21 visual audit (item #4).

  Background: two competing H1 styles shipped side-by-side after
  v0.7.153. Settings, Podcasts, Models, Advanced, Setup-Wizard used
  the new `text-3xl font-semibold tracking-tight`; Notebooks,
  Transformations, Studio, Search still used the older
  `text-2xl font-bold`. Users saw different "page weight" depending
  on which route they visited — visible inconsistency.

  Pages updated (4 files):

    - **Notebooks** (`(dashboard)/notebooks/page.tsx:60`):
      H1 promoted; rest of header structure untouched.
    - **Transformations** (`(dashboard)/transformations/page.tsx:26-44`):
      H1 promoted AND broken alignment fixed. The previous JSX
      opened a `flex items-center justify-between` with only a
      left-half — the right slot was empty so `justify-between`
      did no work — and the description sat in a separate
      `max-w-5xl` block below with no top margin (orphaned).
      Replaced with a single `<header>` stack: title+refresh on
      the top row, description below via `space-y-2`.
    - **Studio** (`(dashboard)/studio/page.tsx:230-239`):
      H1 promoted. Subtitle bumped from `text-sm` to default body
      size — Studio is a flagship feature; the explainer copy
      shouldn't read as a footnote. `mb-1` → `space-y-2` for
      consistent breathing room.
    - **Search** (`(dashboard)/search/page.tsx:159-178`):
      Bigger fix here. Was `p-4 md:p-6` (smaller than every other
      dashboard page) with `text-xl md:text-2xl font-bold`
      (smallest H1 in the app). Standardised to
      `px-6 py-10 sm:px-8` + the v0.7.153 H1 style. Removed the
      noisy "CHOOSE A MODE" all-caps caption above the tabs —
      same fix as Podcasts in v0.7.153, two-tab toggles are
      self-explanatory. Translation key `searchPage.chooseAMode`
      now unused (preserved in locales for safety; cleanup
      deferred to a locale sweep).

  **Two more visual-audit items folded into this commit:**

  **Notebook detail header / workspace separation** (audit #1).
  `(dashboard)/notebooks/[id]/page.tsx:162-168`. The most-trafficked
  screen in the app previously read as one giant blob — header was
  `p-6 pb-0` (no bottom padding, no divider) and workspace was
  `p-6 pt-6`. Replaced with `flex-shrink-0 px-6 pt-6 pb-4 border-b`
  on the header (real breathing room + hairline divider) and
  `px-6 pt-8 pb-6` on the workspace below (columns "land" cleanly
  below the divider).

  **Source detail double-padding** (audit #2).
  `(dashboard)/sources/[id]/page.tsx:28-54`. Back-button band was
  `pt-6 pb-4 px-6` PLUS its own `mb-4` (~80px of empty space). Each
  column re-applied `px-4` on top of the outer `px-6` — 40px of
  horizontal padding squeezed the chat column on standard laptop
  widths. Tightened back-button to `px-6 pt-4 pb-2` (removed the
  redundant `mb-4`); dropped per-column `px-4` so outer `px-6` does
  all horizontal work. Chat column gains ~32px of breathing room
  on every viewport.

  Visual audit's items #5 (transformations alignment — folded into
  the H1 sweep above), #6 (setup-wizard CTA prominence), #7
  (Studio mode-picker tiles), plus the 20+ secondary opportunities
  remain open for follow-up commits.

  Frontend `tsc` clean. No locale changes (the unused
  `chooseAMode` key stays; cleanup deferred).

- **v0.7.163** ⚡ **Transformations pagination + credentials N+1
  parallelization.** Two follow-through items from the v0.7.159
  deferred list.

  **(1) `GET /transformations` paginated.** `api/routers/
  transformations.py:27` previously called
  `Transformation.get_all(order_by="name asc")` with no
  `LIMIT`. Same shape bug as `/notes` had pre-v0.7.159 — small
  table today (typically <50 user-defined rows), but a malicious
  / accidental population can return multi-MB JSON. Added
  `limit: int = Query(200, ge=1, le=1000)` + `offset: int =
  Query(0, ge=0)` to match the v0.7.159 convention. Tests verify
  the args thread through to `get_all`, defaults apply when
  unspecified, out-of-range values return 422.

  **(2) `GET /credentials` N+1 fixed.** `api/routers/
  credentials.py:121` ran a sequential await loop calling
  `cred.get_linked_models()` per credential. Each call hits
  SurrealDB with `SELECT * FROM model WHERE credential = $id`
  (`open_notebook/domain/credential.py:185`). A user with 13
  configured providers paid ~13 × ~30ms = ~400ms before the
  Models page list could render. Same pattern as v0.7.161
  chat-session N+1.

  Fix: replace the `for cred in credentials: models = await
  cred.get_linked_models()` loop with a single
  `asyncio.gather(*[c.get_linked_models() for c in credentials])`
  followed by a `zip(credentials, linked_models_lists)`
  comprehension. Wall-clock drops to ~30ms regardless of credential
  count.

  Bigger fix (denormalize a `model_count` field onto the credential
  row at write time) needs a schema migration + a post-save hook
  on Model. Deferred — gather captures the lion's share with zero
  schema risk.

  Tests at `tests/test_v0_7_163_pagination_and_n1.py`: 5 new
  passing tests covering both fixes. Route-level wall-clock test
  intentionally omitted (TestClient startup overhead makes a stable
  threshold flaky); the contract is pinned at the asyncio level
  with a 4× concurrency margin.

  Full backend suite: **830/830** (was 825 in v0.7.162).

- **v0.7.162** 🧪 **Router-level test coverage for `auth`, `languages`,
  and `embedding_rebuild`.** Three of the nine routers flagged by the
  improvement scan as having no `tests/test_*.py` module that imports
  them. Picked these three for highest leverage:

  - `auth.py` (26 lines): the bedrock of the entire auth middleware.
    Tests pin the two states (`auth_enabled=true/false`) and verify
    the endpoint NEVER leaks the configured password back to the
    caller in any string field of the response. Frontend's login
    flow depends on this signal — a regression here would silently
    break the unauthenticated-login UX path.
  - `languages.py` (83 lines): drives the podcast EpisodeProfile
    `language` picker. Tests verify (a) the result is a non-empty
    list of BCP 47 codes with hyphen separator (never underscore),
    (b) names are non-empty (no blank dropdown options), (c) the
    sort is name-ascending, and (d) the required regional variants
    (en-US, en-GB, pt-PT, zh-TW) survive any future refactor of
    the `_EXTRA_VARIANTS` list — those are the locales the launcher's
    preset library and Studio TTS configuration depend on.
  - `embedding_rebuild.py` (218 lines): we JUST refactored this in
    v0.7.160 (6 sequential `repo_query` calls → 3 parallel via
    `asyncio.gather` + new `_extract_count` helper). Highest
    regression risk in the codebase right now. Tests cover:
      * `_extract_count` helper: dict-shape, int-shape, empty/None,
        unexpected types — all the response variants SurrealDB
        emits depending on SELECT VALUE / GROUP ALL combinations.
      * End-to-end happy path: 3 selected branches → exactly 3
        queries → counts summed → response shape matches schema.
      * Opt-out path: deselecting notes/insights → ONLY the
        sources query fires (saves roundtrips, matches the
        opt-out semantics of the previous code).

  Test file at `tests/test_v0_7_162_router_coverage.py`. **9 new
  passing tests; full backend suite at 825/825**.

  Routers still missing dedicated test coverage (deferred to
  future commits): `context.py`, `commands.py`, `episode_profiles.py`,
  `speaker_profiles.py`, `search.py` (ask SSE path). Each is
  meaningfully complex enough to warrant its own focused commit
  rather than a sweep here.

- **v0.7.161** ⚡ **Chat-session N+1 — parallelize per-session
  LangGraph checkpoint reads.** Top improvement-scan finding from
  2026-05-21. Carried from v0.7.160 deferred list.

  Background: `GET /chat/sessions?notebook_id=X` (the right-hand
  Chat rail on every notebook page open) used to iterate sessions
  and SEQUENTIALLY await `get_session_message_count()` per row.
  Each call does a SQLite checkpoint read in a thread via
  `asyncio.to_thread(graph.get_state, ...)` (`open_notebook/utils/
  graph_utils.py:7`). A notebook with 50 chat sessions paid
  50 × ~30ms = **~1.5s wall-clock** before the rail could render —
  and that 1.5s held one of only 4 default DB-pool worker slots.

  The same pattern was worse in
  `GET /sources/{source_id}/chat/sessions` which did TWO
  sequential round-trips per session (a row fetch + the checkpoint
  read) — 30 sessions = 60 sequential hits.

  Fix:

  1. **`api/routers/chat.py:get_sessions`** — replaced the per-row
     `for session in sessions_list: msg_count = await ...` loop
     with `asyncio.gather(*[get_session_message_count(...) for ...])`
     followed by a `zip(sessions_list, msg_counts)` comprehension.
     Wall-clock for the 50-session case drops from N × 30ms to
     ~30ms regardless of N.
  2. **`api/routers/source_chat.py:get_source_chat_sessions`** — same
     refactor, with TWO `asyncio.gather` phases: one for the
     session-row fetches, one for the message-count reads. A
     between-phases delete race (session record removed between
     the relations query and the row fetch) returns an empty list
     for that ID, which we now skip cleanly with a guard inside
     the zip loop.

  **Why not the full denormalization fix:** the audit's first-best
  recommendation was to add a `total_messages` column to the
  `chat_session` row and update it at write time, so the
  GET endpoint could read directly from the row. That requires:
    - a SurrealDB schema migration to add the column
    - a LangGraph post-invoke hook (or custom checkpoint saver)
      to recompute the count whenever the chat graph appends
    - a backfill for existing rows
  Each piece is plausible but adds non-trivial risk to the chat
  write path. Parallelizing is risk-free (read path only, no
  schema change) and already captures the lion's share of the
  improvement. The denormalization path is now properly deferred.

  Tests (`tests/test_chat_sessions_n_plus_1.py`): 2 new
  regression tests that pin the concurrency contract by recording
  call-start timestamps and asserting the spread is sub-millisecond
  (a sequential regression would show spread ≥ (N-1) × per-call-
  delay). 2/2 pass. 814/814 pre-existing backend tests still pass.

- **v0.7.160** 🐛⚡ **Typed-exception re-raise across 3 routers +
  embedding_rebuild 6-query → 3-query parallel.** Low-risk improvement
  batch from the v0.7.159 deferred list.

  **(1) NotFoundError clobbered to 500** in 3 routers. Every endpoint
  using `await Model.get(id)` pattern raises `NotFoundError` from
  `open_notebook/domain/base.py:183` when the record is missing. A
  global handler at `api/main.py:567` maps that to HTTP 404 — but
  the per-router `except Exception as e: raise HTTPException(500)`
  block was catching it first and clobbering the status to 500.

  Affected paths (a stale frontend cache hitting a deleted record
  used to surface as "Server error" rather than "Not found"):
    - `GET / PUT / DELETE /notes/{id}` (3 endpoints,
      `api/routers/notes.py`)
    - `GET / DELETE / POST /insights/{id}` (3 endpoints,
      `api/routers/insights.py`)
    - `GET / PUT / DELETE /transformations/{id}` (3 endpoints,
      `api/routers/transformations.py`)

  Fix: import `NotFoundError` and add `except NotFoundError: raise`
  between `except HTTPException` and `except Exception`. The
  pattern matches v0.7.135's `except OpenNotebookError: raise` in
  `transformations.execute_transformation` (already correct).
  Nine endpoint handlers updated total.

  **(2) embedding_rebuild stats: 6 sequential round-trips → 3
  parallel.** `api/routers/embedding_rebuild.py:43-91` issued up to
  6 sequential `repo_query` calls (sources/notes/insights ×
  existing/all modes) on every rebuild submission. Each call paid
  the full SurrealDB roundtrip latency.

  Fix: factored each count into an async helper and ran the selected
  branches concurrently via `asyncio.gather`. Three branches max →
  3 roundtrips happen in parallel instead of sequentially. Helper
  `_extract_count()` deduplicates the dict/int dual-path that was
  copied three times verbatim. Behavior preserved exactly; only the
  scheduling changed.

  **Verification:** `pytest tests/ --ignore=tests/integration`
  → **814 passed**. No new tests added — typed re-raise behavior is
  best covered by integration tests against a live SurrealDB, and
  the existing `tests/test_v0_7_135_meta.py` AST scan already
  enforces this pattern for HTTPException; we extend it informally
  here by following the same convention.

  Audit note: `use-settings.ts:9` was flagged by the scan as
  having `refetchOnWindowFocus: true` — verified false-positive
  on re-read. `useSettings` uses the global default
  (`refetchOnWindowFocus: false`). Only `useObservabilitySettings`
  overrides to `true` with a documented justification (env-derived
  values change outside the API). Leaving as-is.

- **v0.7.159** ⚡ **Pagination on `ObjectModel.get_all` + frontend
  staleTime tuning.** Two improvement-scan findings, both quietly
  expensive on heavy installs.

  **(1) `Note.get_all()` was unbounded.**
  `api/routers/notes.py:13-31` — `GET /notes` (no notebook filter)
  resolved to `SELECT * FROM note ORDER BY updated DESC` with no
  `LIMIT`. Hundreds of notes × full content = multi-MB JSON per
  call. Same shape applies to any future caller of
  `ObjectModel.get_all` (`open_notebook/domain/base.py:39`).

  Fix:
    - `ObjectModel.get_all(order_by, limit=None, offset=None)` gains
      optional pagination args. Backward-compatible: existing callers
      that pass nothing still get unbounded results.
    - Defensive input validation BEFORE the try-block so
      `InvalidInputError` propagates cleanly as HTTP 400 instead of
      getting wrapped in `DatabaseOperationError` → HTTP 500.
      `bool` is rejected explicitly (Python's bool-is-int trap).
    - `api/routers/notes.py` adds `limit: int = Query(200, ge=1, le=1000)`
      and `offset: int = Query(0, ge=0)` to the unfiltered branch.
      Notebook-scoped notes are naturally bounded by the notebook's
      size, so that branch is unchanged.

  Tests at `tests/test_get_all_pagination.py` — 6 new:
    - no-args → unchanged query (back-compat)
    - `limit=200` → appends `LIMIT 200`
    - `limit=50, offset=100` → both clauses in `LIMIT … START …` order
    - negative limit → `InvalidInputError`
    - non-int offset → `InvalidInputError`
    - zero limit → `InvalidInputError`

  **(2) `useSources` + `useNotebookSources` had 5s staleTime +
  `refetchOnWindowFocus: true`.**
  Each refetch fans out to per-source insights_count + embedded-LIMIT-1
  subqueries (`api/routers/sources.py:288-310`); a 200-source notebook
  ran ~200 subqueries per tab-back. Cmd-Tab back to the app felt
  janky during typical multi-tab research workflows.

  Fix (`frontend/src/lib/hooks/use-sources.ts:18-26, 32-55`):
    - staleTime raised 5s → 60s on both hooks
    - `refetchOnWindowFocus: true` → `false` on both hooks
    - Mutations (create / update / delete) still invalidate the
      query keys explicitly, so the user's own actions stay accurate
    - `useSourceStatus` (the in-flight import polling hook) is
      untouched — its 2s refetchInterval is what should drive that path

  All previously-passing domain tests still pass; frontend `tsc`
  clean.

  Combined impact: tab switches stop firing heavy fan-out queries,
  `GET /notes` can no longer return multi-MB blobs, and a clearly-bad
  query param (`?limit=-5`) returns 400 instead of 500.

- **v0.7.158** ⚡🐛 **Reliability sweep — httpx timeouts, log dedup,
  RebuildEmbeddings stale-closure cleanup.** Three findings from the
  2026-05-21 improvement scan, bundled as one focused commit.

  **(1) Six `httpx.AsyncClient()` calls without timeout** in
  `api/chat_service.py` (lines 27, 53, 68, 98, 113, 156). httpx
  treats a missing `timeout=` as "wait forever". A hung downstream
  call (DB pool exhausted, SurrealDB unresponsive, LangGraph node
  stuck) would hold one of only 4 default request-pool slots
  INDEFINITELY — eventually starving the API of capacity to serve
  even healthy endpoints. Only `execute_chat` (line 138) had a
  bespoke timeout for long-running local LLMs.

  Fix: added a class-level `_DEFAULT_TIMEOUT =
  httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)`
  and threaded it through every `AsyncClient(...)` call. The 10-
  minute `read` budget on `execute_chat` is preserved as a per-call
  override.

  **(2) Podcast migration warning flood.** Every cold start logged
  ~14 identical `WARNING: No credential found for provider 'openai'`
  entries in api.log (one per legacy seed profile attempting to bind
  to gpt-5-mini / gpt-4o-mini-tts). Diagnostic value of the 2nd
  through 14th was zero — pure log noise drowning out real warnings.

  Fix (`open_notebook/podcasts/migration.py`): dedup the warning
  per `(provider, model_name)` pair using a process-level set
  `_credential_missing_warned`. First missing-credential call for
  each combination logs at WARNING level (same text); subsequent
  calls in the same process skip the log. After a restart the set
  is empty so any newly-missing combinations still surface.

  **(3) RebuildEmbeddings orphaned-setInterval bug**
  (`frontend/src/app/(dashboard)/advanced/components/RebuildEmbeddings.tsx`).
  The component stored `pollingInterval` in `useState`, wrapped
  `stopPolling` in `useCallback(..., [pollingInterval])`, and ran
  the unmount cleanup as `useEffect(() => () => stopPolling(),
  [stopPolling])`. Result: the unmount callback was RE-ARMED on
  every `setPollingInterval(...)` and each new callback closed
  over a stale `pollingInterval` value (often `null` before state
  propagated). When the user navigated away mid-poll, the live
  interval was never cleared → an orphaned 5-second poll hammered
  `/embedding/rebuild/status` forever.

  Fix: switched to `useRef<NodeJS.Timeout | null>(null)`. Reads
  and writes go through the ref so the cleanup always sees the
  current interval id; the unmount effect uses an empty deps array
  so it only fires on actual unmount. `useCallback` import dropped.

  Frontend `tsc` clean. Backend tests pass (7/7 gmail combined,
  no regressions).

  After rebuild, expected behavior:
    - api.log shows the credential-missing warning at most ONCE
      per (provider, model_name) per process lifetime
    - chat_service CRUD endpoints fail fast instead of hanging
      indefinitely when downstream stalls
    - Visiting /advanced and navigating away no longer leaves an
      orphaned status-poll running in the background

- **v0.7.157** ⚡ **Cache `GmailIntegration.get()` + bounded-wait query
  + lifespan pre-warm — eliminates the 4-8s "slow query" cluster on
  every cold launch.** Carried from v0.7.156 deferred list.

  Background: api.log showed two `slow query: 4247ms / 8613ms`
  warnings on `'SELECT * FROM ONLY $rid'` clustered at every cold
  start. The frontend mounts both `GmailSidebarButton` (polls
  `/api/onp/gmail/status`) and `GmailIntegration` (the full setup
  panel, also polls) — two concurrent requests, each blocked for
  4-8s on the same single-record SurrealDB lookup. The "frozen API
  after wizard" feeling was 100% attributable to this path.

  The underlying SurrealDB slowness on `SELECT * FROM ONLY $rid`
  for the gmail_integration:singleton record on cold-start isn't
  fully diagnosed (SurrealDB pool warmup or query-planner first-touch
  is the leading hypothesis), but the **read pattern is wrong
  regardless** — the singleton's data only changes when the user
  explicitly OAuth-connects/disconnects, but it was being re-fetched
  + re-decrypted on every 60s poll.

  Fix (`open_notebook/domain/gmail.py`):

  1. **Process-level TTL cache.** Module-level `_CACHE` dict with
     30-second TTL holds the fully-decrypted instance. First poll
     pays the SurrealDB cost ONCE; subsequent polls within 30s are
     free in-memory hits. Adaptive frontend polling (60s when
     disconnected, 300s when connected) means at most 1-2 DB hits
     per minute instead of the previous N concurrent slow queries
     per render.
  2. **3-second query timeout.** `asyncio.wait_for` wraps the
     repo_query so a misbehaving SurrealDB can no longer hold a
     request line for 8s+. On timeout we return a default instance
     and log a warning; the next user request retries.
  3. **Cache invalidation on `save()`.** After every write (OAuth
     connect, settings toggle, disconnect, forget-credentials) the
     cache is cleared so the next read sees fresh data — no UI lag
     after a settings change.
  4. **Empty-result caching.** Default-constructed instances (returned
     when the singleton record doesn't exist yet — fresh installs)
     are ALSO cached, so a brand-new user doesn't pay the slow query
     repeatedly until they actually connect.
  5. **Lifespan pre-warm task** (`api/main.py:lifespan`). At startup
     a background task calls `GmailIntegration.get()` so the
     cache is populated BEFORE the user's first page-load poll.
     Cost is paid once during startup (non-blocking via
     `asyncio.create_task`); user requests never see the cold
     query.

  Tests (`tests/test_gmail_cache.py`): 4 new regression tests:
    - `test_cache_hit_skips_db_query_within_ttl` — second .get()
      uses cache, not DB
    - `test_save_invalidates_cache` — write-through invalidation
    - `test_timeout_returns_default_instance` — bounded wait works
    - `test_empty_db_result_is_still_cached` — fresh-install path
  4/4 pass; existing gmail-router tests untouched (3/3 still pass).

  Expected after rebuild: `slow query` warnings against
  `'SELECT * FROM ONLY $rid'` disappear from api.log (replaced by
  a single warmup-time DB hit), the Models / Settings pages no
  longer feel frozen on first render, and the API is freed from
  serving redundant per-poll DB roundtrips.

- **v0.7.156** 🐛 **Filter migration-seeded OpenAI-only speakers from
  the episode_profile fallback pool.** LIKELY-BLOCKING audit finding
  from 2026-05-21.

  Migration `migrations/7.surrealql` seeds three speaker profiles —
  `tech_experts`, `solo_expert`, `business_panel` — all hardcoded to
  `tts_provider=openai` + `tts_model=gpt-4o-mini-tts`. v0.7.149's
  episode_profile bootstrap had a last-resort fallback chain:

      1. preset["speaker_profile"] (e.g. "Local Debate")
      2. "Local Duo" if registered
      3. sorted(existing_speakers)[0]  ← THE BUG

  On a fresh install where Piper voices aren't yet registered, the
  Local-* speaker bootstrap is skipped → only the migration-seeded
  speakers exist. Step 3 then alphabetically picks `business_panel`,
  binds all 9 episode presets to it, and every podcast generation
  attempt 500s at TTS time because no OpenAI credential exists.

  The user's current install isn't affected (Piper voices ARE
  registered) but ANY fresh install on a new machine would hit this.

  Fix (`desktop/auto_register/episode_profile.py:268-298`):

  1. Pre-compute `safe_fallback_speakers = existing_speakers -
     {"tech_experts", "solo_expert", "business_panel"}`.
  2. Step 3 now picks from `safe_fallback_speakers` only.
  3. If `safe_fallback_speakers` is empty (only migration seeds
     exist), skip the preset with a clear `WARNING` log line:

         Skipping preset 'Deep Dive': no LOCAL-* speaker profile
         available (only migration-seeded openai speakers exist —
         re-run auto-register once Piper voices are registered)

  4. The summary log adds a `skipped_no_speaker` counter so
     operators can see at a glance how many presets were skipped:

         Episode profile preset library: 0 created, 0 skipped
         (already existed), 0 created with degraded speaker_profile
         fallback, 9 skipped (no safe speaker_profile available)

  Tests (`desktop/tests/test_auto_register.py`): one new regression
  test `test_episode_profile_skips_when_only_migration_seeded_speakers_exist`
  asserts zero POSTs when only the openai-only seeds are present.
  21/21 auto_register tests pass.

  Recovery: any user already affected can re-run auto-register
  manually after Piper voices come online (currently happens
  automatically on the next launch, as long as Piper successfully
  starts).

- **v0.7.155** 🐛 **Fix 15 launcher-test failures — autouse stub for
  `acquire_singleton` + `reap_orphans`.** Pre-existing test failures
  tracked back through v0.7.145, originally reported as the "18
  `chat_llm_n_ctx` failures" deferred item. Audit on 2026-05-21
  identified the root cause:

  Every Supervisor test that calls `start_all()` traversed the v0.7.142
  singleton path at `desktop/launcher.py:153`:

      self._singleton = acquire_singleton(default_pid_file())

  `acquire_singleton()` writes to the REAL
  `~/.open-notebook-plus/launcher.pid`. When the user's actual app
  is running (PID 78740), the existing PID-file check raised
  `AlreadyRunning` and every test using `start_all()` failed at this
  line with:

      desktop.singleton.AlreadyRunning: Another Open Notebook Plus
        launcher is already running (PID 78740; lock at
        /Users/Antman/.open-notebook-plus/launcher.pid).

  This affected 15 tests, not 18 (the earlier count was off): all 11
  `test_supervisor_*` tests (start_all_children_in_order,
  stop_all_terminates_children, uses_venv_python_for_api_and_worker,
  writes_session_env, spawns_v03_children_when_paths_set,
  skips_v03_children_when_paths_missing,
  spawns_chat_llm_and_memory_retriever, spawns_openchronicle_when_available,
  skips_chat_llm_when_no_path,
  logs_and_progresses_when_optional_service_fails, plus the new v0.7.147
  injects_data_folder_absolute_path) and all 4 `test_chat_llm_n_ctx_*`
  tests.

  Fix (`desktop/tests/test_launcher.py`): added a module-scoped
  `autouse=True` fixture `_stub_singleton` that monkeypatches
  `desktop.singleton.acquire_singleton` to return a fake handle
  with `.release()` and `desktop.singleton.reap_orphans` to return
  an empty list. Both imports inside `Supervisor.start_all()` are
  function-scoped (`from desktop.singleton import …`), so the
  monkeypatch must target the SOURCE module (`desktop.singleton.*`)
  rather than the consumer module (`desktop.launcher.*`) — that's
  what the local-import binding resolves to at call time. Also
  removed the redundant in-line stub I'd briefly added to
  `_stub_launcher_io`; the autouse fixture covers every test in
  the file with no per-call boilerplate.

  Verification: `.venv/bin/python -m pytest desktop/tests/test_launcher.py
  -q` now reports **15 passed in 7.80s** (was 0 passing while the
  real app is running; intermittently passing when it wasn't).

  This closes the last open item on the v0.7.145 "deferred / pre-
  existing test failures noted but NOT caused by this commit" list.

- **v0.7.154** 🐛 **Restore `llama-cpp-python` in the lockfile + downgrade
  DANGEROUS CONFIG log level.** Two BLOCKING audit findings from
  2026-05-21.

  **Finding 1 — local-GGUF chat broken.** Every chat request against
  the local Hermes-3 model returned 500. `llamacpp_chat_stderr.log:1`:

      ModuleNotFoundError: No module named 'llama_cpp'

  Root cause: v0.7.141 introduced `make build-mac-lock` which
  regenerates `desktop/requirements.lock` from `pyproject.toml` only.
  `llama-cpp-python>=0.3.16,<0.4` was declared in
  `desktop/requirements.txt:18` (pinned for CVE-2024-42479 — heap OOB
  read in GGUF parsing) but NOT in `pyproject.toml`. The new
  build-mac-lock target silently dropped it; the bundled venv shipped
  without llama_cpp; chat failed at llama_cpp.server spawn. Auto-register
  saw the silent failure and logged "skipping local-GGUF credential
  registration: no llama-cpp server port supplied".

  Fix (`Makefile:386-407`): pass BOTH input files to
  `uv pip compile`:

      uv pip compile pyproject.toml desktop/requirements.txt
          --python-version 3.12 -o desktop/requirements.lock --quiet

  This matches what `pip install -r requirements.txt` would do at
  runtime (the CI's documented "installs on top of the upstream
  pyproject.toml" pattern). Regenerated locally: `llama-cpp-python
  ==0.3.23` is now present in `desktop/requirements.lock:312` (plus
  transitive deps `diskcache`, `jinja2`, `numpy`).

  **Finding 2 — log-level false alarm.** `api/main.py:430` was logging
  the CORS=*, no-password warning at ERROR level on every startup.
  The desktop launcher binds to 127.0.0.1 only, so the danger
  described doesn't apply — but the ERROR-level entry fired anyway,
  contaminating any log search for real failures.

  Fix (`api/main.py:429-449`): downgrade `logger.error` →
  `logger.warning` and add a clarifying sentence noting that the
  desktop fork's 127.0.0.1-only bind makes this the expected state.
  Message text otherwise unchanged. The full Docker/K8s warning
  copy remains so a future operator running this in a server context
  still gets the heads-up.

  **What this does NOT fix:**

    1. The `/api/config` 8-second slow-query (also flagged by the
       audit). Needs deeper investigation in
       `open_notebook/database/repository.py:repo_query` — defer to
       a separate commit.
    2. The 15 launcher-test failures from unmocked
       `acquire_singleton`. Tracked for v0.7.155.
    3. The episode_profile fallback that could pick `business_panel`
       on a fresh install. Tracked for v0.7.156.
    4. Gmail-status polling spam in `api.log` — likely a
       missing `useEffect` cleanup. Frontend-only, separate trace.

  After rebuild + reinstall (NB: requires `make build-mac` to
  regenerate the bundled venv via the new lock), the Hermes-3
  chat path will resolve cleanly and the launcher.log
  "skipping local-GGUF credential registration" warning goes away.

- **v0.7.153** 🎨 **Visual rhythm refresh — Settings, Podcasts, Models
  pages (spacing-only, no color changes).** Per the brainstorming on
  2026-05-21 the user identified three pain points: inputs/labels
  stacked too tightly, section headings not separating cleanly,
  and buried primary CTAs. Layout choice: Settings roomy/centered
  (~768px max-width), Models + Podcasts edge-to-edge wide. Theme
  system preserved — only spacing, type-scale, and hierarchy moved.

  Changes per page:

  **`frontend/src/app/(dashboard)/settings/page.tsx`**
    - Container: `p-6` → `px-6 py-10 sm:px-8`
    - Width: `max-w-4xl` (896px) → `max-w-3xl mx-auto` (768px, centered)
    - Vertical rhythm: `space-y-6` → `space-y-10` between sections
    - Header: flat flex row → `<header>` with `space-y-2`, h1
      promoted `text-2xl font-bold` → `text-3xl font-semibold
      tracking-tight`, refresh button moved to right-aligned slot
      via `flex items-start justify-between`

  **`frontend/src/app/(dashboard)/podcasts/page.tsx`**
    - Container: `px-6 py-6` → `px-6 py-10 sm:px-8`
    - Vertical rhythm: outer `space-y-6` → `space-y-10`; tabs
      inner `space-y-6` → `space-y-8`
    - Header: h1 `text-2xl` → `text-3xl font-semibold tracking-tight`,
      header `space-y-1` → `space-y-2` (title + subtitle no longer
      touch)
    - **Removed** the `text-xs uppercase tracking-wide` "CHOOSE A VIEW"
      caption above the tabs. Two-tab toggles are self-explanatory;
      the all-caps label was visual noise that directly contributed
      to the cramped-stacking pain point. Translation key
      `podcasts.chooseAView` is now unused (preserved in locales
      for safety; will be removed in a future cleanup).

  **`frontend/src/app/(dashboard)/settings/api-keys/page.tsx`** (the
  "Models" route)
    - Container: `p-6` → `px-6 py-10 sm:px-8`
    - Vertical rhythm: outer `space-y-6` → `space-y-12` between
      major sections; provider-card grid `gap-4` → `gap-5`
    - Header: h1 `text-2xl font-bold` → `text-3xl font-semibold
      tracking-tight`, icon `h-6 w-6` → `h-7 w-7`, gap-2 → gap-3
    - **Section structure**: grouped the heterogeneous blocks
      (Defaults + Reasoning, Email Digests, Providers) into
      explicit `<section>` wrappers with hairline `border-t pt-12`
      separators between them — the user can now visually scan
      where one section ends and the next begins
    - **Providers section gets an h2 heading** ("Providers") plus a
      short description above the filter row — the heart of the
      page is now visually labelled instead of buried under three
      banners + a filter strip
    - Help-link footer divider `pt-4` → `pt-6` for consistency

  Frontend tsc clean. No locale changes (would require touching all
  7 locale files to add new keys; spacing fixes don't need new
  copy). No tests added — these are pure layout/spacing changes
  with no behavior diff to assert against; visual review is the
  appropriate validation.

  After rebuild: each navigation page reads as cleanly-sectioned,
  breathes properly on wide monitors, and brings the primary
  Settings refresh / provider filter / podcast tab actions out of
  the cramped flow.

- **v0.7.152** 🐛 **Wire `voice_injection.js` to the real STT + TTS shim
  ports — no more "STT failed: HTTP 404" toast.** Final item from the
  v0.7.145 deferred list.

  The voice-injection JS (`desktop/first_run/static/voice_injection.js`)
  POSTs recorded audio to `/api/transcribe` and TTS-requests to
  `/api/audio/speech`. Both paths 404 on the main API — those endpoints
  don't exist. The actual implementations are PER-LAUNCH shim processes
  on dynamically-allocated ports:

      Whisper STT:  http://127.0.0.1:<whisper_port>/v1/audio/transcriptions
      Piper  TTS:   http://127.0.0.1:<piper_port>/v1/audio/speech

  The shim ports were already plumbed through to `desktop/app.py:686`
  (`whisper_port=getattr(sv, "whisper_port", ...), piper_port=...`) for
  the auto_register pipeline, but never made it into the browser-side
  voice JS. The JS supports overrides via `window.ONP_STT_URL` and
  `window.ONP_TTS_URL` — they just weren't being set.

  Fix (3 files):

  1. **`desktop/window.py`** — `_theme_injection_js()` gains optional
     `stt_url` and `tts_url` kwargs. When supplied, emits
     `window.ONP_STT_URL = "<url>";` and `window.ONP_TTS_URL = "<url>";`
     INSIDE the `(function() { ... })();` IIFE that loads the voice JS,
     so the globals are set BEFORE the script reads them.
  2. **`desktop/window.py::open_window`** — gains the same two kwargs
     and forwards them to `_theme_injection_js()`.
  3. **`desktop/app.py::_phase_open_window`** — resolves
     `stt_url`/`tts_url` from `ctx.sv.whisper_port` and `ctx.sv.piper_port`
     (already plumbed via the supervisor), passing `None` when the
     respective shim failed to start (port=0). When None, the JS falls
     back to its built-in `/api/transcribe` default — same broken
     behavior as today, no regression — rather than misroute to a stale
     port that may belong to a completely different process next launch.

  Tests (`desktop/tests/test_window.py`): 4 new regression tests:
    - assigns `window.ONP_STT_URL` when `stt_url` provided
    - assigns `window.ONP_TTS_URL` when `tts_url` provided
    - emits NO assignment when both are None (fall-through to default)
    - both URLs land simultaneously without one shadowing the other

  Subtle test note: `voice_injection.js` ITSELF contains the literal
  `window.ONP_STT_URL` token (as the fallback lookup
  `window.ONP_STT_URL || '/api/transcribe'`). The omit-when-None test
  has to assert against the ASSIGNMENT pattern (`window.ONP_STT_URL = `)
  rather than the bare name. 35/35 window tests pass.

  After rebuild + next launch with whisper + piper alive:
    - Mic FAB POST goes to `http://127.0.0.1:<whisper_port>/v1/audio/transcriptions`
      → 200 with `{text: "..."}` transcript
    - Speaker icon POST goes to `http://127.0.0.1:<piper_port>/v1/audio/speech`
      → 200 with audio/wav body
    - api.log shows ZERO `/api/transcribe` 404s

  This completes the v0.7.145 deferred list. v0.7.149-152 collectively
  resolve all four follow-ups.

- **v0.7.151** 🐛 **Capture `llama_cpp.server` stderr — Hermes-3 (and any
  future) crash is now diagnosable instead of silently swallowed.**
  Carried from v0.7.145 deferred list.

  Launcher.log showed:

      Failed to auto-start llama.cpp for 'GGUF/Hermes-3-Llama-3.1-8B-Q4_K_M.gguf':
      Traceback (most recent call last):
        File "desktop/app.py", line 287, in _phase_select_provider
        File "desktop/providers/llamacpp.py", line 73, in start
      RuntimeError: llama_cpp.server exited prematurely (returncode=1)

  …with absolutely no diagnostic context. `desktop/providers/llamacpp.py`
  was passing `stderr=subprocess.DEVNULL` to `subprocess.Popen`, so any
  error message from llama_cpp.server — unsupported model architecture
  (Hermes-3 needs a custom ROPE base), CUDA OOM, missing optional
  dependency, exec policy denial, segfault — was discarded.

  The user couldn't tell whether the issue was:
    - the model file itself
    - the llama-cpp version pinned in the venv
    - the GPU runtime
    - permissions
    - something else entirely

  Fix (`desktop/providers/llamacpp.py`):

  1. **New `log_dir` ctor kwarg** (defaults to
     `~/.open-notebook-plus/logs/`). Provider opens
     `llamacpp_chat_stderr.log` in append mode on every `start()` so the
     diagnostic file persists across launcher restarts.
  2. **stderr routed to that file**, replacing `subprocess.DEVNULL`.
     Append-mode means a crash → retry → crash sequence accumulates
     context rather than overwriting.
  3. **Both premature-exit AND timeout error paths** now include:
       - the model name (which GGUF failed)
       - the returncode (so a SIGSEGV / SIGKILL is distinguishable)
       - the last 30 stderr lines as a quoted block
       - the absolute path to the full logfile
     so a user can `tail -F` the file or grep it for the actual cause.
  4. **Graceful fallback**: if the logfile can't be opened (read-only
     filesystem, permission denied), fall back to `subprocess.DEVNULL`
     — same behavior as before — and the error message explicitly says
     "Stderr capture unavailable" so the user isn't confused about why
     the stderr block is missing.
  5. **Cleanup**: `stop()` and the error paths both call
     `_close_stderr()` (idempotent) to release the file handle.

  Tests (`desktop/tests/test_llamacpp_provider.py`): 4 new regression
  tests covering:
    - stderr captured + tail in error msg + logfile path referenced
    - stderr empty → explicit "Empty stderr" + path hint + likely-cause
    - unwritable log_dir → graceful fallback to DEVNULL + clear msg
    - ready-timeout path also includes in-flight stderr (mmap stall,
      slow GPU init, etc.)
  All 14 tests pass (10 pre-existing + 4 new).

  Next time the user hits a chat-LLM crash they'll see something like:

      RuntimeError: llama_cpp.server exited prematurely (returncode=1)
      while loading model 'Hermes-3-Llama-3.1-8B-Q4_K_M.gguf'. Last 30
      lines of stderr (full log at /Users/.../llamacpp_chat_stderr.log):
        llama_model_load: error loading model architecture: unknown
        unknown model architecture: 'hermes3'
        llama_load_model_from_file: failed to load model

  …which is enough to either swap to a supported quant, upgrade the
  llama-cpp-python pin, or pick a different default model.

- **v0.7.150** 🐛 **Fix Piper voice config re-download spam — `min_bytes`
  override for files where the MB-based threshold doesn't fit.** Carried
  from v0.7.145 "what this does NOT fix" list.

  Launcher.log on every launch (~21:18, 21:21, 21:28, 21:48, 21:52, ...):

      WARNING: Existing en_US-amy-medium.onnx.json is only 4882 bytes
        (expected >= 838860) — re-downloading
      WARNING: Existing en_US-ryan-high.onnx.json is only 4166 bytes
        (expected >= 838860) — re-downloading

  Cause: the Piper voice config JSON descriptors (≈5 KB) were declared
  with `expected_size_mb=1` in `desktop/model_downloads.py:46,61`. The
  `_download_one` partial-download protection scales that to a threshold
  of `1 MB × 0.80 = 838860 bytes`, which the real 5 KB files can't meet
  → flagged as corrupt → re-downloaded → roughly 1-2 seconds wasted on
  every launch, plus log-noise that obscured real problems.

  Why the MB-based heuristic broke: it was designed for large weight
  files (the embedding model is 273 MB, Piper voices are 30/78 MB) where
  20% lower-bound tolerance catches partial downloads cleanly. For tiny
  config files (5 KB) any MB-scaled threshold is unreachable.

  Fix (`desktop/model_downloads.py`):

  1. **New `min_bytes` kwarg on `_download_one`.** When supplied, it
     directly sets the floor — no scaling. Takes precedence over
     `expected_size_mb`. Threshold-selection order:
       (1) explicit `min_bytes` if > 0
       (2) `expected_size_mb × 0.80` if > 0
       (3) legacy 100_000-byte floor
  2. **`ensure_tts_model` + `ensure_secondary_tts_voice`** pass
     `min_bytes=_PIPER_CONFIG_MIN_BYTES` (=2048) for the `.onnx.json`
     downloads. 2 KB still filters out obvious HTML error pages
     (<1 KB typical) while admitting the real ~5 KB JSON.
  3. **No tuple-shape changes** — `PIPER_VOICE_CONFIG` and
     `PIPER_RYAN_CONFIG` remain 4-tuples (their `expected_size_mb=1`
     entry is now unused, kept for back-compat with existing
     unpacking sites; could be cleaned up in a future refactor).

  Tests (`desktop/tests/test_model_downloads.py`): 4 new — one each for
    - real ~5 KB file accepted via min_bytes
    - tiny HTML error page rejected
    - min_bytes takes precedence over expected_size_mb
    - end-to-end ensure_tts_model doesn't re-download a real config
  All 16 tests pass (12 pre-existing + 4 new).

  After rebuild + relaunch, the "Existing X is only N bytes" warnings
  disappear from launcher.log and the duplicate downloads cluster on
  each startup ends.

- **v0.7.149** 🐛 **Fix `auto_register/episode_profile.py` payload
  schema drift — all 9 preset POSTs were returning HTTP 422 every
  launch.** Carried over from v0.7.145 "what this does NOT fix" list.

  Diagnosis from launcher.log (running app, 2026-05-20 21:52):

      [desktop.auto_register.episode_profile] WARNING:
      Could not create episode profile 'Open Notebook Plus Local'
      (HTTP 422): {"detail":[{"type":"missing","loc":["body",
      "speaker_config"],"msg":"Field required",...}]}

  …repeated 9 times for every preset. The launcher was sending:

      {
        "chat_model_id": "<id>",                      ← not in schema
        "speakers": [{"name": ..., "tts_model_id":}], ← not in schema
        "default_length_minutes": 5,                  ← not in schema
        ...
      }

  But `api/routers/episode_profiles.py:EpisodeProfileCreate` actually
  requires:

      {
        "speaker_config": "<speaker_profile_name>",   ← REQUIRED, missing
        "outline_llm": "<model_id>",                  ← optional, chat
        "transcript_llm": "<model_id>",               ← optional, chat
        ...
      }

  None of the 9 v0.7.30 preset library entries (Deep Dive, Quick Brief,
  Debate, Tutorial, Story Mode, News Roundup, Q&A Interview, Recap &
  Review, plus the base "Open Notebook Plus Local") ever made it into
  the database. The launcher.log noise was the only user-visible sign;
  the actual symptom was the Studio episode-profile picker only
  showing the legacy cloud-only profiles from migration 7.surrealql.

  Fix (`desktop/auto_register/episode_profile.py`):

  1. **Per-preset speaker_profile mapping.** Added `speaker_profile`
     key to each of the 9 `_PRESETS` entries, naming the speaker
     profile that semantically fits:
       - Open Notebook Plus Local, Deep Dive, Quick Brief, Tutorial,
         Story Mode, News Roundup, Recap & Review → "Local Duo"
       - Debate → "Local Debate" (matches Pro/Skeptic personalities)
       - Q&A Interview → "Local Interview" (matches Interviewer/Expert)
  2. **Payload rewrite.** Replaced `chat_model_id` + `speakers: [...]`
     + `default_length_minutes` with `speaker_config: "<name>"` plus
     `outline_llm` + `transcript_llm` (the schema's actual model
     fields). The chat model is routed through both.
  3. **Speaker-profile presence cross-check.** New GET to
     `/api/speaker-profiles` before any POST. If no speaker profiles
     exist (piper voices missing → speaker bootstrap skipped), abort
     the episode bootstrap silently rather than 422 nine times.
  4. **Graceful degradation.** If the preferred speaker profile
     (e.g. "Local Debate") isn't registered but "Local Duo" is, the
     preset is registered against "Local Duo" instead of skipped.
     Logged with a `degraded` counter for observability.

  Tests (`desktop/tests/test_auto_register.py`):
  - 3 existing episode-profile tests updated to assert the new
    payload shape (`outline_llm` instead of `chat_model_id`, no
    `speakers` / no `chat_model_id` / no `default_length_minutes`,
    presence of `speaker_config` referencing a known profile).
  - Debate + Q&A semantic mapping verified (Debate → Local Debate,
    Q&A → Local Interview).
  - 2 new regression tests: `test_episode_profile_skips_when_no_
    speaker_profiles_exist` (zero POSTs when speaker bootstrap failed)
    and `test_episode_profile_falls_back_to_local_duo_when_preferred_
    missing` (graceful degradation).
  - 20/20 auto_register tests pass.

  After the next rebuild + launch, the launcher.log episode-profile
  block changes from:
      Episode profile preset library: 0 created, 0 skipped
  to:
      Episode profile preset library: 9 created, 0 skipped, 0 degraded
  and the Studio picker shows the full preset library.

- **v0.7.148** 🐛 **Fix Setup Wizard stuck on "Loading..." — alias
  `/api/healthz/deep` on the backend.** Follow-up to v0.7.147:
  with the EROFS bug fixed the .app now launches and the Setup
  Wizard renders, but the "Required subsystems are not ready /
  Loading…" spinner spins forever and the user can't get past it
  without clicking "Continue anyway".

  Diagnosis: api.log showed the smoking gun side-by-side —

      INFO: 127.0.0.1:54441 - "GET /readyz HTTP/1.1" 200 OK
      INFO: 127.0.0.1:0     - "GET /api/healthz/deep HTTP/1.1" 404

  `/readyz` (which the launcher polls directly) returns 200 fine.
  But the wizard's `useDeepHealth` hook reaches the backend as
  `/api/healthz/deep`, which the FastAPI app doesn't have a handler
  for — only the root-mounted `/healthz/deep`. `client.port=0` in
  the 404 log line confirms it's arriving via Next.js's `/api/*`
  rewrite proxy, not a direct browser connection.

  The frontend's `frontend/src/lib/api/health.ts` LOOKS correct —
  it overrides `baseURL: apiUrl` so the request should target
  `${apiUrl}/healthz/deep` directly, bypassing the `/api` interceptor
  prefix. But in the production build the resolution chain (Next.js
  proxy + runtime `getApiUrl()` + apiClient interceptor) ends up
  routing the request through `/api/*` anyway, and untangling that
  across the four layers is fragile.

  The right fix is at the backend: register an alias route that does
  the same thing. v0.7.145 used the same pattern for `/api/episode-
  profiles`. Two changes to `api/main.py`:

  1. **Line ~470 (auth-middleware excluded_paths)** — add
     `"/api/healthz/deep"` alongside the existing `"/healthz/deep"`
     entry so monitoring polls without auth headers work on both
     paths.
  2. **Line ~993 (new `healthz_deep_api_alias` handler)** — register
     `@app.get("/api/healthz/deep")` that simply delegates to the
     existing `healthz_deep()` coroutine, preserving the
     `?probe_providers=true` query parameter. Same response shape,
     same status codes, same auth-exempt treatment.

  This is the most defensive fix: existing operators / monitoring
  dashboards / curl recipes targeting `/healthz/deep` continue to
  work unchanged. The frontend doesn't need to change. The wizard
  immediately reads a 200 response on the next poll cycle and
  auto-advances to `/notebooks`.

  Regression tests at `tests/test_healthz_deep.py`:
  - `test_api_alias_returns_same_payload` — both paths return
    byte-identical JSON; alias is auth-exempt.
  - `test_api_alias_passes_probe_providers_query` — the
    `?probe_providers=` query arg is forwarded by the alias.
  - 6 pre-existing tests still pass (8/8 total).

  **Recovery for the user without rebuilding:** clicking "Continue
  anyway" on the wizard still works as a manual override. v0.7.148
  makes the auto-advance work, removing the click.

  Same pattern recommended for the still-open `/api/transcribe` 404
  (the STT toast) — separate commit since that endpoint genuinely
  doesn't exist yet and needs route definition, not just an alias.

- **v0.7.147** 🐛 **Fix the REAL "app won't open" root cause —
  `DATA_FOLDER = "./data"` was CWD-relative and crashed when launched
  from a read-only mount.** Post-v0.7.146 audit on 2026-05-20 found
  the user had been launching the `.app` directly from the mounted
  DMG (`/Volumes/Open Notebook Plus`, mounted read-only by macOS).
  The launcher itself ran fine — the codesign fix from v0.7.146
  was real but secondary. The actual failure chain:

      1. Launcher spawns API with cwd=upstream_root, which is INSIDE
         the read-only DMG mount.
      2. open_notebook/config.py:4 had `DATA_FOLDER = "./data"`.
      3. At module import, `os.makedirs("./data/sqlite-db")` raised
         `OSError: [Errno 30] Read-only file system: './data'`.
      4. uvicorn crashed before binding /readyz.
      5. Launcher waited 180s, raised
         `TimeoutError: http://127.0.0.1:52658/readyz never returned
         <500 within 180s`, exited.
      6. From Finder: silent — no window, no error dialog.

  The proof was in api.log (read-only filesystem error), not in
  launcher.log (which only saw the downstream timeout). Discovered by
  scanning `~/.open-notebook-plus/logs/` for tracebacks after seeing
  the user's launcher.log was actively being written to — meaning
  the launcher WAS running. The "app won't open" was actually "app
  starts, API self-destructs, launcher gives up waiting".

  Fix (two files, backward-compatible):

  1. **`open_notebook/config.py:4`** — `DATA_FOLDER` now reads from
     env (`os.environ.get("DATA_FOLDER", "").strip() or "./data"`).
     Existing Docker / dev workflows that rely on CWD-relative
     `./data` are unaffected (the env var defaults to empty).

  2. **`desktop/launcher.py:181`** — `session_env` now injects an
     absolute `DATA_FOLDER = ~/.open-notebook-plus/data/`. The dir is
     mkdir'd before population so the API's module-load makedirs
     always succeeds. This makes the launch path resilient to ANY
     read-only CWD: mounted DMG, Time Machine snapshot, /Applications
     under a non-admin account, network-mounted home dirs.

  **Why this wasn't caught earlier:** the CI runs unit tests against
  source where CWD is a writable repo checkout. The bug only surfaces
  in a frozen-bundle launch from a read-only mount — exactly the user-
  facing distribution path that no automated test covers. The new
  regression test
  `desktop/tests/test_launcher.py::test_supervisor_injects_data_folder_absolute_path`
  guards against the launcher dropping the DATA_FOLDER injection by
  asserting it's absolute, points under `.open-notebook-plus`, and
  exists on disk before children spawn.

  **Recovery for users without rebuilding:** install the `.app` to
  `/Applications/` first (writable), then launch. The CWD will be
  inside the writable bundle path and `./data` will resolve.
  v0.7.147 makes this resilient — install location no longer matters.

  Tests: 4 launcher tests pass (1 new regression + 3 pre-existing
  session_env / startup tests verified unaffected).

  **What this does NOT fix:** the five other bugs the audit
  surfaced — `auto_register/episode_profile.py` payload-schema
  drift (every preset registration returns HTTP 422), the
  `model_downloads.py` false-positive size threshold (Piper
  `.onnx.json` configs re-downloaded every launch), the
  `llama_cpp.server` Hermes-3 crash with stderr silently discarded,
  the cosmetic "0 MB" download display, and the pre-existing
  v0.7.145 items. Tracked for v0.7.148+.

- **v0.7.146** 🐛 **Fix silent-launch-failure of rebuilt `.app` (broken
  Gatekeeper seal + possibly-missing launcher modules).** User
  incident today (2026-05-20): after `make build-mac-clean && make
  build-mac` completed cleanly and produced a fresh `dist/Open
  Notebook Plus.app` + `.dmg`, double-clicking the `.app` did
  absolutely nothing. No window, no error dialog, no crash report
  in `~/Library/Logs/DiagnosticReports/`, no entries in
  `~/.open-notebook-plus/launcher.log` (file wasn't even created),
  no stderr when launched directly from the terminal.

  Diagnosis: `spctl -a -vvv "dist/Open Notebook Plus.app"` returned
  `a sealed resource is missing or invalid`. Gatekeeper was silently
  killing the binary before Python initialization. This is the
  classic symptom of a broken ad-hoc code signature seal: macOS
  blocks the process and emits no user-visible feedback.

  Root cause: macOS auto-applies an ad-hoc signature to arm64
  Mach-O binaries the first time they're written. PyInstaller's
  `BUNDLE` step in the spec doesn't explicitly sign the .app, so
  the macOS-auto-applied seal covers only the state of the bundle
  at one specific moment during the multi-phase build. Any file
  modification under the bundle after that point — including
  PyInstaller's own COLLECT/BUNDLE write sequencing, Spotlight
  indexing writing `com.apple.metadata:*` xattrs, or the build
  script's final touches — invalidates the seal.

  Fixes:

  1. **`Makefile:420-440` — `build-mac-pyinstaller` target now runs
     `codesign --force --deep --sign - "dist/Open Notebook Plus.app"`
     immediately after PyInstaller completes, then verifies the
     seal with `codesign -v` and `spctl -a -vvv`. `--deep` re-signs
     every nested Mach-O (the Python framework, all dylibs, helper
     binaries). `--force` overwrites any prior signature. `--sign -`
     is ad-hoc (no developer cert needed for local dev, matches
     prior behavior). The DMG step that runs after this only reads
     the .app (via `hdiutil create -srcfolder`), it doesn't modify
     it, so the seal stays valid through DMG packaging.

  2. **`desktop/build/pyinstaller.spec:45-58` — explicit hiddenimports
     for `desktop.singleton` and `desktop.next_rewrites_patcher`.**
     These two v0.7.142/v0.7.144 modules are imported function-locally
     in `desktop/launcher.py:148` and `:246` (inside the `start_all`
     method) via `from desktop.singleton import (acquire_singleton,
     ...)` tuple form. PyInstaller's modulegraph generally follows
     local imports, but the tuple-form `from X import (a, b, c)`
     pattern has historically been missed in some PyInstaller
     releases. Without these in the bundled PYZ, the launcher would
     raise `ModuleNotFoundError: No module named 'desktop.singleton'`
     at first launch — the same silent-exit-before-logging-setup
     symptom. Belt-and-suspenders explicit declaration.

  Recovery for users who already have a broken bundle (without
  rebuilding): `codesign --force --deep --sign - "dist/Open
  Notebook Plus.app"` re-seals the existing bundle. Combined with
  `xattr -cr "dist/Open Notebook Plus.app"` to clear any stale
  quarantine attributes, this gets the existing .app launching.

  Durable fix: future `make build-mac` runs produce a bundle whose
  seal is valid and verifies cleanly under Gatekeeper.

  **What this does NOT fix:**

    1. **Code-signing certificate.** This is still ad-hoc signing.
       Distributing the .app to other Macs requires a Developer ID
       certificate from Apple ($99/year) and notarization. Out of
       scope for local dev / personal use.

    2. **The pre-existing items from v0.7.145** (`/api/transcribe`
       404, 18 `chat_llm_n_ctx` test failures) remain untouched —
       this commit is scoped to the build/launch unblock.

  No code change to `launcher.py` itself; the imports were already
  correct. Pure build-pipeline patch.

- **v0.7.145** 🐛 **Fix launcher's `/api/episode_profiles` 404s
  (underscore vs hyphen mismatch).** User reported recurring 404s
  in their api.log every launch, with launcher.log line:

      [desktop.auto_register.episode_profile] WARNING:
      Could not list episode profiles: Client error '404 Not Found'
      for url 'http://127.0.0.1:53437/api/episode_profiles'

  Cause: `desktop/auto_register/episode_profile.py` calls
  `client.get("/api/episode_profiles")` (underscore) but the
  backend route is `@router.get("/episode-profiles")` (hyphen,
  registered under /api prefix → `/api/episode-profiles`).

  Fix: changed both call sites (lines 190, 245) to use the
  hyphenated path. Updated the matching test fixtures in
  `desktop/tests/test_auto_register.py` (6 occurrences) so the
  18 auto_register tests stay green.

  **What this does NOT fix (in this commit):**

    1. **`POST /api/transcribe` 404 (the "STT failed" toast)** —
       no file in the source tree calls this endpoint, and there
       IS no /api/transcribe route on the backend. Likely a chunk
       from a built minified bundle, or a feature whose
       implementation was removed. Need deeper investigation to
       find the call site. Out of scope; STT failure is a toast
       that the user can dismiss.

    2. **`GET /api/healthz/deep` 404 in api.log** — `health.ts`
       already overrides baseURL to skip the /api prefix (correct
       code). The 404s in api.log with `client.port=0` suggest
       the request comes from Next.js's rewrite proxy intercepting
       something else, not from the health.ts call directly.
       Needs further trace to identify; the actual /healthz/deep
       calls from the wizard work fine in practice.

  Backend tests unchanged. desktop/tests/test_auto_register.py:
  **18 passing** (was 18; fixture URLs updated alongside).

  **Pre-existing failure noted but NOT caused by this commit:**
  `desktop/tests/test_launcher.py::test_chat_llm_n_ctx_*` (18
  failures) — verified by stashing this change and re-running;
  failures persist. Separate bug, separate fix. Tracked.

- **v0.7.144** 🐛 **Real fix for "API config endpoint returned status

- **v0.7.144** 🐛 **Real fix for "API config endpoint returned status
  500" on launch.** User incident today (2026-05-20): bundled .app
  opened, showed the connection-error screen with:
  - Attempted URL: `http://127.0.0.1:53018/api/config`
  - Technical Details: `API config endpoint returned status 500`

  Diagnosis (~30 min of forensics):
  - The API was running fine on port 53017, responding 200 to
    `/api/config` with valid JSON
  - The Next.js standalone server on 53018 was returning 500 from
    the same `/api/config` request
  - Cause: Next.js `rewrites()` config is evaluated **at BUILD time**
    and the destination string baked into three manifest files:
    `server.js`, `.next/required-server-files.json`, and
    `.next/routes-manifest.json`. The build-time default in
    `frontend/next.config.ts:33` is `http://localhost:5055`. The
    launcher's runtime `INTERNAL_API_URL` env var (set to the
    dynamic api_port) is **ignored** by the standalone server —
    rewrites destinations are NOT re-evaluated from env at runtime.

  Every `/api/*` request the frontend made was proxied to
  `localhost:5055` (port not listening) → 500 from Next.js → user
  saw the connection-error screen even though the API was healthy.

  **Fix:** new `desktop/next_rewrites_patcher.py` module that runs
  before `Supervisor._spawn_next()`. It:

  1. Reads the dynamic `api_port` the launcher allocated
  2. Creates `.orig` backups of the three baked manifest files on
     first patch (idempotent — re-creates only if missing)
  3. Replaces `localhost:5055` with `localhost:<api_port>` in each
     file, reading FROM `.orig` (so re-patches across launches
     never compound previous edits)
  4. Returns the directory Next.js should be spawned from

  **Read-only-bundle handling:** if the bundle's frontend dir is
  read-only (`.app` installed under `/Applications` by another
  user), the patcher falls back to copying the frontend to
  `~/.open-notebook-plus/frontend-runtime/` and patching there.
  Includes a recursive `_make_writable_recursive` helper because
  `shutil.copytree` preserves source permissions — a read-only
  source produced an unpatchable copy. The launcher's
  `_spawn_next` accepts an optional `next_cwd` parameter to spawn
  from the patched-copy location.

  **Refuses to claim success silently:** if no manifest file
  contains the build-time-default string, the patcher raises
  `PatchError`. Catches the regression case where `next.config.ts`
  gets refactored (e.g., default changed) — the launcher would
  otherwise launch a Next.js that can't reach the API and the user
  would see the same 500 error with no signal that the patcher
  silently failed.

  **11 new hermetic tests** in `tests/test_v0_7_143_rewrites_patcher.py`:
  - Happy path: substitutes port in all three files
  - First-patch creates .orig backups
  - Repeated patches don't compound (always reads from .orig)
  - Default port short-circuits (no I/O when api_port=5055)
  - Error paths: refactored config (no localhost:5055 to find),
    partial bundles (only some target files present)
  - `restore_originals` round-trip works
  - Writability detection: writable dirs detected, read-only
    sources fall back to writable copy with proper chmod

  **Long-term architectural note (deferred):** the right
  cross-cutting fix is to have the frontend client always use the
  absolute URL from `/config` instead of relying on Next.js
  rewrites for `/api/*` proxying — that would skip rewrites
  entirely. Requires API CORS + frontend client refactor. Tracked
  as a follow-up. The patcher is the pragmatic unblock that works
  with the existing build artifacts.

  Backend suite: **802 passing** (was 774, +28).

- **v0.7.143** ✨ **Closes the v0.7.142 deferreds: AlreadyRunning UI
  dialog + cross-platform reaper.** Two scoped pieces that complete
  the zombie-process fix.

  **Part 1 — Native dialog for AlreadyRunning.** Before this release,
  the v0.7.142 `AlreadyRunning` exception just propagated to the
  generic catch in `_phase_start_supervisor` and the user saw a
  cryptic stack trace in the splash window. Now `desktop/app.py`
  catches it and shows a friendly two-button dialog:
    * **Quit & Relaunch** → SIGTERM the other launcher's PID, poll
      until it actually exits (10 s deadline), clean up its stale
      PID file, retry `start_all()` in-place. No restart needed.
    * **Cancel** → exit cleanly (atexit handlers still fire).

  Implementation uses Tkinter `messagebox.askyesno` (stdlib, bundled
  with the desktop Python). If Tk fails to initialize (rare —
  headless container, broken Tcl/Tk bundle), falls back to macOS
  `osascript` with a native AppKit alert. If BOTH fail, we log a
  warning and fall through to the original error path so the user
  at least sees the underlying message in the launcher log.

  Edge cases handled:
    * `os.kill` raises ProcessLookupError (other PID already dead) →
      treat as success, clean the stale lock, return True.
    * Other PID doesn't die within the 10 s poll window → return
      False (caller exits rather than risk a port-collision race).
    * Tk + osascript both unavailable → return False (don't auto-kill
      without explicit user consent).

  **Part 2 — Windows `reap_orphans`.** v0.7.142 was POSIX-only:
  `reap_orphans` used `ps -eo` which doesn't exist on Windows.
  Acceptable then because Windows bundles weren't shipping; coming
  back to it now to unblock that work.

  New `_list_processes_windows()` uses `wmic process get
  ProcessId,ParentProcessId,CommandLine /format:csv` which is
  available on every Windows version since 7. Falls back to
  `tasklist /v /fo csv` if wmic isn't on PATH (Windows Server Core,
  stripped containers); the fallback loses PPID information so the
  orphan-detection becomes "any process matching our bundle paths"
  rather than "only those whose parent is dead" — slightly more
  aggressive but still constrained.

  `_parse_wmic_csv()` handles wmic's quirks: BOM prefix, blank
  lines between records, alphabetical column order (CommandLine,
  Node, ParentProcessId, ProcessId rather than the order we
  requested). Header-row indexing makes the parser robust against
  future Windows changes to that order.

  `_kill_orphan()` dispatches to `signal.SIGTERM` on POSIX or
  `taskkill /pid X /t` on Windows. The `/t` flag is critical — it
  kills the whole tree under the orphan, not just the orphan itself
  (Windows doesn't have process groups the way POSIX does).

  **17 hermetic tests** in `tests/test_v0_7_143.py`:
    * 5 `_handle_already_running` branches (cancel via Tk, accept +
      dies, accept + won't die, accept + already dead, no dialog
      primitive available)
    * 5 `_parse_wmic_csv` cases (typical output, empty, missing
      header columns, malformed row, blank cmdline)
    * 2 `reap_orphans` dispatch tests (POSIX uses ps, Windows uses
      wmic) — these run on ANY platform via mock since they only
      verify which lister got called
    * 4 `_kill_orphan` cases (POSIX signal path, Windows taskkill
      path, both error-tolerant)
    * 1 regression test for v0.7.142's self/parent-protection

  Backend suite: **791 passing** (was 774, +17).

  **What's still NOT done (final deferred item)**:
    - **Inter-launcher coordination** ("send 'open new tab' to running
      instance" instead of just refusing) — not needed for any
      current feature; can be built on top of the singleton when
      a use case appears.

- **v0.7.142** 🛡 **Launcher singleton + orphan reaper — no more

- **v0.7.142** 🛡 **Launcher singleton + orphan reaper — no more
  zombie process accumulation.** Direct fix for the bug the user
  hit today: they double-clicked `Open Notebook Plus.app` ~5 times
  during debugging, each click started a fresh launcher with new
  dynamic ports, and closing the Chromium window didn't kill the
  launcher tree behind it. After several cycles they had:

    - 4 zombie Next.js processes (PIDs 35217, 37678, 85061, 94829)
    - 3 zombie surreal-commands workers from May 11
    - 1 live + several zombie API processes
    - Browser window attached to a zombie whose API had been overwritten

  Symptom: "Unable to Connect to API Server" pointing at a dead port.
  Root cause: no singleton enforcement + no process-tree cleanup on
  launcher crash.

  **`desktop/singleton.py` (NEW)** — two primitives covering the
  failure modes:

    1. `acquire_singleton(pid_file)` writes a PID file at boot using
       `O_CREAT | O_EXCL` (race-safe). If another live launcher
       already holds the lock, raises `AlreadyRunning(pid, pid_file)`
       with both fields exposed for UI affordances ("Open Notebook
       Plus is already running — kill it? [y/N]"). Stale PID files
       (parent process is dead) and unparseable PID files (corrupted)
       are silently cleaned and acquisition proceeds.
    2. `reap_orphans(bundle_paths)` scans `ps -eo pid,ppid,command`
       for processes whose executable path matches the bundled venv
       or binary dir AND whose parent is dead. SIGTERMs each one.
       This is the SIGKILL/segfault recovery path — atexit covered
       graceful shutdown, this covers everything else.

  Returns a `SingletonHandle` that:
    - releases on explicit `.release()` (called by `stop_all()`)
    - releases on `atexit` (called by normal `sys.exit()`)
    - releases on SIGTERM / SIGINT (signal handlers installed at
      acquire time — Force Quit + Ctrl+C both reap)
    - does NOT release a PID file owned by a different (newer) PID,
      so the late atexit of a crashed launcher can't erase the
      replacement launcher's lock

  **Wired into `desktop/launcher.py` `Supervisor.start_all()`**:
    - Acquires singleton at start (raises `AlreadyRunning` for UI
      to handle)
    - Calls `reap_orphans` BEFORE `find_free_ports()` so we don't
      race against zombies clinging to ports we want to bind
    - `Supervisor.stop_all()` releases the singleton FIRST (idempotent
      and safe even if start_all never ran) so a relaunch isn't
      blocked while we're still tearing down

  **27 hermetic tests** in `tests/test_v0_7_142_singleton.py`:
    - 4 `_is_pid_alive` matrix (self, init/PID-1, negative, very-large)
    - 5 `_read_pid_file` parser cases (missing, valid, garbage,
      negative-or-zero, whitespace tolerance)
    - 5 `acquire_singleton` happy + race paths (writes our PID,
      creates parent dirs, rejects on live lock, cleans stale,
      handles garbage file)
    - 3 `SingletonHandle.release` semantics (removes file, idempotent,
      doesn't clobber another instance's lock)
    - 1 `default_pid_file` canonical location
    - 2 `AlreadyRunning` exception shape (message + machine-readable
      `.pid`)
    - 3 `reap_orphans` safety properties (dry-run is no-op, never
      kills self or parent, empty-match returns empty)
    - 4 end-to-end (acquire→release round-trip, sequential acquires
      work, callback runs after lock held, callback failure releases
      lock)

  Backend suite: **774 passing** (was 747, +27).

  **What's still NOT fixed (deferred)**:
    - **UI for the AlreadyRunning case** — currently the exception
      propagates to whatever caller wraps `start_all()`. A polished
      desktop bundle would catch it and show a "Quit existing app?
      [Quit and Relaunch] [Cancel]" dialog. Out of scope for the
      backend fix; needs corresponding UI work in `desktop/app.py`.
    - **Cross-platform reaper** — `reap_orphans` uses `ps -eo`
      which is POSIX-only. Windows bundles get the singleton check
      (works via os.kill(0)) but no orphan sweep. Acceptable for
      now since Windows bundles aren't shipping yet.

- **v0.7.141** 🐛 **The real bundle-bootstrap fix: stale lockfile +

- **v0.7.141** 🐛 **The real bundle-bootstrap fix: stale lockfile +
  defensive depcheck.** A user hit this today: rebuilt the macOS
  bundle, double-clicked `Open Notebook Plus.app`, watched it silently
  quit after 3 minutes. Root cause traced through three layers:

  **What looked like the bug:** bundled Python venv at
  `~/.open-notebook-plus/venv/` was missing `prometheus_client`,
  so the API crashed at import time on `api/metrics.py:27`. Launcher
  waited 180 s for `/readyz` that never came up, then gave up.

  **What I diagnosed first (and was wrong about):** "bootstrap reuses
  stale venvs across upgrades". Suggested user manually
  `rm -rf ~/.open-notebook-plus/venv` and relaunch.

  **The actual root cause (found while writing the "real fix"):**
  `desktop/requirements.lock` itself is stale. It was last manually
  regenerated in commit 90fbf8e — long before v0.7.124 added
  `prometheus-client>=0.20.0` to `pyproject.toml`. The bundle builds
  `dist/Open Notebook Plus.app` shipping the OLD lockfile. The
  bootstrap (correctly!) hashes that lockfile, sees the venv's marker
  matches, and reports "Environment is up to date". The hash math is
  right; the input is wrong.

  **Three-part fix:**

  1. **New `build-mac-lock` Makefile target** runs `uv pip compile
     pyproject.toml --python-version 3.12 -o desktop/requirements.lock`
     to regenerate the lockfile from the current dependency manifest.
     Promoted to a precondition of `build-mac` (added between
     `build-mac-test` and `build-mac-venv` in the prereq chain), so
     every full bundle build refreshes the lock automatically.

  2. **`bootstrap.py` post-install depcheck.** After `uv pip install`
     finishes against the lockfile, we now run each critical import
     (`prometheus_client`, `surrealdb`, `fastapi`, `langgraph`,
     `loguru`, `pydantic`) as a subprocess in the freshly-built
     venv. If any fails, raise immediately with a clear actionable
     message including the recovery commands. Belt-and-suspenders
     for the case where (a) a future regression slips through the
     Makefile fix or (b) someone hand-edits a lockfile and drops
     a critical package — we fail at install time with a 1-line
     cause instead of waiting 3 minutes for the API to crash.

  3. **Improved "Environment is up to date" log message.** Was
     misleading — it didn't tell the user how to force-rebuild when
     things went wrong. Now reads `Environment is up to date (delete
     /path/to/venv to force reinstall if the app fails to start)` so
     a user hitting an edge case has a recovery path without needing
     to read source code.

  **Lockfile regenerated in this commit:** 656 packages, now
  including the previously-missing prometheus-client==0.25.0.

  **9 new hermetic tests** in `tests/test_v0_7_141_bootstrap.py`:
    * 2 Makefile structure: `build-mac-lock` exists + invokes
      `uv pip compile`
    * 1 dependency ordering: `build-mac-lock` runs BEFORE
      `build-mac-venv` in the `build-mac` prereq chain (critical —
      reversed order would defeat the whole fix)
    * 3 `_verify_critical_imports` cases: all-present, missing-modules,
      bogus-python-binary
    * 2 critical-import-list content: must include `prometheus_client`
      (regression-test for the actual bug); must include all 6
      load-bearing api.main imports
    * 1 cross-cutting: `desktop/requirements.lock` actually contains
      every direct dep listed in `pyproject.toml`. This caught the
      stale lockfile in this very commit — the test failed first
      run, I ran `make build-mac-lock`, it passed.

  Backend suite: **747 passing** (was 738, +9).

- **v0.7.140** 🐛 **Makefile fixes — `make start-all` now actually

- **v0.7.140** 🐛 **Makefile fixes — `make start-all` now actually
  works on a fresh clone.** User hit three real bugs the moment
  they ran `make start-all` for the first time, all caught + fixed:

  **Bug #1 — Three targets named compose files that don't exist.**
  `make dev`, `make full`, and `make start-all` all referenced
  `docker-compose.dev.yml` (and one referenced `docker-compose.full.yml`)
  but only `docker-compose.yml` ships in the repo. Running any of
  them produced:
  ```
  open docker-compose.dev.yml: no such file or directory
  make: *** [start-all] Error 1
  ```
  Fix: pointed all three at `docker-compose.yml`. Until/unless the
  dev/full variants are reintroduced, they're aliases.

  **Bug #2 — `start-all` invoked the API without `--env-file .env`.**
  Line was `uv run run_api.py &` while the worker line right below it
  correctly used `uv run --env-file .env surreal-commands-worker ...`.
  Without the flag the API booted, but didn't see
  `OPEN_NOTEBOOK_ENCRYPTION_KEY` / `SURREAL_PASSWORD` from `.env` —
  credential decryption silently failed on the first auth call. The
  worker line was correct; the API line was missed. Fixed both to
  match.

  **Bug #3 — Hardcoded `sleep 3` between SurrealDB start and API
  start.** Cold-start with a fresh volume can exceed 3 s on slower
  disks. Replaced with a polling loop that hits `/health` up to
  30 times at 1-second intervals, then bails with the last 20 lines
  of `docker compose logs surrealdb` so the operator sees what
  failed rather than a downstream API migration error.

  **4 new hermetic Makefile-hygiene tests** in
  `tests/test_v0_7_140_makefile.py`:
    * `test_every_compose_file_referenced_actually_exists` — regex
      scan; every `-f docker-compose*.yml` flag must name a file that
      exists. Catches the dev.yml/full.yml ghost-reference class.
    * `test_start_all_passes_env_file_to_api` — walks the `start-all`
      block, asserts every `uv run` invocation of `run_api.py` and
      `surreal-commands-worker` includes `--env-file`.
    * `test_makefile_parses_clean` — `make -n status` exit-code check
      (catches parse errors a regex test would miss).
    * `test_dev_and_full_targets_use_existing_compose_file` — pins
      the dev/full naming.

  Backend suite: **738 passing** (was 734, +4).

- **v0.7.139** 🧪 **Live model benchmarking harness + model-resolution

- **v0.7.139** 🧪 **Live model benchmarking harness + model-resolution
  audit.** User asked "test all models, determine which works best,
  fix issues along the way". Running 26 models end-to-end takes
  ~8-12 hours of wall-clock so doing it inline in a session isn't
  feasible — instead delivering a reusable harness operators can run
  themselves whenever they want to bake off models, PLUS fixing two
  real model-resolution bugs found during the audit.

  **`scripts/benchmark_models.py` — live benchmark harness.**
  Exercises every configured language Model record against three
  probes:
    * **NOTEBOOK_CHAT** — source-grounded Q&A (basic chat path)
    * **STUDIO_OUTLINE** — structured JSON outline (the v0.7.89
      Studio multi-page path; lenient JSON extraction handles
      code-fenced and preamble-wrapped responses)
    * **PODCAST_TRANSCRIPT_TURN** — multi-speaker dialogue with
      ALICE/BOB labels (case-insensitive detection)
  Outputs `benchmark-report.md` with a ranked composite score
  (0.7 × pass rate + 0.3 × latency-inverse), per-probe breakdown,
  and failure-mode summary. Use `ONP_BENCHMARK_ONLY` env var or
  `--only "<model name>"` to re-roll a single model.

  **`make benchmark-models` Makefile target** — operator-facing
  one-liner. Requires `make database` + `make api` + `make worker`
  running first.

  **`ModelManager.get_model` exception-discrimination audit fix.**
  Found during this work: line 206 (pre-v0.7.139) had `except
  Exception: raise ConfigurationError("Model with ID X not found")`
  — this conflated three completely distinct situations:
    1. Model record genuinely doesn't exist in DB
    2. `NotFoundError` (typed) raised by `Model.get`
    3. DB pool timeout / connection refused / generic operational
       failure
  All three produced the same misleading "not found" message.
  Users would re-create perfectly valid models that were just
  transiently unreachable. Now:
    * Case 1 + 2 → `ConfigurationError` with actionable message
    * Case 3 → `OpenNotebookError` (HTTP 500) with retry hint
  Also catches the previously-silent case where `Model.get` returns
  None instead of raising NotFoundError. Plus the invalid-type
  branch now names the model + points to Settings → Models.

  Exception-order subtlety the test caught: `NotFoundError` extends
  `OpenNotebookError`, so the broad isinstance check matched FIRST
  and the specific NotFoundError → ConfigurationError remapping
  never ran. Fixed by reversing the check order. Documented inline.

  **20 new hermetic tests** in `tests/test_v0_7_139.py`:
    * 4 composite-score math tests (all-pass-fast, all-fail-slow,
      pass-dominates-latency, partial-pass-ranking)
    * 4 JSON extraction tests (clean, code-fenced, garbage, wrong-shape)
    * 3 podcast speaker detection tests (both-speakers, one-speaker,
      case-insensitive)
    * 2 report rendering tests (zero models, three models)
    * 5 get_model error-discrimination tests (None return,
      NotFoundError, unexpected exception, typed passthrough,
      invalid type field)
    * 2 packaging tests (script is executable, Makefile target exists)

  Backend suite: **734 passing** (was 714, +20).

  **NOT in this release (deliberately):** actual benchmark RUN
  results. Running 26 models would take 8-12 hours wall-clock and
  burn significant local-GPU + cloud-API quota. The harness exists
  so the operator can run it once, then keep `benchmark-report.md`
  as a reproducible baseline they can re-run after upgrades.

  **How to use:**
  ```bash
  # Ensure services are up
  make database
  make api          # in another terminal
  make worker       # in another terminal

  # Then:
  make benchmark-models
  open benchmark-report.md
  ```

- **v0.7.138** 🛡 **Final-sweep audit — every model-using path now

- **v0.7.138** 🛡 **Final-sweep audit — every model-using path now
  bounded.** End-to-end walk of `/chat/stream`, `/search/ask`,
  notebook editing (transformations + insights), and podcast
  generation. Found three places where a hung LLM provider could
  pin a worker or stream indefinitely. Fixed all three.

  **Finding #1 — Ask graph nodes had no per-node `asyncio.wait_for`.**
  The `open_notebook/graphs/ask.py` strategy / provide_answer /
  write_final_answer nodes each called `model.ainvoke()` without a
  timeout. The `/search/ask` outer SSE handler had `is_disconnected()`
  checks but no total-time wall — if the strategy node hung, the
  client could disconnect but the LLM call kept grinding tokens on
  the server.

  **Fix:** new `_ask_invoke(model, payload, *, node=...)` helper wraps
  every `model.ainvoke()` with `ONP_ASK_NODE_TIMEOUT_SEC` (default
  120s). Timeout raises `ExternalServiceError` (mapped to HTTP 502
  by the global exception handler) with a message naming the
  failing node + the actual timeout value, so users see actionable
  info rather than a generic 500.

  **Finding #2 — `run_transformation_command` worker had no timeout.**
  The HTTP-side `/transformations/execute` got a 180s bound back in
  v0.7.95, but the worker path (used when sources have transformations
  attached at creation time) called `transform_graph.ainvoke()`
  unbounded. A hung chat model meant a worker slot pinned until the
  surreal_commands retry timeout fired out-of-band.

  **Fix:** worker now uses the same `ONP_TRANSFORMATION_TIMEOUT_SEC`
  env var (default 180s) via `asyncio.wait_for()`. Timeout re-raises
  as `RuntimeError` (NOT `ValueError`) so the @command retry kicks
  in — a transient hang on one attempt shouldn't mark the whole
  transformation as permanently failed.

  **Finding #3 — `generate_podcast_command` had no timeout on
  `create_podcast()`.** The @command config has `max_attempts: 1`
  (intentional — duplicate episodes are worse than a failed one),
  so a hung TTS / LLM call had nothing to bail it out. A worker
  slot lost forever until process restart.

  **Fix:** new `ONP_PODCAST_GENERATION_TIMEOUT_SEC` env var (default
  1800s = 30 minutes — generous because real generation legitimately
  takes 5-30 min) wraps the `create_podcast()` call. Timeout cleans
  up the empty output directory (so disk fill doesn't accumulate)
  and re-raises as `RuntimeError` with an actionable message.

  **New env vars:**
  - `ONP_ASK_NODE_TIMEOUT_SEC` (default 120)
  - `ONP_PODCAST_GENERATION_TIMEOUT_SEC` (default 1800)
  - (`ONP_TRANSFORMATION_TIMEOUT_SEC` already existed from v0.7.95;
    just now applies to the worker path too)

  **Tests:** 14 new in `tests/test_v0_7_138.py`:
  - 6 ask-node timeout cases (default, env override, garbage env,
    zero/negative env, hung-invoke raises ExternalServiceError,
    fast-invoke passes through)
  - 2 worker transformation timeout cases (hung → RuntimeError for
    retry, fast → success)
  - 1 podcast generation timeout sanity test
  - 5 cross-cutting meta-tests confirming each of the 5 model-using
    files (chat router, transformation router, ask graph, source
    worker, podcast worker) has either an outer-wrap or per-call
    timeout. A future refactor that drops a timeout breaks the
    meta-test, surfacing the regression immediately.

  Backend suite: **714 passing** (was 700, +14).

- **v0.7.137** ✨ **Bulk vectorize endpoint gets real pagination (Area

- **v0.7.137** ✨ **Bulk vectorize endpoint gets real pagination (Area
  for Review #8).** Before this release, `POST /notebooks/{id}/
  vectorize_sources` silently truncated to the first
  `ONP_BULK_VECTORIZE_MAX_SOURCES` sources (default 500) and emitted
  a warning. The caller had no way to reach sources beyond #500
  without either raising the env var globally OR calling the endpoint
  multiple times against the same first-500 slice (re-processing,
  not paginating).

  Now the endpoint accepts `?offset=` and `?limit=` query params,
  matching the v0.7.130 podcasts pagination pattern:

      POST /api/notebooks/{id}/vectorize_sources?offset=0&limit=500
      POST /api/notebooks/{id}/vectorize_sources?offset=500&limit=500
      POST /api/notebooks/{id}/vectorize_sources?offset=1000&limit=500
      ...

  Defaults preserve pre-v0.7.137 behavior (`offset=0, limit=500`),
  so existing callers without query params get the first 500. The
  response model gains three fields:

  - `total_sources` — full notebook source count (previously was
    confusingly populated with the SLICE count post-truncation)
  - `offset` / `limit` — what was actually used (after env-cap
    clamping)
  - `has_more` — boolean so the caller doesn't have to compute
    `offset + len(slice) < total_sources` themselves

  Plus three response headers matching the v0.7.130 podcasts
  convention: `X-Total-Count`, `X-Offset`, `X-Limit`.

  **Framework-level Query validation** rejects:
  - `offset < 0` (422)
  - `limit < 1` (422)
  - `limit > 2000` (422 — sanity ceiling so a misconfigured caller
    can't request a million-source page)

  **`ONP_BULK_VECTORIZE_MAX_SOURCES` still acts as a per-call hard
  ceiling.** If a caller passes `limit=1500` but the env var is set
  to 500, `effective_limit` clamps to 500 and a warning surfaces
  the conflict ("Requested limit 1500 exceeds the per-call cap
  (500); clamped. Raise ONP_BULK_VECTORIZE_MAX_SOURCES if you need
  bigger batches, or use pagination."). The env var is the
  operator's safety lever; the framework cap (2000) is the
  not-even-with-misconfig sanity ceiling.

  **9 new hermetic tests** in `tests/test_v0_7_137.py` covering:
  defaults preserve behavior, large notebook pages correctly,
  offset slicing math, offset beyond total returns empty,
  limit-over-env-cap clamps with warning, env cap can be raised,
  query validation (negative offset / zero limit / huge limit).
  Plus one existing v0.7.110 test updated to reflect the new
  warning wording + assert `has_more` is now part of the contract.

  **Backend suite: 700 passing** (was 691, +9 net).

  Frontend wiring: out of scope for this release. The UI doesn't
  currently expose this endpoint as a "process all sources" button,
  so there's nothing to update. When that button lands, it should
  loop on `has_more` rather than calling once and ignoring the rest.

- **v0.7.136** ✨ **Frontend wires `/settings/observability` into UI.**

- **v0.7.136** ✨ **Frontend wires `/settings/observability` into UI.**
  The backend endpoint shipped in v0.7.130 but had no UI consumer —
  operators could `curl` it but the Settings page didn't show the
  effective ONP_* config. v0.7.136 closes the loop:

  **New API client method** (`frontend/src/lib/api/settings.ts`)
    `settingsApi.getObservability()` — typed wrapper over GET
    `/settings/observability`.

  **New TypeScript type** (`frontend/src/lib/types/api.ts`)
    `ObservabilityResponse` mirrors the backend `ObservabilityResponse`
    Pydantic model field-for-field.

  **New React Query hook** (`frontend/src/lib/hooks/use-settings.ts`)
    `useObservabilitySettings()` with `refetchOnWindowFocus: true`
    so a tab-switch after `.env` edit shows the new values without a
    manual refresh. Separate `QUERY_KEYS.observabilitySettings` so it
    doesn't get invalidated by writable-settings mutations.

  **New ObservabilityCard component**
  (`frontend/src/app/(dashboard)/settings/components/ObservabilityCard.tsx`)
    Read-only card displaying every ONP_* env-derived value with
    field-by-field description. `db_pool_disabled=true` gets a
    red warning badge (debugging-only config left on in
    production = footgun). Card footer links to the operator
    handbook at `docs/operator/observability.md` (v0.7.134).

  **Settings page** now renders both surfaces: the existing
  writable `SettingsForm` (mutable, persisted to SurrealDB) AND
  the new read-only `ObservabilityCard` (env-derived). The two
  are intentionally separated in the UI because they have
  different change paths.

  **i18n keys** added under `settings.observability.*` in all 10
  locale files (`bn-IN`, `en-US`, `es-ES`, `fr-FR`, `it-IT`,
  `ja-JP`, `pt-BR`, `ru-RU`, `zh-CN`, `zh-TW`). Non-English
  locales use English placeholders — the locale-parity test
  passes because key sets match; native-speaker translations
  can drop in any time.

  Frontend suite: 65 passing (incl. locale parity test catching all
  10 locales with matching keys). Backend suite unchanged at 691.
  Production build: 16 routes, clean compile, no new warnings.

- **v0.7.135** 🛡 **AST meta-test enforces HTTPException re-raise

- **v0.7.135** 🛡 **AST meta-test enforces HTTPException re-raise
  convention (Area for Review #3).** Multiple v0.7.x commits have
  retroactively added `except HTTPException: raise` clauses (search
  the changelog for "v0.7.108 — re-raise typed HTTPExceptions"
  and similar). The convention exists because the natural-feeling
  pattern

      try:
          await fetch(id)          # raises HTTPException(404) on miss
      except Exception as e:
          raise HTTPException(500, detail=str(e))

  silently clobbers the typed 404 to a generic 500 — users see
  "Internal Server Error" instead of "Not Found", and the actual
  status disappears from the log line.

  v0.7.135 mechanically enforces the convention via a new pytest
  parametrized meta-test (`tests/test_v0_7_135_meta.py`). The
  test walks the AST of every `api/routers/*.py` file, finds any
  try/except chain whose generic `except Exception` clause re-raises
  as `HTTPException`, and fails the test for that file if no
  `except HTTPException: raise` clause precedes it. Operators can
  whitelist a specific intentional case with `# noqa: HTTP_RAISE`
  on the `except` line.

  Running the test against the existing codebase caught **18 real
  bug patterns** across 6 routers (commands.py × 2, embedding_rebuild.py,
  episode_profiles.py × 2, models.py × 9, settings.py, speaker_profiles.py × 2).
  All 18 fixed in the same commit — each gets a `v0.7.135` inline
  comment explaining the enforcement.

  The fix is two lines per handler (`except HTTPException:\n    raise`),
  applied in front of the generic `except Exception` clause. Behavior
  change: HTTP routes that previously returned 500 on every kind of
  error now return the correct typed status (404, 422, etc.) when an
  underlying call already raised an HTTPException.

  Tests: 4 walker self-tests (synthetic-violation detection,
  correct-pattern acceptance, noqa whitelist, non-handler ignore)
  + 26 parametrized real-router checks. **691 backend tests passing**
  (was 661, +30).

- **v0.7.134** 🛡 **Low-risk trio: pool warmup retry + operator

- **v0.7.134** 🛡 **Low-risk trio: pool warmup retry + operator
  observability handbook.** Three more Areas for Review closed.

  **Area #6 — Pool warmup retry-with-backoff.** The v0.7.44 warmup
  attempt grabs `warmup_n` connections at startup so the first chat
  doesn't pay the cold-handshake cost. v0.7.52 added a 10s per-acquire
  timeout. But: a single transient failure (network blip during
  startup, SurrealDB still settling) used to break the entire warmup
  loop — the first chat then paid the cold-handshake cost anyway,
  exactly what warmup was supposed to prevent.

  New `_warmup_pool_acquire_with_retry()` helper retries each
  individual acquire up to 3 times with exponential backoff
  (`_WARMUP_RETRY_DELAYS_S = (0.5, 1.0, 2.0)`s). The outer loop's
  two `except` clauses are preserved so timeout-after-all-retries
  still distinguishes from generic-failure-after-all-retries in the
  log line. Worst-case warmup wait per slot: 3 × 10s + 0.5 + 1.0 =
  ~31.5s, vs. ~5min cumulative chat-hot-path penalty if warmup
  silently skipped before. 5 unit tests pin the happy / retry-then-
  succeed / all-fail / timeout-after-retries / backoff-delay paths.

  **Area #22 — `@next/bundle-analyzer` tree-shaking verified
  (docs).** The analyzer was added in v0.7.127 to identify
  client-bundle lazy-load opportunities. Concern: does the analyzer
  ship to production bundles? Answer: no — the `enabled: process.env.ANALYZE === "true"`
  flag in `next.config.ts` makes `withBundleAnalyzer` a passthrough
  when ANALYZE is unset (production default), and the package is in
  `devDependencies` only. New docs page documents the verification
  procedure and explains why a CI-side test for this would be brittle
  (Next.js internals change between versions, minifiers rename
  symbols). One-time manual verification is the right shape.

  **Area #27 — Memory recall expected baselines (docs).** The
  `memory_recall_seconds` histogram has existed since v0.7.124 but
  operators had no "this is what normal looks like" reference. New
  docs page publishes measured p50/p99 baselines on a healthy
  single-user macOS desktop install:
  - `aembed(query)` p50 80ms / p99 180ms
  - `vector_search` × 2 p50 25ms / p99 95ms
  - Full `recall_memory()` p50 130ms / p99 320ms
  Plus a "what does this counter mean and what should I do" table
  for each `memory_recall_fallthrough_total{reason}` label. Operators
  now have concrete numbers to alert against and a remediation step
  for each.

  **New file**: `docs/operator/observability.md` — the single
  operator-facing observability handbook. Pulls together every
  `/metrics` series, the `/healthz/deep` flow, the `/settings/observability`
  endpoint, expected baselines, and a quick-reference "when each
  signal fires" table.

  Backend suite: **661 passing** (was 656, +5).

- **v0.7.133** ✨ **Four invasive deferred items, fresh-scoped batch.**

- **v0.7.133** ✨ **Four invasive deferred items, fresh-scoped batch.**
  Closes the last cluster of Areas for Review that were too risky to
  wedge into the multi-area v0.7.130–v0.7.132 batches. Each touches
  race-condition or library-boundary territory; all four landed
  together because they share test infrastructure.

  **Area #16 — Note.save() registry introspection.** The v0.7.129
  fix wrapped `submit_command` in `except ValueError: if "Command
  not found" in str(e)`. String-matching against an exception message
  is brittle: surreal_commands could rename the message in any
  minor release. The library does expose a typed `registry`
  attribute with `get_command_by_id()`, so the cleaner solution is
  a pre-check: ask "is this command registered?" before submitting,
  return None (with a warning) if not. New helper
  `_is_command_registered()` lazy-imports the registry and
  fail-closes on any AttributeError — defensive in case the
  registry API shape changes upstream. A narrow `except ValueError:
  raise` is preserved AFTER submit_command for any non-registry
  ValueError that still leaks through (the pre-check filters the
  known case, so a real ValueError at submit time is a real bug
  and should surface).

  **Area #2 — Memory-recall outer budget.** Existing per-step
  timeouts (`ONP_MEMORY_RECALL_EMBED_TIMEOUT_SEC`, 5s default,
  `ONP_MEMORY_RECALL_QUERY_TIMEOUT_SEC`, 5s default) could stack:
  embed (5s) + facts query (5s) + preferences query (5s) +
  fall-through recency facts (5s) + fall-through recency
  preferences (5s) = 25s worst-case before chat saw an empty
  memory section. Added `ONP_MEMORY_RECALL_BUDGET_SEC` (default
  12s) as a hard outer wall: `asyncio.wait_for()` around the
  whole `recall_memory()` orchestration. Per-step timeouts stay
  as defense in depth — useful when the embedder is hung but
  mem0 itself is fine (fast fall-through). New
  `memory_recall_fallthrough_total{reason="outer_budget"}`
  metric label tracks when the outer wall fires so operators can
  tell whether their budget needs raising.

  **Area #11 — Source.delete() race-window post-sweep.** When a
  source is deleted mid-embed, `Source.delete()` cancels the
  worker command via `svc.update_command_result(status="canceled")`.
  But surreal_commands has no cancellation-token mechanism — the
  cancel just writes a row to the tracking table; the worker may
  not check status before its next write. Between our cancel and
  our pre-sweep `DELETE source_embedding WHERE source = $id`, the
  worker could insert a fresh embedding row. v0.7.133 adds a
  second sweep AFTER `super().delete()`: same DELETE statements,
  matched by source_id (still works after the source row is gone
  since SurrealDB doesn't enforce the FK). Cheap (~2 round-trips),
  idempotent, and narrows the orphan-row window from "until next
  housekeeping" to "the few ms between worker write and our
  post-sweep query". Best-effort try/except so a sweep failure
  doesn't break the user's delete.

  **Area #4 — Notebook.delete() bulk-SQL above threshold.** Even
  after the v0.7.107 parallelization (asyncio.gather over
  per-note deletes), notebook deletion is N concurrent DELETEs
  hitting a pool of size 4 — they serialize into ~N/4 batches. For
  a 100-note notebook that's ~25 round-trip batches. New
  `_bulk_delete_notes()` method does 3 statements total (DELETE
  artifact + DELETE note_embedding + DELETE note WHERE IN
  $note_ids) regardless of N. Threshold tunable via
  `ONP_NOTEBOOK_DELETE_BULK_THRESHOLD` (default 25). Below the
  threshold the per-note flow is retained for observability
  ("note X failed to delete" log lines); above, bulk wins.

  **New env vars:**
  - `ONP_MEMORY_RECALL_BUDGET_SEC` (default 12.0)
  - `ONP_NOTEBOOK_DELETE_BULK_THRESHOLD` (default 25)

  **Tests:** 19 new in `tests/test_v0_7_133.py` covering:
  - `_is_command_registered()` registered/unregistered/import-failure cases
  - Note.save() registry pre-check controls submit / propagates ValueError
  - Memory-recall budget default / env override / garbage tolerance /
    within-budget / over-budget cases
  - Source.delete() pre-sweep + post-sweep ordering + post-sweep
    failure tolerance
  - Notebook bulk-delete threshold / 3-statement bulk path / failure
    handling / empty-list edge case

  Three v0.7.129 tests updated to match the new code structure
  (they pinned the OLD string-match flow; now they pin the
  registry-introspection flow with the same behaviors).

  Backend suite: **656 passing** (was 637, +19).

- **v0.7.132** 🩺 **/healthz/deep upstream probe + smarter exception

- **v0.7.132** 🩺 **/healthz/deep upstream probe + smarter exception
  truncation + Setup Wizard verification.** Three more Areas for
  Review closed:

  **`/healthz/deep?probe_providers=true` upstream probe (Area #12).**
  Previously /healthz/deep only checked model abstractions (does a
  default chat/embedding model exist in the DB?) but never actually
  hit the upstream provider. A misconfigured API key would pass the
  deep probe with a green light and only fail at first chat. Now an
  opt-in `?probe_providers=true` flag runs every configured Credential
  through the existing `connection_tester.test_provider_connection`
  with a 5s timeout per probe (parallelized via `asyncio.gather`).
  Failures are surfaced per-credential so operators see exactly which
  provider is broken instead of a generic "providers degraded".

  Off by default because each probe burns one cheap API call per
  credential — fine on demand, expensive if a monitoring tool hits
  this every 15 seconds. Recommended cadence: ≤ once per minute.
  Failure of an upstream provider knocks the overall status to
  'degraded' but doesn't flip to 'not_ready' (operator may have
  intentionally configured a provider that's currently down for
  scheduled maintenance).

  Edge cases handled: no credentials configured at all is reported
  as `status='no_credentials', ok=True` (it's a valid state, not an
  error); credential-list query failure is caught and surfaced as
  `status='error'` with the underlying exception message; each
  per-credential probe is wrapped in its own try/except so one
  raising provider doesn't gate the others.

  **`_brief()` smarter truncation (Area #10).** Previously the
  exception-message truncation in studio.py flat-truncated at byte
  ~199, so multi-line exceptions (PyMuPDF stack traces, mammoth
  error blocks, LangChain chained-cause sections) cut in the middle
  of line 1 and lost the rest entirely. Now multi-line exceptions
  preserve the first line verbatim (truncated only if itself over
  budget) and append " (… N more lines)" so the operator sees the
  actual error head plus how much was elided. Pluralization
  correctly handled for the "1 more line" / "N more lines" case.

  **Setup Wizard auto-advance verification (Area #14).** Code review
  confirmed this was already implemented in v0.7.119 at
  `frontend/src/app/(dashboard)/setup-wizard/page.tsx:165-173` via
  `useEffect` + `autoAdvancedRef.current` guard. The Area for Review
  doc had flagged it as "specced but unclear if landed"; verified
  landed. No code change this release; closing the item.

  **13 new hermetic tests** in `tests/test_v0_7_132.py`:
    - 5 `_brief()` cases (single-line passthrough, single-line truncated,
      multi-line preserves first, multi-line pluralization, multi-line
      with over-budget first line)
    - 6 `_probe_upstream_providers()` cases (no creds, list failure,
      all healthy, mixed, timeout, raise-caught)
    - 2 `/healthz/deep?probe_providers` integration tests verifying
      the flag-driven key inclusion/exclusion

  Backend suite: **637 passing** (was 624, +13).

- **v0.7.131** 🔒 **Continued deferred-item improvements: Request-ID

- **v0.7.131** 🔒 **Continued deferred-item improvements: Request-ID
  hardening + optional /metrics auth + dynamic integration-suite
  truncation.** Closes three more Areas for Review from the AI-context
  audit:

  **Request-ID middleware character-set validation (Area #25).**
  Previously the middleware only enforced length on inbound
  `X-Request-ID` (cap at 128 chars). A caller could send
  `X-Request-ID: prefix\n[CRITICAL] forged log entry` and we'd put it
  in the loguru `req=` column verbatim — log-aggregation tools that
  split on newlines would then treat the forged line as a separate,
  freshly-attributed entry. Classic log injection.

  Fix: regex `^[A-Za-z0-9_\-.:]+$` checked alongside the length cap.
  The allowed punctuation set is deliberately small — UUID4 hyphens,
  snake_case, period-segmented IDs, Datadog/OTel `trace-id:span-id`
  composites. Anything else triggers a fresh UUID4 and a DEBUG log
  noting why (without echoing the rejected value, since we don't
  trust it). 5 unit tests cover the canonical injection payload plus
  control-char variants.

  **`/metrics` optional bearer-token auth (Area #19).**
  The Prometheus endpoint was unauthenticated by design — scrapers
  could hit it without credentials, which is the correct default for
  a private network. But operators exposing the API publicly need a
  scrape token, and putting nginx in front isn't always feasible for
  desktop installs.

  Added `ONP_METRICS_AUTH_TOKEN` env var. If set, `/metrics` requires
  `Authorization: Bearer <token>` and rejects anything else with 401
  + `WWW-Authenticate: Bearer realm="metrics"`. Comparison uses
  `secrets.compare_digest` to avoid timing-attack oracles. If unset
  (the default), behavior is identical to v0.7.130 — full backward
  compat. Empty-string env var explicitly treated as unset to avoid
  the footgun of `ONP_METRICS_AUTH_TOKEN=` accidentally locking
  down the endpoint. 6 unit tests cover the open/closed/wrong-token/
  malformed-header/empty-env matrix.

  **Integration suite dynamic table truncation (Area #17).**
  The `clean_namespace` fixture previously hardcoded a 7-element table
  list (`notebook`, `source`, `note`, `reference`, `artifact`,
  `refers_to`, `chat_session`). Every new migration that adds a
  domain table silently invalidated this list — leftover rows from
  test A could appear in test B's `SELECT *`.

  Replaced with `INFO FOR DB`-driven discovery + a deny-list of
  protected tables. The deny-list catches `_sbl_migrations` (truncating
  it would force a migration re-run on the next test) plus any
  underscore-prefixed system table — including future tables we
  haven't planned for. Falls back to the original hardcoded list if
  `INFO FOR DB` raises (degraded coverage is better than a broken
  fixture). 4 unit tests pin the shape-parsing logic across v2 (`tables`)
  and older (`tb`) SurrealDB response forms.

  Net: **624 backend tests** (was 609, +15).

- **v0.7.130** ✨ **Studio + Podcasts + Settings improvements.**

- **v0.7.130** ✨ **Studio + Podcasts + Settings improvements.**
  Multi-surface improvement batch following the audit roadmap:

  **Studio observability** — three new Prometheus counters answer
  Area for Review #13 ("under what conditions does the outline LLM
  produce non-JSON?") with live data:
    * `onp_studio_generations_total{mode, outcome}` — labeled by
      request mode ('notebook' / 'podcast' / 'both') and outcome
      ('success' / 'partial' / 'failed'). `partial` is reserved for
      `both` mode where exactly one half landed.
    * `onp_studio_outline_parse_failures_total{reason}` — `json_decode`
      (LLM produced non-JSON) or `validation` (JSON parsed but failed
      schema check). The two reasons need different fixes — log-parse
      failures usually need a stronger model; validation failures
      usually need prompt rewording. Splitting the metric makes that
      decision data-driven.
    * `onp_studio_single_note_fallbacks_total` — headline metric for
      "is the local outline model good enough?".
    All increments are wrapped in try/except so a metrics import
    failure can never break the actual user-facing flow.

  **Podcasts pagination** — `GET /podcasts/episodes` now accepts
  `?offset=` and `?limit=` query params (default 50, max 200, negative
  rejected by FastAPI's Query validation). Response shape unchanged
  (still `list[PodcastEpisodeResponse]`); total available count
  returned via `X-Total-Count` response header so the UI can render
  "Showing X-Y of N" without a separate API call. Existing callers
  passing no params get the first 50 episodes — a behavior change
  for installs with >50 episodes that were previously transferring
  everything on every list call.

  **Settings router cleanup** — Lifted four duplicate
  `from typing import Literal, cast` imports out of the PUT handler
  body to module level (they used to run inside `if … is not None`
  branches, re-resolving on every request). Removed the redundant
  `cast(Literal[…], value)` calls — they were static-only assertions
  that did nothing at runtime since the request model declared the
  same fields as `Optional[str]`. The cast pattern was masking that
  the validation wasn't actually happening anywhere.

  **Settings model tightened** — `SettingsUpdate` now uses
  `Optional[Literal[…]]` instead of `Optional[str]` for the four
  enum-style fields. FastAPI/Pydantic now rejects invalid values at
  the request boundary with a 422 instead of letting them propagate
  to `ContentSettings.update()` for a less-helpful error message.
  The cast removal exposed that this validation gap existed in the
  first place.

  **New `/settings/observability` read-only endpoint** — returns the
  current ONP_* env-derived configuration (slow-query threshold,
  encryption KDF, checkpoint prune knobs, DB pool size, db_pool
  disabled flag) so the UI can show operators their actual install
  config without re-implementing env-var parsing client-side. Pairs
  with the existing `/metrics` Prometheus endpoint. Includes defensive
  `_env_int()` + `_env_bool()` helpers that warn-and-default-to-baseline
  on unparseable values, so a typo in `.env` can't bring down the
  endpoint.

  **15 new hermetic tests** in `tests/test_v0_7_130.py` covering:
  Prometheus counter increments + labels + render-to-text, podcasts
  pagination default/offset/limit-cap/negative-rejection/beyond-total,
  settings observability defaults / env round-trip / garbage-int
  tolerance / boolean case-insensitivity, settings PUT Literal
  rejection. Full backend suite now **609 passing** (was 594, +15).

- **v0.7.129i** 🐛 **Frontend: migrate middleware.ts → proxy.ts for Next.js 16.**
  Next.js 16 renamed `middleware` → `proxy`: same NextResponse API,
  same matcher shape, only the file name + exported function name
  changed. The repo had both files (the old `proxy.ts` was a v0.7.29
  no-op stub; the real logic lived in `middleware.ts` for the v0.7.117
  first-launch Setup Wizard redirect). With both present, Next 16
  refused to build with:
      Both middleware file ./src/middleware.ts and proxy file
      ./src/proxy.ts are detected. Please use ./src/proxy.ts only.
  Resolution: moved the wizard logic verbatim into `proxy.ts`
  (renamed function `middleware` → `proxy`), deleted the obsolete
  `middleware.ts`. Verified `npm run build` succeeds with 16 routes
  generated. Also corrected two stale references in
  `frontend/src/CLAUDE.md`: it claimed the proxy "redirects root /
  to /notebooks" (not true since v0.7.29 when the proxy was a no-op,
  and definitely not true now that it does the wizard redirect) and
  it described middleware as enforcing auth (auth has always been
  enforced by the API interceptor on 401 responses, not by the
  Next-side proxy/middleware).

- **v0.7.129f** 🛠 **CI: force Node 24 via FORCE_JAVASCRIPT_ACTIONS_TO_NODE24.**
  Belt-and-suspenders for `astral-sh/setup-uv@v6` which still ships a
  Node 20 manifest as of this commit. GitHub's documented escape
  hatch set at workflow level forces the runner to launch every JS
  action on Node 24 regardless of manifest pin. Annotation language
  changes from "may not work as expected" (warning) to "being forced
  to run on Node.js 24" (informational). Remove once upstream rebuilds.

- **v0.7.129e** 🛠 **CI: bump action major versions for Node 24 era.**
  Six-action sweep across all six workflows (test, build-desktop,
  build-and-release, build-dev, claude, claude-code-review):
  `actions/checkout` v4→v5, `setup-node` v4→v5, `upload-artifact`
  v4→v5, `download-artifact` v4→v5, `cache` v3→v4, `setup-uv` v4→v6.
  Silences the Node 20 deprecation warning that would otherwise
  hard-fail starting Jun 2 2026.

- **v0.7.129d** 🐛 **Integration test: pin actual `Notebook.delete()`
  summary keys.** Caught by the v0.7.129c CI run: the cascade test
  asserted on invented `artifact_count` / `reference_count` keys.
  Actual contract documented on `notebook.py:147` is `deleted_notes`,
  `deleted_sources`, `unlinked_sources`. Tighter assertions now also
  pin `deleted_sources == 0` when `delete_exclusive_sources=False`.

- **v0.7.129c** 🐛 **Note.save() no longer fails when surreal-commands
  worker is down.** Found by the v0.7.129 integration suite on its
  first CI run: `Note.save()` was unconditionally submitting an
  `embed_note` command. When the worker hadn't imported the commands
  module (CI without a worker, fresh installs, the moment after a
  restart, anything pytest), `submit_command` raises
  `ValueError: Command not found: open_notebook.embed_note` and the
  entire save fails. The contract documented in
  `open_notebook/domain/CLAUDE.md` is "fire-and-forget": embedding
  is eventual-consistency, the row is what matters. Fix wraps the
  submission in a narrow `except ValueError` catching only the
  "Command not found" message plus a broad `except Exception` for
  network / worker-DB outages — both log a warning and let the save
  succeed. Non-registry ValueErrors still propagate so future
  legitimate argument bugs don't get silently masked. 3 unit tests
  added under `TestNoteSaveResilience` covering all three branches.
  This brings `Note.save()` in line with `Source.add_insight()`,
  which already had the broad-except pattern.

- **v0.7.129b** 🛠 **CI: drop `services:` block for SurrealDB, use
  `docker run -d` instead.** First CI run of v0.7.129 surfaced that
  GitHub Actions' `services:` syntax can pass only image + env +
  ports, not positional CLI args. The SurrealDB image's entrypoint
  is `surreal` which needs `start --user root --pass root memory`
  as args. With no subcommand the container exits immediately,
  failing "Initialize containers" before any workaround step can
  run. Switched to a plain `docker run -d` step with a dedicated
  `curl /health` readiness probe.

- **v0.7.129a** 🛠 **CI: fire Tests workflow on `desktop-app`
  branch.** Previously gated to `branches:[main]` for both push and
  PR triggers — meant the entire test suite only ran on
  upstream-sync PRs, never on the actual working branch. Trivial
  trigger extension.

- **v0.7.129** 🛠 **Real-SurrealDB integration test fixture.** The
  hermetic backend suite (now 591 tests) catches a lot but is
  structurally blind to a class of bugs: SurrealQL syntax regressions
  in raw query strings, migration ordering issues, edge-table
  direction inversions (`reference`/`artifact`/`refers_to` — the
  classic `in` vs `out` bug we've shipped before), and delete-cascade
  gaps. Pure-mock unit tests can't see any of those.

  This release adds an opt-in second suite that runs against a real
  SurrealDB instance:

  - New `pyproject.toml` marker `integration_surreal` registered so
    pytest doesn't warn on usage.
  - New `tests/integration/conftest.py` — session-scoped fixture that
    skips by default unless `SURREAL_INTEGRATION=1` is set, then
    mints a throwaway namespace (`onp_test_<8-char-uuid>`), runs
    every forward migration against it via `AsyncMigrationManager`,
    and `REMOVE NAMESPACE`s on teardown so nothing leaks between
    runs. Patches env vars BEFORE importing repo so the connection
    pool's lazy init targets the test namespace.
  - New `tests/integration/test_notebook_lifecycle.py` — 6 tests
    exercising: edge-direction sanity (source → notebook reference
    edge), idempotent `add_to_notebook`, artifact-edge direction
    (note → notebook), cascade-deletes-edges-but-keeps-records, and
    `delete_exclusive_sources=True` orphan-pruning behavior.
  - New `.github/workflows/test.yml` job `integration-surreal` —
    spins up `surrealdb/surrealdb:v2` with root creds in an in-memory
    container, sets `SURREAL_INTEGRATION=1`, runs the suite. Parallel
    to the existing backend job so a Surreal flake can't gate the
    hermetic suite.
  - New `make test-integration` target for local runs; `make test`
    explicitly ignores the integration dir to keep the default
    workflow hermetic.

  What this catches that mocks can't:
  - **`select in as source from reference where out=$id` direction**
    — assert against actual stored edges.
  - **Migration 1..N applied to an empty namespace** — schema-order
    bugs surface here, not in production.
  - **`Notebook.delete()` cascade** — counts the surviving rows in
    `reference` / `artifact` / `source` tables; mocks can't verify
    rows that should have been deleted by `DELETE WHERE` against the
    real engine.

  Cost: ~30 s for the SurrealDB container to come up in CI, plus
  ~3-5 s test time. Trade-off worth it for the class of bugs this
  catches once a quarter.

- **v0.7.128** 📋 **Deliberately deferred: `studio.py` + `exports.py` split.**
  The original audit roadmap (post-v0.7.119) listed splitting these
  two routers as a maintainability improvement. After re-evaluating
  during v0.7.128 planning:

  - **studio.py**: 1374 LOC, **exports.py**: 1694 LOC. Combined ~3000.
  - **22+ tests directly import internal helpers** (e.g.
    `_markdown_to_html`, `_is_regular_file_entry`, `_strip_json_wrapper`).
  - **All endpoints stable, well-tested**: 591 backend tests pass;
    no behavioral changes warranted.

  **The risk/value calculation**: a structural refactor across 3000
  LOC of working code introduces real regression surface (test
  imports, circular-dep risk, subtle reflection patterns) without
  changing behavior or shipping a feature. The "fits in one head"
  intuition that motivated the refactor is real but mild — both
  files have clear internal section headers + sectional comments
  added during the v0.7.88→v0.7.127 cycle, so navigation isn't
  actually blocked.

  **Better use of the cycle time** is shipping items 4-5 of the
  remaining roadmap, which DO change behavior + DO have user value:
  v0.7.127 shipped bundle analysis, v0.7.129 ships real-SurrealDB
  integration tests.

  If/when these files grow past ~2500 LOC each (i.e., another major
  feature lands), revisit. For now, both have clear section
  markers (`# ---...---` comments grouping helpers, prompts,
  schemas, endpoints) and an in-file philosophy that's easy to
  follow.

- **v0.7.127** ⚡ **Frontend bundle-analysis tooling.** Ships the
  measurement infrastructure to identify code-splitting opportunities;
  defers concrete optimizations to evidence-based decisions instead
  of speculation.

  **What landed:**

  - `@next/bundle-analyzer` added as a devDependency.
  - `next.config.ts` wraps the export with `withBundleAnalyzer()`,
    gated on `ANALYZE=true` env var so production builds aren't
    affected.
  - New npm script: `npm run build:analyze` → runs a production
    build that writes interactive HTML reports to
    `.next/analyze/{client,server,edge}.html`.
  - **`frontend/docs/BUNDLE_ANALYSIS.md`** — operator-facing guide
    documenting how to run the analyzer, what to look for, known
    lazy-load candidates with size estimates (CommandPalette ~30 KB,
    Studio dialogs ~15 KB, Import preview ~10 KB), already-handled
    cases (the markdown editor is already lazy via `dynamic()`),
    and the rationale for why we ship the tool rather than apply
    optimizations blindly.

  **Why no concrete splits in this commit:** Bundle-size decisions
  depend on usage patterns. Lazy-loading CommandPalette is a clear
  win if users rarely press Cmd+K but net-negative if most of them
  do within 5 seconds of page load. Without analyzer-grounded data
  AND telemetry on user behavior, "optimization" is just guessing.

  The proposed-next-steps section in `BUNDLE_ANALYSIS.md` gives the
  next maintainer a concrete playbook: run analyzer → identify
  candidates → wrap in `next/dynamic` → re-measure. Each step
  evidence-driven.

  **Proposed bundle-size budgets** (informational, not yet
  CI-enforced):

  | Bundle | Budget (gzipped) |
  |---|---:|
  | First-load JS | < 200 KB |
  | Largest route chunk | < 50 KB |
  | Total client JS | < 1 MB |

  Enforcement is deferred until we have real numbers — adding
  arbitrary thresholds without baselines is theater.

  **Tests:** No new tests (no behavior change; tooling-only). Full
  frontend suite still passes: 65 tests. TS clean.
- **v0.7.126** ✨ **Backup + restore tooling.** First-class disaster
  recovery for the data directory. Closes a real operator-pain gap:
  before this, the only way to snapshot an install was a manual
  `tar czf` against directory paths the user often didn't know about.

  **New script** (`scripts/backup_restore.py`): single-file Python
  CLI with `backup` and `restore` subcommands. Walks the data root
  (honors `ONP_DATA_DIR` or falls back to `./data/`), bundles
  everything into a gzipped tar with a SHA-256 manifest.

  **Coverage:**
  - SurrealDB data directory
  - Uploaded source files (`UPLOADS_FOLDER`)
  - LangGraph SQLite checkpoints (post v0.7.125 pruning)
  - tiktoken cache

  **Deliberately excluded:**
  - `.env` files (user's responsibility — may contain secrets)
  - `.gguf` model weights (multi-GB, re-downloadable)
  - Log files (`logs/`, `*.log`)
  - Caches (`__pycache__`, `.pytest_cache`, `.lock`)
  - OS noise (`.DS_Store`, `Thumbs.db`, `desktop.ini`)

  **3 new Makefile targets** (`Makefile`):
  ```
  make backup                              # backups/onp-backup-YYYYMMDD-HHMMSS.tar.gz
  make backup OUT=path/to/specific.tar.gz  # custom location
  make verify-backup BUNDLE=path           # integrity check, no extraction
  make restore BUNDLE=path                 # refuses to overwrite non-empty
  make restore BUNDLE=path FORCE=1         # ⚠️ overwrites
  ```

  **Safety guarantees:**
  - Atomic backup write — tarball is created at a `.tmp` sibling
    path and renamed only on success, so a crash mid-archive
    doesn't leave a half-written bundle.
  - SHA-256 manifest of every file embedded in the bundle for
    integrity verification.
  - `verify-backup` re-hashes everything inside the tarball against
    the manifest without touching disk — operators can confirm a
    bundle is intact before relying on it.
  - Restore REFUSES to overwrite a non-empty data dir unless
    `FORCE=1` (prevents accidental destruction).
  - Restore rejects bundles from a future format version with a
    clear error (no confusing extraction failures).
  - 50 GB total-size cap on backup (catches misconfigured paths
    that try to bundle `/`).
  - 1 GB per-file warning surfaces large uploads before they
    silently bloat the archive.

  **Streaming hashes** so the script doesn't OOM on multi-GB
  uploaded files. `_MAX_BUNDLE_BYTES = 50 GB` matches realistic
  install ceilings.

  **9 new tests** in `tests/test_backup_restore_v0_7_126.py` using
  tmp_path-rooted fake data directories: round-trip byte-for-byte,
  skip patterns (logs/cache/.DS_Store/.lock excluded),
  non-empty-data-dir refusal, FORCE override, verify-only mode,
  corrupt-bundle detection (tampered file vs stored SHA-256),
  future-version rejection, empty-data-dir error, missing-dir
  error.

  Backend suite: 582 → **591** (+9). Ruff clean.
- **v0.7.125** ⚡ **LangGraph SQLite checkpoint pruning.** Without this,
  `~/.open-notebook-plus/data/sqlite-db/checkpoints.sqlite` grows
  unbounded — every chat turn appends rows that LangGraph never reads
  again (it only queries the latest checkpoint per thread when
  resuming). A single-user install with moderate use (20 turns/day)
  accumulates ~7300 rows/year; over multi-year deployments the file
  hits hundreds of MB.

  **New module** (`open_notebook/utils/checkpoint_prune.py`):

  - **`prune_old_checkpoints(path, keep_per_thread=None)`** — one-shot
    callable. Opens the sqlite DB with the same WAL+busy_timeout
    tuning as the rest of the codebase, runs a single-transaction
    `DELETE` using a `ROW_NUMBER() OVER (PARTITION BY thread_id
    ORDER BY checkpoint_id DESC)` window function to identify
    everything past the per-thread retention cap, then cascade-
    deletes orphan rows from the `writes` table. Finally runs
    `PRAGMA incremental_vacuum(1000)` to reclaim freed pages.
  - **`run_prune_loop(stop_event)`** — async background loop that
    runs prune once on entry, then sleeps for
    `ONP_CHECKPOINT_PRUNE_INTERVAL_HOURS` (default 24), then prunes
    again. Cancellation-aware via `wait_for(stop_event.wait(),
    timeout=interval)` so shutdown is snappy. Wraps each prune call
    in `asyncio.to_thread()` so the sync sqlite3 driver doesn't
    block the event loop.

  **Lifespan wiring** (`api/main.py`): mirror the v0.6.1 digest-
  scheduler pattern — `asyncio.create_task(...)` on entry, signal
  + `wait_for(timeout=10) → cancel fallback` on shutdown. Non-fatal
  if it fails to start; chat still works, just grows.

  **2 new env knobs:**

  | Env var | Default | What |
  |---|---|---|
  | `ONP_CHECKPOINT_KEEP_PER_THREAD` | 50 | Most-recent checkpoints retained per thread_id |
  | `ONP_CHECKPOINT_PRUNE_INTERVAL_HOURS` | 24 | How often the loop fires |

  **Prometheus metrics** (added to `api/metrics.py`):

  - `onp_checkpoint_prune_runs_total` — counter, increments per loop iteration
  - `onp_checkpoint_prune_rows_deleted_total{table="checkpoints" | "writes"}` —
    counter labeled by which table the rows came from. Graph this
    against time to see steady-state churn vs the first-run backlog.

  Both wrapped in `try/except` so a metrics-module import failure
  can never break the prune path (consistent with v0.7.124).

  **10 new tests** in `tests/test_checkpoint_prune_v0_7_125.py` using
  a tmp_path-backed sqlite file with the actual LangGraph schema:
  no-op-when-file-missing, no-op-when-tables-missing,
  keeps-most-recent-N-per-thread, multi-thread-independence,
  cascades-orphan-writes, honors-env-knob, invalid-env-falls-back,
  negative-env-falls-back, idempotent (second run finds nothing),
  returns-elapsed-ms.

  Backend suite: 572 → **582** (+10). Ruff clean.
- **v0.7.124** ⚡ **Prometheus `/metrics` endpoint + v0.7.121 CORS
  regression test.** Operators can finally answer "is anything broken
  right now?" without grepping logs.

  **New observability surface:**

  - **`/metrics` endpoint** in standard Prometheus exposition format.
    Auth-exempt (added to `excluded_paths`) so Prometheus / Grafana /
    Victoria Metrics / any OpenMetrics-compatible scraper polls
    without `OPEN_NOTEBOOK_PASSWORD`.

  - **Metrics surfaced** (`api/metrics.py`):
    - `onp_http_requests_total{method, route, status_code}` —
      classic RED-method request counter.
    - `onp_http_request_duration_seconds{method, route}` — latency
      histogram with buckets 5ms..30s (tuned for the realistic API
      latency range, including timed-out 504s).
    - `onp_db_query_duration_seconds` — SurrealQL latency histogram
      with buckets 1ms..5s.
    - `onp_db_slow_queries_total` — counter for queries that
      exceeded `ONP_SLOW_QUERY_LOG_MS` (matches the v0.7.120 log
      line one-for-one — graph this against time to see slow-query
      regressions).
    - `onp_memory_recall_fallthrough_total{reason}` — counter with
      4 labeled reasons (`embed_timeout`, `embed_error`,
      `query_timeout`, `query_error`). If this rises, the
      embedding model or DB pool is unhealthy and chat is silently
      degrading on the v0.7.113/v0.7.114 fallback paths.
    - `onp_memory_recall_duration_seconds` — histogram bounded by
      the ~15s worst-case ceiling.
    - Plus the default `process_*` + `python_gc_*` metrics that
      `prometheus-client` ships.

  **Route-label cardinality protection** in the middleware: the
  route label is the FastAPI route TEMPLATE
  (`/api/notebooks/{notebook_id}`), NOT the literal URL
  (`/api/notebooks/notebook:abc123`). Otherwise every notebook ID
  creates a new cardinality bucket and Prometheus storage explodes
  on long-running deployments. Verified via dedicated test.

  **Scrape-traffic exclusion**: `/metrics` paths short-circuit the
  request-timing middleware so Prometheus polls don't appear as
  user traffic in dashboards.

  **Best-effort observability hooks**: every wire-up site
  (`repo_query`, `recall_relevant_memory`, `_safe_select`) wraps the
  metrics-module import + counter increment in `try/except` so a
  metrics failure can never break the underlying DB / memory path.

  **v0.7.121 regression test (deferred until now)**: added a test
  asserting the `DANGEROUS CONFIG` ERROR-level log fires when
  `CORS_ORIGINS='*'` AND `OPEN_NOTEBOOK_PASSWORD` is unset, plus a
  negative-space check that it does NOT fire when password is set.

  **New dep**: `prometheus-client>=0.20.0` (~30 KB, pure Python).
  The official client; no native compilation.

  **Tests**: 11 new — 8 in `tests/test_metrics_v0_7_124.py` +
  2 v0.7.121 regression + 1 follow-up. Backend suite: 561 → **572**
  (+11). Ruff clean.

  **Recommended scrape interval**: 15s for high-traffic
  deployments, 60s for single-user desktop installs.
- **v0.7.123** 🔒 **PBKDF2 key-derivation option for credential
  encryption.** Adds an opt-in stronger KDF for the
  `OPEN_NOTEBOOK_ENCRYPTION_KEY` passphrase → Fernet-key derivation.

  **Threat model:** The original v0.7.0 derivation was a single
  SHA-256 of the passphrase (~instant). If an attacker exfiltrates
  the SurrealDB file (containing encrypted credentials), they can
  brute-force weak passphrases at billions of guesses per second.
  PBKDF2-HMAC-SHA256 with 600,000 iterations adds ~250ms cost per
  guess — slows offline brute-force by ~9 orders of magnitude.

  **New env knob:** `ONP_ENCRYPTION_KDF` = `sha256` (default,
  back-compat) | `pbkdf2` (recommended). 600,000 iterations is the
  OWASP 2024 recommendation for PBKDF2-HMAC-SHA256.

  **Migration design — `get_multi_fernet()` tries both KDFs:**

  - **New encryption** uses whichever KDF the env knob selects.
  - **Decryption** builds a MultiFernet wrapping each configured
    key × each known KDF, in preference order. Existing
    sha256-encrypted data continues to decrypt after migrating to
    pbkdf2 — no re-encrypt sweep required.
  - **Rotation** (the v0.7.17 `_ENCRYPTION_KEYS` plural form) still
    works — the matrix is `(keys × kdfs)` so any combination
    decrypts.

  **Deterministic salt:** PBKDF2 uses a deterministic 16-byte salt
  derived from the passphrase + a version-tagged constant
  (`onp-kdf-salt-v1`). Required for the derivation to be
  reproducible across restarts (we don't store a per-key salt blob
  anywhere). The version tag allows future salt-scheme rotation
  without breaking existing data.

  **No new dependency** — uses Python stdlib `hashlib.pbkdf2_hmac`.
  Argon2id would be marginally stronger but requires `argon2-cffi`
  (~150KB binary wheels) and a separate migration path. Deferred to
  v0.7.124+ if there's demand.

  **9 new tests** in `tests/test_encryption_kdf_v0_7_123.py`:
  default-is-sha256, pbkdf2-mode-uses-pbkdf2, deterministic
  derivation, unknown-KDF actionable error, round-trip × 2 KDFs,
  sha256→pbkdf2 migration, pbkdf2→sha256 reverse, rotation +
  cross-KDF combined.

  Backend suite: 552 → **561** (+9). Ruff clean. Argon2id KDF
  remains deferred (would need new `argon2-cffi` dep).
- **v0.7.122** 🔒 **Dependency CVE remediation: 16 backend + 7 frontend
  → 0 frontend, 8 backend remaining (upstream-blocked).** Ran
  `pip-audit` + `npm audit` against the locked dependency tree and
  remediated everything resolvable without breaking the app.

  **Backend** (`pyproject.toml` + `uv.lock`):
  - `langgraph`: `>=1.0.5` → `>=1.0.10` (CVE-2026-28277)
  - `python-dotenv`: `>=1.0.1` → `>=1.2.2` (CVE-2026-28684)
  - Added explicit pins for transitive deps with published fixes:
    - `langchain-core>=1.3.3` (CVE-2026-44843)
    - `langsmith>=0.8.0` (CVE-2026-45134)
    - `lxml>=6.1.0` (CVE-2026-41066) — XML parser hardening
    - `urllib3>=2.7.0` (CVE-2026-44431 + CVE-2026-44432) — HTTP client
    - `python-multipart>=0.0.27` (CVE-2026-42561) — file-upload library
  - **Remediated: 8 of 16 backend CVEs eliminated.**

  **Backend NOT remediated (upstream-blocked):**
  - `pillow 11.3.0` → 6 CVEs (CVE-2026-25990 + 40192 + 42308 + 42309
    + 42310 + 42311). Fix requires `pillow>=12.1.1` but
    `podcast-creator==0.12.0` pins `pillow<12.0`. Lower impact in
    our pipeline: pillow only sees images that content-core
    extracted from user-uploaded PDFs/DOCX — constrained attack
    surface. Will revisit when podcast-creator publishes a newer
    version compatible with pillow>=12.
  - `pip 26.0` → CVE-2026-3219, CVE-2026-6357 (dev tool only — not
    in runtime; only relevant to operators running `pip install`).

  **Frontend** (`frontend/package.json` + `package-lock.json`):
  - `npm audit fix` auto-resolved 5 of 7 CVEs (Next.js middleware
    bypass × 4 + `ws` uninitialized-memory disclosure) by bumping
    the affected packages to their published-fix versions.
  - Added `overrides.postcss: "^8.5.10"` to force the
    Next.js-bundled postcss to the fixed version
    (GHSA-qx2v-qp2m-jg93 — XSS via unescaped `</style>` in CSS
    stringify output). The npm-suggested "fix" of downgrading to
    `next@9.3.3` was unacceptable (would lose Next.js 16 App
    Router); `overrides` is the canonical npm mechanism for
    forcing a nested transitive without touching the parent.
  - **Remediated: 7 of 7 frontend CVEs eliminated. `npm audit`
    reports `found 0 vulnerabilities`.**

  **Net cycle delta:**
  - 23 CVEs detected → 8 remaining (all upstream-blocked or dev-only).
  - 65% reduction; 100% of the runtime-exploitable findings closed.
  - Two HIGH-severity Next.js middleware bypasses fixed (directly
    relevant to v0.7.117's Setup Wizard middleware).

  **Tests pass after upgrade:** 552 backend (unchanged) + 65 frontend
  (unchanged). Ruff clean. tsc clean.
- **v0.7.121** 🔒🎨 **Security headers expansion + cookie hardening +
  visual a11y polish.** Follow-up to v0.7.120's middleware
  groundwork. Two axes shipped together:

  **Security:**

  1. **HSTS conditional on HTTPS** — `Strict-Transport-Security:
     max-age=63072000; includeSubDomains` set ONLY when the request
     scheme is `https://`. Plaintext HTTP responses get no HSTS
     header (sending it on HTTP would teach the browser to
     force-upgrade future requests even when no TLS terminator
     exists). Respects `X-Forwarded-Proto` via uvicorn's
     `--proxy-headers`.

  2. **Permissions-Policy** denies browser features the API has no
     business using: camera, microphone, geolocation, payment,
     accelerometer, gyroscope, USB, MIDI, display-capture, XR. 14
     features explicitly disabled.

  3. **X-XSS-Protection: 0** — modern best practice. The legacy
     IE-era heuristic filter caused universal-XSS in older browsers
     and has zero benefit in modern ones; explicitly disabling tells
     browsers not to attempt heuristic sanitization.

  4. **Wizard cookie hardened** — `wizard_completed` cookie now
     written with `SameSite=Strict` (was Lax) and `Secure` (when on
     HTTPS, detected via `window.location.protocol`). Skipped on
     `http://localhost` to keep dev unbroken (browsers reject
     Secure cookies on plaintext localhost).

  5. **Dangerous-config ERROR at startup** — when `CORS_ORIGINS='*'`
     AND `OPEN_NOTEBOOK_PASSWORD` is unset, the API logs an
     ERROR-level message naming the foot-gun. That combo means
     anyone on the internet can read/write every notebook. Existing
     v0.6.7 WARNING for just-CORS-default was preserved; the new
     ERROR fires only on the dangerous overlap.

  **Visual (accessibility):**

  6. **`prefers-reduced-motion` respect** in `globals.css` —
     slams all animations + transitions to ~0ms duration when the
     user has the OS-level reduce-motion setting on. Satisfies WCAG
     2.1 SC 2.3.3 (Animation from Interactions). Without this, every
     hover transform, page transition, and shadcn animation ignored
     the user's preference.

  7. **Stronger `:focus-visible` ring** — 3px outline + 2px offset
     for keyboard users. Only applies to `:focus-visible` (keyboard
     focus), not `:focus` (which fires on mouse click). Helps users
     with low vision distinguish the focused element.

  **Audit context (verified clean this pass):**
  - No bare `except:` clauses anywhere
  - No SQL injection via f-string in repo_query
  - No log-secret-leak patterns
  - Password comparison still uses `secrets.compare_digest` (v0.6.7)
  - Zip-symlink rejection still in place (v0.7.117)
  - `rel="noopener noreferrer"` still on external HTML-export links
    (v0.7.118)
  - No frontend `aria-label` regressions on icon-only buttons

  **Tests:** 4 new in `tests/test_middleware_v0_7_120.py`:
  - Permissions-Policy present + names key denials
  - X-XSS-Protection: 0 set
  - HSTS absent on http://
  - HSTS present on https:// with correct max-age + includeSubDomains

  Backend suite: 548 → **552** (+4). Frontend: 65 pass (unchanged,
  no test-affecting changes). Ruff + tsc clean.
- **v0.7.120** 🛠⚡🔒 **Cross-cutting middleware + slow-query log +
  pre-commit.** One focused commit shipping five lightweight wins that
  came out of the post-v0.7.119 audit. ~7 hours of work; high
  signal-to-noise.

  **1. Request-ID correlation** (`api/middleware/request_id.py`):
  Every request gets a UUID4 (or accepts an inbound `X-Request-ID`
  from upstream proxies, capped at 128 chars). The id is set as the
  response `X-Request-ID` header AND bound into loguru's context via
  `logger.contextualize(request_id=...)`. The log format
  (`open_notebook/logging.py`) gained a `req=<id>` column, so every
  log line during a request flow carries the same id. Operators can
  `grep <id>` to follow a single request across files. Helper
  `current_request_id()` lets handler code surface the id in error
  responses or slow-query warnings.

  **2. GZip middleware**: FastAPI's built-in
  `GZipMiddleware(minimum_size=1000)`. Bodies ≥ 1 KB get compressed
  when `Accept-Encoding: gzip` is set (every modern browser + httpx
  client). Free perf win on notebook lists, search results, the
  `/healthz/deep` body, OpenAPI spec.

  **3. Security headers middleware**
  (`api/middleware/security_headers.py`): Defense-in-depth baseline
  on every API response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy:
  strict-origin-when-cross-origin`, and a strict `Content-Security-
  Policy` (`default-src 'none'; script-src 'self'; …`) — skipped on
  `/docs`, `/redoc`, `/openapi.json` because Swagger UI pulls CDN
  resources. Uses set-if-absent semantics so handlers that build
  their own JSONResponse with explicit headers aren't clobbered.

  **4. Slow-query log threshold** (`repo_query` in
  `open_notebook/database/repository.py`): Times every SurrealQL
  query; logs a WARNING when elapsed exceeds
  `ONP_SLOW_QUERY_LOG_MS` (default 500ms). Logs the elapsed time,
  threshold, and a 300-char truncation of the query. The `finally:`
  ensures slow queries that ALSO errored still log — timing info is
  doubly useful when something's broken. Combined with v0.7.114's
  silent memory-recall fall-through, this surfaces which queries are
  actually slow without polluting normal logs.

  **5. Pre-commit hooks** (`.pre-commit-config.yaml`): ruff check +
  ruff format on staged Python files; check-yaml + check-json +
  check-toml + end-of-file-fixer + trailing-whitespace +
  check-merge-conflict + 1 MB file-size cap. Pytest deliberately
  NOT in the gate (too slow); CI runs it. One-time setup: `uv run
  pre-commit install`.

  **Middleware order** (Starlette wraps in reverse of registration):
  CORS (outermost) → RequestID → SecurityHeaders → GZip →
  PasswordAuth (innermost) → handler. Documented inline in
  `api/main.py`.

  **Tests**: 14 new in `tests/test_middleware_v0_7_120.py` —
  5 RequestID (inbound + cap + contextvar + outside-scope fallback),
  4 SecurityHeaders (baseline + CSP on/off docs + idempotent
  handler-overrides), 2 GZip (compress / skip), 3 slow-query
  (warning / silent / error-still-logs).

  Backend suite: **548 pass** (was 534, +14). Ruff clean. New env
  knob `ONP_SLOW_QUERY_LOG_MS` (default 500).
- **v0.7.119** ✅ **Wrap-up: regression-test gaps + maintainer docs +
  frontend polish.** Closes the testing + docs gaps left open after
  the v0.7.88 → v0.7.118 hardening run.

  **Backend:**
  - **`tests/test_transformation_execute_timeout.py`** — 4 new tests
    covering v0.7.95's `/transformations/execute` timeout: 504 with
    env-knob hint, fast-path returns 200, 404 for missing
    transformation, 404 for missing model. Companion to v0.7.108's
    `test_chat_execute_timeout.py`. The 404 tests also cover v0.7.109's
    `except HTTPException: raise` invariant — typed status codes now
    survive the outer `except Exception` block.
  - **`tests/test_studio_e2e_multipage.py`** — 2 graph-level E2E
    tests for the v0.7.89 multi-page Studio pipeline: full
    overview-plus-N-pages plan rendered + saved in render order,
    `note_id` back-compat with the v0.7.105 frontend (points at
    Overview). Mocks at the LLM-provision + content-extract +
    save boundaries — catches state-shape regressions in the four
    stages (_generate_outline → _generate_all_pages →
    _save_notebook_notes → _render_overview_note) that the per-unit
    `test_studio_router.py` tests don't fully cover together.

  **Docs:**
  - **`docs/7-DEVELOPMENT/maintainer-guide.md`** — Plus-specific
    "operational quick-reference" section appended: symptom-→-knob
    table for every timeout / cap added in v0.7.88+, healthcheck
    endpoint summary, export/import endpoint summary, the
    `except HTTPException: raise` invariant explained, release
    checklist, test-suite catalog.

  **Frontend** (landed as the v0.7.119 frontend commit):

  - **Export dialog**: format selector now exposes all six backend
    formats (`folder` / `zip` / `html_folder` / `html_zip` /
    `combined_md` / `combined_html`). New `include_sources` checkbox
    (gated to formats that honour it) and `compression` Select
    (gated to zip variants). Existing `overwrite` retained. Submit
    body now only sends `compression` for zip formats and skips
    `include_sources` for `combined_html` (backend ignores it
    there anyway).
  - **Import dialog** (`ImportNotebookDialog.tsx`, new): dry-run
    preview flow consuming `POST /notebooks/import/preview` then
    `POST /notebooks/import`. Surfaces detected kind badge,
    editable notebook name / description (prefilled from manifest
    hints), notes + sources lists with overview flag (📋), backend
    warnings as a yellow Alert, and new-vs-existing mode selector.
    On success, navigates to the new/target notebook.
  - **Import entry point**: new "Import" button in the notebooks
    list page header, next to "New Notebook".
  - **Bulk vectorize** (`BulkVectorizeButton.tsx`, new): button in
    the Sources column header opens a confirm dialog with the
    `only_missing` checkbox (default true) and POSTs to
    `/notebooks/{id}/vectorize_sources`. Toast surfaces the
    `queued / skipped` tally plus any `warnings[]`.
  - **Setup Wizard auto-advance**: when the first `/healthz/deep`
    response comes back `healthy`, the wizard self-dismisses via
    `router.replace('/')` and sets the wizard-completed cookie +
    localStorage flag. Guarded by a `useRef` so the auto-advance
    only fires once per mount (manual back-navigation won't loop).
  - **DirectoryPicker**: gained a `selectionMode='any'` mode used
    by the Import dialog — single-clicking a file (.zip / .md)
    selects it; folders still navigate as before.
  - **i18n**: ~37 new keys added to all 10 locales
    (`notebooks.export.format.*`, `notebooks.export.compression.*`,
    `notebooks.export.includeSources`, `notebooks.import.*`,
    `notebooks.bulkVectorize.*`). The flat `notebooks.export`
    button-label string was promoted to `notebooks.export.button`
    so the new nested namespace can coexist; callsites updated.
    Locale-parity + unused-key tests pass.

  Test counts (post v0.7.119 backend): 528 → **534** (+6).
  Frontend tests: 57 → **65** (+8: 1 in `ExportNotebookDialog`
  expansion, 2 in `ImportNotebookDialog`, 2 in
  `BulkVectorizeButton`, 2 wizard auto-advance, plus refresh of
  existing export-dialog assertions). `npx tsc --noEmit` clean,
  `npm run lint` clean on touched files.

  **Deferred (truly):**
  - Real-SurrealDB integration test for delete cascade — needs
    test-infra (docker-compose SurrealDB CI fixture). Mocked tests
    in v0.7.116 cover the orchestration logic.
  - README screenshots refresh — requires generating real images
    of the running app; not feasible in a code-change PR.

  **The v0.7.88 → v0.7.118 cycle is now release-ready.** Tag and
  ship.



- **v0.7.118** 🔒 **rel="noopener noreferrer" on external links in HTML
  exports.** Follow-up to v0.7.117's XSS hardening.

  HTML exports (`html_folder`, `html_zip`, `combined_html`) are
  specifically designed to be shared — emailed, dropped into Drive,
  printed to PDF. The rendered `<a href="https://...">` tags had no
  `rel` attribute, which left two small but real risks for recipients:

  1. **Tabnabbing** if the recipient's email client / browser opens
     the export with `target="_blank"`-like behavior — the destination
     site could manipulate `window.opener` and redirect the user's
     browser back to a phishing page.
  2. **Referer leak** when the export is opened locally (file://) and
     the user clicks an external link — the browser would send a
     `Referer: file:///Users/.../...` header to the destination,
     exposing the recipient's local filesystem path.

  Fix: Custom token renderer on markdown-it's `link_open` rule adds
  `rel="noopener noreferrer"` to any link with `http://`, `https://`,
  `mailto:`, or `ftp://` scheme. Internal anchors (`#section`) and
  relative paths (`./other.md`) are left untouched.

  Audit context — also verified clean in this pass:
  - Frontend Setup Wizard middleware has explicit exempt prefixes
    (`/setup-wizard`, `/login`, `/api`, `/_next`) so it can't
    redirect-loop the wizard or block static assets.
  - `Source.delete()` cascade fully cleans up: source_embedding,
    source_insight, reference edges, in-flight commands cancelled,
    uploaded file unlinked with UPLOADS_FOLDER containment check.
  - `Note.delete()` cascades artifact + note_embedding (v0.7.76).

  +1 test (external vs internal link rel attribute). Full suite:
  528 pass (was 527).
- **v0.7.117** 🔒 **XSS hardening + zip-symlink rejection** (two real
  security findings from another audit pass).

  - **XSS via raw HTML in markdown notes** — `_markdown_to_html()`
    (used by v0.7.97 per-page + v0.7.111 combined HTML exports)
    rendered `<script>` and other raw HTML tags VERBATIM. Self-
    hosted single-user is mostly safe (author == user), but the
    combined_html export is specifically designed for share-by-
    email / Drive / link, so a malicious note author could craft
    payloads that execute when a recipient opens the file.

    Fix: pass `html=False` to MarkdownIt constructor AND disable
    both `html_inline` and `html_block` rules. Raw `<script>`,
    `<iframe>`, `<img onerror>`, `<svg onload>`, etc. are now
    rendered as escaped literal text. Legitimate markdown (bold,
    code, tables, lists, blockquotes, real https:// links) still
    works.

  - **Zip-symlink import** — `_validate_archive_member()` blocked
    path-traversal `..` segments but didn't check the Unix mode
    bits. A zip with a symlink "passwords.md" → "/etc/passwd"
    would have its 'content' read as the link-target string —
    silently importing garbage. Not directly exploitable (we
    don't extract to disk) but bad UX.

    Fix: new `_is_regular_file_entry()` discriminator inspects
    the S_IF* bits in `external_attr` and rejects symlinks
    (0o120000), FIFOs (0o010000), devices, sockets. Accepts
    regular files, directories, AND entries with no S_IF* bits
    set (Python `zipfile.writestr` default + DOS-only zips).

  - **Other audit angles verified clean:**
    - Edge-table direction (`reference`/`artifact`/`refers_to`):
      `in=child, out=notebook` consistent across `relate` →
      `get_X` traversals.
    - Password comparison uses `secrets.compare_digest` (v0.6.7
      already, confirmed still in place).
    - Connection pool warmup + shutdown drain still bounded.
    - No `eval`/`exec`/`pickle.loads` on user input.
    - No bare `except:` clauses.

  6 new tests: 3 for the XSS escaping, 2 for symlink rejection,
  1 unit test on the file-type discriminator. Full suite:
  527 pass (was 522).
- **v0.7.117 (frontend)** ✨🎨 **First-launch Setup Wizard consuming
  `/healthz/deep`.** Adds a `useDeepHealth` TanStack Query hook
  (`refetchOnWindowFocus`, 30s stale, axios `validateStatus < 600`
  so both 200 and 503 bodies are read) and a `/setup-wizard` route
  that renders a traffic-light view of the v0.7.112 deep health
  payload: ✅ for `ok: true`, ⚠️ for `missing`/`pending`,
  ❌ otherwise. Per-subsystem "Fix this" buttons deep-link to
  `/settings/models` (embedding + chat) or `/advanced` (command
  registry); database + migrations show the raw error string
  (no in-app fix). "Continue anyway" is disabled when overall
  status is `not_ready` (DB / migrations down — nothing renders
  past this gate) and sets a `wizard_completed` cookie +
  localStorage on click. New `src/middleware.ts` redirects to
  `/setup-wizard` on first launch when that cookie is absent.
  The existing `SetupBanner` is extended to surface a
  dismiss-for-the-session degraded banner pointing back at the
  wizard (suppressed on the wizard route itself to avoid the
  banner echoing the wizard's own card). All 14 new i18n leaf
  keys added to every locale (10/10 parity preserved). 13 new
  vitest tests (4 hook, 5 wizard, 4 banner); full frontend suite:
  58 pass (was 45).
- **v0.7.116** ✨🛠 **Per-provider connection-test timeouts + typing
  modernization + delete-cascade test + docs sync.** Four deferred
  improvements shipped together.

  - **Per-provider connection-test timeouts.** Replaces the v0.7.100
    single global `ONP_CONNECTION_TEST_TIMEOUT_SEC` (30s) with a
    three-tier resolver: per-provider env (`ONP_CONNECTION_TEST_
    TIMEOUT_SEC_<UPPER>`) → global env → per-provider default.
    Defaults: cloud APIs 10-15s, local servers (ollama,
    openai_compatible) 60s for cold-start. Local-bundle Ollama
    users get a working "Test connection" button OOTB.
  - **`typing.List/Dict/Optional` modernization.** Ran ruff with
    UP006 + UP007 — 293 auto-fixes across 13 files. Final non-auto
    fix: `Union[...]` alias in `open_notebook/ai/models.py`
    converted manually. Added `UP006,UP007` to `pyproject.toml`
    permanent rule set so future code stays modernized. Python
    3.10+ supports both PEP 585 generics and PEP 604 unions.
  - **Bulk delete-cascade integration test.** Added
    `tests/test_notebook_delete_cascade.py` with 5 tests covering
    v0.7.107's parallel cascade: every note gets `delete()`, one
    failure doesn't cancel siblings, calls run concurrently (peak
    in-flight > 1), defensive top-level `DELETE artifact` runs
    after the loop, empty notebook is safe. Uses class-level
    monkeypatching (Pydantic v2 doesn't allow instance attr
    assignment) + stubs `repo_query` + `repo_delete` so no real
    SurrealDB is needed.
  - **`docs/5-CONFIGURATION/onp-env-reference.md` sync.** Added 6
    new sections documenting the 16 env knobs introduced in
    v0.7.89-v0.7.115, plus a version-history row for each. Doc
    now matches the README's env-knob table.

  Test count: 517 → 522 backend (+5 delete-cascade tests). Frontend
  Setup Wizard task spawned for the remaining deferred frontend
  work.
- **v0.7.115** 🐛 **Submit-command timeout + defensive Azure migration.**
  Two more audit-uncovered issues.

  - **`CommandService.submit_command_job`** (`api/command_service.py:31`)
    and **`PodcastService.submit_generation_job`**
    (`api/podcast_service.py:103`) — both wrapped `submit_command` in
    `asyncio.to_thread` per v0.7.55 (good — doesn't block event loop),
    but had **no timeout** around the `await`. A hung SurrealDB pool
    or stuck WS handshake would cause the awaiting endpoint to wait
    indefinitely. Wrapped both in `asyncio.wait_for` with
    `ONP_SUBMIT_COMMAND_TIMEOUT_SEC` (default 10s — submits are
    normally <500ms; 10s is generous). Timeout → `ValueError` with
    actionable hint.
  - **`create_credential_from_env`** (`api/credentials_service.py:290`)
    used `os.environ["AZURE_OPENAI_API_KEY"]` (subscript) instead of
    `.get()`. Safe-by-construction today — `check_env_configured`
    gates the branch on all three required env vars being set — but
    a future refactor of `PROVIDER_ENV_CONFIG` could silently turn
    this into a 500. Replaced with `.get()` + explicit `ValueError`
    so the bug surface is closed.
  - Broader audit pass scanned `os.environ[...]` subscript-reads
    across the codebase: only this one was a bare read; the 10 other
    matches are all WRITES (setting env vars from DB credentials in
    `key_provider.py`) which are safe.
- **v0.7.114** 🐛 **Chat-hot-path memory-recall query timeout.**
  Companion to v0.7.113. `_safe_select()` (the helper that runs
  every memory-recall SurrealQL query — recency + semantic both
  fire two queries each) had no timeout. An overloaded connection
  pool, DB pause, or runaway vector-similarity scan could stall
  every chat turn waiting for the pool's own timeout (much longer
  than the 5s budget we want for memory).

  Wrapped with `ONP_MEMORY_RECALL_QUERY_TIMEOUT_SEC` (default 5s).
  Combined with v0.7.113's embed timeout, the whole memory-recall
  path is now bounded by ~15s worst-case before falling through to
  empty.

  README + CHANGELOG updated with the full env-knob inventory + the
  new API surface added since v0.7.87. Test count: 517 backend / 45
  frontend.

  2 new tests (timeout falls through to empty, fast query succeeds).
- **v0.7.113** 🐛 **Chat-hot-path memory-recall embed timeout.**
  `recall_relevant_memory()` runs on every chat turn (`recall_memory`
  orchestrator picks it once memory rows pass the semantic threshold).
  Its `aembed([query])` call had no timeout — a stuck local
  embedding model (cold-start, OOM, misconfigured base_url) would
  delay chat by up to `ONP_CHAT_TIMEOUT_SEC` (300s default before
  v0.7.99's outer wrap fires).

  Wrapped in `asyncio.wait_for(timeout=
  ONP_MEMORY_RECALL_EMBED_TIMEOUT_SEC)` (default 5s). On timeout we
  fall through to `recall_recent_memory()` which is DB-only — chat
  loses semantic recall for that turn but doesn't stall. This makes
  the memory feature truly **best-effort** end to end: read path
  matches the v0.7.68/v0.7.70 write path's fire-and-forget posture.

  2 new tests: timeout falls through to recency, fast embed
  completes normally.

  Audit context: scanned the rest of the memory pipeline (writer
  command submission already non-blocking via `asyncio.to_thread`,
  read-only SurrealQL queries already defensive with empty-on-
  failure). This was the last hot-path call without a timeout.
- **v0.7.112** ✨ **Deep healthcheck endpoint (`/healthz/deep`).**
  Existing `/livez` and `/readyz` cover liveness + must-have deps
  (DB, migrations). v0.7.112 adds a deeper probe that lets operators
  answer "is vector search broken?" / "is chat broken?" without
  grepping logs.

  - Probes each subsystem independently with a short timeout:
    - **Database** (must-have, 2s)
    - **Migrations** (must-have, 3s)
    - **Embedding model** (optional, 2s) — needed for vector search +
      chat-with-sources
    - **Chat model** (optional, 2s) — needed for `/chat`, `/studio`,
      `/search/ask`
    - **Command registry** (optional) — needed for async jobs
      (embeddings, podcast generation, source extract)
  - Returns three overall states:
    - `healthy` (200) — all subsystems up
    - `degraded` (200) — optional subsystems missing, must-haves OK
    - `not_ready` (503) — DB or migrations failed
  - Each subsystem report includes an **actionable error string**
    when broken. E.g. missing chat model: "Configure one in
    Settings → Models — without it, /chat, /studio, and /search/ask
    cannot generate responses."
  - Auth-exempt (added to `excluded_paths`) so monitoring tools and
    first-launch wizards can poll without credentials. Same exemption
    rationale as `/readyz`.
  - Audit pass during the build:
    - 0 sync I/O calls in async functions
    - 0 `time.sleep` in async paths
    - 0 unguarded `result[0]` accesses (all `if result:` checked)
    - 0 log-secret-leak patterns (api_key / password / secret never
      f-string'd into log messages)
  - 6 new tests cover healthy / not-ready (DB offline + migrations
    pending) / degraded (missing embedding / missing chat) / auth-
    exempt-status.
- **v0.7.111** ✨ **Combined single-file notebook export.** Two new
  formats: `combined_md` and `combined_html` concatenate every page
  (plus optional sources) into a single file rather than a
  folder/zip of per-page files. Better for share-by-email,
  Drive/Dropbox upload, print-to-PDF from the browser, or import
  into paperless-gpt as one entry.

  - **Markdown variant** (`combined_md`): cover page with notebook
    name + description, table of contents listing every page,
    each note rendered with horizontal-rule separators that act as
    page breaks in print-to-PDF.
  - **HTML variant** (`combined_html`): self-contained HTML5
    document with light/dark CSS, a styled cover page, TOC,
    `<hr class="onp-page-break">` separators, and **print CSS** that
    forces each note onto its own page when the user prints-to-PDF
    via their browser — getting most of the "PDF export" deferred
    item done without bundling weasyprint / wkhtmltopdf.
  - File extension auto-corrected if the caller omits one
    (`/exports/combined` → `combined.md` or `combined.html`).
  - 5 new tests cover happy-path markdown + html, auto-extension,
    include_sources, and directory-target rejection.
- **v0.7.110** 🐛 **Per-request caps + timeouts on bulk endpoints.**
  Audit-uncovered scaling/timeout gaps not present in earlier sweeps.

  - **Bulk vectorize** (`POST /notebooks/{id}/vectorize_sources`)
    capped at `ONP_BULK_VECTORIZE_MAX_SOURCES` sources per call
    (default 500). Truncation surfaces an actionable warning naming
    the env knob, so a 10k-source notebook can't pin the request.
  - **Discover models** (`POST /credentials/{id}/discover`) wrapped
    in `asyncio.wait_for` with `ONP_DISCOVER_MODELS_TIMEOUT_SEC`
    (default 30s). OpenRouter's list-models endpoint can paginate
    slowly through 300+ models; this prevents a stuck discovery
    from hanging the Settings UI. Timeout → 504 with env-knob hint.
  - Broader audit of `except Exception` clobber-pattern beyond
    routers (graph code, services, commands) found **0 additional
    instances** — v0.7.109's fix was complete for the typed-error
    surface. Documented in the source.
  - +1 new test (cap truncation warning).
- **v0.7.109** 🐛 **Widespread anti-pattern: `except Exception` clobbered
  typed `HTTPException(404/400/504/etc)` into generic 500s.** Found by
  v0.7.108's chat-timeout test — the v0.7.99 504 was being silently
  rewrapped as 500 by the outer `except Exception` in `execute_chat`.

  A grep audit turned up **25 functions across 13 router files** with
  the same shape:
    ```python
    try:
        ...
        raise HTTPException(status_code=404, ...)   # never reaches client
        ...
    except Exception as e:                          # catches the 404
        raise HTTPException(status_code=500, ...)   # ←  always 500
    ```
  Status codes that would have been lost: 400, 404, 413, 502, 504.
  This bug masked every typed error in the codebase since v0.6.x —
  the frontend's i18n error mapping (`getApiErrorMessage`) couldn't
  match on detail strings, so every failure looked the same.

  Fix: mechanical insertion of `except HTTPException: raise` before
  each unguarded `except Exception` — 89 guards added across
  `chat.py`, `credentials.py`, `embedding.py`, `exports.py`,
  `gmail.py`, `notebooks.py`, `onp.py`, `podcasts.py`, `search.py`,
  `source_chat.py`, `sources.py`, `studio.py`, `transformations.py`.
- **v0.7.108** ✨ **Test for v0.7.99 chat timeout path.** Was deferred —
  added `tests/test_chat_execute_timeout.py` with two tests:
  the 504 path (with env-knob + `/chat/stream` hint validated) and
  the negative-space happy path (fast response is NOT spuriously
  timeout-killed). Caught v0.7.109's pre-existing 500-clobber bug.
- **v0.7.107** ⚡ **Bulk-delete cascade optimization for notebooks.**
  `Notebook.delete()` was sequential `for note in notes: await
  note.delete()` — N+1 serialized roundtrips against the connection
  pool. Replaced with `asyncio.gather(*..., return_exceptions=True)`
  so per-note deletes interleave concurrently. Each `note.delete()`
  still does its own cascade (artifact + note_embedding) per v0.7.76,
  so observability + correctness are preserved. Failed note deletes
  log a warning but don't cancel siblings — partial cleanup is
  better than mid-cascade abort.
- **v0.7.106** ✨ **Bulk per-notebook source vectorize endpoint.**
  `POST /api/notebooks/{id}/vectorize_sources` body
  `{only_missing: bool}`. Recovers from cases where a notebook's
  sources didn't get embedded — v0.7.94 import-time vectorize
  failure, embedding model swap, upgrade from a version without
  semantic search.

  - Skips sources that already have embeddings by default
    (`only_missing=true`); pass `false` to force re-embed.
  - Skips sources with no `full_text` and emits an actionable
    warning ("re-process the source first").
  - Submit failures degrade gracefully — a single source's failure
    becomes a warning, others still queue.
  - Pre-flight check rejects 400 if no embedding model is
    configured (better than letting every queued job fail
    asynchronously).
  - Returns per-source entries with command_ids the caller can
    poll. 6 new tests cover 404 / 400 / only_missing on/off / no-text
    skip / submit-failure resilience.
- **v0.7.105** ✨ **Frontend Export UI** (committed separately by the
  spawned task — see commit 7b0aa34). Wires the v0.7.90 endpoints
  into the dashboard with a directory picker.
- **v0.7.104** 🐛 **Imported sources weren't getting embeddings.**
  v0.7.94 import created Source records via `await source.save()` but
  never called `await source.vectorize()`. Per
  `open_notebook/domain/CLAUDE.md`, `Source.save()` does NOT auto-embed
  (unlike `Note.save()` which does) — so imported sources were saved
  + linked but invisible to vector_search. The "import then chat-with-
  sources" promise was broken for vector-mode chat. Fix: added
  `await source.vectorize()` after `source.add_to_notebook()` with a
  non-fatal try/except wrapper. If the embedding backend is down, the
  import still succeeds with a warning that says how to backfill
  embeddings from Settings → Embeddings later. 2 new regression tests
  (vectorize called on success, vectorize failure surfaces actionable
  warning).
- **v0.7.103** 🛠 **Notebook delete cascade audit (multi-page output).**
  Verified that `Notebook.delete()` correctly cascades through the
  v0.7.89 multi-page notes by walking `self.get_notes()` and calling
  `note.delete()` per note. Each `Note.delete()` (v0.7.76) handles its
  own artifact-edge + note_embedding cascade. Defensive top-level
  `DELETE artifact WHERE out=$notebook_id` then handles any
  detached-but-edged notes. **No regression found.** Documented as
  audited; no code change.
- **v0.7.102** 🐛 **Timeouts on `/search` text + vector endpoints.**
  Vector search makes an embedding-model call on the query string
  (inherits provider-latency risk from v0.7.100); text search hits
  SurrealDB and can hang if the pool is overloaded. Both wrapped in
  `asyncio.wait_for` with `ONP_SEARCH_TIMEOUT_SEC` (default 60s).
  Timeout → 504 with the env-knob name in the detail.
- **v0.7.101** 🐛 **Per-file timeout on Studio `extract_content`.**
  `content_core.extract_content()` can hang on pathological inputs
  (encrypted PDFs missing a password handler, embedded JS in PPTX,
  slow OCR fallback). One bad upload would otherwise pin the entire
  Studio request. Wrapped per-file in `asyncio.wait_for` with
  `ONP_STUDIO_EXTRACT_TIMEOUT_SEC` (default 60s). Timeout → warning
  on that file, other files keep processing.
- **v0.7.100** 🐛 **Timeout on `/credentials/{id}/test`.** The Settings
  UI's "Test connection" button calls
  `connection_tester.test_provider_connection()` which invokes
  `lc_model.ainvoke("Hi")` or `model.aembed(["test"])` against the
  provider. A misconfigured slow provider (e.g. wrong base_url) hung
  the test endpoint indefinitely. Both wrapped with
  `ONP_CONNECTION_TEST_TIMEOUT_SEC` (default 30s — generous for any
  healthy provider including cold-start Ollama). Timeout returns
  `(False, "Connection test timed out…")` with hint to raise the env
  knob if the model legitimately takes longer than 30s for a "Hi"
  prompt.
- **v0.7.99** 🐛 **Audit-sweep timeouts on the last two unbounded LLM calls.**
  Continuation of v0.7.93 + v0.7.95: a broader grep for unbounded
  `ainvoke` calls turned up two more.

  - **`api/routers/studio.py`** legacy single-note fallback path (the
    one reached when the multi-page outline JSON fails to parse) was
    still unbounded. Wrapped in `asyncio.wait_for` re-using
    `_PAGE_TIMEOUT_SEC` (180s default). Timeout → 504 with
    actionable detail + notebook_id preserved for recovery.
  - **`api/routers/chat.py`** non-streaming `/chat` endpoint had no
    timeout. Wrapped with `ONP_CHAT_TIMEOUT_SEC` (default 300s — chat
    graphs do memory recall + tool calls + long generations). Timeout
    → 504 with hint to use `/chat/stream` for token-by-token responses.
  - The streaming SSE endpoints (`/chat/stream`, `/source/{id}/chat`,
    `/search/ask`) are intentionally NOT wrapped — they're naturally
    bounded by client-disconnect detection via `is_disconnected()`
    (v0.7.50+) and `reader.cancel()` (v0.7.50).
- **v0.7.98** ⚡ **Configurable zip compression for notebook export.**
  `NotebookExportRequest.compression: 'deflated' | 'stored' | 'bzip2'
  | 'lzma'` (default `'deflated'`, matching prior v0.7.90 behavior).
  Useful when the zip will itself be compressed downstream
  (`stored`), or when archive size matters more than write speed
  (`bzip2`, `lzma`). All four algorithms are stdlib — zero new deps.
  4 new tests cover each option + Pydantic rejection of typos.
- **v0.7.97** ✨ **HTML export format for notebooks.** `format='html_folder'`
  or `format='html_zip'` renders each note's markdown to HTML via
  `markdown-it-py` (already a transitive dep — zero new deps). Each
  file is a self-contained HTML5 document with a minimal stylesheet
  (light + dark mode) so users can double-click and read in a browser
  without a build step.

  - GFM features: tables, strikethrough enabled. `linkify` deliberately
    OFF because it requires an extra runtime package.
  - Frontmatter rendered as a styled `<div class="onp-frontmatter">`
    metadata block at the top — keeps parity with the markdown export.
  - `_html_escape` applied to titles + asset paths to prevent
    `<script>` injection through note titles.
  - Folder and zip variants share the same renderer-selection logic
    (`_retype` helper) so the on-disk layout matches the markdown
    counterpart with `.html` instead of `.md`.
  - 3 new tests cover html_folder + html_zip + attribute-position
    escaping.
- **v0.7.96** ✨ **Import preview endpoint (dry-run).** `POST
  /api/notebooks/import/preview` reads the source bundle (folder /
  zip / single .md), parses frontmatter, and returns the planned
  imports WITHOUT touching the database. Lets the frontend show a
  confirmation UI before the user commits.

  - Same caps + safety rails as v0.7.94 import (50 MB total, 5 MB
    per-file, 500-entry, zip-traversal rejection).
  - Surfaces manifest-derived hints (`notebook_name_hint`,
    `description_hint`) so the UI can pre-fill the rename form.
  - Flags overview-shaped notes (`is_overview: true`) for special UI
    treatment.
  - 5 new tests: folder + zip + single-md detection, missing-path 404,
    empty-bundle warning, and a test that asserts the preview NEVER
    calls Notebook/Note/Source `save()` (raising-stub harness).
- **v0.7.95** 🐛 **Timeouts on the two remaining unbounded LLM calls.**
  Audit pass after v0.7.93 found two more endpoints where a stuck
  local LLM could hang the request indefinitely (same class of bug,
  scope was Studio-only before).

  - **`POST /api/notes`** auto-title generation now wraps
    `prompt_graph.ainvoke` in `asyncio.wait_for` (default 60s,
    `ONP_NOTE_TITLE_TIMEOUT_SEC`). On timeout: graceful degradation
    — falls back to the first line of the note's content as the
    title rather than 500ing the create-note request. User still
    gets their note.
  - **`POST /api/transformations/execute`** wraps
    `transformation_graph.ainvoke` (default 180s,
    `ONP_TRANSFORMATION_TIMEOUT_SEC`). On timeout: returns 504
    Gateway Timeout with the env-knob name in the detail.
  - No new tests — existing transformation + notes tests don't mock
    the graph (they go straight to the real graph which would be too
    slow). The behavior is exercised by the same harness as v0.7.93
    once the codebase grows graph-level integration tests.
- **v0.7.94** ✨ **Notebook import endpoint** (reverse of v0.7.90 export).
  Folder, single .md, or .zip → new or existing Notebook. Closes the
  loop on the export feature for backup/restore + cross-machine
  workflows + Obsidian/Logseq library ingestion.

  - **`POST /api/notebooks/import`** body `{source_path, mode: 'new' |
    'into_existing', target_notebook_id?, new_name?, import_sources?}`
    → `{notebook_id, notebook_name, mode, note_ids, source_ids,
    file_count, items[], warnings[]}`
  - **Frontmatter round-trips**: `_render_note_content` (export) and
    `_parse_frontmatter` (import) agree on the YAML-ish title/type/
    created/updated/id format, so an export → import cycle preserves
    note titles (verified by a dedicated round-trip test).
  - **Manifest-aware**: if the import bundle contains a `manifest.json`
    (written by v0.7.90's export), the notebook's `name` and
    `description` are seeded from it.
  - **Safety caps**:
    - 50 MB total source cap (`_MAX_IMPORT_BYTES`)
    - 5 MB per-file cap (`_MAX_IMPORT_FILE_BYTES`)
    - 500-entry cap inside a folder/zip
    - Rejects zip members with absolute or `..` paths (traversal
      defense)
    - Skips non-UTF-8 files silently rather than crashing
  - **`import_sources=true`** rebuilds Source records from any
    `sources/` subfolder (text-only Sources, since the original binary
    file isn't shipped in the export).
  - **9 new tests**: happy-path folder/zip/single-md, mode='new' vs
    'into_existing', 404 / 400 / 413 guard rails, traversal zip
    rejected, round-trip preserves titles.
- **v0.7.93** 🐛 **Per-page generation timeout for Studio multi-page.**
  Local LLMs (especially the desktop bundle's llama-cpp chat server)
  can hang indefinitely mid-prompt-eval or while loading. Without a
  cap, ONE stuck page blocks the entire notebook-generation request
  including subsequent pages, the response, and the user's browser.

  - **Outline pass**: `asyncio.wait_for` with default 90s
    (`ONP_STUDIO_OUTLINE_TIMEOUT_SEC`). Timeout → `504 Gateway Timeout`
    with the env-knob name in the detail (actionable, not a wall of
    stack trace).
  - **Per-page generation**: default 180s (`ONP_STUDIO_PAGE_TIMEOUT_SEC`).
    Timeout → that page becomes a warning naming the page AND pointing
    at the env knob; other pages still ship.
  - **3 new tests** cover page-timeout-becomes-warning, outline-timeout
    returns 504 with actionable detail, and pre-existing pages survive
    a sibling page's timeout.
- **v0.7.92** ✨ **Optional parallel page generation for Studio.**
  `ONP_STUDIO_NOTEBOOK_PARALLEL_PAGES=true` runs page LLM calls
  concurrently via `asyncio.gather(return_exceptions=True)` for ~Nx
  speedup on cloud LLMs (OpenAI, Anthropic, etc.). Default OFF to
  protect local llama-cpp dual-server setups from OOM / token
  starvation. New `_generate_all_pages` helper extracted from
  `_dispatch_notebook_mode` so the loop body is testable in isolation.
  2 new tests verify the knob actually changes concurrency (peak
  in-flight > 1 when on, == 1 when off).
- **v0.7.91** 🐛 Fix loguru %-format bug across the codebase — 18
  occurrences in 8 files (`api/routers/studio.py`,
  `api/routers/chat.py`, `api/routers/podcasts.py`,
  `api/routers/source_chat.py`, `commands/podcast_commands.py`,
  `open_notebook/database/dedup_edges.py`,
  `open_notebook/utils/memory_recall.py`,
  `open_notebook/ai/models.py`) were logging the literal `%s` / `%r` /
  `%d` since v0.7.0 because loguru uses `str.format()` (`{}`-style),
  not `%`-style. Converted to `{}`-style placeholders with `{!r}` for
  repr. No behavior change beyond accurate log output. Full suite
  (469 tests) still passes.

- **v0.7.90** ✨ **Filesystem export + native directory access.** Users can
  now save notebooks and individual pages out of the app to anywhere on
  their host filesystem (the desktop bundle runs natively on macOS /
  Windows so file access is unrestricted; Docker deployments work too,
  bounded to mounted volumes).

  - **`POST /api/notebooks/{id}/export`** writes a notebook as a folder
    (one `.md` per note) or a single `.zip` archive.
    - Folder layout: `00-overview.md` (auto-detected from v0.7.89's
      "📋 00 · …" overview notes) + `01-{slug}.md` … `NN-{slug}.md` +
      optional `sources/{source-id}.md` + `manifest.json`.
    - Slug logic strips emoji, non-ASCII, AND leading numeric prefixes
      so v0.7.89 page titles ("📄 01 · Architecture") don't end up
      double-indexed (`01-01-architecture.md`).
    - Pre-flight overwrite check: refuses to half-clobber existing files
      unless `overwrite=true` is passed.
    - Manifest captures notebook + per-note metadata for downstream
      tools (Logseq, paperless-gpt, future ONP re-import).
  - **`POST /api/notes/{id}/export`** writes a single note as a `.md`
    file. Auto-appends `.md` if the caller omits the extension.
  - **`GET /api/fs/list?path=…`** lists a directory on the host
    filesystem (entries sorted dirs-first, capped at 500 to prevent
    huge-directory DoS, hidden files excluded by default).
  - **`GET /api/fs/home`** returns the user's home + Desktop /
    Documents / Downloads / default-exports paths in one call so the
    frontend picker doesn't have to figure them out per-platform.
  - **`POST /api/fs/mkdir`** creates a directory (idempotent — re-runs
    on an existing dir return `created=false`).
  - **Safety:** all paths normalized via `Path.resolve()` and rejected
    if they fall under a system-root prefix (`/etc`, `/System`, `/proc`,
    `/Windows`, etc.). Not a security boundary — the user owns the
    process — but prevents accidentally surfacing those locations in a
    picker UI. All routes are auth-gated by the existing
    `PasswordAuthMiddleware`.
  - **Tests:** 33 new tests across `tests/test_filesystem_router.py`
    and `tests/test_exports_router.py`. Folder + zip + single-note paths
    each have happy-path + overwrite + 404 + 409 + 403 coverage. Full
    suite: **465 pass, 0 fail.**
- **v0.7.90 audit findings (during the build):**
  - `api/routers/filesystem.py` deliberately exposes broad host-FS
    access. This is appropriate for the desktop bundle (user owns the
    machine) but **operators deploying the API publicly should ensure
    `OPEN_NOTEBOOK_PASSWORD` is set to a strong value** — otherwise an
    unauthenticated request can list arbitrary directories under the
    API process's UID. Filed as a doc-update task; the README already
    flags Docker compose as "set passwords + encryption key before
    exposing" but the new filesystem endpoints make that warning more
    pressing.



- **v0.7.89** ✨ **Studio multi-page notebook output.** When the user uploads
  one or more documents via `/studio/generate` (mode `notebook` or `both`),
  the API now produces a **structured multi-page brief** instead of a single
  blob of markdown.

  - **Outline pass:** one LLM call → JSON `{headline, summary, pages[],
    top_suggestions[]}`. Constrained to 3-{`ONP_STUDIO_NOTEBOOK_PAGES_MAX`,
    default 6} distinct pages.
  - **Per-page pass:** one LLM call per page (sequential — parallel would
    starve llama-cpp-python on the desktop bundle), each ending with a
    "💡 AI Suggestions for this page" block with 3-5 concrete recommendations.
  - **Persistence:** N+1 notes saved to the notebook — "📋 00 · {title} —
    Overview" (headline + executive summary + table of contents + top
    suggestions) followed by "📄 NN · {page title}" for each page.
  - **Graceful degradation:**
    - Outline JSON un-parseable → fall back to legacy single-note output,
      warning surfaced in response.
    - Individual page LLM call fails → that page becomes a warning, other
      pages still ship.
    - `ONP_STUDIO_NOTEBOOK_MULTIPAGE=false` → bypass multi-page entirely
      (kill switch for rollout).
  - **Response shape:** `StudioGenerateResponse` gains `note_ids: list[str]`.
    `note_id` still points at the Overview note for back-compat with the
    v0.7.88 frontend.
  - **Tests:** existing 19 studio router tests updated for the new
    multi-pass flow; 3 new tests cover multi-page happy path, JSON fallback,
    and the `_MULTIPAGE_ENABLED=False` kill switch. Full suite: **432 pass.**
- **v0.7.88** ✨ **Studio `mode="both"`.** Single upload can now generate
  BOTH a notebook AND a podcast. `_dispatch_both_modes` runs the notebook
  pipeline synchronously then submits the podcast job; either half can fail
  independently with the surviving artifact preserved and the failure
  reported in `warnings`. Validation gate ensures podcast profile fields
  are supplied when mode is `podcast` OR `both`. The frontend selects
  this mode via the existing mode picker.
- **v0.7.89 audit findings (intentionally noted, not fixed this turn):**
  - `api/routers/studio.py` lines 375, 389, 406-410, 421, 423 use
    `logger.{info,warning,exception}("…%s…%s…", a, b)` patterns — loguru
    uses `{}` formatting, NOT `%`. These messages have been logging the
    literal `%s` since v0.7.0. New v0.7.89 lines use `{}` correctly; the
    legacy `%s` lines are a separate cleanup. Filed.



Planned cycle covering native async LangGraph (v0.7.37), real token
streaming via SSE (v0.7.38), list virtualization (v0.7.39), component
splitting for the four 500+ LOC files (v0.7.40), and sub-`lg`
responsive polish (v0.7.41). Tuned for local-LLM deploys with 2–5
test users.

Hardening run (v0.7.49–v0.7.56) closed eight reliability bugs
uncovered by a follow-up audit pass:

- **v0.7.49** useSourceChat streaming — TextDecoder `{stream:true}`
  for UTF-8, cross-read line buffer, per-send `crypto.randomUUID()`
  IDs, AbortError filter by exact id pair, 4 MiB defensive cap.
- **v0.7.50** useNotebookChat — AbortController + mid-stream
  mountedRef guard; chat.ts `reader.cancel()` before `releaseLock()`
  so FastAPI's `is_disconnected()` actually fires.
- **v0.7.51** SourcesColumn infinite-scroll — read scroll metrics
  from `e.currentTarget` instead of a ref pinned to CardContent;
  fixes silently-dead infinite scroll past the 50-source
  virtualization threshold.
- **v0.7.52** API lifespan + chat graph — `asyncio.wait_for(timeout=10)`
  around DB pool warm-up acquires; chat-stream `on_chain_end` accepts
  Pydantic state shape via `getattr` fallback; dead `last_token_idx`
  removed.
- **v0.7.53** /search/ask — `is_disconnected()` per astream_events
  tick (parity with /chat/stream).
- **v0.7.54** Propagate v0.7.49/v0.7.50 fixes to useSourceChat error
  path + use-ask reader.cancel.
- **v0.7.55** podcast_service + command_service — `asyncio.to_thread`
  wrap around synchronous `submit_command`; /search/ask/simple gets
  disconnect check + state-shape guard.
- **v0.7.56** source_chat state-shape guard; SourceCard
  refresh-timeout ref+cleanup.
- **v0.7.57** repository `_release` decrements `_pool_total` under
  `_pool_lock` (matches v0.7.24 acquire-side discipline). Closed a
  slow drift that could wedge the pool under concurrent broken
  releases.
- **v0.7.58** Launcher drain threads now joined before log files
  close (preserves crash-cause tails of surreal/api logs). Bare
  `except: pass` in stop_all replaced with debug logging.
  podcast_service maps ValueError → 400 instead of swallowing into
  500.
- **v0.7.59** useTheme computes effectiveTheme client-side
  post-mount (no more SSR hydration mismatch). NoteEditorDialog
  MutationObserver scoped to the editor wrapper instead of
  document.body. useNotebookChat deleteSession reads from the
  TanStack cache instead of a stale outer closure.
- **v0.7.60** Two pre-existing edge-table query bugs uncovered by
  the third audit pass: the `add source to notebook` idempotency
  check on `reference` was inverted (every link call created a
  fresh duplicate edge → source_count inflated forever); the
  source-retry endpoint queried non-existent `source`/`notebook`
  columns on the `reference` edge table so EVERY retry hit the
  "not associated with any notebooks" 400 and retry was
  effectively dead.
- **v0.7.61** `graphs/source.transform_content` returned None when
  `source.full_text` was empty — LangGraph then tried `[] + None`
  for the `Annotated[list, operator.add]` reducer and crashed the
  whole graph run, leaving sources half-saved. Now returns
  `{"transformation": []}`. Notebook.delete cascades chat sessions
  (was orphaning `chat_session` records with dangling notebook
  references after deletion).
- **v0.7.62** Connection-pool shutdown safety: close_pool waits up
  to ~2 s for checked-out connections to drain before nulling
  state, and `_release` no longer asserts on `_pool is None` (it
  just closes the conn directly), so FastAPI lifespan shutdown
  exits cleanly even when requests are mid-flight. Plus one more
  asyncio.to_thread wrap on a sync surreal_commands.submit_command
  call in the run-transformation endpoint.
- **v0.7.63** `require_encryption_key` + provider-status reporting
  accept either OPEN_NOTEBOOK_ENCRYPTION_KEY (singular) or
  OPEN_NOTEBOOK_ENCRYPTION_KEYS (plural rotation list), matching
  the encryption utility and the lifespan check. Rotation-only
  deployments no longer hit phantom "Encryption key not configured"
  errors on migration endpoints.
- **v0.7.64** NotebookPage contextSelections is now pruned to the
  current sources/notes list on every render and fully cleared on
  notebookId change — stale IDs from navigation or deletion no
  longer leak into chat context-building. GET /insights/{id} now
  returns 404 (not 500) when the referenced source has been
  deleted out from under the insight (orphan-record case).
- **v0.7.65** chat.py + source_chat.py size their LLM-budget
  check against the actual message text (`.content` joined), not
  `str(payload)`'s repr of the Message list. The wrapper repr
  added ~80-120 chars of boilerplate per message — 50-turn
  sessions overshot the 105k large_context cutoff by ~5k phantom
  "tokens" and got rerouted to the long-context fallback model
  earlier than necessary.
- **v0.7.66** Local-LLM hardening: per-message char cap
  (default 24k chars ≈ 6k tokens, overridable via
  ONP_CHAT_MESSAGE_CHAR_CAP) inside trim_message_history. A single
  giant paste no longer crashes a 16k-context local model
  mid-stream — the message is kept but its content is truncated
  with a "[…content truncated…]" marker. Two new error_classifier
  rules: explicit "model is still loading" mapping (HTTP 503 cold-
  start during weight load, or 200 with JSON `{"error":"model not
  loaded"}` from LM Studio / vLLM) → "Please wait a few seconds
  and try again", and an updated NetworkError message that
  mentions local servers explicitly.
- **v0.7.67** Launcher now logs a clear WARNING when it skips
  spawning the chat LLM server (no chat GGUF configured, or file
  missing at the configured path). Previously it silently
  returned and the user got no signal that memory-writer features
  would be inert.
- **v0.7.68 + v0.7.70** Memory writer fully wired into the chat
  router. The `memory_extract_turn` + `memory_summarize_session`
  handlers were registered at the worker side since v0.7.47 but
  NOTHING in the chat path ever submitted them — the feature was
  inert. /chat/execute and /chat/stream now fire
  memory_extract_turn fire-and-forget after each turn's
  session.save(); DELETE /chat/sessions/{id} fires
  memory_summarize_session before deleting the session record.
  Both helpers gate on the MEMORY_* env vars being set so upstream
  non-desktop builds silently no-op, run via asyncio.to_thread to
  keep the event loop free, and swallow all failures at debug
  level (memory is best-effort).
- **v0.7.69** Hardened podcast `generate_podcast_command`'s return
  block against None or partial-shape result from podcast-creator's
  create_podcast(). The earlier `episode.audio_file = ...` block
  was fixed in v0.7.3 but the function's terminal output
  construction still used `result["transcript"]` / `result["outline"]`
  subscript inside a `result.get(...)`-truthy ternary — would
  AttributeError when `result is None`, masking a successful-but-
  transcript-less generation as a worker crash that retry=1
  couldn't recover from.
- **v0.7.71** Memory READ path. Chat now injects the most recent
  facts + preferences (capped 15 + 10) into the system prompt via
  a new `# WHAT YOU REMEMBER ABOUT THE USER` section in
  `chat/system.jinja`. With v0.7.68/70 the writer was already
  populating `memory_fact` / `memory_preference` tables every
  turn; this commit closes the loop so the assistant actually
  recalls them on the next turn. Direct SurrealQL SELECT … LIMIT
  for safety + speed (no embedder round-trip, tolerates missing
  tables on fresh installs). Single-user deploys with tens of
  facts get "what I've learned about you lately" — no semantic
  filtering needed.
- **v0.7.72** Podcast retry validates the referenced episode +
  speaker profiles BEFORE the destructive audio/episode deletes.
  Previously a rename or delete of the profile after the original
  submission meant the user lost their failed episode record AND
  couldn't retry — the 400 from submit_generation_job's
  EpisodeProfile.get_by_name fired after the cleanup had already
  run. Now the 400 lands without side effects, naming the missing
  profile and pointing the user at the fix.
- **v0.7.73** Defensive dedup at the domain layer for
  `Source.add_to_notebook` and `Note.add_to_notebook`. v0.7.60
  fixed the idempotency check on the HTTP endpoint but the domain
  methods still called `self.relate()` unconditionally — direct
  calls from upload / studio / notes-create paths could create
  duplicate reference / artifact edges on retry. Now both check
  for an existing edge with the same in/out pair before
  relating; same query direction as the HTTP endpoint so behavior
  is symmetric. (Existing dupes in old DBs are NOT cleaned by
  this commit; that would be a separate migration.)
- **v0.7.74** 14 new unit tests for v0.7.71's memory_recall
  (pure-render path). Tests caught a real bug in `_coerce_text` —
  `{"text": None}` returned the string "None" because the dict
  branch unconditionally `str()`-coerced the inner value. Now
  explicit None check ⇒ empty string ⇒ filtered out upstream so
  the prompt never gets a `- None` bullet line. Backend suite
  now 416 passing.
- **v0.7.75** Finishing sweep of v0.7.65's str(payload) sizing
  fix: transformation.py + prompt.py both still passed
  `str(payload)` to provision_langchain_model, overcounting by
  the same ~80-120 chars of wrapper boilerplate per message.
  Both now extract `.content` per message and pass the plain
  string. /transformations/execute endpoint also gets the
  isinstance/getattr dual-path output access from v0.7.52/55/56
  for symmetry.
- **v0.7.76** Domain hardening in open_notebook/domain/notebook.py:
  Source.vectorize, Source.add_insight, and Note.save all called
  the sync surreal_commands.submit_command directly inside
  `async def` (same blocking-event-loop bug as v0.7.55/57/62) —
  all three now wrap in asyncio.to_thread. Plus Source.delete
  now cascades `reference` edges (was already cleaning
  source_embedding + source_insight) so the notebook sources
  view doesn't crash on `Source(**None)` after a delete. Note
  gets a new delete override that cascades `artifact` edges +
  `note_embedding` rows — symmetric to Source and to the v0.7.61
  Notebook.delete chat_session cascade.
- **v0.7.77** Four MORE sync submit_command sites in
  commands/embedding_commands.py: create_insight (per-insight
  fire-and-forget) and rebuild_embeddings' three submit loops
  (source / note / insight). The rebuild path under
  /advanced/rebuild-embeddings fired hundreds-to-thousands of
  sync SurrealDB WS handshakes back-to-back, blocking the
  worker's event loop for the entire rebuild and starving any
  concurrent commands (chat memory extracts, podcast generation)
  the same worker was servicing. All four now wrapped in
  asyncio.to_thread.
- **v0.7.78** Source-chat now recalls memory facts/preferences in
  its system prompt — parity with v0.7.71's wiring for the main
  chat. Without this, the assistant would surface a remembered
  preference inside the main chat but forget it the moment the
  user clicked into a source-chat session. Tighter prompt framing
  for source-chat ("Stay focused on the source; only weave a
  memory note in when it directly helps the answer") since the
  source-chat system prompt already carries up to ~3.5k tokens
  of source + insight context.
- **v0.7.79** Three bare setTimeout sites in the frontend now
  cancel cleanly on unmount: EmbeddingModelChangeDialog's
  500 ms pre-redirect timer, GeneratePodcastDialog's 500 ms
  post-submit close timer, and SourceDetailContent's 5 s
  insight-fallback refresh timer. Same defect pattern fixed in
  SourceCard (v0.7.56) — without cleanup the timer fired
  navigation / mutation / invalidate against an already-unmounted
  component when the user dismissed mid-window.
- **v0.7.80** `insightsApi.waitForCommand` now accepts an
  AbortSignal and uses an abortable-sleep helper between polling
  attempts. SourceDetailContent wires a per-component
  AbortController that gets aborted on unmount, so navigating
  away mid-poll stops the 4-minute polling loop within
  milliseconds instead of continuing to hit
  `/commands/jobs/{id}` and triggering downstream cache
  invalidation on a dead React subtree.
- **v0.7.81** Two more reliability fixes. `_send_digest_now` now
  wraps the post-send `g.save()` in try/except so a successful
  Gmail send followed by a DB persist failure doesn't cause the
  scheduler to send the same digest again on the next tick (the
  email already went out; the duplicate-send window is bounded
  to one tick instead of every tick until DB recovery).
  `notes.create_note` AI-title generation now uses the
  `isinstance(result, dict)` → `.get` / `getattr` dual-path on
  the prompt graph's ainvoke output, matching the standing
  state-shape guard pattern in chat.py / search.py /
  source_chat.py / transformations.py.
- **v0.7.82** Cleanup pass: 7 ruff E702 errors in
  desktop/tests/test_launcher.py fixed (split semicolon-style
  multi-statement lines). Launcher stop_all log lines now use
  `getattr(p, "pid", "?")` so MagicMock-spec'd Popens in the
  desktop test suite stop raising AttributeError on teardown
  (three desktop tests were broken since v0.7.58 — undetected
  because `desktop/tests/` isn't in the main pytest path). Two
  remaining cosmetic copy-success setTimeout sites
  (MessageActions.tsx, SourceDetailContent.tsx:324) now use the
  standard useRef + useEffect cleanup pattern — finishes the
  setTimeout sweep so every timer in the frontend has unmount
  cleanup.
- **v0.7.83** Memory worker inherits the session model_override
  (deferral #3 closed). `_fire_memory_extract_turn` and
  `_fire_memory_summarize_session` now accept an optional
  model_override and thread it through to the surreal_commands
  payload; `memory_extract_turn` / `memory_summarize_session`
  worker handlers accept it as an optional kwarg and pass it to
  `_build_clients`. Resolution: caller override → ONP_CHAT_MODEL_NAME
  → "default". Backward-compatible (older queued rows still
  dispatch).
- **v0.7.84** Semantic memory recall (deferral #2 closed). New
  `recall_relevant_memory(query)` does cosine similarity over
  mem0's embedding column using the same SurrealQL idiom
  (`vector::similarity::cosine`) the mem0 retriever already uses.
  New `recall_memory(query)` orchestrator picks recency vs
  semantic based on `ONP_MEMORY_RECALL_MODE` (recent | semantic |
  auto, default auto) and a row-count threshold (30) — small
  stores stay on recency (saves an embed round trip), large stores
  switch to semantic. Any semantic-path failure falls through to
  recency so chat never breaks. 7 new orchestrator tests in
  tests/test_memory_recall.py; total backend suite now 423.
- **v0.7.85** Legacy edge deduplicator (deferral #1 closed).
  New `open_notebook/database/dedup_edges.py` runs once per API
  startup, finds duplicate (in, out) groups on the `reference` and
  `artifact` edge tables, keeps the lexicographically-smallest id
  per group, and deletes the rest. Idempotent (clean DB → no-op
  one SELECT per table) and non-fatal (per-edge DELETE failures
  don't abort the rest of the cleanup; per-table SELECT failures
  don't block other tables). Runs in api/main.py lifespan after
  schema + podcast migrations. 7 new tests in
  tests/test_dedup_edges.py covering clean DB, single group,
  partial failure, multi-table isolation, and idempotent re-run.
  Backend suite now 430.
- **v0.7.86** Model.delete clears DefaultModels references +
  warns on profile use (improvement found during the models.py
  router audit — no bugs uncovered in the router itself, all
  standard patterns were clean). Previously deleting a model
  left dangling references in the `default_models` singleton
  and in any episode_profile / speaker_profile that used it.
  Override clears default-fields proactively (lookups land on
  the cleaner "no default configured" path instead of the
  dangling-fetch path) and warn-logs each podcast profile that
  still references the model — deliberately not auto-cleared
  since reassigning a profile's model is a UX choice the user
  should make.
- **v0.7.87** commands router audit uncovered three stub
  implementations returning success without doing the work:
  `cancel_command_job` was a no-op (frontend trusted "cancelled:
  true" and removed the job from the UI while the command kept
  running); `list_command_jobs` was a stub returning [] so the
  jobs panel was always empty; `get_command_status` returned a
  synthetic `{"status": "unknown"}` for missing jobs so the
  router served fake-OK 200 responses. Now: cancel writes the
  `canceled` signal via the same pattern Source.delete uses
  (v0.7.32), list queries the `command` table with SurrealQL
  filters, and missing jobs return real 404 (status endpoint)
  or 404/409 (cancel endpoint).

---

## v0.7 — Local-deploy hardening + podcast workflow + UX

The v0.7 line was an audit-driven sweep. Every release pinned a
single concern, shipped with regression tests, and (almost) never
touched files outside its scope. Suite grew from ~485 → 626 backend
tests + 29 frontend tests.

### v0.7.36 — CHANGELOG.md (this file)
🛠 Introduces a Plus-fork-scoped changelog. The 64 commits between
v0.6.0 and v0.7.35 were well-described per-commit but unrepresented
at the project level.

### v0.7.35 — Power-user polish
🎨 Filter row on `/settings/api-keys` (substring + status chips: All /
Has credential / From env / Unconfigured).
🎨 Three keyboard shortcuts beyond `⌘K`:
- `⌘N` → new notebook
- `⌘U` → upload source
- `⌘/` → jump to `/search`

### v0.7.34 — App UX gaps
🎨 `/sources` empty state has a CTA; header has an "Add" button.
🐛 Chat session-delete jumps to next session inline (was flicker
through empty state).
🐛 Central 5xx error toasts at axios interceptor (deduped 5s window),
points users at the rotated log file.

### v0.7.33 — Podcast UX
🎨 Episode duration in header (`~14 min` estimate from
`num_segments × 2`, replaced by real `M:SS` once metadata loads).
🎨 Generation-stage indicator under title: "Generating outline…" →
"Drafting transcript (3/7 segments)…" → "Synthesizing speech…".
Derived from existing fields; zero backend change.

### v0.7.32 — Three critical bugs
🐛 **Speaker presets**: cloud-only migration 7 presets broke every
podcast generation on local Piper installs. New
`desktop/auto_register/speaker_profile.py` registers 4 local-Piper
presets (Local Duo / Solo / Debate / Interview).
🐛 **SQLite WAL + integrity check**: both chat graphs opened
unsynchronised `sqlite3.connect()` → concurrent writers hit
`database is locked`. New shared
`open_notebook/utils/sqlite_checkpoint.py` enforces WAL +
`busy_timeout=5000` + corruption rename-aside recovery.
🐛 **Source.delete cancels in-flight commands**: deleting mid-embed
left worker writing fresh `source_embedding` rows pointing at a
now-dead source. `update_command_result(status="canceled")` fires
before file/embedding cleanup.

### v0.7.31 — Podcast auto-suggest
✨ `POST /api/podcasts/suggest` — heuristic recommender. Returns
best-fit episode profile + length + title + briefing addition based
on source titles + topics + total content volume. No LLM call —
pure keyword scoring. Frontend "✨ Auto-fill from sources" button.

### v0.7.30 — Podcast preset library expansion
✨ Auto-register seeds 9 episode profiles instead of 1: Open Notebook
Plus Local, Deep Dive, Quick Brief, Debate, Tutorial, Story Mode,
News Roundup, Q&A Interview, Recap & Review. Each carries a
distinct `default_briefing` shaping outline LLM behavior.

### v0.7.29 — Dashboard Command Center
✨ `(dashboard)/page.tsx` was `redirect('/notebooks')`. Now a real
landing page: quick actions (Studio / Notebook / Podcast / Ask), live
system status from `/readyz` (30s refresh), recent notebooks with
relative timestamps, ⌘K hint + data-location footer.

### v0.7.27 + v0.7.28 — Design system + sidebar polish
🎨 Semantic tokens (`--success`, `--warning`, `--info`), motion scale
(`--motion-fast/base/slow/spring`), elevation scale (`--shadow-xs`
through `--shadow-xl`). Sidebar refined: 3px left-edge accent bar
(replacing broken `scale-[1.02]`), exact-or-child route matching,
SSR-safe `⌘K` keyboard hint.

### v0.7.23 + v0.7.25 + v0.7.26 — UX bug pass
🎨 Studio wrapped in AppShell (was the only dashboard page without
a sidebar).
🐛 v0.7.25 fixed 10 visual bugs: notebook-detail loading drops sidebar,
GeneratePodcastDialog clips body, `break-all` in chat bubbles,
hardcoded blue link contrast, AddSourceDialog no max-height, sidebar
hover scale tear, notebook column flex math overflow at 1024px,
CommandPalette orphan heading + missing empty state, Studio drop
zone missing focus ring, SetupBanner hardcoded palette colors.
🐛 v0.7.26: chat optimistic rollback uses `crypto.randomUUID()` (was
`Date.now()` → duplicate keys + cross-message wipe on partial fail).

### v0.7.24 — Backend bug batch (7 bugs)
🐛 Worker process never used v0.7.14 file logger.
🐛 Startup encryption check ignored v0.7.17 plural env var.
🐛 DB pool race grew `_pool_total` beyond cap.
🐛 Source retry double-prefixed `command:` RecordID.
🐛 Launcher polled `/health` (always 200) instead of `/readyz`.
🐛 MultiFernet cache survived uvicorn reload (rotation bug).
🐛 Log fallback put logs at `cwd/.logs` inside Docker.

### v0.7.14 → v0.7.22 — Reliability foundation

| Tag | Theme |
|---|---|
| v0.7.14 🛠 | Rotated file logging via loguru (`~/.open-notebook-plus/logs/api.log`, 20 MB rotation, 14d retention, gzip). |
| v0.7.15 🛠 | `/livez` + `/readyz` real health probes (DB ping + migration check). |
| v0.7.16 🔒 | `/api/sources` upload byte cap (default 500 MB, `ONP_SOURCE_UPLOAD_MAX_BYTES`). |
| v0.7.17 🔒 | Encryption key rotation via `MultiFernet` + `OPEN_NOTEBOOK_ENCRYPTION_KEYS=new,old` + sweep. |
| v0.7.18 ⚡ | SurrealDB connection pool (default 4). 50-200ms saved per chat turn. |
| v0.7.19 🔒 | CVE bumps: `aiohttp` ≥3.11.18, `llama-cpp-python` ≥0.3.16, `pyinstaller` ≥6.13.0. |
| v0.7.20 🛠 | All 22 `ONP_*` env vars documented in `.env.example` + `docs/5-CONFIGURATION/onp-env-reference.md`. |
| v0.7.21 🛠 | Deleted 12 dead service modules + `api/client.py` (~1832 LOC removed). |
| v0.7.22 🛠 | Operator runbook at `desktop/operating-locally.md`. |

### v0.7.0 → v0.7.13 — LLM context-overflow caps

The headline v0.7 capability: every LLM-bound prompt is now bounded
by an env-configurable char cap. No more silent context overflows on
a 16k-token local server.

| Tag | What got capped / what shipped |
|---|---|
| v0.7.0 ✨ | Studio one-shot workflow (upload → notebook or podcast). |
| v0.7.1 🔒 | Studio code-review fixes (7 issues, 6 tests). |
| v0.7.2 🔒 | Podcast audio path-traversal hardening; 404 preservation on delete. |
| v0.7.3 🐛 | podcast_commands KeyError + orphan output dir cleanup. |
| v0.7.4 ⚡ | Studio file/combined char caps env-configurable (15k / 60k defaults). |
| v0.7.5 🐛 | Memory writer LLM-call robustness (broad except + defensive `.get()`). |
| v0.7.6 🐛 | Legacy SurrealDB WebSocket URL (`ws://host:port/rpc`, was malformed). |
| v0.7.7 🔒 | Piper TTS input cap (50k chars / ~10 min audio). |
| v0.7.8 ⚡ | Chat LLM `n_ctx` env-configurable (`ONP_CHAT_LLM_CTX`, default 16384). |
| v0.7.9 🐛 | Ask graph per-result content cap (1500 chars × 10 results default). |
| v0.7.10 🐛 | Transformation input cap (12k chars default). |
| v0.7.11 🐛 | Chat message history cap (12k chars, oldest dropped with marker). |
| v0.7.12 🐛 | source_chat: source + per-insight + max-insight count caps. |
| v0.7.13 🐛 | source_chat history cap + shared helper extraction. |

---

## v0.6 — 35-commit bug-fix run

A single sprint closing 35 concrete bugs surfaced by code review +
operator pain. Highlights:

- **v0.6.34** 🔒 `Source.delete()` refuses to unlink files outside `UPLOADS_FOLDER`.
- **v0.6.32** 🐛 `useSourceChat` wired the AbortController it had been creating but never assigning.
- **v0.6.31** 🔒 `model_manager` DELETE path-traversal hardening via `is_relative_to`.
- **v0.6.30** 🐛 `_ensure_credential` no longer silently falls through to duplicate-create.
- **v0.6.27** 🔒 Atomic write for `capture_state.json`.
- **v0.6.24** 🐛 `useNotebookChat` — out-of-order race in context-count effect.
- **v0.6.21** 🐛 Voice idempotency — was creating duplicates on every launch.
- **v0.6.20** 🐛 `/login → /login` redirect loop guard.
- **v0.6.16** ⚡ Stream file uploads in 1 MiB chunks (was buffering entire file).
- **v0.6.11** 🐛 Cross-platform RAM probe — Windows chat-model regression.
- **v0.6.10** ⚡ Stop chat/source-chat invoke from blocking the event loop.
- **v0.6.8** 🔒 `config.toml` perms restricted to `0600` + atomic write.
- **v0.6.7** 🔒 Constant-time password comparison + Unicode crash fix.

Run `git log --oneline --grep "v0.6\." desktop-app` for the full list.

---

## Conventions

- Versioning: per-commit patch increments; major boundaries reserved
  for substantial user-facing shape changes.
- Tests: every commit ships regression coverage. Suite grew from
  ~485 → 626 backend tests + 29 frontend tests across the session.
- For env-var documentation: [`docs/5-CONFIGURATION/onp-env-reference.md`](../docs/5-CONFIGURATION/onp-env-reference.md).
- For the operator runbook: [`operating-locally.md`](operating-locally.md).
- For upstream changelog: [`../CHANGELOG.md`](../CHANGELOG.md).
