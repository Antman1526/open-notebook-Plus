"""Unit tests for Apple Silicon FlashAttention and KV quantization in LlamaCppProvider."""

from pathlib import Path
import pytest
from desktop.providers.llamacpp import LlamaCppProvider


def test_build_server_argv_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN", raising=False)
    monkeypatch.delenv("DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT", raising=False)

    provider = LlamaCppProvider(model_dir=tmp_path)
    model_path = tmp_path / "model.gguf"
    argv = provider.build_server_argv(model_path, port=8000)

    assert "--flash_attn" in argv
    assert argv[argv.index("--flash_attn") + 1] == "true"
    assert "--type_k" in argv
    assert argv[argv.index("--type_k") + 1] == "q8_0"
    assert "--type_v" in argv
    assert argv[argv.index("--type_v") + 1] == "q8_0"


def test_build_server_argv_custom_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN", "false")
    monkeypatch.setenv("DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT", "q4_0")

    provider = LlamaCppProvider(model_dir=tmp_path)
    model_path = tmp_path / "model.gguf"
    argv = provider.build_server_argv(model_path, port=8000)

    assert "--flash_attn" not in argv
    assert "--type_k" in argv
    assert argv[argv.index("--type_k") + 1] == "q4_0"
    assert "--type_v" in argv
    assert argv[argv.index("--type_v") + 1] == "q4_0"


def test_build_server_argv_disabled_kv_quant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT", "none")

    provider = LlamaCppProvider(model_dir=tmp_path)
    model_path = tmp_path / "model.gguf"
    argv = provider.build_server_argv(model_path, port=8000)

    assert "--type_k" not in argv
    assert "--type_v" not in argv
