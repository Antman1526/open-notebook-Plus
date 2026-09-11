# Project Handoff: Deeper Notebook (for Claude)

**Date**: September 11, 2026 (v0.8.128)  
**Repository Path**: `/Users/Antman/Desktop/BrainPulse Ventures LLC/DeeperNotebook/Deeper-Notebook`  
**Current Branch**: `main`  
**Latest Commits & Additions** (v0.8.115 – v0.8.128):
- `0f85e969`: `fix(tests): strict ORDER BY projection guard, alias-safe env fixture, and the full-suite baseline failures` (v0.8.128 — first fully green full backend run)
- `10ab30c0`: `feat(health): per-worker heartbeat rows, shutdown cleanup, worker status in Settings` (v0.8.128)
- `b10f312a`: `fix(sources): explicit processing outcome instead of an extracted-text proxy` (v0.8.128)
- `f1fd99cd`: `feat(podcasts): Quick podcast dialog carries the notebook id from source views` (v0.8.128)
- `04dbc218`: `feat(health): background worker heartbeat surfaced in /healthz/deep and the setup wizard` (v0.8.127, verified live)
- `e761a3cb`: `feat(podcasts): Podcast Studio submissions carry the notebook id` (v0.8.127)
- `2a32727e`: `fix(studio): visual exports write over their recorded paths too` (v0.8.127)
- `0f02658d` / `f4178266`: sources without a command row report "completed"; tests log to a temp dir (v0.8.127)
- `c4c5609b` / `a3c0f3b7` / `22a14da7` / `50eabdb0` / `d7b6d1c8`: the v0.8.126 batch
- `0942cfcb` / `a5c50a36` / `d8fb2759`: the three v0.8.125 live-run bug fixes
- `80d67390` / `711aee12` / `8108d1c1`: bundle export, mind map preview/search/cluster, retention job (v0.8.124)
- `8b48a72e`: `feat(studio): mind map deep-linking, semantic minimap, bundle cleanup & canvas filter chips` (v0.8.123)
- `976f37b6`: `feat(studio): video overview disk safety, mind map note editing, graph artifact grounding & i18n polish` (v0.8.122)
- `7228baf3`: `fix(studio): preserve content in exports and synthesis, clean up orphaned export files` (v0.8.121)
- `db815041`: `feat(studio): surface export staleness and per-item regeneration in Evidence Studio` (v0.8.120)
- `948b1ba8`: `fix(i18n): resolve translation gaps behind defaultValue across all 14 locales` (v0.8.120)
- `33dc3461`: `feat(studio): support multi-file export regeneration, aliases, and staleness tracking` (v0.8.120)
- `44d56e68`: `fix(studio): pair Arial Unicode with Arial Bold on macOS for PDF export` (v0.8.120)
- `92981998`: `feat(studio): regenerate a single export format on demand` (v0.8.119)
- `72f5edce`: `feat(studio): render non-Latin text in the course-pack PDF` (v0.8.119)
- `f63d611a`: `test(i18n): key guard also checks single-segment keys and reports defaultValue-only gaps` (v0.8.119)
- `827cdc27`: `fix(i18n): podcast toasts showed a literal "{name}"; add placeholder parity test` (v0.8.119)
- `dc89cf6a`: `feat(i18n): localize the Studio artifact export menu` (v0.8.119)
- `497f49de`: `feat(studio): text-flow PDF export for course packs via reportlab` (v0.8.118)
- `06dc8cd3`: `feat(studio): EPUB 3 export for course packs` (v0.8.118)
- `eb5eaf29`: `fix(frontend): detect stalled model streams instead of hanging in "streaming" forever` (v0.8.116)

> **Path note:** the Desktop repo folder is a symlink to
> `~/Documents/Open Notebook/Deeper-Notebook`; tool output may print either path.

---

## 1. Executive Summary & Context

Deeper Notebook is a privacy-first, local-first research engine and intelligent synthesis notebook. It supports local models (via MLX, Ollama, Llama.cpp, vLLM) as well as cloud providers, full SurrealDB/SQLite vector search, audio transcription & podcast synthesis, Obsidian vault bidirectional synchronization, Evidence Studio artifacts, and an agency-grade visual design system.

Over recent sessions, 13 core architectural superpowers, a comprehensive 3-phase agency-grade visual redesign, a production native macOS `.dmg` release pipeline, and zero-defect codebase hardening passes were completed.

---

## 2. Environments & Tooling (Critical Gotchas)

### A. Python Environments
- **Backend Runtime & Tests**: Use `.venv/` (Python 3.12.13).  
  *Command*: `.venv/bin/pytest <test_path>`  
  *Warning*: Do NOT run tests with the global system `python3` (Python 3.14), as it lacks project-specific packages like `loguru`.
- **Desktop PyInstaller Build**: Use `.build-venv/` (Python 3.12, PyInstaller 6.22.0).  
  *Command*: `.build-venv/bin/pyinstaller desktop/build/pyinstaller.spec`

### B. Frontend Runtime (Next.js / React 19)
- **Node Version**: Node 26.7.0 / npm in `frontend/`.
- **Test Runner**: Vitest forks pool (`vitest run --pool=forks --maxWorkers=1`).
- **Web Storage Gotcha**: Node 26 provides a global native `Storage` object which can shadow jsdom's `window.localStorage`. Always spy directly on `window.localStorage` in tests (e.g. `vi.spyOn(window.localStorage, 'setItem')`).

### C. Rebrand Governance (Strict Rule)
- The codebase enforces a strict rebrand verification check:
  ```bash
  python3 scripts/rebrand_audit.py --check
  ```
- **Rule**: Must exit with code 0 (0 unexpected active identities, 0 stale entries).
- **If edits shift lines in tracked files**: Never edit `scripts/rebrand-allowlist.json` by hand. Run the automated pin repair tool:
  ```bash
  python3 scripts/repair_rebrand_pins.py
  ```
  This automatically recalculates SHA256 line context hashes and updates `_PINNED_SELECTOR_INVENTORY_SHA256` in `scripts/rebrand_audit.py`.

---

## 3. Verified Health & Quality Metrics

All gates are currently passing 100%:

| Gate / Suite | Command | Status |
| :--- | :--- | :--- |
| **Frontend Vitest** | `cd frontend && npm test` | **266/266 test files passed (2,000 tests)** |
| **Frontend ESLint** | `cd frontend && npm run lint` | **0 errors, 0 warnings** |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **0 errors** |
| **Desktop Smoke Tests**| `.venv/bin/pytest desktop/tests/test_package_release_smoke.py` | **32/32 passed** |
| **Backend full suite** (strict query guard, ~5 min) | `.venv/bin/pytest tests -q -p no:cacheprovider` | **5,110 passed, 133 skipped, 0 failed** (first green full run, v0.8.128) |
| **Backend ruff** | `.venv/bin/python -m ruff check .` | **12 pre-existing `I001` import-order findings in untouched test files; 0 in touched files** |
| **Rebrand Audit** | `python3 scripts/rebrand_audit.py --check` | **0 unexpected identities, 0 stale entries** |
| **Desktop Package** | `hdiutil verify dist/Deeper-Notebook-mac-arm64.dmg` | **Checksum VALID (183MB DMG)** |

---

## 4. Key Architectural Additions & Features

1. **Mind Map to Studio Artifact Deep-Linking (v0.8.123)**:
   - Connected `onSelectArtifact` in `MindMapButton.tsx` to dispatch the `dn:select-artifact` custom event and close the mind map dialog.
   - Added an active listener in `ArtifactRail.tsx` that catches `dn:select-artifact`, finds the target artifact in memory, and immediately selects it to open the full viewer drawer/modal with markdown preview, citations, export options, and revision history.

2. **Semantic MiniMap Styling & Graph Node Type Alignment (v0.8.123)**:
   - Configured React Flow's `<MiniMap />` in `MindMap.tsx` with dynamic `nodeColor` callback mapping each node to its semantic CSS design token (`--dn-graph-source`, `--dn-graph-note`, `--dn-graph-artifact`, and fallback `--dn-graph-fallback`).
   - Aligned frontend `NotebookGraphNode` and `NotebookGraphEdge` TypeScript definitions with backend models (`studio_artifact`, `grounded_in`).

3. **Multi-File Bundle Directory Disk Cleanup (v0.8.123)**:
   - Extended `StudioArtifact._cleanup_export_files()` in `deeper_notebook/domain/notebook.py` to sweep and safely unlink all files and child bundle directories (e.g. SCORM, xAPI, research bundles matching `{slug}-*` or `{slug}`) within `_artifact_export_dir()`, with strict path traversal containment guards, ensuring zero orphaned directory trees upon artifact deletion.

4. **Mind Map Node-Type Filter Chips & 14-Locale i18n (v0.8.123)**:
   - Added an interactive filter chips bar atop the Mind Map canvas (`All ({count})`, `Sources ({count})`, `Notes ({count})`, `Artifacts ({count})`) with dynamic radial coordinate redistribution and visible edge filtering.
   - Added `filterAll`, `filterSources`, `filterNotes`, and `filterArtifacts` under `mindMap` across all 14 supported locales (`en-US`, `zh-CN`, `zh-TW`, `ja-JP`, `pt-BR`, `es-ES`, `fr-FR`, `de-DE`, `it-IT`, `ru-RU`, `tr-TR`, `pl-PL`, `ca-ES`, `bn-IN`) with 100% `{count}` placeholder parity and 0 unused keys.

5. **Video Overview Orphaned File Disk Safety (v0.8.122)**:
   - `StudioArtifact._cleanup_export_files()` in `deeper_notebook/domain/notebook.py` unlinks generated video overview assets (`video_mp4`, `video_captions`) located in `DATA_FOLDER / "video-overviews" / {artifact_slug}` upon artifact deletion.
   - Enforces strict path traversal containment checks (`candidate.is_file() and any(root in candidate.parents for root in allowed_roots)`).
   - Recursively sweeps and prunes empty artifact video directories to prevent disk accumulation.

2. **Mind Map Interactive Note Reading & Editing (v0.8.122)**:
   - Wired `NoteEditorDialog` into `MindMapButton.tsx` and connected `onSelectNote` callback.
   - Clicking note nodes in the React Flow mind map immediately opens the note editor modal with full markdown editing, live updates, and query cache invalidation.

3. **Knowledge Graph Studio Artifact Grounding (v0.8.122)**:
   - `Notebook.get_graph()` in `deeper_notebook/domain/notebook.py` fetches `StudioArtifact.get_for_notebook(self.id)`.
   - Adds studio artifacts as graph nodes (`type: "studio_artifact"`) with radial hub connections and creates `"grounded_in"` edges to the notebook's referenced sources (`source_ids`).
   - Extended `MindMap.tsx` and semantic theme styling to render artifact nodes and handle `onSelectArtifact`.

4. **Complete 14-Locale i18n Polish (v0.8.122)**:
   - Localized `searchPage.viewEvidence` (`"View evidence for {title}"`) and `studio.trustMargin` across all 14 supported locales.
   - Maintained 100% `{title}` placeholder parity and 0 unused keys across all languages.

5. **Notebook Export & Executive Synthesis Content Integrity (v0.8.121)**:
   - `Notebook.get_sources(include_full_text: bool = False)` and `Notebook.get_notes(include_content: bool = False)` in `deeper_notebook/domain/notebook.py`.
   - Preserves fast query times for sidebar lists (`False` default) while supplying full note markdown and source full text to export and synthesis pipelines (`True`).
   - Added lazy hydration fallbacks in `api/routers/exports.py` and `api/routers/notebooks.py` so content is never omitted on disk or in cross-source synthesis.

6. **Orphaned Export File Cleanup (v0.8.121)**:
   - `StudioArtifact.delete()` in `deeper_notebook/domain/notebook.py` unlinks generated export files on disk before deleting the database row.
   - Guarded against path traversal and escapes: resolves paths and ensures they are strictly contained inside `_artifact_export_dir()`.

7. **Batch Stale Export Refresh (v0.8.121)**:
   - `ArtifactExportMenu.tsx` surfaces a "Refresh all outdated ({count})" batch button whenever two or more export formats are marked stale.
   - Sequentially triggers regeneration per format to prevent disk and database write races.

8. **Multi-File On-Demand Export Regeneration & Format Aliases (v0.8.120)**:
   - `POST /studio/artifacts/{id}/exports/{format}` supports course pack bundles (`scorm_package`, `xapi_package`, `research_bundle`, `instructor_guide`, `learner_handout`, `module_checklist`, `assessment`) alongside single-file formats (`docx`, `pptx`, `pdf`, `xlsx`, `csv`, `markdown`, `json`, `png`, `svg`).
   - Format aliases (`scorm`, `xapi`, `bundle`, `svg`, `checklist`) canonicalized via `canonical_export_format`.

9. **Export Freshness Tracking & UI Stale Badging (v0.8.120)**:
   - Artifact content SHA-256 hashes persisted per export in `output_payload["export_hashes"]`.
   - Backend tracks `stale_export_formats` using `is_export_stale` and exposes them in `StudioArtifactResponse`.
   - Export menu surfaces an amber "Outdated" badge and per-item 1-click regenerate action.

10. **Obsidian Vault Exporter & Bidirectional Sync**:
   - `api/routers/exports.py`: Exports notebook contents as `"obsidian_folder"` or `"obsidian_zip"` with YAML frontmatter, `[[wikilinks]]`, `Index.md` Map of Content, and `.obsidian/app.json`.

---

## 5. Completed Items & Next Areas to Explore

### Recently Completed in v0.8.128
- [x] All five v0.8.127 open items:
  1. **Quick podcast dialog scoped to the notebook.** `QuickPodcastDialog`, `SourceDetailContent`, `SourceDialog`, `SourceCard`, `SourcesColumn` thread `notebookId`; `podcast-studio-store.open(selections, destination, explicitNotebookId?)`.
  2. **Worker row in Settings.** `ObservabilityCard` reads `useDeepHealth().checks.worker`: online/offline, active count, start-command hint (key `settings.observability.workerDesc`, 14 locales).
  3. **Per-worker heartbeat rows.** `worker_heartbeat:⟨host_pid⟩`; graceful stop (atexit / SIGTERM) deletes the process's own row; `read_worker_status()` aggregates `active_workers_count` and prunes rows older than a day. No orphan row after a clean stop.
  4. **Runtime ORDER BY projection guard.** `check_query_ordered_projection()` in `deeper_notebook/database/repository.py` runs in `repo_query`: always warns; `DEEPER_NOTEBOOK_STRICT_QUERY_GUARD=1` raises before the database is touched. Tolerates `rand()`/`count()`, `GROUP BY`, and dotted fields whose root is projected. `tests/conftest.py` sets it for the whole suite.
  5. **Explicit source outcome.** Sync creates write `provenance.processing_status` (`completed`/`failed` + error); worker sets `failed` on an orphan; precedence command → explicit outcome → text proxy → none.
- [x] **First fully green full backend run** (5,110 passed). Fixed what it found, all present at the previous commit too: MCP doubles missing the client's `transport`/`command`/`args`/`env` kwargs; `launcher_prefs.py` / `notebooks.py` missing `except HTTPException: raise` (meta test); `desktop/__version__` stuck at 0.8.123; six logging/encryption tests that passed alone but failed in suite order.
- [x] **Order-dependent test cause.** `api/main.py:16` and `commands/__init__.py:30` call `apply_product_environment(os.environ)` at import, mirroring canonical settings into legacy alias names; a later `monkeypatch.delenv` on the canonical name leaves the mirror visible to `resolve_env`. Use the `unset_setting` fixture (`tests/conftest.py`) whenever a test needs a setting *absent*; a bare `delenv` of the canonical name is not enough once either module was imported.

### Recently Completed in v0.8.127
- [x] All five v0.8.126 open items: worker heartbeat + health row + wizard hint (verified live: degraded with no worker, online within one tick), sync-source status derivation, visual export orphans, Podcast Studio notebook scoping, test log isolation.

### Running the stack locally (verified 2026-09-11)
- SurrealDB: `docker compose -f docker-compose.yml up -d surrealdb`.
- API: `API_RELOAD=false uv run --env-file .env run_api.py` while others edit (reload shutdown bounded at 20 s anyway).
- Worker: `DEEPER_NOTEBOOK_WORKER_PROCESS=1 uv run --env-file .env surreal-commands-worker --import-modules commands` (the Makefile and desktop launcher set the flag; without it argv detection still works). `/healthz/deep` and the setup wizard now say when it is missing. Synchronous source creates do not need it.
- macOS has no `timeout`; do not wrap the worker in it when scripting.
- Frontend: `cd frontend && npm run dev`.

### Next Recommended Areas to Explore
1. **Make the full backend run a standing gate.** Until v0.8.128 only touched suites ran before a push, which is how 22 failures accumulated unnoticed. Add a `make test-backend` target (`.venv/bin/pytest tests -q -p no:cacheprovider`, ~5 min) and run it before every push, or at least once per version.
2. **Pre-existing ruff `I001` in 12 untouched test files** (`tests/test_vault_git_sync.py`, `tests/test_reranker.py`, `tests/test_mcp_superpowers.py`, `tests/test_audio_dictate.py`, `tests/test_notebook_synthesis.py`, `tests/test_hardware_profiler.py`, `tests/test_source_resilient_ingest.py`, `tests/test_stream_keepalive_http.py`, `desktop/tests/test_llamacpp_engine_opts.py`, `desktop/tests/test_companion_draft.py`, `desktop/tests/test_reranker_sidecar.py`, …). `ruff check --fix` clears them; `ruff format --check` would also reformat `api/routers/notebooks.py` and `deeper_notebook/database/repository.py` (formatting is not a gate today — decide whether it should be).
3. **Other tests that `delenv` only a canonical name.** The six fixed in v0.8.128 were the ones that failed; any test asserting a setting is *absent* has the same latent trap (`grep -n 'delenv("DEEPER_NOTEBOOK_' tests`). Either migrate them to `unset_setting`, or add an autouse fixture that snapshots and restores the product names in `os.environ` per test — but keep the integration session fixture's `SURREAL_*` writes intact (`tests/integration/conftest.py` sets them for the session and restores them itself).
4. **v0.8.128 frontend changes were verified by vitest only.** The Quick podcast dialog scoping (`f1fd99cd`) and the Settings worker row (`10ab30c0`) have unit coverage but were not driven live in the browser this round (the v0.8.127 heartbeat/wizard row was). Start the stack (section 5 above), open a source card → Quick podcast, and confirm the created episode carries the notebook id; stop the worker and confirm the Settings observability card flips to offline with the start hint.

---

## 6. Quick Cheat-Sheet for Common Commands

```bash
# 1. Run all frontend vitest tests
cd frontend && npm test

# 2. Run frontend linter
cd frontend && npm run lint

# 3. Check TypeScript compilation
cd frontend && npx tsc --noEmit

# 4. Check rebrand governance
python3 scripts/rebrand_audit.py --check

# 5. Fix shifted allowlist pins (if any file with approvals was modified)
python3 scripts/repair_rebrand_pins.py

# 6. Run desktop smoke tests (use the project venv, not system python3)
.venv/bin/pytest desktop/tests/test_package_release_smoke.py

# 7. Run backend pytest suite on exports and synthesis
.venv/bin/pytest tests/test_exports_router.py tests/test_notebook_synthesis.py tests/test_studio_export_staleness.py

# 8. Rebuild the macOS DMG
cd desktop && ./build_dmg.sh
```
