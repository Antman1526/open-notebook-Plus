"""llama.cpp provider: scan a directory for GGUFs, spawn llama-cpp-python server."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

from desktop.data_root import active_data_root
from desktop.ports import find_free_port
from desktop.providers import ProviderEnv

# Files smaller than this are treated as Git LFS pointers / aborted downloads
# and skipped during model listing.
MIN_GGUF_BYTES = 1 * 1024 * 1024

# v0.7.151 — Number of stderr lines to include in the RuntimeError message
# when llama_cpp.server exits prematurely or never becomes ready. Enough
# context to identify the actual failure (model architecture unsupported,
# OOM, missing dependency) without bloating the exception message.
_STDERR_TAIL_LINES = 30


def _http_ready(port: int) -> bool:
    try:
        return (
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=0.5).status_code
            == 200
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return False


def _default_log_dir() -> Path:
    """Where to write llama_cpp.server stderr if no override is supplied.
    Matches desktop/app.py's `log_dir = ~/.deeper-notebook/logs`."""
    return active_data_root() / "logs"


def _tail_lines(path: Path, n: int) -> str:
    """Read at most the last `n` lines of `path` for inclusion in error
    messages. Returns the empty string if the file is missing or unreadable
    (the caller will fall back to a generic message). Never raises."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    tail = lines[-n:]
    return "".join(tail).rstrip()


def find_companion_draft_model(target_model_path: Path) -> Path | None:
    """Auto-detect a compatible draft model in the same folder for speculative decoding.

    Pairs larger models (e.g. 7B, 8B, 9B, 3B) with smaller 1B/1.5B draft models
    from the same family (e.g. Llama-3.2-1B with Llama-3.2-3B, or Qwen2.5-1.5B with 7B).
    """
    if not target_model_path.is_file():
        return None
    target_name = target_model_path.name.lower()
    has_large = any(tag in target_name for tag in ("7b", "8b", "9b", "3b", "14b"))
    if not has_large:
        return None
    parent = target_model_path.parent
    if not parent.is_dir():
        return None
    for cand in sorted(parent.glob("*.gguf")):
        if cand.resolve() == target_model_path.resolve():
            continue
        cand_name = cand.name.lower()
        if "1b" in cand_name or "1.5b" in cand_name:
            try:
                if cand.stat().st_size >= MIN_GGUF_BYTES:
                    if ("llama" in target_name and "llama" in cand_name) or (
                        "qwen" in target_name and "qwen" in cand_name
                    ) or ("deepseek" in target_name and "deepseek" in cand_name):
                        return cand
            except OSError:
                continue
    return None


class LlamaCppProvider:
    name: str = "llamacpp"

    def __init__(
        self,
        model_dir: Path,
        ready_probe: Callable[[int], bool] = _http_ready,
        max_wait: float = 60.0,
        python_executable: str | Path | None = None,
        log_dir: Path | None = None,
        draft_model_path: Path | None = None,
        draft_n_predict: int | None = None,
    ) -> None:
        self.model_dir = model_dir
        self._ready_probe = ready_probe
        self._max_wait = max_wait
        # python_executable: interpreter used to spawn llama_cpp.server.
        # Defaults to sys.executable (unfrozen/dev); pass the venv python when
        # running inside the frozen .app so llama_cpp is importable.
        self._python_executable: str | Path = (
            sys.executable if python_executable is None else python_executable
        )
        # v0.8.2 Item A — optional path to a smaller "draft" GGUF for
        # llama.cpp speculative decoding. When set, --model_draft <path>
        # is appended to the spawned argv and llama_cpp.server uses the
        # draft model to propose tokens that the target model verifies
        # in parallel — typical 1.5–2x decode speedup with no quality
        # loss when the draft and target share a tokenizer family
        # (e.g. Llama-3.2-1B drafting for Hermes-3-Llama-3.1-8B). Wired
        # from DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH in desktop/app.py
        # _phase_select_provider. Default None = current behavior
        # unchanged; the flag is only added to argv when this is set.
        self._draft_model_path: Path | None = draft_model_path
        # v0.8.2 Item C — operator-tunable draft token count per
        # verification pass. llama_cpp.server default is 8; raising it
        # speeds throughput when the draft model agrees often with the
        # target (similar-architecture pairs), but wastes work on
        # disagreement-heavy pairs (different tokenizer families).
        # Meaningless when draft_model_path is unset; the flag is only
        # emitted to argv when BOTH knobs are configured.
        self._draft_n_predict: int | None = draft_n_predict
        # v0.7.151 — Where to write llama_cpp.server stderr. Until this
        # release stderr was DEVNULL'd, so when the server exited with
        # returncode=1 (e.g. unsupported model architecture, OOM, missing
        # CUDA toolkit) the user got "exited prematurely (returncode=1)"
        # with zero diagnostic context. Now we open a per-instance logfile
        # and surface its tail in the RuntimeError message.
        self._log_dir: Path = log_dir or _default_log_dir()
        self._stderr_log: Path | None = None
        self._stderr_fh = None  # type: ignore[var-annotated]
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None

    def is_available(self) -> bool:
        return any(True for _ in self._iter_ggufs())

    def list_models(self) -> list[str]:
        return sorted(
            p.relative_to(self.model_dir).as_posix() for p in self._iter_ggufs()
        )

    def start(self, model: str) -> ProviderEnv:
        path = self.model_dir / model
        if not path.exists() or path.stat().st_size < MIN_GGUF_BYTES:
            raise FileNotFoundError(f"GGUF not found or too small: {path}")
        if self._proc is not None:
            self.stop()

        port = find_free_port()

        # v0.7.151 — Open a per-launch stderr logfile so the inevitable
        # llama_cpp.server crash on an unsupported quant / arch is
        # diagnosable from the launcher.log instead of completely silent.
        # The file path is included in the RuntimeError message so the
        # user can `tail -F` it.
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._stderr_log = self._log_dir / "llamacpp_chat_stderr.log"
            # Append (not overwrite) so a crash followed by a manual retry
            # still has the original failure context. The user can rotate
            # if it gets large; this is a diagnostic file, not a hot loop.
            self._stderr_fh = self._stderr_log.open("ab", buffering=0)
        except OSError:
            # If we can't open the logfile (read-only fs, permission denied),
            # fall back to DEVNULL — the previous behavior. Better than
            # crashing before even attempting to spawn the model server.
            self._stderr_log = None
            self._stderr_fh = subprocess.DEVNULL

        # Base argv. v0.8.2 Item A — append --model_draft only when
        # the operator has set DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH;
        # missing draft path or unset env keeps current behavior.
        argv = [
            str(self._python_executable),
            "-m",
            "llama_cpp.server",
            "--model",
            str(path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        draft_candidate = self._draft_model_path if self._draft_model_path is not None else find_companion_draft_model(path)
        if draft_candidate is not None:
            # Skip silently if the configured path no longer exists or
            # is too small to be a real GGUF — spec'd as non-fatal so a
            # stale env var doesn't take the whole sidecar down. The
            # main model still loads; user just doesn't get the speedup.
            if (
                draft_candidate.is_file()
                and draft_candidate.stat().st_size >= MIN_GGUF_BYTES
            ):
                argv.extend(["--model_draft", str(draft_candidate)])
                # v0.8.2 Item C — also pass --n_predict_draft if the
                # operator tuned it; otherwise llama_cpp.server uses
                # its built-in default (currently 8 tokens / verify).
                # Only emit when the draft itself was accepted above
                # so a stray env var without a draft model can't
                # generate a malformed argv that llama_cpp.server
                # rejects at parse time.
                if self._draft_n_predict is not None and self._draft_n_predict > 0:
                    argv.extend(
                        [
                            "--n_predict_draft",
                            str(self._draft_n_predict),
                        ]
                    )
        self._proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_fh,
        )
        self._port = port

        deadline = time.monotonic() + self._max_wait
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                # v0.7.151 — surface the captured stderr tail so the user
                # can actually diagnose the crash without grepping logs.
                self._close_stderr()
                tail = (
                    _tail_lines(self._stderr_log, _STDERR_TAIL_LINES)
                    if self._stderr_log
                    else ""
                )
                msg = (
                    f"llama_cpp.server exited prematurely "
                    f"(returncode={self._proc.returncode}) "
                    f"while loading model {model!r}"
                )
                if tail:
                    msg += (
                        f". Last {_STDERR_TAIL_LINES} lines of stderr "
                        f"(full log at {self._stderr_log}):\n{tail}"
                    )
                elif self._stderr_log:
                    msg += (
                        f". Empty stderr at {self._stderr_log} — "
                        f"the server died before writing any diagnostics "
                        f"(possible: missing llama_cpp install, exec policy)"
                    )
                else:
                    msg += (
                        ". Stderr capture unavailable "
                        "(logfile could not be opened — read-only fs?)"
                    )
                raise RuntimeError(msg)
            if self._ready_probe(port):
                # Upstream uses esperanto's openai_compatible provider; these
                # are the env var names esperanto's OpenAICompatibleLanguageModel
                # actually reads. (`OPENAI_API_BASE` is NOT read by upstream.)
                return ProviderEnv(
                    OPENAI_COMPATIBLE_BASE_URL=f"http://127.0.0.1:{port}/v1",
                    OPENAI_COMPATIBLE_API_KEY="sk-no-key",
                )
            time.sleep(0.5)

        # v0.7.151 — Timeout path: process is still alive but never bound
        # the port. Include stderr tail too, since the model may be
        # silently hung (mmap stuck, infinite tokenizer load, …).
        self.stop()
        tail = (
            _tail_lines(self._stderr_log, _STDERR_TAIL_LINES)
            if self._stderr_log
            else ""
        )
        msg = (
            f"llama_cpp.server on port {port} never became ready "
            f"within {self._max_wait}s (model={model!r})"
        )
        if tail:
            msg += f". Last stderr (full log at {self._stderr_log}):\n{tail}"
        raise RuntimeError(msg)

    def _close_stderr(self) -> None:
        """v0.7.151 — flush and close the stderr handle if it's a file we
        opened. Idempotent; safe to call from stop() and from the exit-path
        RuntimeError construction."""
        fh = self._stderr_fh
        if fh is None:
            return
        if fh is subprocess.DEVNULL:
            self._stderr_fh = None
            return
        try:
            fh.close()
        except Exception:
            pass
        self._stderr_fh = None

    def stop(self) -> None:
        if self._proc is None:
            self._close_stderr()  # v0.7.151 — idempotent cleanup
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        self._port = None
        self._close_stderr()  # v0.7.151 — flush + release the logfile handle

    def pick_default_model(self) -> str:
        """Choose a sensible default GGUF from the user's model dir.

        Preference order (first match wins):
          1. Hermes-3 family (best chat-tuned model in the user's typical set)
          2. Mistral-7B-Instruct (reliable baseline)
          3. Qwen2.5-7B-Instruct
          4. llama-3.2-3b (small but capable)
          5. phi-3.5-mini
          6. Any GGUF at all (first in list_models() sorted order)
        Returns "" if no usable GGUF exists.
        """
        try:
            models = self.list_models()
        except Exception:
            return ""
        if not models:
            return ""
        by_name = {m.lower(): m for m in models}
        for hint in (
            "hermes-3",
            "mistral-7b-instruct",
            "qwen2.5-7b-instruct",
            "llama-3.2-3b",
            "phi-3.5-mini",
        ):
            for k, original in by_name.items():
                if hint in k:
                    return original
        # Fallback: first model in sorted list (list_models already filters <1 MB files).
        return models[0]

    def _iter_ggufs(self):
        if not self.model_dir.exists():
            return
        for p in self.model_dir.rglob("*.gguf"):
            if p.is_file() and p.stat().st_size >= MIN_GGUF_BYTES:
                yield p
