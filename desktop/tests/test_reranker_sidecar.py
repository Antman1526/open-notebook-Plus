"""Unit tests for reranker sidecar detection and spawning in desktop.launcher."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from desktop.launcher import Supervisor


class DummyCfg:
    def __init__(self, model_dir: Path | None = None):
        self.model_dir = model_dir


def test_detect_reranker_model_from_env(tmp_path: Path, monkeypatch):
    reranker_file = tmp_path / "bge-reranker-v2-m3-Q4_K_M.gguf"
    reranker_file.write_bytes(b"x" * 100)

    monkeypatch.setenv("DEEPER_NOTEBOOK_RERANKER_MODEL_PATH", str(reranker_file))

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.cfg = DummyCfg()
    supervisor.nomic_embed_path = None

    detected = supervisor._detect_reranker_model()
    assert detected == reranker_file


def test_detect_reranker_model_from_model_dir(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_RERANKER_MODEL_PATH", raising=False)

    reranker_file = tmp_path / "bge-reranker-large-Q8_0.gguf"
    reranker_file.write_bytes(b"x" * 100)

    other_file = tmp_path / "qwen2.5-7b.gguf"
    other_file.write_bytes(b"x" * 100)

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.cfg = DummyCfg(model_dir=tmp_path)
    supervisor.nomic_embed_path = None

    detected = supervisor._detect_reranker_model()
    assert detected == reranker_file


def test_detect_reranker_model_none_found(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_RERANKER_MODEL_PATH", raising=False)

    other_file = tmp_path / "qwen2.5-7b.gguf"
    other_file.write_bytes(b"x" * 100)

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.cfg = DummyCfg(model_dir=tmp_path)
    supervisor.nomic_embed_path = None

    detected = supervisor._detect_reranker_model()
    assert detected is None


def test_spawn_llamacpp_rerank_calls_spawn_with_rerank_flag(tmp_path: Path, monkeypatch):
    reranker_file = tmp_path / "bge-reranker-large.gguf"
    reranker_file.write_bytes(b"x" * 100)
    monkeypatch.setenv("DEEPER_NOTEBOOK_RERANKER_MODEL_PATH", str(reranker_file))

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.cfg = DummyCfg()
    supervisor.nomic_embed_path = None
    supervisor.venv_python = Path("/fake/python")
    supervisor.upstream_root = Path("/fake/root")
    supervisor.session_env = {}
    supervisor._spawn = MagicMock()
    supervisor._push_env_to_api = MagicMock()

    supervisor._spawn_llamacpp_rerank(port=8085)

    assert supervisor._spawn.called
    spawn_args, spawn_kwargs = supervisor._spawn.call_args
    cmd_args = spawn_args[0]

    assert "--rerank" in cmd_args
    assert cmd_args[cmd_args.index("--rerank") + 1] == "true"
    assert "--model" in cmd_args
    assert cmd_args[cmd_args.index("--model") + 1] == str(reranker_file)
    assert "--port" in cmd_args
    assert cmd_args[cmd_args.index("--port") + 1] == "8085"
    assert spawn_kwargs["name"] == "llamacpp_rerank"
    assert supervisor.session_env.get("DEEPER_NOTEBOOK_RERANKER_URL") == "http://127.0.0.1:8085"
