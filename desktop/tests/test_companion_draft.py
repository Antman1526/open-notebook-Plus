"""Unit tests for companion draft model auto-discovery in LlamaCppProvider."""

from pathlib import Path
from desktop.providers.llamacpp import find_companion_draft_model, MIN_GGUF_BYTES


def test_find_companion_draft_model_pairs_correctly(tmp_path: Path):
    target = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    target.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    companion = tmp_path / "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf"
    companion.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    # Should pair Qwen 7B with Qwen 1.5B
    found = find_companion_draft_model(target)
    assert found is not None
    assert found.name == "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf"


def test_find_companion_draft_model_pairs_llama(tmp_path: Path):
    target = tmp_path / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    target.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    companion = tmp_path / "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    companion.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    found = find_companion_draft_model(target)
    assert found is not None
    assert found.name == "Llama-3.2-1B-Instruct-Q4_K_M.gguf"


def test_find_companion_draft_model_skips_small_or_mismatched(tmp_path: Path):
    target = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    target.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    # Mismatched family
    llama_draft = tmp_path / "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    llama_draft.write_bytes(b"x" * (MIN_GGUF_BYTES + 10))

    found = find_companion_draft_model(target)
    assert found is None
