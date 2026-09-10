# Project Handoff: Deeper Notebook (for Claude)

**Date**: September 10, 2026  
**Repository Path**: `/Users/Antman/Desktop/BrainPulse Ventures LLC/DeeperNotebook/Deeper-Notebook`  
**Current Branch**: `main`  
**Latest Commits & Additions** (v0.8.115 – v0.8.121):
- `feat(studio): export content integrity, orphaned export cleanup, and batch stale refresh` (v0.8.121)
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
| **Frontend Vitest** | `cd frontend && npm test` | **260/260 test files passed (1,934 tests)** |
| **Frontend ESLint** | `cd frontend && npm run lint` | **0 errors, 0 warnings** |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **0 errors** |
| **Desktop Smoke Tests**| `.venv/bin/pytest desktop/tests/test_package_release_smoke.py` | **32/32 passed** |
| **Backend export + studio suites** | `.venv/bin/pytest tests/test_exports_router.py tests/test_notebook_synthesis.py tests/test_studio_export_staleness.py` | **70/70 passed** |
| **Rebrand Audit** | `python3 scripts/rebrand_audit.py --check` | **0 unexpected identities, 0 stale entries** |
| **Desktop Package** | `hdiutil verify dist/Deeper-Notebook-mac-arm64.dmg` | **Checksum VALID (183MB DMG)** |

---

## 4. Key Architectural Additions & Features

1. **Notebook Export & Executive Synthesis Content Integrity (v0.8.121)**:
   - `Notebook.get_sources(include_full_text: bool = False)` and `Notebook.get_notes(include_content: bool = False)` in `deeper_notebook/domain/notebook.py`.
   - Preserves fast query times for sidebar lists (`False` default) while supplying full note markdown and source full text to export and synthesis pipelines (`True`).
   - Added lazy hydration fallbacks in `api/routers/exports.py` and `api/routers/notebooks.py` so content is never omitted on disk or in cross-source synthesis.

2. **Orphaned Export File Cleanup (v0.8.121)**:
   - `StudioArtifact.delete()` in `deeper_notebook/domain/notebook.py` unlinks generated export files on disk before deleting the database row.
   - Guarded against path traversal and escapes: resolves paths and ensures they are strictly contained inside `_artifact_export_dir()`.

3. **Batch Stale Export Refresh (v0.8.121)**:
   - `ArtifactExportMenu.tsx` surfaces a "Refresh all outdated ({count})" batch button whenever two or more export formats are marked stale.
   - Sequentially triggers regeneration per format to prevent disk and database write races.
   - Complete localization across all 14 languages with 100% `{count}` placeholder parity.

4. **Multi-File On-Demand Export Regeneration & Format Aliases (v0.8.120)**:
   - `POST /studio/artifacts/{id}/exports/{format}` supports course pack bundles (`scorm_package`, `xapi_package`, `research_bundle`, `instructor_guide`, `learner_handout`, `module_checklist`, `assessment`) alongside single-file formats (`docx`, `pptx`, `pdf`, `xlsx`, `csv`, `markdown`, `json`, `png`, `svg`).
   - Format aliases (`scorm`, `xapi`, `bundle`, `svg`, `checklist`) canonicalized via `canonical_export_format`.

5. **Export Freshness Tracking & UI Stale Badging (v0.8.120)**:
   - Artifact content SHA-256 hashes persisted per export in `output_payload["export_hashes"]`.
   - Backend tracks `stale_export_formats` using `is_export_stale` and exposes them in `StudioArtifactResponse`.
   - Export menu surfaces an amber "Outdated" badge and per-item 1-click regenerate action.

6. **PDF Bold Font Pairing on macOS (v0.8.120)**:
   - When `Arial Unicode.ttf` is selected as the body font, headings pair with `Arial Bold.ttf` for high-contrast bold titles without fallback degradation.

7. **Stream Resilience (v0.8.115 / v0.8.116)**:
   - `frontend/src/lib/utils/stream-stall.ts`: per-read idle-timeout guard (`readWithIdleTimeout`, `StreamStallError`).
   - `api/utils/stream_keepalive.py`: heartbeat frames + server-side idle limit wrapped around the streaming endpoints.

8. **Obsidian Vault Exporter & Bidirectional Sync**:
   - `api/routers/exports.py`: Exports notebook contents as `"obsidian_folder"` or `"obsidian_zip"` with YAML frontmatter, `[[wikilinks]]`, `Index.md` Map of Content, and `.obsidian/app.json`.

---

## 5. Completed Items & Next Areas to Explore

### Recently Completed in v0.8.120 – v0.8.121
- [x] **Notebook export and executive synthesis content integrity:** Added `include_full_text` and `include_content` flags to domain methods with defensive fallback lazy hydration.
- [x] **Orphaned export file cleanup on artifact deletion:** Implemented `StudioArtifact.delete()` with sandboxed file unlinking.
- [x] **Batch stale export refresh:** Added "Refresh all outdated ({count})" batch button in `ArtifactExportMenu.tsx`.
- [x] **Translation gaps resolved across all 14 locales:** Audited all 141 unlocalized translation keys; 100% key and placeholder parity maintained across 14 languages.
- [x] **Bold Unicode font pairing on macOS:** Paired Arial Unicode with Arial Bold for clear headings.
- [x] **Multi-file on-demand export regeneration:** Supported all course-pack bundle formats with friendly aliases.

### Next Recommended Areas to Explore
1. **Inline format previews:** Preview EPUB or SVG diagrams directly inside the Evidence Studio artifact drawer.
2. **Streaming chunk backpressure:** Fine-tune backpressure buffering for low-memory containerized environments.
3. **Automated archive compaction:** Compact older studio revisions or export zip bundles past a configurable age threshold.

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
