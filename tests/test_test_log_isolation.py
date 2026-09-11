"""v0.8.127 — the test suite must not write into the operator's log dir."""

from pathlib import Path

from deeper_notebook.logging import default_log_dir


def test_tests_log_to_a_temporary_directory():
    log_dir = default_log_dir()
    home_logs = Path.home() / ".deeper-notebook" / "logs"
    assert log_dir.resolve() != home_logs.resolve()
    assert "deeper-notebook-test-logs-" in str(log_dir)
