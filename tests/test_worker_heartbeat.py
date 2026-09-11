"""v0.8.127 — tests for the background worker heartbeat.

Covers:
  - `is_worker_process()` detection via env flag / argv / neither.
  - `start_heartbeat()` writes via `repo_query` and is idempotent.
  - `read_worker_status()` online / offline / never-seen / error paths.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from deeper_notebook import worker_heartbeat as wh


@pytest.fixture(autouse=True)
def _stop_heartbeat_after_each_test():
    """Guard against a heartbeat thread leaking into the next test."""
    yield
    wh.stop_heartbeat()


def _clear_worker_process_env(monkeypatch):
    # Every accepted spelling of the setting, taken from the environment
    # registry so this test never names legacy prefixes itself (the rebrand
    # audit forbids that in active code).
    from deeper_notebook.environment import _setting_for

    for name in _setting_for("DEEPER_NOTEBOOK_WORKER_PROCESS").precedence:
        monkeypatch.delenv(name, raising=False)


class TestIsWorkerProcess:
    def test_true_when_env_flag_set(self, monkeypatch):
        _clear_worker_process_env(monkeypatch)
        monkeypatch.setenv("DEEPER_NOTEBOOK_WORKER_PROCESS", "1")
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/uvicorn", "api.main:app"])
        assert wh.is_worker_process() is True

    def test_true_when_argv0_is_worker_binary(self, monkeypatch):
        _clear_worker_process_env(monkeypatch)
        monkeypatch.setattr(
            sys,
            "argv",
            ["/opt/venv/bin/surreal-commands-worker", "--import-modules", "commands"],
        )
        assert wh.is_worker_process() is True

    def test_false_when_neither_signal_present(self, monkeypatch):
        _clear_worker_process_env(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/uvicorn", "api.main:app"])
        assert wh.is_worker_process() is False

    def test_false_when_env_flag_is_falsy_string(self, monkeypatch):
        _clear_worker_process_env(monkeypatch)
        monkeypatch.setenv("DEEPER_NOTEBOOK_WORKER_PROCESS", "0")
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/uvicorn"])
        assert wh.is_worker_process() is False

    def test_does_not_fire_for_api_process(self, monkeypatch):
        """The API process also imports `commands` at startup but must
        never be mistaken for the worker."""
        _clear_worker_process_env(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/python", "-m", "uvicorn", "api.main:app"])
        assert wh.is_worker_process() is False


class TestStartStopHeartbeat:
    def test_writes_via_repo_query_and_is_idempotent(self, monkeypatch):
        calls: list[tuple[str, dict | None]] = []
        wrote = threading.Event()

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            calls.append((query_str, vars))
            wrote.set()
            return []

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)

        wh.start_heartbeat(interval_sec=0.02)
        first_thread = wh._heartbeat_thread

        # Idempotent: a second call while already running must not spin
        # up a new thread.
        wh.start_heartbeat(interval_sec=0.02)
        assert wh._heartbeat_thread is first_thread

        assert wrote.wait(timeout=2.0), "heartbeat thread never wrote"
        wh.stop_heartbeat()
        assert wh._heartbeat_thread is None

        assert calls, "expected at least one repo_query call"
        query_str, bind_vars = calls[0]
        assert "UPSERT worker_heartbeat:" in query_str
        assert "time::now()" in query_str
        assert bind_vars is not None
        assert set(bind_vars) == {"pid", "hostname", "version"}

    def test_write_failures_are_swallowed(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(wh, "repo_query", _boom)

        wh.start_heartbeat(interval_sec=0.02)
        time.sleep(0.1)
        # The thread must survive a repo_query failure instead of dying.
        assert wh._heartbeat_thread is not None
        assert wh._heartbeat_thread.is_alive()
        wh.stop_heartbeat()


class TestReadWorkerStatus:
    async def test_online_when_recent_row(self, monkeypatch):
        recent = datetime.now(timezone.utc) - timedelta(seconds=5)

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [{"last_seen": recent, "pid": 123, "hostname": "host-a"}]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status["online"] is True
        assert status["pid"] == 123
        assert status["hostname"] == "host-a"
        assert status["last_seen"] is not None
        assert status["age_seconds"] < 60
        assert status["active_workers_count"] == 1

    async def test_offline_when_stale_row(self, monkeypatch):
        stale = datetime.now(timezone.utc) - timedelta(seconds=999)

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [{"last_seen": stale, "pid": 1, "hostname": "h"}]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status["online"] is False
        assert status["age_seconds"] > 180
        assert status["active_workers_count"] == 0

    async def test_offline_when_never_seen(self, monkeypatch):
        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return []

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status == {
            "online": False,
            "last_seen": None,
            "age_seconds": None,
            "pid": None,
            "hostname": None,
            "active_workers_count": 0,
        }

    async def test_offline_when_query_raises(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(wh, "repo_query", _boom)
        status = await wh.read_worker_status()

        assert status["online"] is False
        assert status["last_seen"] is None
        assert status["active_workers_count"] == 0

    async def test_respects_custom_stale_threshold_env(self, monkeypatch):
        monkeypatch.setenv("DEEPER_NOTEBOOK_WORKER_HEARTBEAT_STALE_SEC", "5")
        ten_seconds_ago = datetime.now(timezone.utc) - timedelta(seconds=10)

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [{"last_seen": ten_seconds_ago, "pid": 1, "hostname": "h"}]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        # 10s old row against a 5s configured threshold -> stale/offline.
        assert status["online"] is False
        assert status["active_workers_count"] == 0

    async def test_multi_worker_reports_count_and_freshest_worker(self, monkeypatch):
        now = datetime.now(timezone.utc)
        worker_1 = {"last_seen": now - timedelta(seconds=20), "pid": 101, "hostname": "node-1"}
        worker_2 = {"last_seen": now - timedelta(seconds=5), "pid": 102, "hostname": "node-2"}

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [worker_1, worker_2]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status["online"] is True
        assert status["active_workers_count"] == 2
        # Freshest worker is worker_2 (5s ago vs 20s ago)
        assert status["pid"] == 102
        assert status["hostname"] == "node-2"
        assert status["age_seconds"] < 10

    async def test_multi_worker_excludes_stale_from_active_count(self, monkeypatch):
        now = datetime.now(timezone.utc)
        active_worker = {"last_seen": now - timedelta(seconds=10), "pid": 201, "hostname": "live-host"}
        dead_worker = {"last_seen": now - timedelta(seconds=500), "pid": 202, "hostname": "dead-host"}

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [active_worker, dead_worker]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status["online"] is True
        assert status["active_workers_count"] == 1
        assert status["pid"] == 201
        assert status["hostname"] == "live-host"


class TestShutdownCleanup:
    def test_stop_heartbeat_deletes_own_row(self, monkeypatch):
        """v0.8.128 — a graceful stop removes this process's row."""
        queries: list[str] = []

        async def fake_repo_query(query, params=None):
            queries.append(query)
            return []

        monkeypatch.setattr(wh, "repo_query", fake_repo_query)
        monkeypatch.setattr(wh, "_shutdown_hooks_installed", True)  # no real signal hooks in tests
        wh.start_heartbeat(interval_sec=60)
        wh.stop_heartbeat()
        deletes = [q for q in queries if q.strip().upper().startswith("DELETE")]
        assert deletes, queries
        assert wh._worker_instance_key() in deletes[-1]

    def test_stop_without_start_deletes_nothing(self, monkeypatch):
        queries: list[str] = []

        async def fake_repo_query(query, params=None):
            queries.append(query)
            return []

        monkeypatch.setattr(wh, "repo_query", fake_repo_query)
        wh.stop_heartbeat()
        assert queries == []

    def test_shutdown_hooks_register_atexit_once(self, monkeypatch):
        registered: list[object] = []
        monkeypatch.setattr(wh.atexit, "register", lambda fn: registered.append(fn))
        monkeypatch.setattr(wh, "_shutdown_hooks_installed", False)
        monkeypatch.setattr(wh.signal, "signal", lambda *a, **k: None)
        wh._install_shutdown_hooks()
        wh._install_shutdown_hooks()
        assert registered == [wh.stop_heartbeat]

    async def test_read_status_prunes_day_old_rows(self, monkeypatch):
        queries: list[str] = []

        async def fake_repo_query(query, params=None):
            queries.append(query)
            return []

        monkeypatch.setattr(wh, "repo_query", fake_repo_query)
        await wh.read_worker_status()
        assert any("DELETE worker_heartbeat WHERE last_seen < time::now() - 1d" in q for q in queries)
