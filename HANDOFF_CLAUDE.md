# Project Handoff: Deeper Notebook (for Claude)

**Date**: September 9, 2026  
**Repository Path**: `/Users/Antman/Desktop/BrainPulse Ventures LLC/DeeperNotebook/Deeper-Notebook`  
**Current Branch**: `main`  
**Latest Commits**:
- `8039f65d`: `fix(frontend): resolve typescript AskModels mismatch, eliminate lint errors, and guard dialog hook dependencies`
- `00cade8a`: `fix(test): stabilize desktop smoke exit race, probe timeouts, and Node 26 storage mocks`
- `a22beba2`: `feat(ui): elevate capture inbox, studio, knowledge header, and settings with agency visual architecture`
- `a069089c`: `feat(ui): elevate visual architecture with doppelrand cards, frosted dialogs, and button-in-button controls`

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
| **Frontend Vitest** | `cd frontend && npm test` | **249/249 test files passed (1,847 tests)** |
| **Frontend ESLint** | `cd frontend && npm run lint` | **0 errors, 0 warnings** |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **0 errors** |
| **Desktop Smoke Tests**| `python3 -m pytest desktop/tests/test_package_release_smoke.py` | **32/32 passed** |
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

---

## 5. Recommended Next Areas to Improve (Backlog for Claude)

Here are the highest-impact areas to continue driving quality and feature excellence:

### Priority 1: Large-Scale List & Graph Virtualization
- **Context**: For notebooks containing hundreds of sources or complex knowledge graphs, DOM rendering can become heavy.
- **Location**: `frontend/src/components/sources/SourceGallery.tsx`, `frontend/src/components/vault/ResearchCoreWorkspace.tsx`.
- **Opportunity**: Leverage `@tanstack/react-virtual` (already in `package.json`) to virtualize dense lists and review canvas node culling.

### Priority 2: Offline & Network Resilience for Local Model Inference
- **Context**: When running local models via Ollama or Llama.cpp, unexpected daemon crashes or sleep-wake cycles during long inference streams can leave pending UI states.
- **Location**: `frontend/src/lib/hooks/use-ask.ts`, `frontend/src/lib/hooks/use-chat.ts`.
- **Opportunity**: Add auto-reconnection attempts, stream timeout detection, and user-friendly "Resume Generation" / "Fallback to Offline Buffer" recovery toasts.

### Priority 3: Enhanced Knowledge Graph Interactions & Keyboard Accessibility
- **Context**: The Knowledge Graph in `/knowledge` has an elevated header, but keyboard navigation through graph nodes can be improved.
- **Location**: `frontend/src/components/vault/ResearchCoreWorkspace.tsx` and related graph components.
- **Opportunity**: Add standard keyboard shortcuts (`Tab`/`Arrow` traversal, `Enter` to open node detail, `/` to focus graph search filter).

### Priority 4: Export Format Extensions
- **Context**: The Obsidian exporter is complete and verified. Users may also benefit from rich EPUB or PDF bundled course-pack exports from the Studio.
- **Location**: `api/routers/studio.py`, `frontend/src/app/(dashboard)/studio/page.tsx`.

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

# 6. Run desktop smoke tests
python3 -m pytest desktop/tests/test_package_release_smoke.py

# 7. Run backend pytest suite on a specific router
.venv/bin/pytest tests/test_vault_api.py

# 8. Rebuild the macOS DMG
cd desktop && ./build_dmg.sh
```
