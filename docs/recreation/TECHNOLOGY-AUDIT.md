# Deeper Notebook — Complete Technology Audit

Every language, runtime, framework, library, tool, and external service the project uses,
with **what it specifically does in this codebase** — not what it does in general.

**Snapshot:** desktop `0.8.114` · server track `1.8.5` · 2026-08-21 · measured at `58ff44b4`
**Sources:** `pyproject.toml`, `desktop/requirements.txt`, `frontend/package.json`,
`desktop/build/runtimes.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/`.

---

## 1. Languages

| Language | Where | Specific role |
|---|---|---|
| **Python 3.12** (`>=3.11,<3.13`) | 514 files, ~179k LOC | Backend, business logic, desktop shell, launcher, build tooling. Upper bound is `<3.13` because `llama-cpp-python` wheels and `mlx-lm` lag a release |
| **TypeScript 5** | 693 files, ~125k LOC | All frontend code. `strict` on; Zod schemas at API boundaries mean runtime shape errors surface as typed failures |
| **SurrealQL** | 92 migration files | Schema DDL, cascade events, graph traversal (`count(<-reference.in)`), vector search. No ORM — queries are hand-written |
| **CSS** | `workspace.css` + Tailwind layers | The notebook design language (paper grain, rule lines, density tokens) lives in hand-written CSS; utility layout is Tailwind |
| **Bash / zsh** | `scripts/*.sh`, `Makefile` | Build orchestration, signing identity creation, DB repair, API readiness waits |
| **JavaScript (ESM)** | `frontend/start-server.js`, `scripts/verify-feature-env-build.mjs` | Packaged Next server entry; build-time verification that feature-flag literals were actually inlined |

---

## 2. Runtimes bundled into the app

Fetched by `desktop/build/fetch_runtimes.py`, SHA-256 verified with `hmac.compare_digest`,
pinned in `runtimes.toml`.

| Runtime | Version | Specific role |
|---|---|---|
| **python-build-standalone** (CPython) | `20260814` / **3.12.14** | The interpreter the app actually runs on. Bumped from 3.12.8 specifically because its OpenSSL 3.0 TLS fingerprint got Wikimedia's edge to return HTTP 403 — the keyless search provider was dead in packaged builds only |
| **SurrealDB** | `2.1.0` | The database binary, spawned as a child process by `launcher.py`. Bundled so the user installs nothing |
| **Node.js** | `20.18.0` | Runs the packaged Next.js standalone server inside the `.app` |
| **uv** | `0.5.11` | Creates and populates the user venv at `~/.deeper-notebook/venv` on first run and after a lock change |

---

## 3. Backend framework and web layer

| Package | Floor | Specific role |
|---|---|---|
| **fastapi** | `>=0.136.3` | 47 router modules / 279 route handlers. Dependency injection carries `check_api_password`; `lifespan` runs migrations and warms clients |
| **uvicorn** | `>=0.24.0` | ASGI server for the API process |
| **starlette** | `>=1.2.1` | Pinned directly, not left transitive — CVE-2026-48710 (BadHost) |
| **pydantic** | `>=2.9.2` | Every request/response schema. `model_copy(update=…)` is how the capability sentinel is stamped onto source rows |
| **pydantic-settings** | `>=2.14.2` | Settings models where env-backed config needs validation |
| **python-multipart** | `>=0.0.31` | Multipart parsing for source file upload — the highest-risk parser in the app, hence the direct floor |
| **prometheus-client** | `>=0.20.0` | `/metrics`, token-guarded. Chosen for stable histogram-bucket semantics |
| **loguru** | `>=0.7.2` | Every log sink: launcher, api, surreal, worker, per-sidecar `.tail` files |
| **httpx[socks]** | `>=0.27.0` (desktop pins `==0.28.1`) | Every outbound HTTP call. The pooled `AsyncClient` in `web_search.py` (8 keepalive / 16 max) cut a search from 513 ms to 341 ms |
| **h2** | `>=4.4.1` | HTTP/2 for httpx; floor is PYSEC-2026-3628 |
| **aiohttp** | `>=3.14.3` (desktop `>=3.11.18`) | Used by MCP transports and the desktop shims |
| **click** | `>=8.3.3` | CLI entry points (surreal-commands worker, maintenance scripts); floored directly rather than left transitive |
| **urllib3 / idna / soupsieve / lxml / lxml-html-clean** | floors set | Transitive deps pinned up out of CVE range rather than left to resolution luck |

---

## 4. AI orchestration

| Package | Floor | Specific role |
|---|---|---|
| **langgraph** | `>=1.0.10` | The graph runtime for `chat`, `ask`, `source`, `source_chat`, `transformation`, `agent_fsm`. Bumped for CVE-2026-28277 |
| **langgraph-checkpoint** / **-sqlite** / **-sdk** | `>=4.1.1` / `>=3.1.1` / `>=0.3.15` | Conversation checkpointing. Checkpoint cleanup on notebook delete lives in the router, not the domain model, to preserve layering |
| **langchain** / **langchain-core** / **langchain-classic** / **langchain-community** | `>=1.3.9` / `>=1.3.3` / `>=1.0.7` / `>=0.4.1` | Message types, tool binding (`bind_tools`), and the `ToolMessage` loop |
| **langchain-openai / -anthropic / -google-genai / -groq / -mistralai / -deepseek / -ollama** | each pinned | One adapter per provider. All are optional — a missing key means that provider is simply absent from model discovery |
| **langsmith** | `>=0.8.18` | Comes in transitively via langchain; floored for CVE-2026-45134 |
| **esperanto** | `>=2.20.0,<3` | Provider-agnostic model abstraction inherited from upstream; underlies model resolution |
| **ai-prompter** | `>=0.4,<1` | Renders the Jinja templates in `prompts/` into system/user messages — including `prompts/chat/debate.jinja`, swapped in per-turn when Debate mode is on |
| **tiktoken** | `>=0.12.0` | Token counting for the context budgets that cap memory recall and source context |
| **mcp** | `>=1.28.1,<2` | Model Context Protocol client — external tool servers reachable from chat over streamable-http |
| **fastmcp** | `>=3.0,<4` | Serves the desktop shims (memory, OpenChronicle) as MCP endpoints |
| **skillopt** | `>=0.1.0,<0.2` | Microsoft SkillOpt — backs the prompt optimizer subsystem |

---

## 5. Local inference and media

| Package | Floor | Specific role |
|---|---|---|
| **llama-cpp-python[server]** | `>=0.3.16,<0.4` | Runs GGUF models as OpenAI-compatible servers — two instances: chat and embeddings. The `[server]` extra is load-bearing: without it `starlette_context` is missing and both sidecars die at import, leaving every upload stuck "Processing". Floor is CVE-2024-42479 (heap OOB in GGUF parsing — a real risk since users download model files) |
| **mlx-lm** | `>=0.31,<0.32`, darwin+arm64 only | Apple-Silicon inference against repos under the MLX model root. Its `/v1/models` returns an empty 200 body and later times out while completions keep working — `health/local_models.py` compensates for both |
| **faster-whisper** | `>=1.1.0,<2` | Speech-to-text sidecar for audio sources and voice input |
| **piper-tts** | `>=1.2.0,<2` | Text-to-speech sidecar; the maintained successor to the broken `piper` namespace |
| **mem0ai** | `>=2.0.18,<3` | Long-term memory: fact/preference/episode extraction and recall. Backed by a **custom SurrealDB vector store** (`desktop/memory/surreal_store.py`) so memory shares the app's one database |
| **huggingface-hub** | `>=1.3.0` | `snapshot_download` for managed local-model installs |
| **imageio-ffmpeg** | `>=0.6.0,<1.0` | Package-managed FFmpeg binary for Video Overview composition — deliberately avoids assuming a system FFmpeg |
| **numpy** | `>=2.4.1` | Vector math for embeddings and cosine similarity in memory recall |

---

## 6. Database

| Package | Floor | Specific role |
|---|---|---|
| **surrealdb** (Python SDK) | `>=1.0.4` | The client behind `repo_query`. Values are `$`-bound; identifiers are whitelist-validated before interpolation |
| **surreal-commands** | `>=1.3.1,<2` | The background job system — podcast generation, visual extraction, embedding rebuilds. `api/command_service.py` wraps submit/list/cancel |

Schema is managed by 92 hand-written `.surrealql` migrations, each with a `_down`
counterpart, applied by `deeper_notebook/database/async_migrate.py` at startup.

---

## 7. Document and content processing

| Package | Floor | Specific role |
|---|---|---|
| **content-core** | `>=1.14.1,<2` | Primary text extraction across PDF/HTML/office/media. Deliberately **never given a raw URL** — its fetcher has a different localhost policy than our SSRF boundary |
| **python-docx** | `>=1.2.0,<2.0` | `.docx` sources |
| **python-pptx** | `>=1.0.2,<2.0` | `.pptx` sources |
| **openpyxl** | `>=3.1.5,<4.0` | `.xlsx` sources |
| **reportlab** | `>=4.2,<6.0` | Text-flow PDF export for Studio course packs (v0.8.117). Pure Python, no system libraries, so the desktop bootstrap installs it from `desktop/requirements.lock` without packaging changes |
| **pillow** | `>=11.3.0,<12.0` | Source-visual thumbnail generation and WebP encoding. **Held below 12** by `podcast-creator → moviepy>=2.2.1 → Pillow<12`; residual advisories accepted under DN-DEP-PILLOW-2026-08-11 |
| **markdown-it-py** | `>=4.0.0,<5` | Direct dependency of the vault Markdown parser |
| **pyyaml** | `>=6.0.3,<7` | Vault frontmatter parsing |
| **watchdog** | `>=6.0.0,<7.0` | Filesystem watching for the capture inbox and incremental vault sync |
| **lxml** | `>=6.1.0` | HTML parsing in web-source ingestion; floored for CVE-2026-41066 |

---

## 8. Domain-specific libraries

| Package | Floor | Specific role |
|---|---|---|
| **fsrs** | `>=6.3.1,<7.0` | The FSRS spaced-repetition scheduler behind the Study workbench, including cards seeded from ExamLab's missed-question set |
| **genanki** | `==0.13.1` | Anki `.apkg` export |
| **podcast-creator** | `>=0.12.0,<1` | Episode generation pipeline (profiles, retries, TTS orchestration) |
| **pycountry** | `>=26.2.16` | Language/country normalization for search and TTS voice selection |
| **babel** | `>=2.18.0` | Locale-aware formatting on the server side |

---

## 9. Security and crypto

| Package | Floor | Specific role |
|---|---|---|
| **cryptography** | `>=50.0.0` | Encrypts provider credentials at rest in the `credential` table; supports multi-key rotation via `DEEPER_NOTEBOOK_ENCRYPTION_KEYS` |
| **authlib** | `>=1.6.12` | OAuth flows for connected services |
| **pyjwt** | `>=2.13.0` | Token handling |
| **joserfc** | `>=1.6.8` | JOSE primitives; floor is PYSEC-2026-2528/-2530 |
| **pyasn1** | `>=0.6.4` | Transitive certificate parsing; floored deliberately |

Application-level security is code, not a library: `security/outbound_url.py` (fail-closed
SSRF boundary that resolves and checks every address, refusing non-canonical IP literals
and DNS rebinding) and `security/mcp_transport.py` (a deliberately *different*, more
permissive policy — a localhost MCP server is legitimate).

---

## 10. Desktop shell and packaging

| Package | Version | Specific role |
|---|---|---|
| **pywebview** | `==5.4` (exact) | The native macOS window. Pinned exactly because the settings dict is version-sensitive — the shell sets only keys the installed version defines. Exposes exactly one JS bridge method (`relaunch`); downloads disabled, devtools off, external links to the system browser |
| **pyinstaller** | `>=6.13.0,<7` | Freezes the launcher into `Deeper Notebook.app`. Floor covers macOS 14+ codesign bundle handling |
| **python-dotenv** | `>=1.2.2` | Reads `launcher.env` and `.env`; floored for CVE-2026-28684 |
| **tomli** | `>=2.0.2` | Parses `config.toml` and `runtimes.toml` |
| **pip / setuptools** | `>=26.1.2` / `>=83.0.0` | Build tooling floors — `ai-prompter` pulls pip as a build dep |

---

## 11. Frontend — framework and rendering

| Package | Version | Specific role |
|---|---|---|
| **next** | `^16.2.12` | App Router, `standalone` output for packaging, `/api` rewrites to the FastAPI port. **`NEXT_PUBLIC_*` inlining at build time is why frontend feature flags cannot be rolled back in a packaged app** |
| **react** / **react-dom** | `^19.2.3` | UI runtime |
| **typescript** | `^5` | Types |
| **tailwindcss** | `^4` (+ `@tailwindcss/postcss`, `@tailwindcss/typography`) | Utility layout and prose styling. The notebook design language sits alongside it in hand-written CSS |
| **tw-animate-css** | `^1.3.5` | Animation utilities |
| **class-variance-authority** / **clsx** / **tailwind-merge** | — | Variant-driven component styling and safe class merging |
| **postcss** | `8.5.26` (override) | Pinned via `overrides` so no transitive resolves a different build |

---

## 12. Frontend — components and interaction

| Package | Version | Specific role |
|---|---|---|
| **@radix-ui/react-*** (18 packages) | — | Accessible primitives: dialog, dropdown, popover, select, tabs, tooltip, accordion, scroll-area, radio-group, checkbox, collapsible, progress, separator, label, slot, alert-dialog |
| **lucide-react** | `^0.525.0` | Icon set across the shell, dock, and gallery |
| **cmdk** | `^1.1.1` | The command bar (`CommandBar`) |
| **framer-motion** | `^12.42.0` | Shell transitions. Motion is compositor-only (transform/opacity/shadow) and fully disabled under `prefers-reduced-motion` |
| **sonner** | `^2.0.6` | Toasts |
| **react-resizable-panels** | `^2.1.9` | The workspace's resizable three-pane layout |
| **@tanstack/react-virtual** | `^3.13.24` | Virtualises long source and note lists |
| **@xyflow/react** | `^12.11.1` | The knowledge-graph / relation canvas |
| **react-hook-form** + **@hookform/resolvers** | `^7.60` / `^5.1.1` | All settings and credential forms |
| **use-debounce** | `^10.0.6` | Search-as-you-type and autosave throttling |

---

## 13. Frontend — data, state, content

| Package | Version | Specific role |
|---|---|---|
| **@tanstack/react-query** | `^5.83.0` | Server-state cache with targeted invalidation — e.g. a visual-extraction command invalidates only that source's row |
| **zustand** | `^5.0.6` | Client state. `display-preferences-store` (with `persist`) holds the density preference — Comfortable by default |
| **axios** | `^1.18.1` | HTTP client under the generated API layer |
| **zod** | `^4.0.5` | Runtime validation of API responses, including `source-visuals.ts` — this is what makes the `state: "disabled"` sentinel type-safe on the client |
| **@codemirror/*** (6 packages) | — | The Markdown note editor: state, view, language, markdown mode, search, commands |
| **@uiw/react-md-editor** | `^4.0.8` | Higher-level Markdown editing surface |
| **react-markdown** + **remark-gfm** + **remark-math** + **rehype-katex** + **katex** | — | Renders chat answers and notes: GitHub-flavoured Markdown plus LaTeX math |
| **react-pdf** | `^10.4.1` | In-app PDF viewing for PDF sources |
| **date-fns** | `^4.1.0` | Relative timestamps ("updated 3 minutes ago") |
| **i18next** + **react-i18next** + **i18next-browser-languagedetector** | — | UI localisation; locale files under `src/lib/locales/` |

---

## 14. Testing

| Tool | Version | Specific role |
|---|---|---|
| **pytest** | `>=9.0.3,<10` | 4,906 backend tests + 940 desktop tests. Migrated 8→9 this cycle (PYSEC-2026-1845) |
| **pytest-asyncio** | `>=1.2.0,<2` | Async route and graph tests |
| **vitest** | `^4.1.8` | 1,832 frontend unit tests. Run `--pool=forks --maxWorkers=1` — parallel workers made the jsdom suites flaky |
| **@testing-library/react** + **jest-dom** | `^16.2.0` / `^6.6.3` | Component tests written against user-visible behaviour |
| **jsdom** | `^26.0.0` | DOM for vitest |
| **@vitejs/plugin-react** | `^4.3.4` | JSX transform for the test build |
| **@playwright/test** | `1.61.1` (exact) | E2E, three projects: `mocked-browser`, `native-runtime`, `packaged-device`. `workers: 1`, port 3117. Exact pin because visual-diff baselines are browser-build sensitive |
| **@vitest/ui** | `^4.1.8` | Local test UI |
| **remark-parse** / **unified** | — | Dev-only: assertions over rendered Markdown structure |

---

## 15. Build, quality, and release tooling

| Tool | Where | Specific role |
|---|---|---|
| **uv** | `Makefile`, bootstrap | Dependency resolution, lockfile generation, and the runtime venv install |
| **ruff** | `>=0.14.13`, pre-commit | Lint (`E,F,I,UP006,UP007`) + format. Its import reflow has twice broken a source-shape test — one file carries `# noqa: I001` for exactly that reason |
| **mypy** | `>=1.11.1` | Optional static checking (dev extra) |
| **pre-commit** | `>=4.1.0` | ruff check/format, check-yaml/json/toml, EOF fixer, trailing whitespace, merge-conflict marker, >1 MB file guard. Pytest is deliberately **not** in the hook — too slow |
| **bandit** (via `uvx`) | `make security-scan` | SAST at `--severity-level high` over `deeper_notebook api desktop`. Drove the B608 burn-down from 79 → 0 |
| **pip-audit** (via `uvx`) | `make security-scan` | Dependency CVE scan against `desktop/requirements.lock` |
| **gitleaks** | pre-commit + push range | Secret scanning. The 574-commit push scan returned one hit, verified as a test fixture string |
| **eslint** + **eslint-config-next** | `^9` / `^16.2.12` | Frontend lint |
| **@next/bundle-analyzer** | `^16.2.12` | Bundle-budget verification — the Source Visual Gallery landed at −4 bytes gzip |
| **`scripts/rebrand_audit.py`** | 2,896 lines | Product-identity governance. Line-pinned, self-validating allowlist keyed on `(path, pattern, source, line, column, context_sha256)`. `unexpected_active_identity` must stay at 0 or the build fails |
| **`scripts/verify-feature-env-build.mjs`** | frontend | Proves `NEXT_PUBLIC_*` literals were actually inlined into the built chunks |
| **`scripts/create-signing-identity.sh`** | — | Creates the stable self-signed identity that keeps macOS TCC grants across rebuilds. (Two real bugs recorded: `-r trustRoot` not `trustAsRoot`; never use `find-identity -v`, which hides untrusted identities) |
| **`scripts/backup_restore.py`** | `make backup/restore/verify-backup` | DB backup with verification |
| **`scripts/benchmark_models.py`**, **`measure_source_visuals.py`** | — | The measurement harnesses behind the performance numbers |
| **Make** | 40+ targets | The whole build graph: `build-mac` = test → lock → venv → frontend → runtimes → pyinstaller → dmg |
| **PyInstaller spec** | `desktop/build/pyinstaller.spec` | Bundle assembly, including the fetched runtimes |
| **`post_build_mac.sh`**, **`package_smoke.py`**, **`release_manifest.py`** | — | Codesign, packaged smoke test, and release manifest generation |

---

## 16. CI/CD

| Workflow | Specific role |
|---|---|
| `.github/workflows/test.yml` | Backend + frontend test suites on push/PR |
| `build-desktop.yml` | macOS `.app` / `.dmg` build |
| `build-windows.yml` | Windows packaging |
| `build-and-release.yml` | Tags and publishes the server container image, versioned from `pyproject.toml` (`1.8.5`) |
| `build-dev.yml` | Development builds |
| `claude.yml`, `claude-code-review.yml` | Claude Code automation on PRs |

Also present: issue templates (bug / feature / installation) and a PR template.

---

## 17. Containerisation (server track only)

| Asset | Specific role |
|---|---|
| `Dockerfile` | Multi-service server image |
| `Dockerfile.single` | Single-container variant |
| `docker-buildx-*` make targets | Multi-arch build setup |

**The desktop app never runs in Docker.** These exist for the upstream-compatible server
deployment, which is a separate distribution track.

---

## 18. External services (all optional, all fail-soft)

### LLM providers
OpenAI · Anthropic · Google (Gemini) · Groq · Mistral · DeepSeek · Ollama (local HTTP).
Absent key ⇒ absent from model discovery. No provider is required — a fully local
configuration is a supported first-class state.

### Web search — the failover chain
| Provider | Key | Role in the chain |
|---|---|---|
| **Serper** | required | Primary paid provider |
| **Tavily** | required | Second paid provider |
| **Brave** | required | Third paid provider |
| **SearXNG** | none (self-hosted URL) | Free instance; an empty result advances the chain |
| **Wikipedia** | **none** | Keyless terminal fallback — guarantees search works with zero configuration. This is the provider the Python runtime bump was made for |

Rule worth noting: a **paid** provider returning empty is a legitimate answer and ends the
chain; a **free** one advances. Falling through on paid results would double the bill for
no information.

*Rejected after live testing:* DuckDuckGo. Both the HTML endpoint and the official Instant
Answer API return HTTP 202 anti-bot challenges to scripted clients. A working parser was
written, unit-tested, and then deleted rather than ship something that looks configured and
returns nothing.

### Scholarly
**OpenAlex** and **arXiv** — keyless, behind a separate `scholarly_search` tool rather than
extra entries in the web-search chain (Wikipedia terminates that chain first, and a paper
is a poor answer to a price question). arXiv XML is size-bounded at 5 MB before parsing.

### Other
**Hugging Face** (model snapshot downloads) · **MCP servers** (user-configured, any
transport the client supports) · **Gmail** (connected-service integration) ·
**OpenChronicle** (local personal-memory bridge via a desktop shim) · **GitHub Releases**
(update check against the Deeper-Notebook repo).

---

## 19. Platform APIs

| API | Specific role |
|---|---|
| **macOS TCC** | Consent for file access. Ad-hoc signing resets grants on every rebuild, which presents as a silent launch wedge — hence the stable self-signed identity |
| **`codesign` / `security`** | Signing and keychain identity management. Verify with `-dvv`; `-dv` is not enough |
| **`hdiutil`** | DMG creation |
| **macOS menubar / tray** | `desktop/tray.py` |
| **Screen geometry APIs** | `window_state.py` persists window position and validates it against currently attached displays |

---

## 20. Notable *absences* — and why

| Not used | Why |
|---|---|
| **Electron / Tauri** | A Python backend is already required; Electron would ship a second full runtime for no gain |
| **PostgreSQL + pgvector** | SurrealDB gives documents, graph edges, and vectors in one bundled binary with no user install |
| **An ORM** | SurrealQL's graph traversal syntax has no mature ORM. The discipline is whitelist-identifiers + `$`-bound values, enforced by Bandit |
| **Redis / Celery** | `surreal-commands` runs background jobs against the database already present |
| **Notarization** | Owner decision. First launch of a fresh DMG needs right-click → Open |
| **A cloud dependency of any kind at runtime** | The governing product constraint: it must work with the network cable unplugged |

---

## 21. Accepted security residuals

| Package | Advisory | Reason |
|---|---|---|
| `pillow 11.3.0` | ~20 PYSECs | DN-DEP-PILLOW-2026-08-11 — `podcast-creator → moviepy` requires `<12`. Floor held, residuals reported, no overrides |
| `diskcache 5.6.3` | PYSEC-2026-2447 | No fixed release exists |

Bandit MEDIUMs remaining (4, all triaged false positives): `B108` (`/tmp` appearing in a
**denylist** of forbidden roots), `B102` (a build tool `exec`ing the repo's own version
file), `B310` (`urlopen` against a hardcoded `127.0.0.1`), `B314` (parsing the repo's own
canonical SVG at build time).

Full triage: `docs/verification/2026-08-16-security-scan.md`.

---

*Companion documents: [PROJECT-DEEP-DIVE.md](./PROJECT-DEEP-DIVE.md) (architectural review context) and the
15-part recreation set, `01-project-overview-architecture.md` → `15-file-structure-code-organization.md`.*
