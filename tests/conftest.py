"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the API and Deeper Notebook modules.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure password auth is disabled for tests BEFORE any imports
# The PasswordAuthMiddleware skips auth when this env var is not set
# Set to empty string instead of deleting to prevent it from being reloaded
os.environ["DEEPER_NOTEBOOK_PASSWORD"] = ""

# v0.8.127 — keep test logs out of the operator's ~/.deeper-notebook/logs.
# Fixture runs (fake credentials, forced failures) were landing there as
# ERROR lines and polluting live diagnostics. load_dotenv below does not
# override an existing variable, so this wins over a value in .env.
import tempfile  # noqa: E402

_TEST_LOG_DIR = tempfile.mkdtemp(prefix="deeper-notebook-test-logs-")
os.environ.setdefault("DEEPER_NOTEBOOK_LOG_DIR", _TEST_LOG_DIR)

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# v0.8.64 — `.env` may now carry real web-search provider keys
# (SERPER_API_KEY / TAVILY_API_KEY / SEARXNG_BASE_URL), which conftest loads via
# load_dotenv above. Left in place, those would silently enable the built-in
# `web_search` tool inside the chat tool loop — which (a) changes tool-loop
# tests that assume no tools are bound (e.g. v0.8.56's "no outcome when no tools
# bound") and (b) risks real network calls from the suite. Strip them before
# EVERY test so the suite is deterministic regardless of the developer's .env;
# tests that exercise web search opt in by setting the vars explicitly (see
# tests/test_v0_8_64_web_search.py). monkeypatch auto-restores after each test.
_WEB_SEARCH_ENV_VARS = (
    "SERPER_API_KEY",
    "TAVILY_API_KEY",
    "SEARXNG_BASE_URL",
    "DEEPER_NOTEBOOK_WEB_SEARCH_PROVIDER",
    "DEEPER_NOTEBOOK_WEB_SEARCH_MAX_RESULTS",
    "DEEPER_NOTEBOOK_WEB_SEARCH_TIMEOUT_SEC",
)


@pytest.fixture(autouse=True)
def _isolate_web_search_env(monkeypatch):
    for name in _WEB_SEARCH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_data_root_home(tmp_path, monkeypatch):
    """Keep every backend test's resolver inside its own temporary home."""
    test_home = tmp_path
    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("USERPROFILE", str(test_home))

    from desktop import data_root

    original_resolve = data_root.resolve_data_root
    allowed_root = tmp_path.resolve()

    def guarded_resolve(*, home=None, failure_injector=None):
        candidate = (
            Path(home) if home is not None else Path(os.environ["HOME"])
        ).resolve()
        assert candidate.is_relative_to(allowed_root), (
            f"test attempted data-root resolution outside {allowed_root}: {candidate}"
        )
        return original_resolve(home=home, failure_injector=failure_injector)

    monkeypatch.setattr(data_root, "resolve_data_root", guarded_resolve)


# v0.8.68 — pin the network-state service to "online" for every test so the
# suite is deterministic regardless of the machine's actual connectivity
# (the offline gate / web_search short-circuit would otherwise change
# behavior on an airgapped CI box, and the real TCP probe is a network
# call the suite must never make). Tests that exercise offline behavior
# opt in by monkeypatching get_network_state_with_settings / _probe_once
# themselves (see tests/test_offline_gate.py, tests/test_web_search_offline.py).
@pytest.fixture(autouse=True)
def _pin_network_state_online(monkeypatch):
    from deeper_notebook.health import network

    network.reset_network_state_for_tests()
    monkeypatch.setattr(network, "_probe_once", lambda: True)
    yield
    network.reset_network_state_for_tests()
