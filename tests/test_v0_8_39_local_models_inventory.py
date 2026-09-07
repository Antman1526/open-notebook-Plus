"""v0.8.39 Phase 4a — GGUF inventory + metadata tests.

Backend-only this phase. Covers:
  - parse_quant_from_filename
  - parse_param_count_b
  - parse_gguf_metadata (filename-fallback path; the gguf-library
    path is tested implicitly by the existing v0.7.206 launcher
    fixture — we don't re-cover it here to avoid coupling to whether
    the optional `gguf` dep is installed in CI)
  - enumerate_models (real tempdir with hand-written `.gguf` stubs)
  - GET /api/local-models/inventory endpoint (env precedence, dir
    missing, dir present)
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import local_models as local_models_router
from deeper_notebook.local_models.gguf_metadata import (
    parse_gguf_metadata,
    parse_param_count_b,
    parse_quant_from_filename,
)
from deeper_notebook.local_models.inventory import (
    LocalModelInfo,
    enumerate_models,
)


def test_local_model_readiness_contract_is_public():
    """Task 5 exposes one pure readiness classifier for routing gates."""
    import deeper_notebook.local_models as local_models

    assert hasattr(local_models, "classify_model_readiness")


def test_inventory_accepts_explicit_external_root_trust():
    """Discovery must not infer permission to traverse an external symlink."""
    assert "trusted_external_roots" in inspect.signature(enumerate_models).parameters


def test_inventory_never_recurses_an_untrusted_external_stt_symlink(tmp_path):
    """The exact selected-link and resolved-target fingerprints are required."""
    from deeper_notebook.local_models.contracts import ExternalModelRootTrust
    from deeper_notebook.local_models.inventory import model_root_fingerprint

    selected_root = tmp_path / "AI_Models"
    selected_root.mkdir()
    external_stt = tmp_path / "external-stt"
    external_stt.mkdir()
    voice = external_stt / "whisper-small-q5_k_m.gguf"
    voice.write_bytes(b"owner-managed-stt-weights")
    link = selected_root / "STT"
    link.symlink_to(external_stt, target_is_directory=True)
    fixture_hash = hashlib.sha256(voice.read_bytes()).hexdigest()
    trust = ExternalModelRootTrust(
        selected_root_fingerprint=model_root_fingerprint(link),
        resolved_target_fingerprint=model_root_fingerprint(external_stt),
    )

    assert enumerate_models(selected_root) == []
    trusted_rows = enumerate_models(
        selected_root,
        trusted_external_roots=[trust],
    )

    assert [row.name for row in trusted_rows] == ["whisper-small-q5_k_m"]
    assert hashlib.sha256(voice.read_bytes()).hexdigest() == fixture_hash


def test_inventory_never_follows_an_untrusted_external_mlx_root(tmp_path):
    selected_root = tmp_path / "AI_Models"
    selected_root.mkdir()
    external_mlx = tmp_path / "external-mlx"
    repo = external_mlx / "mlx-community__outside-7B-4bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text('{"model_type": "qwen"}')
    (repo / "model.safetensors").write_bytes(b"external MLX weights")
    (selected_root / "MLX").symlink_to(external_mlx, target_is_directory=True)

    assert enumerate_models(selected_root) == []


# ---------------------------------------------------------------------------
# parse_quant_from_filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("qwen2.5-7b-instruct-q4_k_m.gguf", "Q4_K_M"),
        ("hermes-3-llama-3.1-8b.Q5_K_M.gguf", "Q5_K_M"),
        ("Llama-3.2-3B-Instruct-Q8_0.gguf", "Q8_0"),
        ("phi-3-mini-4k-instruct.IQ4_XS.gguf", "IQ4_XS"),
        # Longest-match-wins: Q5_K_M not Q5
        ("foo-q5_k_m.gguf", "Q5_K_M"),
        # No quant marker
        ("model.gguf", None),
        ("", None),
    ],
)
def test_parse_quant_from_filename(filename, expected):
    assert parse_quant_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# parse_param_count_b
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("qwen2.5-7b-instruct-q4_k_m.gguf", 7.0),
        ("hermes-3-8b.gguf", 8.0),
        ("llama-3.2-1.5b-instruct-q4.gguf", 1.5),
        ("model-13b.gguf", 13.0),
        # No param marker → None
        ("model.gguf", None),
        ("foo-bar.gguf", None),
        ("", None),
    ],
)
def test_parse_param_count_b(filename, expected):
    assert parse_param_count_b(filename) == expected


# ---------------------------------------------------------------------------
# parse_gguf_metadata (filename-fallback path)
# ---------------------------------------------------------------------------


def test_parse_gguf_metadata_filename_fallback(tmp_path):
    """When the `gguf` library is missing OR the file isn't really a
    GGUF, we fall back to filename heuristics for arch/quant/params
    and `os.stat` for size. context_length stays None."""
    p = tmp_path / "qwen2.5-7b-instruct-q4_k_m.gguf"
    p.write_bytes(b"NOT-A-REAL-GGUF-FILE")
    md = parse_gguf_metadata(p)
    # Heuristics from filename
    assert md.quant == "Q4_K_M"
    assert md.parameter_count_b == 7.0
    assert md.architecture == "qwen2"  # via _parse_arch_from_filename
    # File size is always real
    assert md.file_size_bytes == len(b"NOT-A-REAL-GGUF-FILE")
    # context_length can't be inferred without library + valid file
    assert md.context_length is None


def test_parse_gguf_metadata_handles_missing_file(tmp_path):
    """Non-existent file → file_size_bytes=0, everything else best-effort
    from filename. Never raises.

    Note: the quant patterns require fully-qualified llama.cpp names
    (Q5_0, Q5_K_M, etc) — a bare `-q5` in a filename is ambiguous and
    intentionally NOT matched. Use a tagged filename here so the test
    covers the happy path."""
    p = tmp_path / "hermes-3-8b-q5_0.gguf"
    md = parse_gguf_metadata(p)
    assert md.file_size_bytes == 0
    assert md.quant == "Q5_0"
    assert md.parameter_count_b == 8.0


# ---------------------------------------------------------------------------
# enumerate_models
# ---------------------------------------------------------------------------


def test_enumerate_models_empty_dir(tmp_path):
    """Empty dir → empty list, no error."""
    assert enumerate_models(tmp_path) == []


def test_enumerate_models_missing_dir():
    """Non-existent dir → empty list (no exception)."""
    assert enumerate_models(Path("/no/such/dir/exists/anywhere/v0_8_39")) == []


def test_enumerate_models_filters_non_gguf(tmp_path):
    """Filters: non-.gguf files, dotfiles, .tmp/.part, zero-byte stubs."""
    # Real .gguf files
    (tmp_path / "qwen2.5-7b-q4_k_m.gguf").write_bytes(b"x" * 100)
    (tmp_path / "hermes-3-8b-q5.gguf").write_bytes(b"x" * 200)
    # Junk we should skip
    (tmp_path / "README.md").write_text("not a model")
    (tmp_path / ".hidden.gguf").write_bytes(b"x" * 50)
    (tmp_path / "download-in-progress.gguf.tmp").write_bytes(b"x" * 50)
    (tmp_path / "zero-byte.gguf").write_bytes(b"")
    # HuggingFace-style subdirs should be scanned; junk inside them is
    # still filtered the same way as top-level files.
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "should-show.gguf").write_bytes(b"x" * 100)
    (sub / "should-not-show.gguf.part").write_bytes(b"x" * 100)

    rows = enumerate_models(tmp_path)
    names = sorted(r.name for r in rows)
    assert names == ["hermes-3-8b-q5", "qwen2.5-7b-q4_k_m", "should-show"]


def test_enumerate_models_recurses_into_huggingface_style_subdirs(tmp_path):
    """v0.8.69 — real AI_Models layouts store downloaded GGUFs inside
    repo-named folders under GGUF/. Inventory must match the recursive
    launcher auto-register scan so Settings shows usable installed models."""
    nested = tmp_path / "GGUF" / "tvall43__Qwen3.6-14B-A3B-FableVibes-GGUF"
    nested.mkdir(parents=True)
    model = nested / "Qwen3.6-14B-A3B-FableVibes-Q4_K_M.gguf"
    model.write_bytes(b"x" * 2048)

    rows = enumerate_models(tmp_path)

    assert [r.name for r in rows] == ["Qwen3.6-14B-A3B-FableVibes-Q4_K_M"]
    assert rows[0].path == str(model)
    assert rows[0].metadata.quant == "Q4_K_M"


def test_enumerate_models_skips_mmproj_auxiliary_ggufs(tmp_path):
    """v0.8.69 — multimodal projection GGUFs are companion files, not
    standalone chat models. Do not show them as set-active candidates."""
    repo = tmp_path / "GGUF" / "vision-model"
    repo.mkdir(parents=True)
    (repo / "Qwable-5-27B-Coder-Q6_K.gguf").write_bytes(b"x" * 2048)
    (repo / "mmproj-Qwable-5-27B-Coder-f16.gguf").write_bytes(b"y" * 2048)
    (repo / "Qwable-mmproj-F16.gguf").write_bytes(b"z" * 2048)

    rows = enumerate_models(tmp_path)

    assert [r.name for r in rows] == ["Qwable-5-27B-Coder-Q6_K"]


def test_enumerate_models_includes_mlx_model_repos(tmp_path):
    """Evidence Studio model fleet: MLX repos under AI_Models/MLX should
    show up as runnable local models without mutating the model folder."""
    repo = tmp_path / "MLX" / "mlx-community__Qwen3-Coder-30B-A3B-MLX-4bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text(
        '{"model_type": "qwen3", "max_position_embeddings": 262144}'
    )
    (repo / "model.safetensors").write_bytes(b"x" * 2048)
    (repo / "tokenizer.json").write_text("{}")
    (tmp_path / "MLX" / ".cache").mkdir()
    (tmp_path / "MLX" / "empty-repo").mkdir()

    rows = enumerate_models(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.runtime == "mlx"
    assert row.name == "mlx-community/Qwen3-Coder-30B-A3B-MLX-4bit"
    assert row.path == str(repo)
    assert row.metadata.architecture == "qwen3"
    assert row.metadata.context_length == 262144
    assert row.metadata.quant == "4bit"
    assert row.metadata.parameter_count_b == 30.0
    assert row.metadata.file_size_bytes == 2048


def test_enumerate_models_includes_transformers_model_repos(tmp_path):
    """Local fleet inventory should also surface complete HuggingFace-style
    Transformers repos under AI_Models/Transformers so downloaded local
    models are visible even before a runtime provider is configured."""
    repo = tmp_path / "Transformers" / "microsoft__FastContext-1.0-4B-SFT"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text(
        '{"model_type": "fastcontext", "max_position_embeddings": 65536}'
    )
    (repo / "model-00000-of-00002.safetensors").write_bytes(b"x" * 2048)
    (repo / "model-00001-of-00002.safetensors").write_bytes(b"y" * 4096)
    (tmp_path / "Transformers" / "partial-repo").mkdir()

    rows = enumerate_models(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.runtime == "transformers"
    assert row.name == "microsoft/FastContext-1.0-4B-SFT"
    assert row.path == str(repo)
    assert row.metadata.architecture == "fastcontext"
    assert row.metadata.context_length == 65536
    assert row.metadata.quant is None
    assert row.metadata.parameter_count_b == 4.0
    assert row.metadata.file_size_bytes == 6144


def test_enumerate_models_returns_metadata(tmp_path):
    """Each row carries the full GGUFMetadata bundle."""
    (tmp_path / "qwen2.5-7b-instruct-q4_k_m.gguf").write_bytes(b"x" * 1024)
    rows = enumerate_models(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, LocalModelInfo)
    assert row.name == "qwen2.5-7b-instruct-q4_k_m"
    assert row.path.endswith(".gguf")
    assert row.metadata.quant == "Q4_K_M"
    assert row.metadata.parameter_count_b == 7.0
    assert row.metadata.architecture == "qwen2"
    assert row.metadata.file_size_bytes == 1024


def test_enumerate_models_stable_sort(tmp_path):
    """Alphabetical name sort — UI shouldn't see random ordering."""
    (tmp_path / "zebra-1b.gguf").write_bytes(b"x" * 10)
    (tmp_path / "alpha-1b.gguf").write_bytes(b"x" * 10)
    (tmp_path / "mike-1b.gguf").write_bytes(b"x" * 10)
    rows = enumerate_models(tmp_path)
    assert [r.name for r in rows] == ["alpha-1b", "mike-1b", "zebra-1b"]


# ---------------------------------------------------------------------------
# GET /api/local-models/inventory
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(local_models_router.router)
    return a


def test_inventory_endpoint_returns_unavailable_when_dir_missing(
    app,
    monkeypatch,
    tmp_path,
):
    bogus = tmp_path / "does-not-exist"
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(bogus))
    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["models"] == []
    assert body["model_dir"] == str(bogus)


def test_inventory_endpoint_lists_models(app, monkeypatch, tmp_path):
    """Happy path — env var points at a dir with GGUFs; endpoint returns them."""
    (tmp_path / "qwen2.5-7b-instruct-q4_k_m.gguf").write_bytes(b"x" * 2048)
    (tmp_path / "hermes-3-8b-q5_k_m.gguf").write_bytes(b"y" * 4096)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(tmp_path))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()
    assert body["available"] is True
    assert body["model_dir"] == str(tmp_path)
    assert len(body["models"]) == 2
    by_name = {m["name"]: m for m in body["models"]}
    qwen = by_name["qwen2.5-7b-instruct-q4_k_m"]
    assert qwen["quant"] == "Q4_K_M"
    assert qwen["parameter_count_b"] == 7.0
    assert qwen["file_size_bytes"] == 2048
    assert qwen["architecture"] == "qwen2"
    hermes = by_name["hermes-3-8b-q5_k_m"]
    assert hermes["quant"] == "Q5_K_M"
    assert hermes["parameter_count_b"] == 8.0


def test_inventory_endpoint_lists_mlx_models(app, monkeypatch, tmp_path):
    repo = tmp_path / "MLX" / "mlx-community__North-Mini-Code-1.0-6bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text('{"model_type": "qwen2"}')
    (repo / "model.safetensors").write_bytes(b"x" * 4096)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(tmp_path))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()

    assert body["available"] is True
    assert len(body["models"]) == 1
    model = body["models"][0]
    assert model["runtime"] == "mlx"
    assert model["name"] == "mlx-community/North-Mini-Code-1.0-6bit"
    assert model["path"] == str(repo)
    assert model["launcher_model_ref"] == "MLX/mlx-community__North-Mini-Code-1.0-6bit"
    assert model["quant"] == "6bit"
    assert model["file_size_bytes"] == 4096


def test_inventory_endpoint_includes_safe_launcher_config_summary(
    app,
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "AI_Models"
    model_dir.mkdir()
    (model_dir / "qwen-7b-q4.gguf").write_bytes(b"x" * 2048)
    config_home = tmp_path / "home"
    config_dir = config_home / ".deeper-notebook"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f"model_dir = '{model_dir}'",
                "provider = 'mlx'",
                "default_model = 'MLX/mlx-community__North-Mini-Code-1.0-6bit'",
                "surreal_user = 'root'",
                "surreal_password = 'do-not-leak'",
                "encryption_key = 'also-do-not-leak'",
            ]
        )
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("HOME", str(config_home))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()

    assert body["launcher_config"] == {
        "available": True,
        "path": str(config_path),
        "provider": "mlx",
        "default_model": "MLX/mlx-community__North-Mini-Code-1.0-6bit",
        "model_dir": str(model_dir),
        "model_dir_matches_inventory": True,
        "active_gguf_model": "",
        "active_mlx_model": "",
    }
    assert "do-not-leak" not in str(body)
    assert "also-do-not-leak" not in str(body)


def test_inventory_endpoint_marks_activation_state(
    app,
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "AI_Models"
    gguf = model_dir / "GGUF" / "Qwen3-8B-Q4_K_M.gguf"
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"x" * 2048)
    mlx = model_dir / "MLX" / "mlx-community__North-Mini-Code-1.0-6bit"
    mlx.mkdir(parents=True)
    (mlx / "config.json").write_text('{"model_type": "qwen2"}')
    (mlx / "model.safetensors").write_bytes(b"y" * 4096)
    config_home = tmp_path / "home"
    config_dir = config_home / ".deeper-notebook"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                f"model_dir = '{model_dir}'",
                "provider = 'mlx'",
                "default_model = 'MLX/mlx-community__North-Mini-Code-1.0-6bit'",
            ]
        )
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL", str(gguf))
    monkeypatch.setenv("HOME", str(config_home))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()
    by_name = {model["name"]: model for model in body["models"]}

    assert body["launcher_config"]["active_gguf_model"] == str(gguf)
    assert by_name["Qwen3-8B-Q4_K_M"]["is_live_active"] is True
    assert by_name["Qwen3-8B-Q4_K_M"]["is_launch_default"] is False
    assert by_name["Qwen3-8B-Q4_K_M"]["activation_mode"] == "active_now"
    assert "live chat model" in by_name["Qwen3-8B-Q4_K_M"]["activation_detail"]

    assert by_name["mlx-community/North-Mini-Code-1.0-6bit"]["is_live_active"] is False
    assert (
        by_name["mlx-community/North-Mini-Code-1.0-6bit"]["is_launch_default"] is True
    )
    assert (
        by_name["mlx-community/North-Mini-Code-1.0-6bit"]["activation_mode"]
        == "launch_default"
    )


def test_set_launch_default_updates_native_config_for_mlx_model(
    app,
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "AI_Models"
    repo = model_dir / "MLX" / "mlx-community__North-Mini-Code-1.0-6bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text('{"model_type": "qwen2"}')
    (repo / "model.safetensors").write_bytes(b"x" * 4096)
    config_home = tmp_path / "home"
    config_dir = config_home / ".deeper-notebook"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f"model_dir = '{model_dir}'",
                "provider = 'none'",
                "default_model = ''",
                "surreal_user = 'root'",
                "surreal_password = 'keep-this-secret'",
                "theme = 'dracula'",
                "openchronicle_choice = 'prompt'",
                "encryption_key = 'keep-this-key'",
            ]
        )
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("HOME", str(config_home))

    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/launch-default",
            json={
                "launcher_model_ref": "MLX/mlx-community__North-Mini-Code-1.0-6bit",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["launcher_config"]["provider"] == "mlx"
    assert (
        body["launcher_config"]["default_model"]
        == "MLX/mlx-community__North-Mini-Code-1.0-6bit"
    )
    updated = config_path.read_text()
    assert "provider = 'mlx'" in updated
    assert "default_model = 'MLX/mlx-community__North-Mini-Code-1.0-6bit'" in updated
    assert "surreal_password = 'keep-this-secret'" in updated
    assert "encryption_key = 'keep-this-key'" in updated
    assert "theme = 'dracula'" in updated
    assert "openchronicle_choice = 'prompt'" in updated


def test_set_launch_default_updates_native_config_for_gguf_model(
    app,
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "AI_Models"
    gguf = model_dir / "GGUF" / "Qwen3-8B-Q4_K_M.gguf"
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"x" * 2048)
    config_home = tmp_path / "home"
    config_dir = config_home / ".deeper-notebook"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f"model_dir = '{model_dir}'",
                "provider = 'none'",
                "default_model = ''",
                "surreal_user = 'root'",
                "surreal_password = 'keep-this-secret'",
                "theme = 'dracula'",
                "openchronicle_choice = 'prompt'",
                "encryption_key = 'keep-this-key'",
            ]
        )
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("HOME", str(config_home))

    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/launch-default",
            json={"launcher_model_ref": "GGUF/Qwen3-8B-Q4_K_M.gguf"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["launcher_config"]["provider"] == "llamacpp"
    assert body["launcher_config"]["default_model"] == "GGUF/Qwen3-8B-Q4_K_M.gguf"
    updated = config_path.read_text()
    assert "provider = 'llamacpp'" in updated
    assert "default_model = 'GGUF/Qwen3-8B-Q4_K_M.gguf'" in updated
    assert "surreal_password = 'keep-this-secret'" in updated
    assert "encryption_key = 'keep-this-key'" in updated
    assert "theme = 'dracula'" in updated
    assert "openchronicle_choice = 'prompt'" in updated


def test_set_launch_default_rejects_inventory_only_model(
    app,
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "AI_Models"
    repo = model_dir / "Transformers" / "microsoft__FastContext-1.0-4B-SFT"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text('{"model_type": "fastcontext"}')
    (repo / "model.safetensors").write_bytes(b"x" * 4096)
    config_home = tmp_path / "home"
    config_dir = config_home / ".deeper-notebook"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                f"model_dir = '{model_dir}'",
                "provider = 'none'",
                "default_model = ''",
                "surreal_user = 'root'",
                "surreal_password = 'keep-this-secret'",
            ]
        )
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("HOME", str(config_home))

    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/launch-default",
            json={
                "launcher_model_ref": "Transformers/microsoft__FastContext-1.0-4B-SFT",
            },
        )

    assert resp.status_code == 400
    assert "not supported" in resp.json()["detail"]


def test_inventory_endpoint_marks_runtime_capabilities(app, monkeypatch, tmp_path):
    gguf = tmp_path / "GGUF" / "qwen-7b-q4_k_m.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"x" * 2048)
    transformers = tmp_path / "Transformers" / "microsoft__FastContext-1.0-4B-SFT"
    transformers.mkdir(parents=True)
    (transformers / "config.json").write_text(
        '{"model_type": "fastcontext", "max_position_embeddings": 65536}'
    )
    (transformers / "model.safetensors").write_bytes(b"y" * 4096)
    experimental = tmp_path / "Experimental" / "antman__Prototype-7B"
    experimental.mkdir(parents=True)
    (experimental / "config.json").write_text(
        '{"model_type": "prototype", "max_position_embeddings": 32768}'
    )
    (experimental / "model.safetensors").write_bytes(b"z" * 4096)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(tmp_path))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()
    by_name = {model["name"]: model for model in body["models"]}

    assert by_name["qwen-7b-q4_k_m"]["runtime"] == "gguf"
    assert by_name["qwen-7b-q4_k_m"]["runnable"] is True
    assert by_name["qwen-7b-q4_k_m"]["activation_supported"] is True
    assert by_name["qwen-7b-q4_k_m"]["runtime_status"] == "runnable"
    assert by_name["qwen-7b-q4_k_m"]["runtime_note"] is None
    assert by_name["qwen-7b-q4_k_m"]["setup_href"] is None
    assert by_name["qwen-7b-q4_k_m"]["setup_label"] is None

    assert by_name["microsoft/FastContext-1.0-4B-SFT"]["runtime"] == "transformers"
    assert by_name["microsoft/FastContext-1.0-4B-SFT"]["runnable"] is False
    assert by_name["microsoft/FastContext-1.0-4B-SFT"]["activation_supported"] is False
    assert (
        by_name["microsoft/FastContext-1.0-4B-SFT"]["runtime_status"]
        == "inventory_only"
    )
    assert "provider" in by_name["microsoft/FastContext-1.0-4B-SFT"]["runtime_note"]
    assert (
        by_name["microsoft/FastContext-1.0-4B-SFT"]["setup_href"]
        == "/settings/launcher-prefs"
    )

    assert by_name["antman/Prototype-7B"]["runtime"] == "experimental"
    assert by_name["antman/Prototype-7B"]["runnable"] is False
    assert by_name["antman/Prototype-7B"]["activation_supported"] is False
    assert by_name["antman/Prototype-7B"]["runtime_status"] == "inventory_only"
    assert "Experimental" in by_name["antman/Prototype-7B"]["runtime_note"]
    assert (
        by_name["microsoft/FastContext-1.0-4B-SFT"]["setup_label"]
        == "Open launcher preferences"
    )


def test_inventory_endpoint_env_precedence(app, monkeypatch, tmp_path):
    """DEEPER_NOTEBOOK_MODEL_DIR wins over the launcher default."""
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "m-1b-q4.gguf").write_bytes(b"x" * 10)

    launcher_default = tmp_path / "launcher_default"
    launcher_default.mkdir()
    (launcher_default / "other-1b-q4.gguf").write_bytes(b"y" * 10)

    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(explicit))
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR_DEFAULT", str(launcher_default))

    with TestClient(app) as client:
        resp = client.get("/api/local-models/inventory")
    body = resp.json()
    assert body["model_dir"] == str(explicit)
    assert [m["name"] for m in body["models"]] == ["m-1b-q4"]
