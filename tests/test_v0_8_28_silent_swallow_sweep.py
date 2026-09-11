"""v0.8.28 — Silent-swallow sweep across 4 sites.

After v0.8.19 (memory_recall._safe_select) and v0.8.27
(digest._safe_query) closed the two highest-impact instances of
`except Exception: return <sentinel>` with no log, this sweep
closes the remaining four:

  - deeper_notebook/domain/gmail.py:_fernet (security boundary)
  - deeper_notebook/domain/gmail.py:_dec (encryption decode)
  - deeper_notebook/database/async_migrate.py:get_all_versions
  - deeper_notebook/utils/chunking.py:detect_content_type_from_extension

Each fix logs at WARNING (genuine bug) or DEBUG (benign bootstrap
case). Tests below pin the new contracts so a future refactor that
"simplifies" any of them back to silent-swallow fails loudly.

We use a loguru sink rather than stdlib caplog because the project
uses loguru throughout — same pattern as
tests/test_memory_recall.py (v0.8.19) and tests/test_digest_builder.py
(v0.8.27).
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import InvalidToken
from loguru import logger


def _capture_loguru(level: str = "DEBUG") -> tuple[list[dict], int]:
    """Attach a loguru sink that collects {level, message} dicts.
    Returns (capture_list, sink_id) — call logger.remove(sink_id)
    in finally to clean up."""
    captured: list[dict] = []
    sink_id = logger.add(
        lambda msg: captured.append(
            {
                "level": msg.record["level"].name,
                "message": msg.record["message"],
            }
        ),
        level=level,
    )
    return captured, sink_id


# ---------------------------------------------------------------------------
# _fernet — silent at security boundary
# ---------------------------------------------------------------------------


def test_v0828_fernet_logs_warning_on_construction_failure(monkeypatch):
    """When the env var is set but Fernet() raises (cryptography lib
    bug, binary garbage, etc.), pre-v0.8.28 returned None silently and
    the downstream RuntimeError said "key not set" — misleading the
    operator. Must now emit a WARNING naming the real failure."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "any-value")

    from deeper_notebook.domain import gmail as gmail_mod

    # Patch Fernet to raise on construction.
    def _boom_fernet(_key):
        raise ValueError("simulated cryptography library bug")

    monkeypatch.setattr(gmail_mod, "Fernet", _boom_fernet)

    captured, sink_id = _capture_loguru(level="WARNING")
    try:
        result = gmail_mod._fernet()
    finally:
        logger.remove(sink_id)

    assert result is None
    warnings = [c for c in captured if c["level"] == "WARNING"]
    assert warnings, (
        "v0.8.28: _fernet must log a WARNING when Fernet construction "
        "fails. Otherwise the downstream RuntimeError ('key not set') "
        "misleads the operator about the real root cause."
    )
    assert any("misleading" in w["message"] for w in warnings), (
        f"Expected the warning to call out that the downstream "
        f"'key not set' error is misleading; got: {warnings}"
    )


def test_v0828_fernet_silent_when_key_unset(unset_setting):
    """The unset-env-var path is INTENTIONAL — no log needed because
    the downstream behavior (return None → caller raises clear
    RuntimeError) is correct. We're only logging the Fernet-raises
    case, not the no-key case."""
    unset_setting("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "DEEPER_NOTEBOOK_ENCRYPTION_KEY")

    from deeper_notebook.domain import gmail as gmail_mod

    captured, sink_id = _capture_loguru(level="DEBUG")
    try:
        result = gmail_mod._fernet()
    finally:
        logger.remove(sink_id)

    assert result is None
    # No WARNING — the no-key case is intentional and the downstream
    # _enc raises a clear RuntimeError that names the env var.
    assert not [c for c in captured if c["level"] in ("WARNING", "ERROR")], (
        f"v0.8.28: the unset-env-var path must NOT log — the "
        f"downstream RuntimeError already names the env var. Got: "
        f"{captured}"
    )


# ---------------------------------------------------------------------------
# _dec — split InvalidToken (quiet) from unexpected exceptions (loud)
# ---------------------------------------------------------------------------


def test_v0828_dec_quiet_on_invalid_token(monkeypatch):
    """InvalidToken is the canonical 'wrong key / rotated key' case
    and is expected during key rotation. Must stay quiet — otherwise
    every legacy unencrypted row logs a WARNING on read."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "k" * 32)

    from deeper_notebook.domain import gmail as gmail_mod

    class _RaiseInvalidToken:
        def decrypt(self, _bytes):
            raise InvalidToken("wrong key")

    monkeypatch.setattr(
        gmail_mod,
        "_fernet",
        lambda: _RaiseInvalidToken(),
    )

    captured, sink_id = _capture_loguru(level="DEBUG")
    try:
        result = gmail_mod._dec("ciphertext-bytes")
    finally:
        logger.remove(sink_id)

    assert result is None
    assert not [c for c in captured if c["level"] in ("WARNING", "ERROR")], (
        f"v0.8.28: InvalidToken must NOT log — it's the canonical "
        f"key-rotation case and would spam launcher.log on every read. "
        f"Got: {captured}"
    )


def test_v0828_dec_warns_on_unexpected_exception(monkeypatch):
    """Anything OTHER than InvalidToken (binary garbage, OOM,
    cryptography library bug) is a real bug — must surface as
    WARNING so the operator doesn't see Gmail integration
    silently disappear."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "k" * 32)

    from deeper_notebook.domain import gmail as gmail_mod

    class _RaiseRuntimeError:
        def decrypt(self, _bytes):
            raise RuntimeError("simulated cryptography lib bug")

    monkeypatch.setattr(
        gmail_mod,
        "_fernet",
        lambda: _RaiseRuntimeError(),
    )

    captured, sink_id = _capture_loguru(level="WARNING")
    try:
        result = gmail_mod._dec("ciphertext-bytes")
    finally:
        logger.remove(sink_id)

    assert result is None
    warnings = [c for c in captured if c["level"] == "WARNING"]
    assert warnings, (
        "v0.8.28: non-InvalidToken errors in _dec must log WARNING. "
        "Otherwise GmailIntegration credentials silently disappear "
        "from the loaded row."
    )


# ---------------------------------------------------------------------------
# get_all_versions — DEBUG for fresh-install, WARNING for real errors
# ---------------------------------------------------------------------------


def test_v0828_get_all_versions_debug_on_table_missing(monkeypatch):
    """Fresh install: _sbl_migrations table doesn't exist yet. DEBUG
    only — otherwise every first-run installs warns on startup."""
    from deeper_notebook.database import async_migrate as am_mod

    async def _missing(_q):
        raise RuntimeError("Table missing: _sbl_migrations")

    monkeypatch.setattr(am_mod, "repo_query", _missing)

    captured, sink_id = _capture_loguru(level="DEBUG")
    try:
        result = asyncio.new_event_loop().run_until_complete(
            am_mod.get_all_versions(),
        )
    finally:
        logger.remove(sink_id)

    assert result == []
    assert not [c for c in captured if c["level"] in ("WARNING", "ERROR")], (
        f"v0.8.28: 'Table missing' is the bootstrap case and must stay "
        f"DEBUG so fresh installs don't warn on every startup. Got: "
        f"{captured}"
    )


def test_v0828_get_all_versions_warns_on_other_errors(monkeypatch):
    """Connection drop / auth failure / unknown SurrealDB error must
    surface as WARNING — otherwise the migration runner silently
    re-runs every migration when the DB is misbehaving."""
    from deeper_notebook.database import async_migrate as am_mod

    async def _connection_drop(_q):
        raise RuntimeError("Connection refused: ws://127.0.0.1:8000")

    monkeypatch.setattr(am_mod, "repo_query", _connection_drop)

    captured, sink_id = _capture_loguru(level="WARNING")
    try:
        result = asyncio.new_event_loop().run_until_complete(
            am_mod.get_all_versions(),
        )
    finally:
        logger.remove(sink_id)

    assert result == []
    warnings = [c for c in captured if c["level"] == "WARNING"]
    assert warnings, (
        "v0.8.28: unexpected DB errors in get_all_versions must "
        "WARN. Without this, the migration runner silently treats "
        "the DB as fresh and re-runs every migration — corrupting "
        "data."
    )
    assert any("re-run" in w["message"].lower() for w in warnings), (
        f"WARN message should explain the consequence (migrations "
        f"may re-run); got: {warnings}"
    )


# ---------------------------------------------------------------------------
# detect_content_type_from_extension — DEBUG only (low-impact fallback)
# ---------------------------------------------------------------------------


def test_v0828_detect_content_type_logs_debug_on_exception(monkeypatch):
    """Path/.suffix is normally infallible on str input, but if it
    ever raises (exotic input), the fallback to heuristic detection
    is correct and DEBUG is the right level. Pre-v0.8.28 was silent."""
    from deeper_notebook.utils import chunking as chunking_mod

    class _BoomPath:
        def __init__(self, _v):
            raise RuntimeError("simulated Path failure")

    monkeypatch.setattr(chunking_mod, "Path", _BoomPath)

    captured, sink_id = _capture_loguru(level="DEBUG")
    try:
        result = chunking_mod.detect_content_type_from_extension(
            "/tmp/foo.txt",
        )
    finally:
        logger.remove(sink_id)

    assert result is None
    debug_lines = [c for c in captured if c["level"] == "DEBUG"]
    assert debug_lines, (
        "v0.8.28: detect_content_type_from_extension must log DEBUG "
        "on exception so a recurring failure can be diagnosed. "
        "Pre-v0.8.28 was silent."
    )
