# DEEPER_NOTEBOOK_* Environment Variable Reference

Deeper Notebook uses `DEEPER_NOTEBOOK_*` environment variables for its
desktop and local-first configuration. They tune the v0.7.x context-
overflow caps, the connection pool, file logging, the source upload
ceiling, and encryption key rotation. **All have sensible defaults
— set them only when you need to deviate.**

This file is the canonical reference. The shorter cheatsheet lives in
`.env.example`.

> All knobs use defensive parsing: garbage values fall back to the
> default with a logged WARNING. Below-minimum values (typo guard)
> also fall back. Watch the API log if a knob isn't taking effect.

## Compatibility aliases and precedence

Settings resolve in this deterministic order:

1. `DEEPER_NOTEBOOK_<NAME>` (canonical long name)
2. `DN_<NAME>` (canonical short name, where supported)
3. `OPEN_NOTEBOOK_<NAME>` (deprecated legacy long name)
4. `ONP_<NAME>` (deprecated legacy short name)
5. Built-in default

An explicitly empty higher-precedence value still wins. Legacy names remain
functional during the staged migration and produce value-free deprecation
warnings. Child processes receive the winning value under canonical names and
compatibility mirrors. Secret values and `*_FILE` contents never appear in
warnings or resolution receipts.

---

## Encryption (v0.7.17)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_ENCRYPTION_KEY` | _(none — required)_ | Single passphrase used to derive the Fernet key. Encrypts API credentials at rest. |
| `DEEPER_NOTEBOOK_ENCRYPTION_KEYS` | _(unset)_ | Comma-separated list for rotation. First entry is the active key (used for all new encryption); remaining entries are accepted for decryption only. Overrides the singular var when set. |
| `DEEPER_NOTEBOOK_ENCRYPTION_KEY_FILE` | _(unset)_ | Docker-secrets variant: path to a file containing the key. Same precedence as the env var. |

**Rotation workflow:**

1. Set `DEEPER_NOTEBOOK_ENCRYPTION_KEYS="new-secret,old-secret"` and restart.
2. (Optional) Run a sweep that calls `re_encrypt_value(blob)` on each
   stored credential. Once swept, the old key is no longer needed.
3. Set `DEEPER_NOTEBOOK_ENCRYPTION_KEYS="new-secret"` and restart.

If you drop the old key BEFORE running the sweep, existing data
becomes undecryptable until you re-add it. The error message points
you at this case explicitly.

---

## Logging (v0.7.14)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_LOG_DIR` | `~/.deeper-notebook/logs` | Directory for rotated log files. Created if missing. |
| `DEEPER_NOTEBOOK_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `DEEPER_NOTEBOOK_LOG_JSON` | `0` | Set to `1` for a parallel `<component>.jsonl` file (for log aggregators). |

**Files written:**
- `api.log` (rotated at 20 MB, kept 14 days, gzip-compressed)
- `launcher.log` + `bootstrap.log` (already handled by the desktop bundle)

---

## Health probes (v0.7.15)

These are endpoints, not env vars — listed here for discoverability.

| Endpoint | Use |
|---|---|
| `GET /livez` | Process responding. No I/O. Used by external watchdogs. |
| `GET /readyz` | Full dependency check: DB reachable + migrations applied. 200 on success, 503 on any failure. |
| `GET /health` | Backward-compat alias for `/livez`. |

---

## Source upload byte cap (v0.7.16)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_SOURCE_UPLOAD_MAX_BYTES` | `524288000` (500 MB) | Hard cap on POST /api/sources upload size. Returns 413 if exceeded. Minimum 1 MB (typo guard). |

Browser uploads pass through the Next.js rewrite proxy first. The bundled
frontend defaults `proxyClientMaxBodySize` to `500mb` so the UI path matches the
backend default. If you rebuild the frontend with a larger backend cap, make
sure the Next.js proxy and any external reverse proxy are raised too.

---

## SurrealDB connection pool (v0.7.18)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_DB_POOL_SIZE` | `4` | Number of pooled `AsyncSurreal` connections. Range 1–32. |
| `DEEPER_NOTEBOOK_DB_POOL_DISABLED` | _(unset)_ | Set to `1` to fall back to per-query open/close (legacy behavior, useful for debugging). |

---

## Studio one-shot generation (v0.7.4)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_STUDIO_MAX_FILE_CHARS` | `15000` | Per-file character ceiling for the Studio extractor. Local-model-friendly. |
| `DEEPER_NOTEBOOK_STUDIO_MAX_COMBINED_CHARS` | `60000` | Combined-input ceiling across all files in one Studio request. |

---

## Chat LLM server (v0.7.5, v0.7.8)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_CHAT_LLM_CTX` | `16384` | `--n_ctx` for the spawned `llama-cpp-python` server. Modern local models (Hermes-3, Qwen 2.5/3.x, Mistral-7B, Llama-3.2) support 32k–131k; raise this if your hardware allows. |
| `DEEPER_NOTEBOOK_CHAT_TIMEOUT_S` | `30` | Per-turn timeout for the memory writer's LLM call. |
| `DEEPER_NOTEBOOK_CHAT_MODEL_NAME` | `default` | Model name string passed to the local OpenAI-compatible endpoint. Most local servers accept `default` or echo back whatever was loaded. |
| `DEEPER_NOTEBOOK_CHAT_RAM_GB_CEILING` | _(unset)_ | Optional ceiling for the capability-aware spawner. Skips models the host can't run. |

---

## Ask graph result caps (v0.7.9)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_ASK_MAX_RESULTS` | `10` | Max rows kept from `vector_search` before feeding the LLM. |
| `DEEPER_NOTEBOOK_ASK_PER_RESULT_CHAR_CAP` | `1500` | Per-result `matches` content cap (truncation marker appended). Minimum 200. |

---

## Transformation input cap (v0.7.10)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_TRANSFORMATION_INPUT_CAP` | `12000` | `source.full_text` (or `input_text`) character cap before the LLM call. Minimum 500. |

---

## Chat history caps (v0.7.11 + v0.7.13)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_CHAT_HISTORY_CHAR_CAP` | `12000` | Total character budget for persisted message history in the standard chat graph. Older turns dropped from the front; current turn always kept. |
| `DEEPER_NOTEBOOK_SOURCE_CHAT_HISTORY_CHAR_CAP` | `8000` | Same idea, separate knob for source-chat (which already burns context budget on injected source + insight content). |

Both have a minimum of 500. Truncation prepends a `SystemMessage`
marker so the model knows context was elided.

---

## source_chat context caps (v0.7.12)

| Variable | Default | Purpose |
|---|---|---|
| `DEEPER_NOTEBOOK_SOURCE_CHAT_SOURCE_CHAR_CAP` | `4000` | Source `full_text` cap inside `_format_source_context`. Minimum 500. |
| `DEEPER_NOTEBOOK_SOURCE_CHAT_INSIGHT_CHAR_CAP` | `1000` | Per-insight content cap. Minimum 200. |
| `DEEPER_NOTEBOOK_SOURCE_CHAT_MAX_INSIGHTS` | `10` | Max insights injected. Excess elided with a footer noting how many were dropped. |

---

## Piper TTS shim (v0.7.7)

| Variable | Default | Purpose |
|---|---|---|
| (no env var) | _(internal cap)_ | The shim hard-caps a single TTS request at 50,000 chars (about 10 minutes of audio). Callers that need more must split the script into segments. |

---

## Internal / launcher-injected (not user-configurable)

These are set by `desktop/launcher.py` when spawning child processes
and shouldn't be set manually:

- `DEEPER_NOTEBOOK_MEMORY_URL` — memory-shim endpoint (when the memory bundle is enabled)
- `DEEPER_NOTEBOOK_MEMORY_INJECTED` — flag indicating memory layer is wired
- `DEEPER_NOTEBOOK_STT_URL` — local speech-to-text shim endpoint
- `DEEPER_NOTEBOOK_TTS_URL` — local text-to-speech shim endpoint
- `DEEPER_NOTEBOOK_VOICE_INJECTED` — flag indicating voice stack is wired
- `DEEPER_NOTEBOOK_REMIND_OPENCHRONICLE` — first-run wizard reminder flag

---

## Studio multi-page generation (v0.7.89 → v0.7.101)

Studio uploads produce a multi-page brief (overview + N pages w/ inline
AI suggestions). Every LLM and file-extraction call is timeout-bounded.

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_MULTIPAGE` | true | Kill switch back to single-note output |
| `DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_PAGES_MAX` | 6 | Hard cap on page count (2..12) |
| `DEEPER_NOTEBOOK_STUDIO_NOTEBOOK_PARALLEL_PAGES` | false | Run page LLM calls concurrently (cloud opt-in) |
| `DEEPER_NOTEBOOK_STUDIO_OUTLINE_TIMEOUT_SEC` | 90 | Outline-pass LLM call |
| `DEEPER_NOTEBOOK_STUDIO_PAGE_TIMEOUT_SEC` | 180 | Per-page LLM call (also covers legacy single-note fallback) |
| `DEEPER_NOTEBOOK_STUDIO_EXTRACT_TIMEOUT_SEC` | 60 | `content_core.extract_content()` per file |

## Studio artifact retention (v0.8.124)

Background job that prunes old Studio artifact revisions and unlinks
stale-and-old export files. **Disabled by default** — see
`deeper_notebook/studio/retention.py` for the rationale (Studio content
is user-facing, unlike LangGraph checkpoint rows, so this job requires
an explicit opt-in rather than defaulting to "on"). To try it safely
before enabling deletion, set the interval and `STUDIO_RETENTION_DRY_RUN=true`
first and check the logs / `onp_studio_retention_runs_total` metric.

| Env var | Default | What it controls |
|---|---:|---|
| `DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS` | `0` (disabled) | How often the retention job runs. `0` = job never runs. Set > 0 to opt in. |
| `DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT` | `10` | Per top-level artifact, how many of its newest revisions to keep; older revisions are deleted (which also removes their export files). `<= 0` disables revision pruning. |
| `DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS` | `30` | An export file is only unlinked if it is BOTH stale (content hash no longer matches, or the recorded path/hash is missing) AND older than this many days on disk. `<= 0` disables stale-export pruning. |
| `DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN` | `false` | Accepts `true`/`1`/`yes`. When true, the job counts everything it would delete/unlink but changes nothing — use this to preview a run before enabling deletion. |

## LLM-call timeouts (v0.7.93, v0.7.95, v0.7.99)

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_NOTE_TITLE_TIMEOUT_SEC` | 60 | Auto-title generation on `POST /notes` |
| `DEEPER_NOTEBOOK_TRANSFORMATION_TIMEOUT_SEC` | 180 | `POST /transformations/execute` |
| `DEEPER_NOTEBOOK_CHAT_TIMEOUT_SEC` | 300 | Non-streaming `/chat/execute` (streaming `/chat/stream` is naturally bounded by SSE disconnect) |

## Memory-recall timeouts (v0.7.113 + v0.7.114)

`recall_memory()` runs on every chat turn; unbounded embed-or-query
calls would stall chat. Timeout falls through to recency-only recall
so chat never blocks.

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_MEMORY_RECALL_EMBED_TIMEOUT_SEC` | 5 | Embed call on the recall query string |
| `DEEPER_NOTEBOOK_MEMORY_RECALL_QUERY_TIMEOUT_SEC` | 5 | Each SurrealQL query in the recall path |
| `DEEPER_NOTEBOOK_MEMORY_RECALL_BUDGET_SEC` (v0.7.133) | 12 | Outer wall on the whole recall flow |
| `DEEPER_NOTEBOOK_MEMORY_RECALL_MODE` (v0.7.84) | `auto` | `recent` \| `semantic` \| `auto` recall strategy |

## Memory recall content + retention (v0.8.49 + v0.8.50)

| Env var | Default | What it controls |
|---|---:|---|
| `DEEPER_NOTEBOOK_MEMORY_RECALL_EPISODES` (v0.8.49) | `1` (on) | Recall whole-session summaries (`memory_episode`) into the chat prompt, alongside facts/preferences. Set `0`/`false`/`off` to suppress (stops old conversations resurfacing; reclaims ~1k chars of prompt budget). |
| `DEEPER_NOTEBOOK_MEMORY_KEEP_PER_TABLE` (v0.8.50) | `500` | Retention ceiling — newest N rows kept per memory table (`memory_fact`/`preference`/`episode`); older rows are pruned at session end and behind a per-turn high-water gate. Closes the unbounded-growth gap (Finding #3). Invalid / `<1` values fall back to the default. |
| `DEEPER_NOTEBOOK_MEMORY_BATCH_TURNS` (v0.8.54) | `1` | Fact-extraction batching. `1` = extract once per turn (default, unchanged). `N>1` buffers turns per session and runs ONE extraction over the combined transcript every N turns (drained at session end) — fewer LLM calls + whole-conversation context. Invalid / `<1` → `1`. |
| `DEEPER_NOTEBOOK_MEMORY_CONFIDENCE_FLOOR` (v0.8.55) | `0.0` | Drop extracted facts/preferences whose model-assigned confidence is below this (0.0-1.0). `0.0` = keep everything (default, unchanged). Raise it to filter speculative/low-confidence memories. A missing/garbled score counts as `1.0` (never dropped). Invalid / out-of-range → `0.0`. |

## Fail-closed privacy gate (v0.8.51, Phase 5.2a)

| Env var | Default | What it controls |
|---|---:|---|
| `DEEPER_NOTEBOOK_PRIVACY_GATE` | `off` | When `on` (aliases `1`/`true`/`yes`/`local`), turns the smart router would send to **cloud** are scanned for structured secrets/PII (API keys, AWS/GitHub/Google/Slack tokens, private-key blocks, SSNs, Luhn-valid card numbers, emails, `secret=`-style assignments). On a hit the turn is kept on the **local** model instead; if no local model is configured the request is **blocked** (HTTP 422) rather than leaked. Off → zero change to routing. |

| `DEEPER_NOTEBOOK_PRIVACY_CLASSIFIER_URL` (v0.8.57) | _unset_ | Optional local OpenAI-compatible endpoint for a model-backed PII layer that catches **unstructured** PII (names/addresses/health in prose) the regex misses. Unset → regex-only (the v0.8.51 behaviour). Set to a URL, or to `auto` (v0.8.59 — aliases `sidecar`/`chat-sidecar`/`local`) to reuse the running local chat sidecar as the classifier (no second model to provision). Findings are UNIONed with the regex floor (model can only catch *more*); best-effort (a flaky/missing classifier never blocks chat). Only called on gate-on, cloud-bound turns. |
| `DEEPER_NOTEBOOK_PRIVACY_CLASSIFIER_MODEL` (v0.8.57) | `default` | Model name sent to the classifier endpoint. |
| `DEEPER_NOTEBOOK_PRIVACY_CLASSIFIER_TIMEOUT_SEC` (v0.8.57) | `5` | Per-call timeout for the classifier; invalid/≤0 → 5. |

Scope: gates the **auto-route cloud-fallback** path only (turns going through
the smart router). The regex floor catches *structured* secrets reliably; the
optional `DEEPER_NOTEBOOK_PRIVACY_CLASSIFIER_URL` model layer (v0.8.57) adds unstructured
PII. See `docs/7-DEVELOPMENT/phase-5-advanced-memory.md`.

## Agent-loop FSM (v0.8.52 core, v0.8.53 ask gate, Phase 5.3)

| Env var | Default | What it controls |
|---|---:|---|
| `DEEPER_NOTEBOOK_AGENT_FSM` | `on` | Enabled by default (aliases `1`/`true`/`yes`): (a) the `ask` graph declares `clarify` and asks the user to refine instead of synthesizing an ungrounded answer when no searches returned grounded content (v0.8.53); and (b) the chat MCP tool loop lets the model declare a terminal `<state>complete|clarify</state>`, surfaced as `agent_state` on the chat response / stream `done` event (v0.8.60 — `clarify` = the model paused to ask the user). Set `0`/`false`/`off` to roll back to the pre-FSM behavior. |

## Native chat web search (v0.8.64 + v0.8.65)

Opt-in: the built-in `web_search` chat tool is bound only when at least one
provider below is configured — no key/URL → tool absent → zero behaviour change.
When several are set they form a **failover chain** (precedence Serper > Tavily >
SearXNG; an attempt that errors falls through to the next). Public SearXNG
mirrors usually block the JSON API — see
**[Private SearXNG](private-searxng-web-search.md)** to run your own.

| Env var | Default | What it controls |
|---|---:|---|
| `SERPER_API_KEY` | _unset_ | Serper (Google Search API) — https://serper.dev |
| `TAVILY_API_KEY` | _unset_ | Tavily search API — https://tavily.com |
| `SEARXNG_BASE_URL` | _unset_ | SearXNG instance URL. Comma-separate several for per-instance failover, e.g. `http://127.0.0.1:8889/,https://searx.example/` |
| `DEEPER_NOTEBOOK_WEB_SEARCH_PROVIDER` | `auto` | Force one of `serper`/`tavily`/`searxng` (a stale value naming an unconfigured provider is ignored → auto) |
| `DEEPER_NOTEBOOK_WEB_SEARCH_MAX_RESULTS` | `5` | Results per query (clamped 1–20) |
| `DEEPER_NOTEBOOK_WEB_SEARCH_TIMEOUT_SEC` | `10` | Per-request HTTP timeout (1–60) |
| `DEEPER_NOTEBOOK_WEB_SEARCH_TOTAL_BUDGET_SEC` | `25` | Total wall-clock across the whole failover chain (1–120; kept under the 30s chat tool-call timeout). Each attempt gets `min(per-attempt, remaining budget)` so a slow/hanging instance can't starve a fast later one. |

> The `web_search` tool only fires if the active chat model supports tool/function
> calling — most cloud models do; many small local GGUFs do not.

## Connection-test + discover (v0.7.100 + v0.7.110 + v0.7.116)

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_CONNECTION_TEST_TIMEOUT_SEC_<PROVIDER>` | per-provider | Override for one provider (e.g. `..._OLLAMA=120`) |
| `DEEPER_NOTEBOOK_CONNECTION_TEST_TIMEOUT_SEC` | varies | Global override (applies to all providers) |
| `DEEPER_NOTEBOOK_DISCOVER_MODELS_TIMEOUT_SEC` | 30 | `/credentials/{id}/discover` provider list-models |

Per-provider defaults (v0.7.116): cloud APIs (openai, anthropic,
google, groq, mistral, deepseek, xai, voyage) → 10s. Slower
cloud-routing providers (openrouter, elevenlabs, azure, vertex,
dashscope, minimax) → 15s. Local-server providers (ollama,
openai_compatible) → 60s for cold-start model-load latency.

## Search + bulk endpoint caps (v0.7.102 + v0.7.110)

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_SEARCH_TIMEOUT_SEC` | 60 | `/search` text + vector queries |
| `DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC` | 10 | Heartbeat frame interval on the streaming endpoints (`/chat/stream`, `/search/ask`, source chat) while the model is silent; `0` disables heartbeats (v0.8.116) |
| `DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC` | 300 | Server-side idle limit: if the model produces nothing for this long the stream ends with an error frame and the model call is cancelled; `0` disables (v0.8.116). The client-side guard is `NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS` (default 60 s) |
| `DEEPER_NOTEBOOK_BULK_VECTORIZE_MAX_SOURCES` | 500 | Per-request cap on `POST /notebooks/{id}/vectorize_sources` |

## Command-queue submission (v0.7.115)

Async job submission (embeddings, podcasts, source extract) opens a
sync SurrealDB WS handshake. Already wrapped in `asyncio.to_thread`
(v0.7.55); v0.7.115 adds a timeout around the `await`.

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_SUBMIT_COMMAND_TIMEOUT_SEC` | 10 | `submit_command` blocking handshake |

---

## Synchronous source processing (v0.8.126)

`POST /api/sources` with `async_processing=false` runs source processing
in-process — `await`ing the `process_source_command` function directly
instead of queueing it through `execute_command_sync` — because the sync
path has no `surreal-commands-worker` process to service the SurrealDB
command queue. Previously this always hung for the full 300s timeout and
failed.

| Env var | Default | What it bounds |
|---|---:|---|
| `DEEPER_NOTEBOOK_SOURCE_SYNC_TIMEOUT_SEC` | 300 | In-process `process_source_command` call on the sync source-creation path |

---

## API server process (`run_api.py`, v0.8.126)

Plain `os.getenv` process settings for the uvicorn-hosted API server —
not `DEEPER_NOTEBOOK_*` product settings, so they have no `DN_` / `ONP_`
aliases.

| Env var | Default | Purpose |
|---|---:|---|
| `API_HOST` | `127.0.0.1` | Bind host for the API server |
| `API_PORT` | `5055` | Bind port for the API server |
| `API_RELOAD` | `true` | Enable uvicorn's file-watching auto-reload (dev only) |
| `API_GRACEFUL_SHUTDOWN_SEC` | 20 | `uvicorn.run(..., timeout_graceful_shutdown=...)` — bounds how long the old process waits for in-flight requests to finish before a dev reload (or shutdown) forces it closed. Without this, a long-running request (e.g. the sync source-processing path above) could keep the previous process holding the port indefinitely across a reload. |

---

## Background worker heartbeat (v0.8.127)

Queued work (podcasts, embeddings, async source processing, Studio
generation) only runs while a `surreal-commands-worker` process is
alive. `commands/__init__.py` starts a daemon thread — only inside the
actual worker process, never the API process, which also imports
`commands` — that upserts a `worker_heartbeat:primary` row roughly once
a minute. `/healthz/deep` reports a `worker` subsystem from that row:
`degraded` (never `not_ready`) when the heartbeat is missing or stale,
since chat/search/notes all work without a worker.

| Env var | Default | Purpose |
|---|---:|---|
| `DEEPER_NOTEBOOK_WORKER_PROCESS` | _(unset)_ | Set to `1` on the worker's own process env so `is_worker_process()` detects it without relying on argv alone. Set automatically by the Makefile `worker`/`start-all` targets and the desktop launcher's worker spawn — not something operators normally set by hand. |
| `DEEPER_NOTEBOOK_WORKER_HEARTBEAT_STALE_SEC` | 180 | Age (seconds) past which the last heartbeat is considered stale and `/healthz/deep`'s `worker` check reports `offline` |

---

## Version history of caps

| Version | What changed |
|---|---|
| v0.7.4 | Studio file/combined caps env-driven |
| v0.7.5 | Memory writer timeout + model name env vars |
| v0.7.7 | Piper input cap (internal constant) |
| v0.7.8 | Chat LLM `n_ctx` env var |
| v0.7.9 | Ask graph result + content caps |
| v0.7.10 | Transformation input cap |
| v0.7.11 | Chat message history cap |
| v0.7.12 | source_chat context caps |
| v0.7.13 | source_chat history cap (separate from chat) |
| v0.7.14 | Logging env vars |
| v0.7.16 | Source upload byte cap |
| v0.7.17 | Encryption key rotation (`OPEN_NOTEBOOK_ENCRYPTION_KEYS`) |
| v0.7.18 | DB pool size + disable flag |
| v0.7.89 | Studio multi-page generation env vars |
| v0.7.92 | `ONP_STUDIO_NOTEBOOK_PARALLEL_PAGES` |
| v0.7.93 | Studio outline + per-page timeouts |
| v0.7.95 | `ONP_NOTE_TITLE_TIMEOUT_SEC`, `ONP_TRANSFORMATION_TIMEOUT_SEC` |
| v0.7.99 | `ONP_CHAT_TIMEOUT_SEC` for non-streaming chat |
| v0.7.100 | `ONP_CONNECTION_TEST_TIMEOUT_SEC` (global) |
| v0.7.101 | `ONP_STUDIO_EXTRACT_TIMEOUT_SEC` |
| v0.7.102 | `ONP_SEARCH_TIMEOUT_SEC` |
| v0.7.110 | `ONP_DISCOVER_MODELS_TIMEOUT_SEC`, `ONP_BULK_VECTORIZE_MAX_SOURCES` |
| v0.7.113 | `ONP_MEMORY_RECALL_EMBED_TIMEOUT_SEC` |
| v0.7.114 | `ONP_MEMORY_RECALL_QUERY_TIMEOUT_SEC` |
| v0.7.115 | `ONP_SUBMIT_COMMAND_TIMEOUT_SEC` |
| v0.7.116 | Per-provider `ONP_CONNECTION_TEST_TIMEOUT_SEC_<PROVIDER>` |
| v0.8.116 | `ONP_STREAM_HEARTBEAT_SEC`, `ONP_STREAM_IDLE_TIMEOUT_SEC` |
| v0.8.126 | `ONP_SOURCE_SYNC_TIMEOUT_SEC`; `API_GRACEFUL_SHUTDOWN_SEC` and `API_RELOAD` documented |
| v0.8.127 | `DN_WORKER_PROCESS`, `DN_WORKER_HEARTBEAT_STALE_SEC` |
