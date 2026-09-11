"""v0.8.126 — run_api.py graceful-shutdown config tests.

The real bug: `run_api.py` runs uvicorn with `reload=True` by default and no
graceful-shutdown bound. A dev reload (triggered by a file change) could
leave the OLD process holding the port while it waited out an in-flight
request (e.g. the 300s sync-source-processing hang fixed alongside this),
wedging :5055 for the next `make dev` run.

`run_api.py` is a script gated by `if __name__ == "__main__":`, so it can't
be imported and exercised directly the way a module normally would without
starting a real server. Following the pattern in
`tests/test_v0_7_141_bootstrap.py` (which asserts on Makefile source text),
this file asserts on the *source text* of run_api.py — a contract test that
the `uvicorn.run(...)` call passes `timeout_graceful_shutdown` wired to the
`API_GRACEFUL_SHUTDOWN_SEC` env var.
"""

from __future__ import annotations

import re
from pathlib import Path

_RUN_API = Path("run_api.py")


def _uvicorn_run_call_text() -> str:
    src = _RUN_API.read_text()
    match = re.search(r"uvicorn\.run\(\s*\n(.*?)\n\s*\)", src, re.DOTALL)
    assert match, "Couldn't locate the uvicorn.run(...) call in run_api.py"
    return match.group(1)


def test_uvicorn_run_passes_timeout_graceful_shutdown_kwarg():
    call_body = _uvicorn_run_call_text()
    assert "timeout_graceful_shutdown=" in call_body, (
        "uvicorn.run(...) should pass timeout_graceful_shutdown so an "
        "in-flight request can't wedge the port across a dev reload"
    )


def test_timeout_graceful_shutdown_reads_env_var_with_default_20():
    call_body = _uvicorn_run_call_text()
    assert "API_GRACEFUL_SHUTDOWN_SEC" in call_body, (
        "timeout_graceful_shutdown should be configurable via the "
        "API_GRACEFUL_SHUTDOWN_SEC environment variable"
    )
    match = re.search(
        r'timeout_graceful_shutdown=int\(os\.getenv\("API_GRACEFUL_SHUTDOWN_SEC",\s*"(\d+)"\)\)',
        call_body,
    )
    assert match, (
        "timeout_graceful_shutdown should be "
        'int(os.getenv("API_GRACEFUL_SHUTDOWN_SEC", "<default>"))'
    )
    assert match.group(1) == "20", "Default graceful shutdown window should be 20s"


def test_api_reload_env_var_still_honored():
    """Guard against accidentally dropping the existing API_RELOAD wiring
    while adding the graceful-shutdown kwarg."""
    src = _RUN_API.read_text()
    assert 'os.getenv("API_RELOAD", "true")' in src


def test_versioned_comment_present_for_graceful_shutdown_change():
    src = _RUN_API.read_text()
    assert "v0.8.126" in src, (
        "run_api.py should carry a versioned comment marking the "
        "graceful-shutdown change, matching this repo's changelog-comment "
        "convention"
    )
