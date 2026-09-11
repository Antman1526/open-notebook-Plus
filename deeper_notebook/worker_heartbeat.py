"""v0.8.127 — Background worker heartbeat.

Problem (observed live 2026-09-11): with no `surreal-commands-worker`
running, queued work (podcasts, embeddings, async source processing,
studio generation) sits in `queued` until the stale-command reaper
fails it, and nothing in the UI says a worker is missing.

This module gives the worker process a cheap, best-effort "I'm alive"
signal: once a minute it upserts a single row (`worker_heartbeat:primary`)
into SurrealDB with the current timestamp, its pid, hostname, and the
running app version. `/healthz/deep` (api/main.py) reads that row back
and reports the `worker` subsystem as `degraded` (never `not_ready` —
chat/search/notes all work fine without a worker, only async jobs
don't) when the heartbeat is missing or stale.

Detection of "am I the worker process" (`is_worker_process`) matters
because the API process ALSO imports the `commands` package at startup
("Commands imported in API process" — see commands/__init__.py) so a
heartbeat must not start merely on import; only the actual worker
process should start one. Two independent signals are checked, either
sufficient:

  1. `DEEPER_NOTEBOOK_WORKER_PROCESS=1` — set explicitly on the child
     env by the Makefile `worker`/`start-all` targets and by the
     desktop launcher's `_spawn_worker`, so detection doesn't rely on
     argv alone (e.g. a frozen/bundled entrypoint could rewrite argv).
  2. `sys.argv[0]`'s basename contains `surreal-commands-worker` —
     covers a developer running `uv run surreal-commands-worker
     --import-modules commands` by hand, outside either spawn path.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from loguru import logger

from deeper_notebook.database.repository import repo_query
from deeper_notebook.environment import resolve_env

__all__ = [
    "is_worker_process",
    "start_heartbeat",
    "stop_heartbeat",
    "read_worker_status",
]

_HEARTBEAT_RECORD = "worker_heartbeat:primary"
_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_STALE_SEC = 180.0

_heartbeat_lock = threading.Lock()
_heartbeat_thread: threading.Thread | None = None
_heartbeat_stop_event: threading.Event | None = None


def is_worker_process() -> bool:
    """True when the current process is the surreal-commands worker.

    Deliberately does NOT fire for the API process, which also imports
    `commands` at startup but sets neither signal below.
    """
    flag = (resolve_env("DEEPER_NOTEBOOK_WORKER_PROCESS") or "").strip().lower()
    if flag and flag not in ("0", "false", "no", "off"):
        return True
    argv0 = sys.argv[0] if sys.argv else ""
    basename = PurePath(argv0).name
    return "surreal-commands-worker" in basename


def _coerce_datetime(value: Any) -> datetime | None:
    """Normalize a SurrealDB-returned timestamp to an aware UTC datetime.

    The Python SurrealDB SDK typically deserializes `datetime` fields to
    `datetime.datetime` already, but defensively also accepts an ISO
    string in case that ever changes (or a test stubs raw rows).
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


async def _write_heartbeat_once() -> None:
    pid = os.getpid()
    hostname = socket.gethostname()
    try:
        from deeper_notebook import __version__ as app_version
    except Exception:  # pragma: no cover — defensive only
        app_version = "unknown"

    await repo_query(
        f"UPSERT {_HEARTBEAT_RECORD} MERGE "
        "{ last_seen: time::now(), pid: $pid, hostname: $hostname, "
        "version: $version };",
        {"pid": pid, "hostname": hostname, "version": app_version},
    )


def _heartbeat_loop(stop_event: threading.Event, interval_sec: float) -> None:
    while not stop_event.is_set():
        try:
            asyncio.run(_write_heartbeat_once())
        except Exception as exc:  # noqa: BLE001 — best-effort, never crash the worker
            logger.warning("worker_heartbeat: failed to write heartbeat: {}", exc)
        if stop_event.wait(interval_sec):
            break


def start_heartbeat(interval_sec: float = _DEFAULT_INTERVAL_SEC) -> None:
    """Start the daemon heartbeat thread. Idempotent — a second call
    while the thread is already running is a no-op."""
    global _heartbeat_thread, _heartbeat_stop_event
    with _heartbeat_lock:
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_heartbeat_loop,
            args=(stop_event, interval_sec),
            name="worker-heartbeat",
            daemon=True,
        )
        _heartbeat_stop_event = stop_event
        _heartbeat_thread = thread
        thread.start()


def stop_heartbeat(timeout: float = 2.0) -> None:
    """Stop the heartbeat thread (test hook; also safe in production)."""
    global _heartbeat_thread, _heartbeat_stop_event
    with _heartbeat_lock:
        thread = _heartbeat_thread
        stop_event = _heartbeat_stop_event
        _heartbeat_thread = None
        _heartbeat_stop_event = None
    if stop_event is not None:
        stop_event.set()
    if thread is not None:
        thread.join(timeout=timeout)


async def read_worker_status() -> dict[str, Any]:
    """Read the current worker heartbeat row and report its freshness."""
    stale_after_raw = resolve_env(
        "DEEPER_NOTEBOOK_WORKER_HEARTBEAT_STALE_SEC", str(_DEFAULT_STALE_SEC)
    )
    try:
        stale_after = float((stale_after_raw or "").strip() or _DEFAULT_STALE_SEC)
    except ValueError:
        logger.warning(
            "DEEPER_NOTEBOOK_WORKER_HEARTBEAT_STALE_SEC=%r is not a number; "
            "using default %s",
            stale_after_raw,
            _DEFAULT_STALE_SEC,
        )
        stale_after = _DEFAULT_STALE_SEC

    empty: dict[str, Any] = {
        "online": False,
        "last_seen": None,
        "age_seconds": None,
        "pid": None,
        "hostname": None,
    }

    try:
        rows = await repo_query(f"SELECT * FROM {_HEARTBEAT_RECORD};")
    except Exception as exc:
        logger.warning("worker_heartbeat: failed to read heartbeat status: {}", exc)
        return empty

    if not rows:
        return empty

    row = rows[0]
    last_seen_dt = _coerce_datetime(row.get("last_seen"))
    if last_seen_dt is None:
        return {**empty, "pid": row.get("pid"), "hostname": row.get("hostname")}

    age_seconds = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
    return {
        "online": age_seconds < stale_after,
        "last_seen": last_seen_dt.isoformat(),
        "age_seconds": age_seconds,
        "pid": row.get("pid"),
        "hostname": row.get("hostname"),
    }
