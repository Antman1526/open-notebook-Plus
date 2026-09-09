"""Contract tests for the serial release package smoke runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.build import package_release_smoke as release_smoke
from desktop.build import package_smoke as smoke
from desktop.build import package_smoke_fixture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def browser_probe_result(
    *,
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
    stdout_limit_exceeded: bool = False,
    stderr_limit_exceeded: bool = False,
) -> release_smoke.BrowserProbeResult:
    stdout_bytes = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
    stderr_bytes = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
    return release_smoke.BrowserProbeResult(
        returncode=returncode,
        stdout=stdout_bytes,
        stderr=stderr_bytes,
        stdout_limit_exceeded=stdout_limit_exceeded,
        stderr_limit_exceeded=stderr_limit_exceeded,
        peak_stdout_bytes=len(stdout_bytes),
    )


def browser_receipt_for(
    mode: str, frontend_url: str, api_url: str
) -> dict[str, object]:
    mode_spec = release_smoke.MODE_SPECS[mode]
    features = dict(mode_spec.expected_features)
    observed_requests = [
        {"method": "GET", "url": frontend_url, "path": "/"},
        {
            "method": "GET",
            "url": f"{api_url}/api/features",
            "path": "/api/features",
        },
    ]
    observed_responses = [
        {"status": 200, "url": frontend_url, "path": "/"},
        {
            "status": 200,
            "url": f"{api_url}/api/features",
            "path": "/api/features",
        },
    ]
    if mode == "source-visuals-off":
        observed_requests.append(
            {
                "method": "GET",
                "url": f"{api_url}/api/sources",
                "path": "/api/sources",
            }
        )
        observed_responses.append(
            {
                "status": 200,
                "url": f"{api_url}/api/sources",
                "path": "/api/sources",
            }
        )
    receipt: dict[str, object] = {
        "status": "passed",
        "mode": mode_spec.browser_mode,
        "frontend_url": frontend_url,
        "api_url": api_url,
        "feature_response": {"status": 200, "body": {"features": features}},
        "feature_checks": {
            name: {
                "expected": expected,
                "actual": expected,
                "passed": True,
            }
            for name, expected in mode_spec.expected_features.items()
        },
        "observed_requests": observed_requests,
        "observed_responses": observed_responses,
        "blocked_requests": [],
        "http_methods": ["GET"],
        "non_get_requests": [],
        "visual_mutation_request_observed": False,
    }
    if mode == "default":
        receipt.update(
            {
                "theme": "gemini-forward-light",
                "visual_system_v2_shell_visible": True,
            }
        )
    else:
        receipt.update(
            {
                "sources_main_visible": True,
                "sources_heading_visible": True,
                "source_list_get_observed": True,
            }
        )
    return receipt


def prepare_ready_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    browser_receipt: dict[str, object],
) -> tuple[argparse.Namespace, SimpleNamespace, list[object]]:
    arguments = arguments_for(tmp_path)
    fixture_root = tmp_path / "fixture-ready"
    fixture = package_smoke_fixture.SmokeFixture(
        root=fixture_root,
        home=fixture_root / "home",
        data_dir=fixture_root / "data",
        model_dir=fixture_root / "models",
        readiness_file=fixture_root / "data" / "logs" / "desktop-readiness.json",
        environment={"HOME": str(fixture_root / "home")},
    )
    process = SimpleNamespace(pid=904, returncode=None)
    stopped: list[object] = []
    monkeypatch.setattr(
        release_smoke, "prepare_smoke_fixture", lambda *_args, **_kwargs: fixture
    )
    monkeypatch.setattr(
        release_smoke,
        "launch_monitored_process",
        lambda *_args, **_kwargs: (process, process.pid),
    )
    monkeypatch.setattr(
        release_smoke,
        "wait_for_readiness",
        lambda *_args, **_kwargs: (
            "http://127.0.0.1:52005",
            "http://127.0.0.1:52006/",
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "_run_browser_probe",
        lambda *_args, **_kwargs: browser_probe_result(
            stdout=json.dumps(browser_receipt)
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "stop_process",
        lambda process_arg, _timeout: stopped.append(process_arg),
    )
    return arguments, process, stopped


def arguments_for(tmp_path: Path) -> argparse.Namespace:
    artifact = tmp_path / "release.dmg"
    artifact.write_bytes(b"release artifact")
    executable = tmp_path / "Deeper Notebook"
    executable.write_bytes(b"executable fixture")
    playwright_module = tmp_path / "playwright"
    playwright_module.mkdir()
    uv_cache_dir = tmp_path / "uv-cache"
    uv_cache_dir.mkdir()
    return argparse.Namespace(
        executable=executable,
        artifact=artifact,
        output_root=tmp_path / "smoke-output",
        uv_cache_dir=uv_cache_dir,
        playwright_module=playwright_module,
        expected_artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        timeout_seconds=5.0,
    )


def test_release_smoke_runs_default_then_off_without_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    def fake_run_mode(
        mode: str, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        events.append(mode)
        return {"status": "passed", "mode": mode}

    monkeypatch.setattr(release_smoke, "run_mode", fake_run_mode)

    assert release_smoke.run_release_smoke(arguments_for(tmp_path)) == 0
    assert events == ["default", "source-visuals-off"]
    assert (
        json.loads(
            (tmp_path / "smoke-output" / "summary.json").read_text(encoding="utf-8")
        )["status"]
        == "passed"
    )


def test_release_smoke_stops_default_before_launching_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    current_mode = ["unknown"]
    events: list[str] = []

    def fake_prepare(root, *, source_visuals, uv_cache_dir):
        current_mode[0] = "default" if source_visuals else "source-visuals-off"
        return package_smoke_fixture.SmokeFixture(
            root=root,
            home=root / "home",
            data_dir=root / "data",
            model_dir=root / "models",
            readiness_file=root / "data" / "logs" / "desktop-readiness.json",
            environment={"MODE": current_mode[0]},
        )

    process = SimpleNamespace(pid=900, returncode=None)
    monkeypatch.setattr(release_smoke, "prepare_smoke_fixture", fake_prepare)
    monkeypatch.setattr(
        release_smoke,
        "launch_monitored_process",
        lambda *_args, **_kwargs: (
            events.append(f"{current_mode[0]}:launch") or (process, 900)
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "wait_for_readiness",
        lambda *_args, **_kwargs: ("http://127.0.0.1:53001", "http://127.0.0.1:53002/"),
    )
    monkeypatch.setattr(
        release_smoke,
        "_run_browser_probe",
        lambda *_args, **_kwargs: browser_probe_result(
            stdout=json.dumps(
                browser_receipt_for(
                    current_mode[0],
                    "http://127.0.0.1:53002/",
                    "http://127.0.0.1:53001",
                )
            )
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "stop_process",
        lambda *_args, **_kwargs: events.append(f"{current_mode[0]}:stop"),
    )

    assert release_smoke.run_release_smoke(arguments) == 0
    assert events == [
        "default:launch",
        "default:stop",
        "source-visuals-off:launch",
        "source-visuals-off:stop",
    ]


def test_release_smoke_does_not_start_off_after_default_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    def fail_default(mode: str, *_args: object, **_kwargs: object) -> dict[str, object]:
        events.append(mode)
        raise smoke.SmokeFailure("default failed")

    monkeypatch.setattr(release_smoke, "run_mode", fail_default)

    assert release_smoke.run_release_smoke(arguments_for(tmp_path)) == 1
    assert events == ["default"]
    summary = json.loads(
        (tmp_path / "smoke-output" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "failed"
    assert summary["failed_mode"] == "default"


def test_release_smoke_refuses_non_empty_output_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    arguments.output_root.mkdir()
    sentinel = arguments.output_root / "preserve.txt"
    sentinel.write_text("keep", encoding="utf-8")
    started: list[str] = []
    monkeypatch.setattr(
        release_smoke,
        "run_mode",
        lambda mode, *_args, **_kwargs: started.append(mode),
    )

    assert release_smoke.run_release_smoke(arguments) == 1
    assert started == []
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_release_smoke_rejects_an_output_root_below_a_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "linked-parent"
    symlink.symlink_to(target, target_is_directory=True)
    output_root = symlink / "new-output-root"

    with pytest.raises(smoke.SmokeFailure, match="symlinked ancestor"):
        release_smoke._validate_output_root(output_root)

    assert not (target / "new-output-root").exists()


def test_run_mode_creates_private_fixtures_parent_before_real_fixture_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    fixtures_parent = arguments.output_root / "fixtures"
    fixture_root = fixtures_parent / "default"
    real_prepare = release_smoke.prepare_smoke_fixture
    monkeypatch.setattr(
        package_smoke_fixture,
        "MODEL_PLACEHOLDERS",
        {Path("placeholder.bin"): 1},
    )
    observed_roots: list[Path] = []

    def observe_prepare(root: Path, *, source_visuals: bool, uv_cache_dir: Path):
        observed_roots.append(root)
        if not fixtures_parent.is_dir():
            raise smoke.SmokeFailure("fixtures parent missing before fixture creation")
        return real_prepare(
            root,
            source_visuals=source_visuals,
            uv_cache_dir=uv_cache_dir,
        )

    monkeypatch.setattr(release_smoke, "prepare_smoke_fixture", observe_prepare)
    launch_calls: list[tuple[object, ...]] = []

    def block_launch(*args: object, **_kwargs: object):
        launch_calls.append(args)
        raise smoke.SmokeFailure("launch blocked by filesystem contract test")

    monkeypatch.setattr(release_smoke, "launch_monitored_process", block_launch)

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert "launch blocked" in result["error"]
    assert observed_roots == [fixture_root]
    assert fixtures_parent.is_dir()
    assert fixture_root.is_dir()
    assert (fixture_root / package_smoke_fixture.FIXTURE_MANIFEST_NAME).is_file()
    assert launch_calls
    if os.name == "posix":
        assert stat.S_IMODE(fixtures_parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("parent_kind", ["file", "symlink"])
def test_run_mode_rejects_invalid_fixtures_parent_before_fixture_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parent_kind: str
) -> None:
    arguments = arguments_for(tmp_path)
    arguments.output_root.mkdir()
    fixtures_parent = arguments.output_root / "fixtures"
    target = tmp_path / "fixtures-target"
    if parent_kind == "file":
        fixtures_parent.write_text("not a directory", encoding="utf-8")
    else:
        target.mkdir()
        try:
            fixtures_parent.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("symlink creation is unavailable")

    fixture_calls: list[object] = []
    monkeypatch.setattr(
        release_smoke,
        "prepare_smoke_fixture",
        lambda *args, **kwargs: fixture_calls.append((args, kwargs)),
    )

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert fixture_calls == []
    if parent_kind == "symlink":
        assert not (target / "default").exists()


def test_release_smoke_verifies_artifact_before_any_mode_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    arguments.expected_artifact_sha256 = "0" * 64
    started: list[str] = []
    monkeypatch.setattr(
        release_smoke,
        "run_mode",
        lambda mode, *_args, **_kwargs: started.append(mode),
    )

    assert release_smoke.run_release_smoke(arguments) == 1
    assert started == []
    summary = json.loads(
        (arguments.output_root / "summary.json").read_text(encoding="utf-8")
    )
    assert "sha256 mismatch" in summary["error"]


@pytest.mark.parametrize("cache_state", ["missing", "file"])
def test_validate_inputs_rejects_invalid_uv_cache_before_fixture_or_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cache_state: str
) -> None:
    arguments = arguments_for(tmp_path)
    if cache_state == "missing":
        arguments.uv_cache_dir.rmdir()
    else:
        arguments.uv_cache_dir.rmdir()
        arguments.uv_cache_dir.write_text("not a directory", encoding="utf-8")

    forbidden_calls: list[str] = []
    monkeypatch.setattr(
        release_smoke,
        "prepare_smoke_fixture",
        lambda *_args, **_kwargs: forbidden_calls.append("fixture"),
    )
    monkeypatch.setattr(
        release_smoke,
        "launch_monitored_process",
        lambda *_args, **_kwargs: forbidden_calls.append("launch"),
    )

    with pytest.raises(smoke.SmokeFailure, match="uv cache"):
        release_smoke._validate_inputs(arguments)

    assert forbidden_calls == []

    monkeypatch.setattr(
        release_smoke,
        "run_mode",
        lambda *_args, **_kwargs: forbidden_calls.append("mode"),
    )
    assert release_smoke.run_release_smoke(arguments) == 1
    assert forbidden_calls == []
    summary = json.loads(
        (arguments.output_root / "summary.json").read_text(encoding="utf-8")
    )
    assert "uv cache" in summary["error"]


def test_run_mode_rejects_a_bare_browser_success_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments, _process, stopped = prepare_ready_mode(
        monkeypatch, tmp_path, {"status": "passed"}
    )

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert "browser receipt" in result["error"]
    assert len(stopped) == 1


def test_run_mode_rejects_browser_success_after_application_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    arguments, process, stopped = prepare_ready_mode(
        monkeypatch, tmp_path, browser_receipt_for("default", frontend_url, api_url)
    )
    liveness_polls: list[float] = []

    def exited_after_browser(received_process: object, timeout_seconds: float) -> int:
        assert received_process is process
        liveness_polls.append(timeout_seconds)
        return 9

    monkeypatch.setattr(
        smoke,
        "_application_exit_status",
        exited_after_browser,
    )

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert "exited with code 9" in result["error"]
    assert len(liveness_polls) == 1
    assert 0 < liveness_polls[0] <= arguments.timeout_seconds
    assert stopped == [process]


def test_browser_receipt_validator_rejects_inconsistent_success_data() -> None:
    mode = release_smoke.MODE_SPECS["default"]
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    valid = browser_receipt_for("default", frontend_url, api_url)
    invalid_receipts = [
        {"status": "passed"},
        {**valid, "mode": "off"},
        {**valid, "frontend_url": "http://127.0.0.1:52007/"},
        {**valid, "api_url": "http://127.0.0.1:52008"},
        {**valid, "feature_checks": {}},
        {**valid, "blocked_requests": ["http://127.0.0.1:52006/"]},
        {**valid, "non_get_requests": [{"method": "POST"}]},
        {**valid, "visual_mutation_request_observed": True},
        {**valid, "visual_system_v2_shell_visible": False},
    ]

    for receipt in invalid_receipts:
        with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
            release_smoke._validate_browser_receipt(
                receipt, mode, frontend_url, api_url
            )


def test_browser_receipt_validator_requires_the_complete_off_mode_proof() -> None:
    mode = release_smoke.MODE_SPECS["source-visuals-off"]
    frontend_url = "http://127.0.0.1:52010/"
    api_url = "http://127.0.0.1:52009"
    receipt = browser_receipt_for("source-visuals-off", frontend_url, api_url)
    receipt["source_list_get_observed"] = False

    with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
        release_smoke._validate_browser_receipt(receipt, mode, frontend_url, api_url)


def test_browser_receipt_validator_rejects_contradictory_raw_evidence() -> None:
    mode = release_smoke.MODE_SPECS["default"]
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    valid = browser_receipt_for("default", frontend_url, api_url)
    invalid_receipts = [
        {
            **valid,
            "feature_response": {
                "status": 500,
                "body": {"features": dict(mode.expected_features)},
            },
        },
        {
            **valid,
            "feature_response": {
                "status": 200,
                "body": {
                    "features": {
                        **mode.expected_features,
                        "sourceVisuals": False,
                    }
                },
            },
        },
        {
            **valid,
            "observed_requests": [
                {
                    "method": "POST",
                    "url": f"{api_url}/api/visuals",
                    "path": "/api/visuals",
                }
            ],
            "http_methods": ["POST"],
            "non_get_requests": [],
        },
        {**valid, "http_methods": []},
        {**valid, "non_get_requests": [{"method": "POST"}]},
        {
            **valid,
            "observed_requests": [
                {
                    "method": "GET",
                    "url": "http://127.0.0.1:52099/other",
                    "path": "/other",
                }
            ],
        },
        {
            **valid,
            "observed_responses": [{"status": "200", "url": frontend_url, "path": "/"}],
        },
    ]

    for receipt in invalid_receipts:
        with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
            release_smoke._validate_browser_receipt(
                receipt, mode, frontend_url, api_url
            )


def test_browser_receipt_validator_derives_off_source_list_from_raw_requests() -> None:
    mode = release_smoke.MODE_SPECS["source-visuals-off"]
    frontend_url = "http://127.0.0.1:52010/"
    api_url = "http://127.0.0.1:52009"
    receipt = browser_receipt_for("source-visuals-off", frontend_url, api_url)
    receipt["observed_requests"] = receipt["observed_requests"][:2]

    with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
        release_smoke._validate_browser_receipt(receipt, mode, frontend_url, api_url)


def test_browser_receipt_validator_rejects_evidence_over_its_bounded_limits() -> None:
    mode = release_smoke.MODE_SPECS["default"]
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    valid = browser_receipt_for("default", frontend_url, api_url)
    for field, limit in (
        ("observed_requests", release_smoke.MAX_BROWSER_OBSERVED_REQUESTS),
        ("observed_responses", release_smoke.MAX_BROWSER_OBSERVED_RESPONSES),
    ):
        receipt = dict(valid)
        entry = receipt[field][0]
        receipt[field] = [dict(entry) for _ in range(limit + 1)]
        with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
            release_smoke._validate_browser_receipt(
                receipt, mode, frontend_url, api_url
            )


def test_browser_receipt_validator_requires_correlated_success_responses() -> None:
    frontend_url = "http://127.0.0.1:52010/"
    api_url = "http://127.0.0.1:52009"
    off_mode = release_smoke.MODE_SPECS["source-visuals-off"]
    off_receipt = browser_receipt_for("source-visuals-off", frontend_url, api_url)
    missing_sources_response = dict(off_receipt)
    missing_sources_response["observed_responses"] = off_receipt["observed_responses"][
        :-1
    ]

    default_mode = release_smoke.MODE_SPECS["default"]
    default_receipt = browser_receipt_for("default", frontend_url, api_url)
    unmatched_response = dict(default_receipt)
    unmatched_response["observed_responses"] = [
        *default_receipt["observed_responses"],
        {"status": 200, "url": f"{api_url}/ghost", "path": "/ghost"},
    ]
    conflicting_feature_responses = dict(default_receipt)
    conflicting_feature_responses["observed_responses"] = [
        *default_receipt["observed_responses"],
        {
            "status": 500,
            "url": f"{api_url}/api/features",
            "path": "/api/features",
        },
    ]
    duplicate_feature_responses = dict(default_receipt)
    duplicate_feature_responses["observed_requests"] = [
        *default_receipt["observed_requests"],
        {
            "method": "GET",
            "url": f"{api_url}/api/features",
            "path": "/api/features",
        },
    ]
    duplicate_feature_responses["observed_responses"] = [
        *default_receipt["observed_responses"],
        {
            "status": 200,
            "url": f"{api_url}/api/features",
            "path": "/api/features",
        },
    ]

    for receipt, mode in (
        (missing_sources_response, off_mode),
        (unmatched_response, default_mode),
        (conflicting_feature_responses, default_mode),
        (duplicate_feature_responses, default_mode),
    ):
        with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
            release_smoke._validate_browser_receipt(
                receipt, mode, frontend_url, api_url
            )


def test_browser_receipt_validator_rejects_an_oversized_evidence_url() -> None:
    mode = release_smoke.MODE_SPECS["default"]
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    receipt = browser_receipt_for("default", frontend_url, api_url)
    receipt["observed_requests"].append(
        {
            "method": "GET",
            "url": f"{api_url}/?{'x' * (300 * 1024)}",
            "path": "/",
        }
    )

    with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
        release_smoke._validate_browser_receipt(receipt, mode, frontend_url, api_url)


def test_browser_receipt_validator_requires_the_exact_api_feature_response() -> None:
    mode = release_smoke.MODE_SPECS["default"]
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    receipt = browser_receipt_for("default", frontend_url, api_url)
    frontend_features_url = f"{frontend_url}api/features"
    unrelated_api_features_url = f"{api_url}/api/features?unrelated=1"
    receipt["observed_requests"] = [
        receipt["observed_requests"][0],
        {
            "method": "GET",
            "url": frontend_features_url,
            "path": "/api/features",
        },
        {
            "method": "GET",
            "url": unrelated_api_features_url,
            "path": "/api/features",
        },
    ]
    receipt["observed_responses"] = [
        receipt["observed_responses"][0],
        {
            "status": 200,
            "url": frontend_features_url,
            "path": "/api/features",
        },
        {
            "status": 200,
            "url": unrelated_api_features_url,
            "path": "/api/features",
        },
    ]

    with pytest.raises(smoke.SmokeFailure, match="browser receipt"):
        release_smoke._validate_browser_receipt(receipt, mode, frontend_url, api_url)


def test_browser_probe_transport_kills_stdout_before_retaining_two_mebibytes(
    tmp_path: Path,
) -> None:
    result = release_smoke._run_browser_probe(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "sys.stdout.buffer.write(b'x' * (2 * 1024 * 1024)); "
                "sys.stdout.flush(); time.sleep(10)"
            ),
        ],
        cwd=tmp_path,
        timeout_seconds=5.0,
    )

    assert result.stdout_limit_exceeded is True
    assert result.returncode != 0
    assert result.peak_stdout_bytes <= release_smoke.MAX_BROWSER_RECEIPT_BYTES
    assert len(result.stdout) <= release_smoke.MAX_BROWSER_RECEIPT_BYTES
    assert result.peak_stdout_bytes < 2 * 1024 * 1024
    with pytest.raises(smoke.SmokeFailure, match="exceeded"):
        release_smoke._parse_browser_receipt(result)


def test_browser_probe_transport_kills_the_first_byte_over_limit_promptly(
    tmp_path: Path,
) -> None:
    started_at = time.monotonic()
    result = release_smoke._run_browser_probe(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "sys.stdout.buffer.write(b'x' * 65537); "
                "sys.stdout.flush(); time.sleep(2)"
            ),
        ],
        cwd=tmp_path,
        timeout_seconds=5.0,
    )
    elapsed = time.monotonic() - started_at

    assert result.stdout_limit_exceeded is True
    assert result.returncode != 0
    assert len(result.stdout) <= release_smoke.MAX_BROWSER_RECEIPT_BYTES
    assert elapsed < 1.0


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_browser_probe_timeout_terminates_descendants_holding_pipes(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "descendant.pid"
    started_at = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        release_smoke._run_browser_probe(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib, subprocess, sys, time; "
                    "child = subprocess.Popen(['/bin/sleep', '2']); "
                    "pathlib.Path(sys.argv[1]).write_text(str(child.pid), "
                    "encoding='utf-8'); time.sleep(2)"
                ),
                str(descendant_pid_path),
            ],
            cwd=tmp_path,
            timeout_seconds=0.35,
        )
    elapsed = time.monotonic() - started_at

    assert descendant_pid_path.is_file()
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    assert elapsed < 1.0
    assert len(raised.value.output or b"") <= release_smoke.MAX_BROWSER_RECEIPT_BYTES
    assert len(raised.value.stderr or b"") <= release_smoke.MAX_BROWSER_STDERR_BYTES
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("browser probe descendant survived process-group termination")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_browser_probe_reaps_descendants_after_a_clean_leader_exit(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "clean-leader-descendant.pid"
    started_at = time.monotonic()
    result = release_smoke._run_browser_probe(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, subprocess, sys; "
                "child = subprocess.Popen(['/bin/sleep', '2'], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), "
                "encoding='utf-8'); "
                'sys.stdout.write(\'{"status":"passed"}\'); '
                "sys.stdout.flush()"
            ),
            str(descendant_pid_path),
        ],
        cwd=tmp_path,
        timeout_seconds=5.0,
    )
    elapsed = time.monotonic() - started_at

    assert result.returncode == 0
    assert result.stdout == b'{"status":"passed"}'
    assert elapsed < 1.0
    assert descendant_pid_path.is_file()
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("browser probe clean-exit descendant survived process-group reap")


def test_browser_probe_transport_bounds_stderr_without_deadlocking(
    tmp_path: Path,
) -> None:
    result = release_smoke._run_browser_probe(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.buffer.write(b'e' * (2 * 1024 * 1024))",
        ],
        cwd=tmp_path,
        timeout_seconds=5.0,
    )

    assert result.returncode == 0
    assert result.stderr_limit_exceeded is True
    assert len(result.stderr) <= release_smoke.MAX_BROWSER_STDERR_BYTES


def test_browser_probe_transport_preserves_timeout_semantics(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        release_smoke._run_browser_probe(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            timeout_seconds=0.05,
        )


def test_parse_browser_receipt_rejects_oversized_stdout_before_json_loading() -> None:
    browser = subprocess.CompletedProcess(
        args=["node"],
        returncode=0,
        stdout=json.dumps({"status": "passed", "url": "x" * (300 * 1024)}),
        stderr="",
    )

    with pytest.raises(smoke.SmokeFailure, match="exceeded"):
        release_smoke._parse_browser_receipt(browser)


def test_release_smoke_does_not_persist_an_oversized_browser_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frontend_url = "http://127.0.0.1:52006/"
    api_url = "http://127.0.0.1:52005"
    arguments, _process, _stopped = prepare_ready_mode(
        monkeypatch,
        tmp_path,
        browser_receipt_for("default", frontend_url, api_url),
    )
    oversized_stdout = json.dumps(
        {
            "status": "passed",
            "frontend_url": f"{frontend_url}?{'x' * (300 * 1024)}",
        }
    )
    monkeypatch.setattr(
        release_smoke,
        "_run_browser_probe",
        lambda *_args, **_kwargs: browser_probe_result(stdout=oversized_stdout),
    )

    assert release_smoke.run_release_smoke(arguments) == 1

    for receipt_name in ("default.json", "summary.json"):
        raw_receipt = (arguments.output_root / receipt_name).read_bytes()
        assert len(raw_receipt) <= 64 * 1024
        receipt = json.loads(raw_receipt)
        assert "browser" not in receipt


def test_run_mode_uses_all_feature_expectations_and_cleans_up_on_browser_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    monkeypatch.setenv("DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED", "0")
    fixture_root = tmp_path / "fixture"
    fixture = package_smoke_fixture.SmokeFixture(
        root=fixture_root,
        home=fixture_root / "home",
        data_dir=fixture_root / "data",
        model_dir=fixture_root / "models",
        readiness_file=fixture_root / "data" / "logs" / "desktop-readiness.json",
        environment={
            "HOME": str(fixture_root / "home"),
            "DEEPER_NOTEBOOK_DATA_DIR": str(fixture_root / "data"),
            "UV_CACHE_DIR": str(arguments.uv_cache_dir),
            "UV_OFFLINE": "1",
            "OPENCHRONICLE_MCP_URL": "http://[::1]:1/mcp",
        },
    )
    process = SimpleNamespace(pid=901, returncode=None)
    launched: list[tuple[list[str], dict[str, str]]] = []
    stopped: list[object] = []

    monkeypatch.setattr(
        release_smoke, "prepare_smoke_fixture", lambda *_a, **_k: fixture
    )

    def fake_launch(command, environment, _timeout):
        launched.append((command, environment))
        return process, process.pid

    monkeypatch.setattr(release_smoke, "launch_monitored_process", fake_launch)
    monkeypatch.setattr(
        release_smoke,
        "wait_for_readiness",
        lambda *_a, **_k: ("http://127.0.0.1:52001", "http://127.0.0.1:52002/"),
    )
    monkeypatch.setattr(
        release_smoke,
        "_run_browser_probe",
        lambda *_a, **_k: browser_probe_result(returncode=1, stderr="browser failed"),
    )
    monkeypatch.setattr(
        release_smoke,
        "stop_process",
        lambda process_arg, _timeout: stopped.append(process_arg),
    )

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert stopped == [process]
    assert launched[0][0] == [str(arguments.executable)]
    assert release_smoke.DEFAULT_EXPECTED_FEATURES == {
        "evidenceStudio": True,
        "modelFleet": True,
        "researchRuns": True,
        "sourceVisuals": True,
        "studyWorkbench": True,
        "visualRefresh": True,
    }
    assert "DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED" not in launched[0][1]
    receipt = json.loads(
        (arguments.output_root / "default.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "failed"


def test_run_mode_cleans_up_after_child_readiness_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    fixture_root = tmp_path / "fixture-child-failure"
    fixture = package_smoke_fixture.SmokeFixture(
        root=fixture_root,
        home=fixture_root / "home",
        data_dir=fixture_root / "data",
        model_dir=fixture_root / "models",
        readiness_file=fixture_root / "data" / "logs" / "desktop-readiness.json",
        environment={"HOME": str(fixture_root / "home")},
    )
    process = SimpleNamespace(pid=903, returncode=None)
    stopped: list[object] = []
    monkeypatch.setattr(
        release_smoke, "prepare_smoke_fixture", lambda *_a, **_k: fixture
    )
    monkeypatch.setattr(
        release_smoke,
        "launch_monitored_process",
        lambda *_args, **_kwargs: (process, process.pid),
    )
    monkeypatch.setattr(
        release_smoke,
        "wait_for_readiness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            smoke.SmokeFailure("child exited before readiness")
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "stop_process",
        lambda process_arg, _timeout: stopped.append(process_arg),
    )

    result = release_smoke.run_mode("default", arguments)

    assert result["status"] == "failed"
    assert stopped == [process]
    receipt = json.loads(
        (arguments.output_root / "default.json").read_text(encoding="utf-8")
    )
    assert receipt["checks"]["clean_shutdown"] == {"passed": True}


def test_run_mode_off_changes_only_source_visuals_and_parses_stdout_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = arguments_for(tmp_path)
    fixture_root = tmp_path / "fixture-off"
    fixture = package_smoke_fixture.SmokeFixture(
        root=fixture_root,
        home=fixture_root / "home",
        data_dir=fixture_root / "data",
        model_dir=fixture_root / "models",
        readiness_file=fixture_root / "data" / "logs" / "desktop-readiness.json",
        environment={
            "HOME": str(fixture_root / "home"),
            "DEEPER_NOTEBOOK_DATA_DIR": str(fixture_root / "data"),
            "UV_CACHE_DIR": str(arguments.uv_cache_dir),
            "UV_OFFLINE": "1",
            "OPENCHRONICLE_MCP_URL": "http://[::1]:1/mcp",
            "DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED": "0",
        },
    )
    process = SimpleNamespace(pid=902, returncode=None)
    launched: list[dict[str, str]] = []
    commands: list[list[str]] = []
    stopped: list[object] = []
    browser_receipt = browser_receipt_for(
        "source-visuals-off",
        "http://127.0.0.1:52004/",
        "http://127.0.0.1:52003",
    )

    monkeypatch.setattr(
        release_smoke, "prepare_smoke_fixture", lambda *_a, **_k: fixture
    )
    monkeypatch.setattr(
        release_smoke,
        "launch_monitored_process",
        lambda command, environment, _timeout: (
            commands.append(command)
            or launched.append(environment)
            or (process, process.pid)
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "wait_for_readiness",
        lambda *_a, **_k: ("http://127.0.0.1:52003", "http://127.0.0.1:52004/"),
    )
    monkeypatch.setattr(
        release_smoke,
        "_run_browser_probe",
        lambda *_a, **_k: browser_probe_result(
            stdout=json.dumps(browser_receipt), stderr="ignored"
        ),
    )
    monkeypatch.setattr(
        release_smoke,
        "stop_process",
        lambda process_arg, _timeout: stopped.append(process_arg),
    )

    result = release_smoke.run_mode("source-visuals-off", arguments)

    assert result["status"] == "passed"
    assert stopped == [process]
    assert commands == [[str(arguments.executable)]]
    assert launched[0]["DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED"] == "0"
    assert launched[0]["HOME"] == fixture.environment["HOME"]
    assert (
        launched[0]["DEEPER_NOTEBOOK_DATA_DIR"]
        == fixture.environment["DEEPER_NOTEBOOK_DATA_DIR"]
    )
    browser_command = result["browser_command"]
    assert browser_command[browser_command.index("--mode") + 1] == "off"
    assert result["browser"] == browser_receipt
