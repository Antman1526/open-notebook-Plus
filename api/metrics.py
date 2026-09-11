"""ONP v0.7.124 — Prometheus metrics surface.

Gives operators visibility into the RED-method classics (Rate / Errors
/ Duration) plus the open-notebook-specific signals that the
v0.7.88-v0.7.123 timeout-coverage cycle made interesting:

  * HTTP request counter + latency histogram (by route + method + status)
  * Database query latency histogram + slow-query counter (matches
    v0.7.120's `DEEPER_NOTEBOOK_SLOW_QUERY_LOG_MS` threshold log line)
  * Memory-recall fall-through counter (v0.7.113 / v0.7.114 — counts
    times the chat-hot-path recall hit a timeout and returned empty)

Exposed at `/metrics` in standard Prometheus exposition format. The
endpoint is auth-exempt (operators / Prometheus scrapers / dashboards
poll it without credentials) and excluded from `RequestIDMiddleware`'s
log noise via short-circuit.

All counters use the canonical `_total` suffix per Prometheus naming
conventions; histograms use buckets sized for the typical p99 range
of each operation type.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)

# -------------------------------------------------------------------- #
# HTTP request metrics (set by the per-request middleware below)
# -------------------------------------------------------------------- #

http_requests_total = Counter(
    "onp_http_requests_total",
    "Total HTTP requests handled by the API.",
    ["method", "route", "status_code"],
)

# Buckets cover the realistic API latency range:
# 5ms (cache hit) → 30s (slow LLM). The 0.005-second floor captures
# fast healthcheck paths; the 30-second ceiling catches the
# almost-timed-out 504s.
http_request_duration_seconds = Histogram(
    "onp_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# -------------------------------------------------------------------- #
# Database query metrics (called from repository.repo_query)
# -------------------------------------------------------------------- #

db_query_duration_seconds = Histogram(
    "onp_db_query_duration_seconds",
    "SurrealQL query latency in seconds.",
    # Buckets tuned for SurrealDB: most queries 1-50ms; the long tail
    # is vector-search (50-500ms) and the occasional pool-acquire
    # delay or migration (1-5s).
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

db_slow_queries_total = Counter(
    "onp_db_slow_queries_total",
    "Number of SurrealQL queries that exceeded "
    "DEEPER_NOTEBOOK_SLOW_QUERY_LOG_MS (default 500ms).",
)


# -------------------------------------------------------------------- #
# Memory recall metrics (called from memory_recall)
# -------------------------------------------------------------------- #

memory_recall_fallthrough_total = Counter(
    "onp_memory_recall_fallthrough_total",
    "Number of times chat-hot-path memory recall hit a timeout or "
    "error and fell through to empty. Reasons: 'embed_timeout' / "
    "'embed_error' / 'query_timeout' / 'query_error' (per-step, "
    "v0.7.113 / v0.7.114), 'outer_budget' (v0.7.133 outer "
    "DEEPER_NOTEBOOK_MEMORY_RECALL_BUDGET_SEC wall fired).",
    ["reason"],
)

memory_recall_seconds = Histogram(
    "onp_memory_recall_duration_seconds",
    "Total memory-recall duration (embed + DB queries) per chat turn.",
    # Most calls 5-200ms; cap at the ~15s worst-case ceiling (1 embed
    # at 5s + 2 queries at 5s each).
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0),
)

# v0.8.51 — Phase 5.2a privacy gate. How often the fail-closed gate caught
# structured secrets/PII in a turn the router would have sent to cloud.
# outcome='local' → rerouted on-device; 'blocked' → no local model, request
# blocked rather than leaked. A rising counter is a SECURITY-relevant signal.
privacy_gate_redirects_total = Counter(
    "onp_privacy_gate_redirects_total",
    "Times the fail-closed privacy gate diverted a cloud-bound turn that "
    "contained structured secrets/PII. outcome: 'local' (rerouted to the "
    "local model) | 'blocked' (no local model — request blocked, not leaked).",
    ["outcome"],
)

# v0.8.56 — Phase 5.3c observability. Terminal state of the chat MCP tool
# loop. outcome='truncated' means the loop hit max_iterations while the model
# still wanted to call tools → the answer is likely incomplete (the tool
# budget, not the model, was the limiting factor). A rising 'truncated' ratio
# says DEEPER_NOTEBOOK_MCP_TOOL_TIMEOUT_SEC / the iteration cap may be too tight.
agent_tool_loop_outcomes_total = Counter(
    "onp_agent_tool_loop_outcomes_total",
    "Terminal state of the chat MCP tool loop (only counted when MCP tools "
    "were bound). outcome: 'complete' (model stopped requesting tools) | "
    "'truncated' (hit max_iterations with tool calls still pending).",
    ["outcome"],
)


# -------------------------------------------------------------------- #
# v0.7.125 — LangGraph SQLite checkpoint pruning metrics
# -------------------------------------------------------------------- #

checkpoint_prune_runs_total = Counter(
    "onp_checkpoint_prune_runs_total",
    "Number of times the LangGraph SQLite checkpoint-pruning task "
    "has executed (default cadence: every 24h, configurable via "
    "DEEPER_NOTEBOOK_CHECKPOINT_PRUNE_INTERVAL_HOURS).",
)

checkpoint_prune_rows_deleted_total = Counter(
    "onp_checkpoint_prune_rows_deleted_total",
    "Number of rows deleted by the LangGraph SQLite checkpoint-pruning "
    "task, labeled by table ('checkpoints' or 'writes').",
    ["table"],
)


# -------------------------------------------------------------------- #
# v0.8.124 — Studio artifact retention (revisions + stale exports)
# -------------------------------------------------------------------- #

studio_retention_runs_total = Counter(
    "onp_studio_retention_runs_total",
    "Number of times the Studio retention task (revision pruning + "
    "stale-export cleanup) has executed. Ships disabled by default "
    "(DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS=0); stays at 0 "
    "until an operator opts in.",
)


# -------------------------------------------------------------------- #
# v0.7.130 — Studio generation observability
#
# Three counters that together answer the Area for Review question
# "under what conditions does the outline LLM produce non-JSON?" with
# live data instead of guesses. Pair these with the Loguru warning
# lines for the cleaned-LLM-output context (they're already there).
# -------------------------------------------------------------------- #

studio_generations_total = Counter(
    "onp_studio_generations_total",
    "Studio /generate invocations. `mode` is the request mode "
    "('notebook', 'podcast', 'both'). `outcome` reflects the final "
    "delivery: 'success' (both halves landed), 'partial' (one half "
    "of `both` succeeded), 'failed' (no usable output).",
    ["mode", "outcome"],
)

studio_outline_parse_failures_total = Counter(
    "onp_studio_outline_parse_failures_total",
    "Studio multi-page outline-LLM responses that couldn't be parsed "
    "into a usable outline. `reason` is 'json_decode' (returned text "
    "that wasn't valid JSON after _strip_json_wrapper) or 'validation' "
    "(parsed JSON but failed _validate_outline schema/page-count check).",
    ["reason"],
)

studio_single_note_fallbacks_total = Counter(
    "onp_studio_single_note_fallbacks_total",
    "Times Studio fell back from multi-page to single-note generation "
    "because the outline pass produced unusable output. This is the "
    "headline metric for 'is the local outline model good enough?' — "
    "consistently elevated here means rebuild the prompt, swap the "
    "model, or both.",
)


# -------------------------------------------------------------------- #
# Helpers
# -------------------------------------------------------------------- #


def render_prometheus() -> tuple[bytes, str]:
    """Render the current registry to Prometheus exposition format.
    Returns (body_bytes, content_type) so the FastAPI handler can
    return both. Always reads from the global REGISTRY (one process,
    one set of metrics)."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


@contextmanager
def time_db_query():
    """Context manager that observes the elapsed time into the DB
    query histogram. Used inside repo_query to record EVERY query
    (slow or fast) — slow ones additionally increment
    `db_slow_queries_total`."""
    start = time.monotonic()
    try:
        yield
    finally:
        db_query_duration_seconds.observe(time.monotonic() - start)


@contextmanager
def time_memory_recall():
    """Context manager for memory-recall total duration."""
    start = time.monotonic()
    try:
        yield
    finally:
        memory_recall_seconds.observe(time.monotonic() - start)


def record_slow_query() -> None:
    """Called by repo_query when a query exceeded DEEPER_NOTEBOOK_SLOW_QUERY_LOG_MS.
    Wrapper exists so call sites don't have to import the Counter
    directly (keeps the boundary clean for refactor)."""
    db_slow_queries_total.inc()


def record_memory_fallthrough(reason: str) -> None:
    """Bumps the memory-recall fall-through counter with a labeled
    reason. Reasons: 'embed_timeout', 'embed_error', 'query_timeout',
    'query_error'."""
    memory_recall_fallthrough_total.labels(reason=reason).inc()


def record_privacy_gate_redirect(outcome: str) -> None:
    """v0.8.51 — Bump the privacy-gate counter. outcome: 'local'
    (rerouted on-device) | 'blocked' (no local model — request blocked)."""
    privacy_gate_redirects_total.labels(outcome=outcome).inc()


def record_agent_tool_loop_outcome(outcome: str) -> None:
    """v0.8.56 — Bump the chat tool-loop outcome counter. outcome:
    'complete' | 'truncated' (hit max_iterations with pending tool calls)."""
    agent_tool_loop_outcomes_total.labels(outcome=outcome).inc()


def record_studio_generation(mode: str, outcome: str) -> None:
    """v0.7.130 — Bump studio_generations_total with the request mode +
    outcome. Call sites pass the literal mode ('notebook' | 'podcast' |
    'both') and outcome ('success' | 'partial' | 'failed'). Unknown
    labels are still recorded — Prometheus tolerates anything, and a
    surprise label value is the kind of thing we want to see in a
    dashboard rather than silently swallow."""
    studio_generations_total.labels(mode=mode, outcome=outcome).inc()


def record_studio_outline_parse_failure(reason: str) -> None:
    """v0.7.130 — Reason: 'json_decode' or 'validation'. Wrapper exists
    so the studio router doesn't have to import the Counter directly."""
    studio_outline_parse_failures_total.labels(reason=reason).inc()


def record_studio_single_note_fallback() -> None:
    """v0.7.130 — Increment when multi-page outline fails and Studio
    falls back to legacy single-note generation."""
    studio_single_note_fallbacks_total.inc()
