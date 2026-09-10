# Project Handoff: Deeper Notebook (for Claude)

**Date**: September 9, 2026  
**Repository Path**: `/Users/Antman/Desktop/BrainPulse Ventures LLC/DeeperNotebook/Deeper-Notebook`  
**Current Branch**: `main`  
**Latest Commits** (v0.8.115 / v0.8.116, 2026-09-09):
- `5ccd162e`: `test(frontend): behavioural streaming tests for useAsk and useSourceChat`
- `80c8e5b8`: `feat(knowledge): keyboard navigation for the vault graph`
- `3ee72316`: `perf(search): virtualize result lists of 50+ cards with the shared VirtualizedListAuto`
- `c3fe0d8e`: `feat(chat): keep partial answers and offer one-click retry when a stream stalls`
- `8e98616e`: `feat(streaming): backend heartbeat frames and server-side idle limit for model streams`
- `eb5eaf29`: `fix(frontend): detect stalled model streams instead of hanging in "streaming" forever`

> **Path note:** the Desktop repo folder is a symlink to
> `~/Documents/Open Notebook/Deeper-Notebook`; tool output may print either path.

---

## 1. Executive Summary & Context

Deeper Notebook is a privacy-first, local-first research engine and intelligent synthesis notebook. It supports local models (via MLX, Ollama, Llama.cpp, vLLM) as well as cloud providers, full SurrealDB/SQLite vector search, audio transcription & podcast synthesis, Obsidian vault bidirectional synchronization, and an agency-grade visual design system.

Over the recent sessions, 13 core architectural superpowers, a comprehensive 3-phase agency-grade visual redesign, a production native macOS `.dmg` release pipeline, and a zero-defect codebase hardening pass were completed.

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
| **Frontend Vitest** | `cd frontend && npm test` | **254/254 test files passed (1,893 tests)** |
| **Frontend ESLint** | `cd frontend && npm run lint` | **0 errors, 0 warnings** |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **0 errors** |
| **Desktop Smoke Tests**| `.venv/bin/pytest desktop/tests/test_package_release_smoke.py` | **32/32 passed** |
| **Backend stream routers** | `.venv/bin/pytest tests/test_stream_keepalive.py tests/test_chat_stream.py tests/test_v0_7_200_search_typed_exceptions.py tests/test_v0_8_44_source_chat_mcp_disable.py` | **passing (94 across the touched router suites)** |
| **Rebrand Audit** | `python3 scripts/rebrand_audit.py --check` | **0 unexpected identities, 0 stale entries** |
| **Desktop Package** | `hdiutil verify dist/Deeper-Notebook-mac-arm64.dmg` | **Checksum VALID (183MB DMG)** |

---

## 4. Key Architectural Additions & Features

1. **Apple Silicon Hardware Profiler & Advisor**:
   - `desktop/hardware_profiler.py`: Analyzes CPU/GPU core counts, unified memory, memory bandwidth, recommending context length and GGUF quantization tiers.
   - `frontend/src/app/(dashboard)/settings/launcher-prefs/page.tsx`: Interactive hardware advisor card with 1-click apply, FlashAttention, and KV cache quantization toggles.

2. **Obsidian Vault Exporter**:
   - `api/routers/exports.py`: Exports notebook contents as `"obsidian_folder"` or `"obsidian_zip"` with YAML frontmatter, `[[wikilinks]]`, `Index.md` Map of Content, and `.obsidian/app.json`.
   - `frontend/src/app/(dashboard)/notebooks/components/ExportNotebookDialog.tsx`: Full i18n UI across all 14 supported locales.

3. **Bi-Directional In-Text Citation Passage Highlighting**:
   - `frontend/src/components/source/SourceDetailContent.tsx`: Grounded citations highlight exact passages in `<mark id="inline-cited-passage">` and provide smooth jump-to-passage controls.

4. **Executive Cross-Source Synthesis**:
   - `api/routers/notebooks.py` (`POST /notebooks/{id}/synthesis`): Generates structured consensus, tensions, knowledge gaps, and actionable findings.
   - `frontend/src/components/notebooks/ExecutiveSynthesisDialog.tsx`: KaTeX markdown rendering, copy, and save-as-note actions with notebook-switch guards.

5. **Reranker Pipeline & UI Badging**:
   - `api/routers/search.py` & `frontend/src/app/(dashboard)/search/page.tsx`: Cross-encoder scoring and badging (`Rerank: 0.94`, "Cross-Encoder Reranked").

6. **Interactive Audio Transcript Sync & Export**:
   - `frontend/src/components/podcasts/SyncedTranscript.tsx`: Center-locked auto-scrolling during playback, speaker badges, WebVTT (`.vtt`) download, and Markdown transcript copy.

7. **Remote Git Vault Sync**:
   - `deeper_notebook/vault/git_sync.py` & `api/routers/vault.py`: Local git repository management, snapshot creation, remote origin configuration, push, and pull.
   - `frontend/src/components/vault/VaultGitHistoryDialog.tsx`: Commit history visualizer, manual snapshot capture, and push/pull trigger controls.

8. **Agency-Grade Visual Design System**:
   - Concentric double-bezel cards (`"Doppelrand"`: subtle outer border, deep inset dark card, high contrast border).
   - Tactile button-in-button controls and luminous focus halos (`shadow-[0_0_20px_rgba(45,212,191,0.12)]`).
   - Frosted glass dialogs (`backdrop-blur-md bg-card/60`).
   - Collapsible reasoning blocks (`<think>` tags) with elapsed duration timers in chat streaming.

9. **macOS Production Release Packaging**:
   - Next.js standalone build -> PyInstaller `.app` bundle -> codesign ad-hoc re-sealing -> `.dmg` drag-and-drop installer (`dist/Deeper-Notebook-mac-arm64.dmg`).

10. **Stream Resilience (v0.8.115 / v0.8.116)**:
   - `frontend/src/lib/utils/stream-stall.ts`: per-read idle-timeout guard (`readWithIdleTimeout`, `StreamStallError`) used by `lib/api/chat.ts`, `lib/hooks/use-ask.ts`, `lib/hooks/useSourceChat.ts`. `NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS` (default 60 s, `0` disables). Never add a bare `await reader.read()` on a streaming body; `stream-stall-guard.test.ts` fails on it.
   - `api/utils/stream_keepalive.py`: heartbeat frames + server-side idle limit wrapped around the three streaming endpoints. `DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC` (10) / `DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC` (300). New env settings must be registered in `deeper_notebook/environment.py` or `resolve_env` raises `KeyError`.
   - Stall recovery: partial answers are kept and the stall toast carries a "Try Again" action in all three hooks.
   - `frontend/src/test/stream-harness.ts` + `*.behaviour.test.ts(x)`: real streaming tests for the hooks.

---

## 5. Recommended Next Areas to Improve (Backlog for Claude)

> **Correction (2026-09-09):** the previous version of this section cited
> `components/sources/SourceGallery.tsx`, `components/vault/ResearchCoreWorkspace.tsx`,
> and `lib/hooks/use-chat.ts`. None of those files exist. The real locations
> are given below. Priorities 1–4 of the old list were addressed in v0.8.116
> (see section 4, item 10, and `desktop/CHANGELOG.md`).

### Status of the previous backlog
- **List virtualization** — `SourcesColumn.tsx` was already virtualized; the search results page (`app/(dashboard)/search/page.tsx`) is now virtualized at 50+ cards. The CSS-grid gallery at `components/deeper-notebook/source-gallery/SourceGallery.tsx` is intentionally not virtualized (grid layout, small N).
- **Offline / network resilience** — done: client idle guard, backend heartbeat + idle limit, partial-answer retention, retry toast.
- **Graph keyboard accessibility** — done in `components/vault/VaultGraph.tsx` (React Flow). `/` already opens the knowledge command surface via `KnowledgeCommandBridge.tsx`.
- **Export format extensions (EPUB / PDF course packs)** — still open; `api/routers/studio.py`, `app/(dashboard)/studio/page.tsx`.

### Open items, in priority order
1. **Interrupted-message marker in the chat UI.** The hooks keep partial text on a stall, but nothing in `components/source/ChatPanel.tsx` (or the notebook chat panel) shows that the message was cut off. Add an `interrupted` flag on the local message and a small badge + inline retry; the toast is the only affordance today.
2. **Behavioural test for `useNotebookChat`.** `use-ask` and `useSourceChat` now have real streaming tests; the notebook hook is still covered only by the source-text contract. Reuse `src/test/stream-harness.ts` (NDJSON framing via `pushRaw`) and mock `chatApi.streamMessage`.
3. **Backend heartbeat coverage through the HTTP layer.** `tests/test_stream_keepalive.py` tests the wrapper directly; add one `TestClient` test per endpoint that stubs the generator to sleep past the heartbeat interval and asserts a heartbeat frame is on the wire.
4. **EPUB / PDF course-pack export** (unchanged from the previous backlog).
5. **Search page size.** `app/(dashboard)/search/page.tsx` is ~940 lines with deep-research, ask, and search modes in one component; the result card is already extracted (`renderSearchResultCard`), which is the natural seam for splitting the modes into files.

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

# 7. Run backend pytest suite on a specific router
.venv/bin/pytest tests/test_vault_api.py

# 8. Rebuild the macOS DMG
cd desktop && ./build_dmg.sh
```
