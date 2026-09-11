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
    for name in (
        "DEEPER_NOTEBOOK_WORKER_PROCESS",
        "DN_WORKER_PROCESS",
        "OPEN_NOTEBOOK_WORKER_PROCESS",
        "ONP_WORKER_PROCESS",
    ):
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
        assert "UPSERT worker_heartbeat:primary" in query_str
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

    async def test_offline_when_stale_row(self, monkeypatch):
        stale = datetime.now(timezone.utc) - timedelta(seconds=999)

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [{"last_seen": stale, "pid": 1, "hostname": "h"}]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        assert status["online"] is False
        assert status["age_seconds"] > 180

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
        }

    async def test_offline_when_query_raises(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(wh, "repo_query", _boom)
        status = await wh.read_worker_status()

        assert status["online"] is False
        assert status["last_seen"] is None

    async def test_respects_custom_stale_threshold_env(self, monkeypatch):
        monkeypatch.setenv("DEEPER_NOTEBOOK_WORKER_HEARTBEAT_STALE_SEC", "5")
        ten_seconds_ago = datetime.now(timezone.utc) - timedelta(seconds=10)

        async def _fake_repo_query(query_str, vars=None, **kwargs):
            return [{"last_seen": ten_seconds_ago, "pid": 1, "hostname": "h"}]

        monkeypatch.setattr(wh, "repo_query", _fake_repo_query)
        status = await wh.read_worker_status()

        # 10s old row against a 5s configured threshold -> stale/offline.
        assert status["online"] is False
