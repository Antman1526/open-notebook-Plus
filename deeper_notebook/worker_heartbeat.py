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
import atexit
import os
import signal
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

_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_STALE_SEC = 180.0


def _worker_instance_key(hostname: str | None = None, pid: int | None = None) -> str:
    host = hostname or socket.gethostname()
    p = pid or os.getpid()
    safe_host = "".join(c if c.isalnum() else "_" for c in host).strip("_") or "host"
    return f"worker_heartbeat:`{safe_host}_{p}`"


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
    record_key = _worker_instance_key(hostname, pid)
    try:
        from deeper_notebook import __version__ as app_version
    except Exception:  # pragma: no cover — defensive only
        app_version = "unknown"

    await repo_query(
        f"UPSERT {record_key} MERGE "
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


_shutdown_hooks_installed = False


async def _delete_own_row() -> None:
    """Remove this process's heartbeat row so a stopped worker reads offline
    immediately instead of after the stale window. Best-effort."""
    try:
        await repo_query(f"DELETE {_worker_instance_key()};")
    except Exception as exc:  # noqa: BLE001 — shutdown must never fail on this
        logger.debug(f"worker heartbeat: own-row delete skipped: {exc}")


def _run_blocking(coro) -> None:
    """Run a coroutine from a plain thread; skip if a loop is already running here."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    coro.close()


def _install_shutdown_hooks() -> None:
    """v0.8.128 — atexit + SIGTERM (chaining any prior handler) so a graceful
    stop deletes the row. Only touches signals from the main thread."""
    global _shutdown_hooks_installed
    if _shutdown_hooks_installed:
        return
    _shutdown_hooks_installed = True
    atexit.register(stop_heartbeat)
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        previous = signal.getsignal(signal.SIGTERM)

        def _on_sigterm(signum, frame):  # pragma: no cover — exercised live
            stop_heartbeat()
            if callable(previous) and previous not in (signal.SIG_DFL, signal.SIG_IGN):
                previous(signum, frame)
            else:
                raise SystemExit(0)

        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError) as exc:
        logger.debug(f"worker heartbeat: SIGTERM hook not installed: {exc}")


def start_heartbeat(interval_sec: float = _DEFAULT_INTERVAL_SEC) -> None:
    """Start the daemon heartbeat thread. Idempotent — a second call
    while the thread is already running is a no-op."""
    global _heartbeat_thread, _heartbeat_stop_event
    with _heartbeat_lock:
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        _install_shutdown_hooks()
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
        # v0.8.128 — only a process that was heartbeating owns a row to remove.
        _run_blocking(_delete_own_row())


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
        "active_workers_count": 0,
    }

    try:
        rows = await repo_query("SELECT * FROM worker_heartbeat;")
        # v0.8.128 — rows from workers that died without cleanup stay forever;
        # prune anything older than a day (best-effort, never fails the read).
        try:
            await repo_query("DELETE worker_heartbeat WHERE last_seen < time::now() - 1d;")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"worker heartbeat: prune skipped: {exc}")
    except Exception as exc:
        logger.warning("worker_heartbeat: failed to read heartbeat status: {}", exc)
        return empty

    if not rows:
        return empty

    now = datetime.now(timezone.utc)
    active_count = 0
    freshest_row: dict[str, Any] | None = None
    freshest_age: float | None = None
    freshest_dt: datetime | None = None

    for row in rows:
        last_seen_dt = _coerce_datetime(row.get("last_seen"))
        if last_seen_dt is None:
            continue
        age_seconds = max(0.0, (now - last_seen_dt).total_seconds())
        if age_seconds < stale_after:
            active_count += 1
        if freshest_age is None or age_seconds < freshest_age:
            freshest_age = age_seconds
            freshest_row = row
            freshest_dt = last_seen_dt

    if freshest_row is None or freshest_dt is None or freshest_age is None:
        first = rows[0]
        return {
            **empty,
            "pid": first.get("pid"),
            "hostname": first.get("hostname"),
        }

    return {
        "online": active_count > 0,
        "last_seen": freshest_dt.isoformat(),
        "age_seconds": freshest_age,
        "pid": freshest_row.get("pid"),
        "hostname": freshest_row.get("hostname"),
        "active_workers_count": active_count,
    }
