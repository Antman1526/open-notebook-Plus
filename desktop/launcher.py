"""Process supervisor for the desktop app.

Starts SurrealDB, FastAPI (uvicorn), the open-notebook worker, and the Next.js
frontend in dependency order. Each child gets the per-session env (DB creds,
ports, model provider). Window code (window.py) opens once frontend_url returns
HTTP 200.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable

import httpx

from deeper_notebook.environment import normalize_product_environment, resolve_env
from desktop.data_root import active_data_root

if TYPE_CHECKING:
    from desktop.progress import ProgressBus

from desktop import launcher_prefs  # v0.8.6 — file-backed preference layer
from desktop.config import Config
from desktop.paths import user_home
from desktop.ports import find_free_ports

# v0.6.5 — debugging supervised-child failures was painful: every optional
# service had `except Exception: pass`, so a misconfigured Piper voice path
# (or a missing whisper binary, or an OOM-killed llama.cpp) produced only
# "supervisor.piper: error" in the UI with zero log trail. Now we log the
# exception at warning level AND surface the message through the progress
# bus so the wizard's status overlay can show it.
log = logging.getLogger(__name__)


class ResourceGovernor:
    """Bounded, in-memory resource ledger for local sidecars.

    It records reservations only; it never probes or modifies a model library.
    """

    def __init__(self, memory_limit_bytes: int | None = None) -> None:
        self.memory_limit_bytes = memory_limit_bytes
        self._reservations: dict[str, int] = {}
        self._heavyweight_mlx: str | None = None
        self._queued_heavyweight_swaps: list[str] = []

    def reserve(
        self, name: str, bytes_needed: int, *, heavyweight_mlx: bool = False
    ) -> str:
        required = max(0, int(bytes_needed))
        if heavyweight_mlx and self._heavyweight_mlx not in {None, name}:
            if name not in self._queued_heavyweight_swaps:
                self._queued_heavyweight_swaps.append(name)
            return "queued"
        if (
            self.memory_limit_bytes is not None
            and sum(self._reservations.values()) + required > self.memory_limit_bytes
        ):
            return "blocked"
        self._reservations[name] = required
        if heavyweight_mlx:
            self._heavyweight_mlx = name
        return "reserved"

    def release(self, name: str) -> None:
        self._reservations.pop(name, None)
        if self._heavyweight_mlx == name:
            self._heavyweight_mlx = None

    def start_provider(
        self,
        name: str,
        *,
        reservation_bytes: int,
        spawn,
        health_check,
        heavyweight_mlx: bool = False,
    ) -> bool:
        if (
            self.reserve(name, reservation_bytes, heavyweight_mlx=heavyweight_mlx)
            != "reserved"
        ):
            return False
        proc = spawn()
        if health_check(proc):
            return True
        try:
            proc.terminate()
        finally:
            self.release(name)
        return False

    def snapshot(self) -> dict[str, object]:
        reserved = sum(self._reservations.values())
        return {
            "memory_limit_bytes": self.memory_limit_bytes,
            "reserved_bytes": reserved,
            "memory_pressure": "limited"
            if self.memory_limit_bytes is not None
            and reserved >= self.memory_limit_bytes
            else "normal",
            "reservations": dict(self._reservations),
            "queued_heavyweight_swaps": list(self._queued_heavyweight_swaps),
        }


def _n_gpu_layers(env_key: str, *, mac_default: int = -1) -> str:
    """v0.8.67c — resolve llama.cpp `--n_gpu_layers` for a sidecar.

    CRITICAL FIX. The chat/embed sidecars spawned `llama_cpp.server` with NO
    `--n_gpu_layers`, so llama-cpp-python defaulted to 0 → the ENTIRE model ran
    on CPU. On Apple Silicon that made an 8B chat model so slow it never returned
    a completion within the chat timeout — the chatbot was silent for this reason
    even once the backend was healthy. Measured on an M1 Max: 0/33 layers (CPU) →
    no response in 90 s; -1 → 33/33 on Metal → 1.7 s.

    Default: macOS ships a Metal build of llama-cpp-python and uses unified
    memory, so full offload (-1 = all layers) is free and almost always correct
    → mac_default=-1. Other OSes default to CPU (0) so a CUDA build with limited
    VRAM can't OOM the box; those users opt in via `env_key`. Any value is
    overridable per sidecar via the env var without a rebuild."""
    raw = (resolve_env(env_key) or "").strip()
    if raw:
        try:
            return str(int(raw))
        except ValueError:
            pass
    return str(mac_default) if sys.platform == "darwin" else "0"


def _wait_tcp(
    host: str,
    port: int,
    timeout: float = 30.0,
    proc: "subprocess.Popen | None" = None,
) -> None:
    """v0.7.188 — Added optional `proc` arg for early-exit on dead child.
    Pre-fix, if the spawned process crashed at startup (binary missing,
    port collision after our SO_REUSEADDR test), `_wait_tcp` waited the
    full `timeout` seconds before raising. The user stared at "Starting
    SurrealDB…" for up to 30s when the failure was visible in
    `proc.returncode` within 100ms.

    With `proc` passed in, we poll() between probes — a non-None
    returncode means the child exited and there is NO chance the port
    will ever come up. Raise immediately with the child's exit code
    so the launcher can surface a useful error."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"child for {host}:{port} exited rc={proc.returncode} "
                f"before the port came up — check the per-child log "
                f"in the debug-mode logs dir"
            )
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"tcp {host}:{port} never came up within {timeout}s")


def _wait_http(
    url: str,
    timeout: float = 60.0,
    proc: "subprocess.Popen | None" = None,
    consecutive: int = 1,
    follow_redirects: bool = False,
) -> None:
    """v0.7.188 — Same early-exit-on-dead-child treatment as _wait_tcp.
    Without it, `_wait_http("/readyz", timeout=180)` would wait 3
    minutes on a uvicorn binary that crashed in 200ms.

    v0.8.68 — `consecutive` + `follow_redirects` for the frontend gate:
    a single lucky probe against a just-bound Next.js socket let the main
    window open while the server could still drop the webview's one-shot
    navigation ("This page couldn't load" at launch). Requiring N
    successes in a row against the FINAL page (the bare "/" is a 307 to
    the wizard/login) makes the gate match what the webview actually
    requests."""
    deadline = time.monotonic() + timeout
    streak = 0
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"child for {url} exited rc={proc.returncode} before "
                f"the endpoint became reachable — check the per-child "
                f"log in the debug-mode logs dir"
            )
        try:
            r = httpx.get(url, timeout=2.0, follow_redirects=follow_redirects)
            if r.status_code < 500:
                streak += 1
                if streak >= max(1, consecutive):
                    return
                time.sleep(0.2)
                continue
            streak = 0
        except (httpx.RequestError, httpx.TimeoutException):
            streak = 0
        time.sleep(0.3)
    raise TimeoutError(f"http {url} never returned <500 within {timeout}s")


def _startup_timeout(env_key: str, default: float) -> float:
    """v0.8.67b — env-tunable startup readiness timeout (core-service defaults
    raised from the historical 30 s).

    ROOT CAUSE this addresses: the first launch after an app update re-extracts
    the Python runtime and rebuilds the venv; that disk I/O + a cold page cache
    can delay SurrealDB's port bind past the old 30 s `_wait_tcp` gate. Because
    SurrealDB is a *core* service, that gate raised TimeoutError and aborted the
    ENTIRE supervisor (EARLY-INIT FAILURE) — no API, no sidecars, a dead app and
    an empty chatbot.

    Raising the ceiling is safe: `_wait_tcp`/`_wait_http` already early-exit via
    `proc.poll()` the instant a child actually dies, so a larger timeout only
    ever costs wall-clock on a slow-but-successful start (post-update I/O, or a
    cold mmap of a large GGUF) — never on a real crash. Operators can override
    per service via env without a rebuild; a non-positive or unparseable value
    falls back to `default`."""
    raw = (resolve_env(env_key) or "").strip()
    if raw:
        try:
            val = float(raw)
        except ValueError:
            return default
        if val > 0:
            return val
    return default


class Supervisor:
    def __init__(
        self,
        cfg: Config,
        repo_root: Path,
        bin_dir: Path,
        surreal_arch: str,
        node_arch: str,
        extra_env: dict[str, str] | None = None,
        debug_mode: bool = False,
        log_dir: Path | None = None,
        venv_python: Path | None = None,
        upstream_root: Path | None = None,
        whisper_model_path: Path | None = None,
        piper_voices: dict[str, Path] | None = None,
        nomic_embed_path: Path | None = None,
        chat_llm_path: Path | None = None,
        openchronicle_available: bool = False,
        progress: "ProgressBus | None" = None,
        stage_recorder: "Callable[[str, int], None] | None" = None,
    ) -> None:
        self.cfg = cfg
        # v0.8.99 — optional startup-stage sink. `core_ready` was a single
        # opaque bucket covering all of start_all(); a slow launch reported one
        # number (114s on a fresh install here) with no way to tell whether the
        # cost was the database, the API, the Next server, or a model mmap.
        # Nothing can be optimised that cannot be measured, so record a
        # milestone as each dependency comes up. Purely observational: a
        # recorder that raises must never affect the launch.
        self._stage_recorder = stage_recorder
        self._start_all_began_at: float | None = None
        self.resource_governor = ResourceGovernor(cfg.local_model_memory_limit_bytes)
        self.repo_root = repo_root
        self.bin_dir = bin_dir
        self.surreal_arch = surreal_arch
        self.node_arch = node_arch
        self.extra_env = dict(extra_env or {})
        self.debug_mode = debug_mode
        self.log_dir = log_dir or (active_data_root() / "logs")
        # venv_python: the Python interpreter used to spawn FastAPI/worker children.
        # When None, falls back to sys.executable (unfrozen/dev path).
        self.venv_python: Path = venv_python or Path(sys.executable)
        # upstream_root: cwd for the API + worker subprocesses. It must contain
        # api/, commands/, the canonical deeper_notebook package, and the
        # open_notebook compatibility shim. In the frozen .app, these live at
        # MEIPASS/upstream/;
        # the frontend lives at MEIPASS/frontend/. They're not the same dir.
        # In unfrozen/dev mode, upstream_root defaults to repo_root (they coincide).
        self.upstream_root: Path = upstream_root or repo_root
        # whisper_model_path may be a Path to a legacy ggml .bin file (kept for
        # type compatibility) OR a Path whose str() is a faster-whisper model
        # name like "base.en".  _spawn_whisper no longer checks .exists() so
        # that model-name strings (which are not real filesystem paths) work.
        self.whisper_model_path = whisper_model_path
        self.piper_voices = piper_voices or {}
        self.nomic_embed_path = nomic_embed_path
        self.progress = progress
        self._procs: list[subprocess.Popen] = []
        self._log_files: list[IO[bytes]] = []
        # v0.7.58 — track drainer threads so stop_all can join them
        # BEFORE closing the log files they're writing into. Without
        # the join, daemon=True meant the OS reaped them at process
        # exit without waiting — but if any line was mid-write at the
        # moment we closed the log file, that buffered tail (often the
        # crash cause) was lost or corrupted. A 1-2s join window is
        # plenty given the drain loop is just iter(readline).
        self._drain_threads: list[threading.Thread] = []
        self.session_env: dict[str, str] = {}
        # v0.8.40 — per-kind Popen tracker for the launcher control plane.
        # Restart needs to find the *specific* sidecar Popen (not the
        # whole _procs list which also contains surreal, api, frontend,
        # etc). _spawn_llamacpp_chat / _spawn_llamacpp_embed /
        # _spawn_whisper / _spawn_piper / _spawn_memory_retriever each
        # populate this on successful spawn.
        self._sidecar_procs: dict[str, subprocess.Popen] = {}
        # v0.8.40 — also remember the (port, name) so restart can pass
        # the same args as the original spawn. Filled in alongside
        # _sidecar_procs at each successful spawn.
        self._sidecar_spawn_args: dict[str, tuple[int, str]] = {}
        # v0.8.40 — the launcher's control plane HTTP server.
        # Lazily started in start_all; cleanly shut down in stop_all.
        self._control_server: "object | None" = None
        self.frontend_url: str = ""
        self.embed_port: int = 0
        self.whisper_port: int = 0
        self.piper_port: int = 0
        self.chat_llm_path = chat_llm_path
        self.openchronicle_available = openchronicle_available
        # New v0.4 ports — initialised to 0 so auto_register can skip cleanly
        # when a server failed to start.
        self.chat_llm_port: int = 0
        self.memory_port: int = 0
        self.openchronicle_port: int = 0
        # v0.8.7 — resolved chat-LLM n_ctx, computed once at start_all
        # time so BOTH session_env (for the router's
        # DEEPER_NOTEBOOK_LOCAL_N_CTX) and _spawn_llamacpp_chat
        # (--n_ctx argv) read from the same value. Pre-v0.8.7 the
        # resolution lived inside _spawn_llamacpp_chat — too late to
        # propagate into session_env, which is built earlier — so the
        # router defaulted to 32768 even when the GGUF auto-detect
        # would have picked a higher native context (e.g. Hermes-3
        # 131k). Operators with high-capacity GGUFs were under-routing
        # to cloud. 0 = unresolved / no chat sidecar.
        self.chat_llm_n_ctx: int = 0

    def _next_frontend_dir(self) -> Path:
        """Return the directory containing the Next standalone server.

        Packaged applications ship ``frontend/server.js`` directly.  A source
        checkout keeps the server under the output tracing root recorded in
        ``frontend/.next/required-server-files.json``.  That can be a nested
        path such as ``frontend/.next/standalone/frontend/server.js`` when the
        worktree is below the configured tracing root.  Resolve both layouts
        so the launcher never falls back to a directory without ``server.js``.
        """
        frontend_dir = self.repo_root / "frontend"
        if (frontend_dir / "server.js").is_file():
            return frontend_dir

        standalone_root = frontend_dir / ".next" / "standalone"
        candidates = [standalone_root]
        metadata_path = frontend_dir / ".next" / "required-server-files.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            app_dir = Path(metadata["appDir"]).resolve()
            tracing_root = Path(metadata["config"]["outputFileTracingRoot"]).resolve()
            relative_app = app_dir.relative_to(tracing_root)
            if relative_app != Path("."):
                candidates.insert(0, standalone_root / relative_app)
        except (KeyError, OSError, TypeError, ValueError):
            pass

        for candidate in candidates:
            if (candidate / "server.js").is_file():
                self._ensure_source_standalone_assets(frontend_dir, candidate)
                return candidate
        return frontend_dir

    @staticmethod
    def _ensure_source_standalone_assets(frontend_dir: Path, server_dir: Path) -> None:
        """Make source-build static/public assets visible beside server.js."""
        for source, destination in (
            (frontend_dir / ".next" / "static", server_dir / ".next" / "static"),
            (frontend_dir / "public", server_dir / "public"),
        ):
            if not source.exists() or destination.exists():
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(source, destination, target_is_directory=True)
            except OSError:
                shutil.copytree(source, destination)

    def _record_stage(self, stage: str) -> None:
        """Best-effort startup milestone, measured from start_all()'s entry."""
        if self._stage_recorder is None or self._start_all_began_at is None:
            return
        try:
            elapsed_ms = int((time.monotonic() - self._start_all_began_at) * 1000)
            self._stage_recorder(stage, elapsed_ms)
        except Exception:
            # Instrumentation must never be able to fail a launch.
            pass

    def _record_sidecar_stage(self, step: str, started_at: float) -> None:
        """Record one sidecar's own elapsed time (not cumulative).

        v0.8.100 — sidecars spawn sequentially inside one `sidecars_up` span,
        so a cumulative number would just restate that span. Per-sidecar
        duration is what identifies the slow one.
        """
        if self._stage_recorder is None:
            return
        try:
            name = step.split(".", 1)[-1] or step
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            self._stage_recorder(f"sidecar_{name}", elapsed_ms)
        except Exception:
            pass

    def start_all(self) -> None:
        # v0.7.142 — Singleton enforcement + orphan reaper.
        # Before this release, double-clicking the .app twice spawned two
        # complete process trees with independent dynamic ports. The user
        # would end up with multiple "Unable to Connect" browser windows,
        # each attached to a zombie launcher whose API had since been
        # overwritten. See desktop/singleton.py docstring for the full
        # incident.
        #
        # Now: acquire a PID-file lock at start. If another live instance
        # holds it, AlreadyRunning propagates up to the app's UI which
        # can show a friendly "Deeper Notebook is already running"
        # dialog. Then sweep any orphans from prior crashed launchers
        # before we bind our own ports.
        # v0.8.6 — Merge launcher.env file values into os.environ BEFORE
        # session_env is built below. Keys already in os.environ (shell
        # export / CI override) are NOT overwritten — env wins. This
        # single call makes every subsequent reader in this method
        # (_spawn_llamacpp_chat, _local_chat_healthy_cached, etc.) see
        # the file-backed values transparently without special-casing.
        self._start_all_began_at = time.monotonic()
        launcher_prefs.merge_with_env(os.environ)
        launcher_environment = normalize_product_environment(os.environ)

        from desktop.singleton import (
            acquire_singleton,
            default_pid_file,
            reap_orphans,
        )

        self._singleton = acquire_singleton(
            default_pid_file(),
            on_signal_cleanup=lambda _signum: self.stop_all(),
        )
        # Best-effort orphan reap. The bundle paths cover the two places
        # our subprocess children live: the user-data venv (Python API +
        # worker) and the bundled binary dir (Node, surreal, llama-cpp).
        bundle_paths = [
            active_data_root() / "venv",
            self.bin_dir,
        ]
        try:
            orphans = reap_orphans(bundle_paths=bundle_paths)
            if orphans:
                log.warning(
                    "Reaped %d orphaned process(es) from prior launch",
                    len(orphans),
                )
                # Give the OS a moment to actually free the ports they
                # were holding so find_free_ports below doesn't race
                # against zombies clinging to them.
                time.sleep(0.5)
        except Exception as exc:
            # Reap is best-effort — never let a scan failure block boot.
            log.debug("Orphan reap failed (non-fatal): %s", exc)

        (
            surreal_port,
            api_port,
            frontend_port,
            embed_port,
            whisper_port,
            piper_port,
            chat_llm_port,
            memory_port,
            openchronicle_port,
        ) = find_free_ports(9)

        api_url = f"http://127.0.0.1:{api_port}"
        # v0.7.147 — Pin DATA_FOLDER to a per-user, ALWAYS-writable absolute
        # path. deeper_notebook/config.py used to hardcode "./data" (CWD-
        # relative) and the API subprocess inherits cwd=upstream_root, which
        # is read-only when the .app is launched from a mounted DMG. The
        # resulting EROFS at module import crashed uvicorn before /readyz
        # ever came up, the launcher waited 180s, then exited silently.
        # Injecting an absolute path here makes the launch path resilient
        # to ANY read-only CWD (DMG, Time Machine snapshot, /Applications
        # under a non-admin user, …) without affecting Docker / dev where
        # the env var would simply not be set otherwise.
        data_folder = active_data_root() / "data"
        data_folder.mkdir(parents=True, exist_ok=True)
        # v0.8.7 — Resolve chat-LLM n_ctx HERE, before session_env is
        # built, so DEEPER_NOTEBOOK_LOCAL_N_CTX can carry the actual
        # ceiling the sidecar will use (env-override, GGUF-autodetect,
        # or capped fallback). The router (provision.py) reads that
        # env at chat-turn time; previously it always defaulted to
        # 32768 because _spawn_llamacpp_chat resolved n_ctx later.
        self.chat_llm_n_ctx = self._resolve_chat_llm_n_ctx()
        # v0.8.40 — Bring up the launcher control plane BEFORE session_env
        # is built so the URL + token can be exported to the API
        # subprocess. The API uses these to POST sidecar-restart and
        # (future) hot-swap commands; without them, the v0.8.38b
        # restart endpoint has no way to call back into the launcher.
        try:
            from desktop.launcher_control import ControlServer

            self._control_server = ControlServer()
            self._control_server.register_callback(
                "restart_sidecar", self.restart_sidecar
            )
            # v0.8.40b — chat-GGUF hot-swap callback. Receives the
            # new absolute path; updates chat_llm_path + restarts the
            # chat sidecar without quitting the app.
            self._control_server.register_callback("hot_swap_chat", self.hot_swap_chat)
            self._control_server.start()
            control_url = getattr(self._control_server, "url", "")
            control_token = getattr(self._control_server, "token", "")
        except Exception as exc:
            # Control server is best-effort — the launcher must come
            # up even if the control plane can't bind. Without it, the
            # API's restart endpoint will return 503 with a clear
            # message, but the rest of the app keeps working.
            log.warning("launcher control server failed to start: %s", exc)
            control_url = ""
            control_token = ""
        self.session_env = normalize_product_environment(
            {
                **launcher_environment,
                **self.extra_env,
                "DATA_FOLDER": str(data_folder),
                # The packaged API/worker import source files from the signed
                # Resources/upstream tree. Rewriting their adjacent .pyc files at
                # runtime invalidates the macOS bundle seal after first launch.
                "PYTHONDONTWRITEBYTECODE": "1",
                # v0.8.40 — expose control plane to the API subprocess.
                # Empty string when the control server failed to start.
                "DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL": control_url,
                "DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN": control_token,
                "SURREAL_URL": f"ws://127.0.0.1:{surreal_port}/rpc",
                "SURREAL_USER": self.cfg.surreal_user,
                "SURREAL_PASSWORD": self.cfg.surreal_password,
                "SURREAL_NAMESPACE": "open_notebook",
                "SURREAL_DATABASE": "open_notebook",
                "API_PORT": str(api_port),
                # v0.7.205 — DO NOT put `PORT` in the shared session_env.
                # Background: `PORT=<frontend_port>` was set here so the
                # Next.js child would honour the dynamic port. But
                # `session_env` is the env every child inherits via
                # `_spawn(env=self.session_env)`. uvicorn's pydantic_settings
                # in `llama_cpp.server` (and any other server we spawn
                # that uses uvicorn's env-driven config) reads `PORT` from
                # env and treats it as authoritative, OVERRIDING any
                # `--port <X>` CLI arg.
                #
                # Symptom: a fresh launch allocated api_port=60432,
                # frontend_port=60433, embed_port=60434. The embed server
                # was spawned with `--port 60434` but uvicorn picked up
                # `PORT=60433` from inherited env and bound 60433 instead.
                # macOS then routed `127.0.0.1:60433` to the more-specific
                # Python listener (embed server) instead of node's
                # `*:60433` (Next.js) — so the webview opened the
                # frontend URL and got `{"detail":"Not Found"}` from
                # llama_cpp.server's FastAPI root, not the actual
                # Next.js UI.
                #
                # PORT is now passed ONLY to the Next.js spawn via a
                # per-child env override in `_spawn_next` below.
                # Upstream Next.js reads these (see frontend/next.config.ts and
                # frontend/src/app/config/route.ts):
                # - API_URL: where the browser makes direct API calls
                # - INTERNAL_API_URL: where the Next.js server-side proxy forwards
                # - NEXT_PUBLIC_API_URL: client-bundle fallback
                # All three point at our dynamic uvicorn port.
                "API_URL": api_url,
                "INTERNAL_API_URL": api_url,
                "NEXT_PUBLIC_API_URL": api_url,
                "NEXT_PUBLIC_API_BASE": api_url,  # legacy, kept for safety
                "DEEPER_NOTEBOOK_ENCRYPTION_KEY": self.cfg.encryption_key,
                "DEEPER_NOTEBOOK_MODEL_DIR": str(self.cfg.model_dir),
                "DEEPER_NOTEBOOK_EXECUTION_POLICY": self.cfg.execution_policy,
                "DEEPER_NOTEBOOK_COMPUTE_PROFILE": self.cfg.compute_profile,
                "DEEPER_NOTEBOOK_LOCAL_MODEL_MEMORY_LIMIT_BYTES": str(
                    self.cfg.local_model_memory_limit_bytes or 0
                ),
                # v0.4 memory layer: predeclare URLs so the surreal-commands worker
                # (spawned before these servers actually bind) sees them in its env.
                # The real servers come up later in start_all; worker connects
                # lazily on first command invocation.
                "MEMORY_CHAT_LLM_URL": f"http://127.0.0.1:{chat_llm_port}/v1",
                "MEMORY_EMBED_URL": f"http://127.0.0.1:{embed_port}/v1",
                "MEMORY_SURREAL_URL": f"ws://127.0.0.1:{surreal_port}/rpc",
                # v0.8.4 — CRITICAL fix: the v0.8.0 Phase 3 smart router
                # in deeper_notebook/ai/provision.py reads this env var to
                # know where the local llama.cpp chat sidecar lives so it
                # can probe `/v1/models` for health. Without it set,
                # `_local_chat_healthy_cached()` returns False every call,
                # so `pick_provider(local_chat_healthy=False)` always took
                # the cloud branch — i.e. v0.8.0 smart routing's "prefer
                # local when healthy" code path was effectively dead in
                # production. The launcher's chat_llm_port matches what
                # auto_register registers as the Local-GGUF credential
                # (since v0.7.193), so threading the same value through
                # here gives provision.py the URL it expected the whole
                # time.
                "DEEPER_NOTEBOOK_LOCAL_CHAT_BASE_URL": (
                    f"http://127.0.0.1:{chat_llm_port}/v1"
                ),
                # v0.8.7 — Export the same n_ctx value the sidecar will
                # use, so the router's pick_provider() math matches reality.
                # Pre-v0.8.7 the router defaulted to 32768 regardless of
                # what the launcher had auto-detected (e.g. Hermes-3
                # native 131k → sidecar bound at 131k, but router only
                # gave it ~31k of headroom before flipping to cloud).
                # An explicit DEEPER_NOTEBOOK_LOCAL_N_CTX already in
                # os.environ wins (v0.8.5 precedence chain in provision.py
                # reads it first), so this is the GGUF-autodetect channel
                # rather than an override.
                "DEEPER_NOTEBOOK_LOCAL_N_CTX": str(self.chat_llm_n_ctx),
                # v0.8.38 — point the API at the launcher's log dir so
                # `GET /healthz/sidecars/{kind}/log` can read the per-
                # sidecar `.tail` files (last ~50 stderr lines) the new
                # _start_tail_drainer writes. Without this the API has
                # no way to surface why a sidecar died.
                "DEEPER_NOTEBOOK_LAUNCHER_LOG_DIR": str(self.log_dir),
            }
        )

        # v0.8.67l — self-heal a live-query-corrupted DB BEFORE SurrealDB starts
        # (clean slate, nothing connected yet). The flag is set by the worker
        # watcher on a prior boot's crash; repair is backup-first, abort-safe,
        # and one-shot, so it can never cause a boot loop.
        self._maybe_repair_db_on_boot()

        self._progress("supervisor.surreal", "running")
        self._spawn_surreal(surreal_port)
        self._record_stage("database_up")
        # v0.7.188 — pass the just-spawned proc to _wait_tcp so the
        # probe can early-exit if the child dies before binding.
        # self._procs[-1] is the latest Popen pushed by _spawn().
        _wait_tcp(
            # v0.8.67b — was a hard 30 s. On the first launch after an app
            # update the bootstrap re-extracts the runtime + rebuilds the venv;
            # that I/O delayed SurrealDB's bind past 30 s and, since this is a
            # core service, aborted the WHOLE supervisor (EARLY-INIT FAILURE →
            # dead app, empty chatbot). Raised + env-tunable; proc.poll() still
            # fails fast on an actual crash, so the bigger ceiling only ever
            # waits on a slow-but-alive start.
            "127.0.0.1",
            surreal_port,
            timeout=_startup_timeout(
                "DEEPER_NOTEBOOK_SURREAL_TCP_TIMEOUT",
                90.0,
            ),
            proc=self._procs[-1] if self._procs else None,
        )
        self._progress("supervisor.surreal", "done")

        # v0.8.67m — start periodic background exports of the running DB so a
        # corruption or accidental delete is always recoverable (daily by
        # default, keeping the last 7; non-blocking).
        self._start_periodic_export(surreal_port)

        self._progress("supervisor.api", "running")
        self._spawn_api(api_port)
        # v0.8.99 — "spawned", not "ready": this fires the moment the uvicorn
        # process exists (~200 ms). The expensive part is the /readyz wait
        # below, recorded separately as `api_ready`. Conflating the two put a
        # 20 s cost under the NEXT milestone and made the worker look slow.
        self._record_stage("api_spawned")
        # First-launch SurrealDB schema migrations + the heavy upstream import
        # chain (langchain + langgraph + podcast_creator) take 20-60 s before
        # uvicorn finishes startup. Subsequent launches are much faster but
        # we leave the generous timeout in place — better to wait than to
        # tear down an API that was about to come up.
        #
        # v0.7.24 — wait on /readyz, not /health. /health (preserved for
        # back-compat) returns 200 the instant uvicorn binds, even
        # mid-migration. /readyz only returns 200 once the DB is
        # actually reachable AND migrations have applied — the real
        # signal that downstream services (worker, frontend window)
        # can safely come up against the API.
        # v0.7.188 — pass the API proc so 3-minute wait → fail-fast on
        # a uvicorn that crashed in 200ms (binary missing, port
        # collision, EACCES on logging dir, etc.).
        _wait_http(
            # v0.8.67d — was a hard 180 s. A post-update first boot rebuilds the
            # venv, so the API's cold import (langchain/langgraph/podcast_creator)
            # can exceed 180 s and abort the whole app AT THIS GATE — exactly what
            # happened on the boot right after the v0.8.67c reinstall. Raised +
            # env-tunable; _wait_http still fail-fasts via proc.poll() on a real
            # uvicorn crash, so the bigger ceiling only waits on a slow-but-alive
            # cold import (never on a crash).
            f"http://127.0.0.1:{api_port}/readyz",
            timeout=_startup_timeout(
                "DEEPER_NOTEBOOK_API_READY_TIMEOUT",
                300.0,
            ),
            proc=self._procs[-1] if self._procs else None,
        )
        # The dominant startup cost on a warm launch: measured 20,208 ms here
        # against 91 ms for SurrealDB and 206 ms to spawn uvicorn itself. It is
        # the API's cold import chain (langchain + langgraph + podcast_creator
        # + transformers) plus migrations, exactly as the comment above
        # predicts. Everything downstream blocks on it.
        self._record_stage("api_ready")
        self._progress("supervisor.api", "done")

        self._progress("supervisor.worker", "running")
        self._spawn_worker()
        self._record_stage("worker_up")
        # v0.8.67l — watch worker.log for the live-query "key already exists"
        # crash; if seen, flag an automatic repair for the next boot
        # (non-blocking daemon thread, never delays startup).
        self._watch_worker_for_lq_corruption()
        # Worker has no port; just give it a beat to subscribe.
        time.sleep(0.5)
        self._progress("supervisor.worker", "done")

        # v0.7.143 — Patch Next.js standalone build to use the launcher's
        # dynamic API port. The rewrites destination is baked at BUILD
        # time pointing at `localhost:5055`; without this patch, every
        # /api/* request the frontend makes gets proxied to a port that
        # doesn't exist and the user sees "API config endpoint returned
        # status 500" with no API actually broken. See
        # desktop/next_rewrites_patcher.py for the full incident write-up.
        from desktop.next_rewrites_patcher import (
            PatchError,
            patch_rewrites_for_api_port,
        )

        next_cwd = self._next_frontend_dir()
        try:
            next_cwd = patch_rewrites_for_api_port(next_cwd, api_port)
        except PatchError as exc:
            # Logging not raising — the launcher should keep trying;
            # the user will get the same 500-on-/api/config error
            # screen as before, which now includes a clearer error.
            log.error(
                "Could not patch Next.js rewrites for api_port=%d: %s. "
                "Frontend will likely fail to reach the API.",
                api_port,
                exc,
            )

        self._progress("supervisor.next", "running")
        self._spawn_next(frontend_port, next_cwd=next_cwd)
        self._record_stage("frontend_up")
        # v0.7.188 — same early-exit pattern as the API wait above.
        _wait_http(
            # v0.8.67d — was 120 s; raised + env-tunable for the same post-update
            # cold-start reason as the /readyz gate above.
            f"http://127.0.0.1:{frontend_port}/",
            timeout=_startup_timeout(
                "DEEPER_NOTEBOOK_FRONTEND_READY_TIMEOUT",
                180.0,
            ),
            proc=self._procs[-1] if self._procs else None,
            # v0.8.68 — the webview navigates exactly once; gate on what it
            # will actually request (final page after the / redirect) and
            # demand back-to-back successes, not one lucky probe.
            consecutive=3,
            follow_redirects=True,
        )
        self.frontend_url = f"http://127.0.0.1:{frontend_port}/"
        self._progress("supervisor.next", "done")

        # v0.6.5 — replace 6 copy-pasted try/except blocks with one helper
        # that logs + reports through _progress. Avoids the silent-swallow
        # bug that made debugging missing/broken optional services painful.
        self._try_spawn(
            "supervisor.llamacpp_embed", self._spawn_llamacpp_embed, embed_port
        )
        self._try_spawn("supervisor.whisper", self._spawn_whisper, whisper_port)
        self._try_spawn("supervisor.piper", self._spawn_piper, piper_port)

        # v0.7.197 — Stash ports for auto_register, BUT ONLY if the
        # corresponding spawn actually produced a server.
        #
        # Before: we unconditionally stored the allocated port even
        # when `_spawn_*` early-returned (no nomic embed file, no
        # whisper model, no piper voices). auto_register then registered
        # credentials with `base_url=http://127.0.0.1:<unused_port>/v1`,
        # and the memory_retriever child was started with
        # `--embed-url http://127.0.0.1:<dead_port>/v1` — so the user
        # saw a "Local Embeddings (llama.cpp)" credential in the UI
        # that failed every test, and the first source upload hung
        # because the embed call to the dead port silently timed out.
        #
        # Mirror the early-return conditions in _spawn_llamacpp_embed /
        # _spawn_whisper / _spawn_piper. If the prerequisite is
        # missing, store 0 so downstream code (auto_register,
        # _spawn_memory_retriever) sees "no embed server" instead of
        # "embed server on a port that nothing is listening on".
        embed_alive = (
            self.nomic_embed_path is not None and self.nomic_embed_path.exists()
        )
        whisper_alive = self.whisper_model_path is not None
        # _spawn_piper additionally requires at least one voice path to
        # actually exist on disk (the dict is filtered there too); keep
        # that check in sync to avoid the same false-port-stash trap.
        piper_alive = bool(self.piper_voices) and any(
            p.exists() for p in self.piper_voices.values()
        )
        self.embed_port = embed_port if embed_alive else 0
        self.whisper_port = whisper_port if whisper_alive else 0
        self.piper_port = piper_port if piper_alive else 0

        # v0.4 additions — order matters: chat LLM must be up before the
        # memory retriever boots, because the retriever instantiates
        # mem0.Memory which validates the LLM endpoint at startup.
        self._try_spawn(
            "supervisor.llamacpp_chat", self._spawn_llamacpp_chat, chat_llm_port
        )
        # v0.7.197 chat_alive mirrors _spawn_llamacpp_chat's preconditions
        # (used immediately below for the v0.7.198 _wait_tcp gate, and
        # below in the v0.7.197 conditional stash).
        chat_alive = self.chat_llm_path is not None and self.chat_llm_path.exists()
        # v0.7.198 — Wait for the chat server to actually bind its port
        # BEFORE spawning the memory retriever. llama-cpp typically
        # takes 10-30 s to mmap a multi-GB GGUF and warm; without this
        # gate, mem0.Memory's startup validation hit a closed port and
        # the memory child exited rc=1 silently (production-mode
        # DEVNULL — same trap as v0.7.195). The user then saw "Memory
        # (local)" → Cannot connect to server in the credentials UI
        # even though everything was "configured correctly".
        #
        # Generous timeout (60 s) because a cold-cache mmap of a 16 GB
        # GGUF on a slow SSD can legitimately take that long. `proc=`
        # lets us short-circuit if the child crashed (binary missing,
        # GGUF corrupt, etc.) instead of waiting the full minute.
        # On timeout we LOG a warning and proceed — better to let the
        # rest of the launcher come up degraded than freeze the UI.
        if chat_alive and self._procs:
            try:
                _wait_tcp(
                    "127.0.0.1",
                    chat_llm_port,
                    # v0.8.67b — was 60 s; raised + env-tunable. A cold mmap of
                    # a large GGUF (the 14B-30B models here) can exceed 60 s.
                    # This gate already LOGS-and-proceeds on timeout (below), so
                    # it never aborts the app — the bump just lets big models be
                    # marked healthy instead of prematurely red.
                    timeout=_startup_timeout(
                        "DEEPER_NOTEBOOK_SIDECAR_TCP_TIMEOUT",
                        90.0,
                    ),
                    proc=self._procs[-1],
                )
            except (TimeoutError, RuntimeError) as exc:
                log.warning(
                    "v0.7.198 llamacpp_chat readiness probe failed: %s "
                    "(memory_retriever spawn will proceed but mem0 init "
                    "may fail)",
                    exc,
                )
        self.chat_llm_port = chat_llm_port if chat_alive else 0

        self._try_spawn("supervisor.memory", self._spawn_memory_retriever, memory_port)
        self.memory_port = memory_port

        if self.openchronicle_available:
            self._try_spawn(
                "supervisor.openchronicle",
                self._spawn_openchronicle_bridge,
                openchronicle_port,
            )
        self.openchronicle_port = (
            openchronicle_port if self.openchronicle_available else 0
        )
        # Final milestone: everything start_all() owns is up. Subtracting
        # `frontend_up` from this isolates the local-model sidecars (embed,
        # chat, whisper, piper, memory), which is where a large GGUF mmap
        # shows up.
        self._record_stage("sidecars_up")

    def stop_all(self) -> None:
        # v0.8.40 — Tear down the launcher control plane first so any
        # in-flight API calls fail-fast with a connection-refused
        # rather than racing the subprocess teardown below.
        if self._control_server is not None:
            try:
                self._control_server.stop()  # type: ignore[attr-defined]
            except Exception as exc:
                log.debug("launcher control server stop failed: %s", exc)
            self._control_server = None
        # v0.7.142 — Release the singleton FIRST so a relaunch isn't
        # blocked while we're still tearing down. The singleton release
        # is idempotent (safe even if start_all never ran or already
        # released). atexit also calls release independently, so the
        # only thing this gets us is faster recovery for the "relaunch
        # immediately after Cmd+Q" case.
        singleton = getattr(self, "_singleton", None)
        if singleton is not None:
            try:
                singleton.release()
            except Exception as exc:
                log.debug("singleton release failed: %s", exc)

        # v0.7.58 — log terminate/wait/close failures at debug level
        # instead of swallowing silently. Previously a zombie child
        # that survived terminate() was invisible; the launcher exited
        # "clean" but the OS still had the worker holding the SurrealDB
        # lock, and the next launch failed with a cryptic "address
        # already in use".
        #
        # v0.7.173 — Kill the entire process GROUP (the v0.7.173 spawn
        # uses `start_new_session=True` so each child is its own group
        # leader; sending SIGTERM to the pgid takes out the immediate
        # child PLUS any forked grandchildren in one signal). The bare
        # `p.terminate()` only signalled the immediate child — Next.js
        # grandchildren (`next-server`) reparented to PID 1 and
        # survived past the .app close. Falls back to terminate() if
        # killpg fails (e.g. process already exited, mocked Popen in
        # tests that doesn't have a real pgid).
        owned_posix_groups: set[int] = set()
        for p in reversed(self._procs):
            try:
                pid = getattr(p, "pid", None)
                if isinstance(pid, int) and pid > 0 and sys.platform != "win32":
                    try:
                        # killpg with the LEADER's PID (which equals
                        # the pgid because start_new_session=True).
                        os.killpg(pid, signal.SIGTERM)
                        owned_posix_groups.add(pid)
                    except (ProcessLookupError, PermissionError, OSError):
                        # Process already gone, no permission, or pgid
                        # missing (mock). Fall through to terminate().
                        try:
                            p.terminate()
                        except Exception:
                            pass
                elif isinstance(pid, int) and pid > 0 and sys.platform == "win32":
                    # v0.7.185 — was `os.kill(pid, CTRL_BREAK_EVENT)`. That
                    # only works when the target shares a console with us;
                    # a PyInstaller windowed .exe has NO console, so the
                    # signal is silently dropped and grandchildren leak —
                    # exactly the same bug v0.7.173 fixed for POSIX.
                    # `taskkill /F /T /PID` is the Windows equivalent of
                    # `killpg(SIGKILL)` and works without a console.
                    #   /F = force kill, /T = kill subtree (children +
                    #   grandchildren), /PID <n> = target by PID.
                    # Wrapped in subprocess.run with check=False so a
                    # non-existent PID (already-dead process) doesn't
                    # raise — same forgiveness pattern as the POSIX
                    # killpg branch above. Audit finding #3.
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    except Exception:
                        # taskkill missing (sandboxed environment) /
                        # timed out / other failure — fall back to
                        # the soft signal + terminate sequence.
                        try:
                            os.kill(pid, signal.CTRL_BREAK_EVENT)
                        except Exception:
                            try:
                                p.terminate()
                            except Exception:
                                pass
                else:
                    # No real PID (test mock) — fall back to terminate().
                    p.terminate()
            except Exception as exc:
                # v0.7.82 — `getattr(p, "pid", "?")` instead of `p.pid` so
                # mocked process objects in desktop/tests/test_launcher.py
                # don't raise AttributeError during stop_all teardown.
                # Real subprocess.Popen always has .pid; tests that
                # MagicMock(spec=Popen) may not.
                log.debug("terminate pid=%s failed: %s", getattr(p, "pid", "?"), exc)
        # v0.8.67g — shutdown grace is env-tunable (was a hard 5 s). SurrealDB
        # flushes its RocksDB + live-query bookkeeping on SIGTERM; if it gets
        # SIGKILLed before finishing (too-short grace on a big/busy DB), the
        # persisted live-query state can corrupt and block the next worker's
        # db.live("command") with "key already exists" — the source-processing
        # outage that needed a full DB re-import to repair. Default 8 s; raise via
        # DEEPER_NOTEBOOK_SHUTDOWN_GRACE_SECS for large databases.
        try:
            _grace = float(resolve_env("DEEPER_NOTEBOOK_SHUTDOWN_GRACE_SECS", "8") or 8)
        except ValueError:
            _grace = 8.0
        if _grace <= 0:
            _grace = 8.0
        deadline = time.monotonic() + _grace
        for p in self._procs:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                p.wait(timeout=remaining if remaining > 0 else 0.1)
            except subprocess.TimeoutExpired:
                pid = getattr(p, "pid", None)
                if not (
                    sys.platform != "win32"
                    and isinstance(pid, int)
                    and pid in owned_posix_groups
                ):
                    p.kill()
            except Exception as exc:
                log.debug("wait pid=%s failed: %s", getattr(p, "pid", "?"), exc)

        # A process-group leader can honor SIGTERM and exit while one of its
        # grandchildren ignores the same signal. Popen.wait() then succeeds,
        # even though the still-owned group keeps API ports or the database
        # lock alive. Give every surviving group the remainder of the shared
        # graceful deadline, then escalate the *whole group* and wait until it
        # is gone before returning from cleanup.
        def active_owned_groups(groups: set[int]) -> set[int]:
            active: set[int] = set()
            for process_group in groups:
                try:
                    os.killpg(process_group, 0)
                except ProcessLookupError:
                    continue
                except (PermissionError, OSError):
                    # The group was created by this supervisor. A transient
                    # probe error must fail closed and retain it for escalation.
                    active.add(process_group)
                else:
                    active.add(process_group)
            return active

        remaining_groups = active_owned_groups(owned_posix_groups)
        while remaining_groups and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            remaining_groups = active_owned_groups(remaining_groups)

        for process_group in remaining_groups:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError) as exc:
                log.warning(
                    "process-group escalation pgid=%s failed: %s",
                    process_group,
                    exc,
                )

        hard_deadline = time.monotonic() + 2.0
        remaining_groups = active_owned_groups(remaining_groups)
        while remaining_groups and time.monotonic() < hard_deadline:
            time.sleep(0.05)
            remaining_groups = active_owned_groups(remaining_groups)
        if remaining_groups:
            log.warning(
                "owned process groups remain after SIGKILL: %s",
                sorted(remaining_groups),
            )

        # Reap group leaders that reached SIGKILL after the first shared wait.
        for p in self._procs:
            try:
                p.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except Exception:
                    pass
            except Exception as exc:
                log.debug(
                    "final wait pid=%s failed: %s",
                    getattr(p, "pid", "?"),
                    exc,
                )
        # Join drainer threads with a short timeout BEFORE closing the
        # log files they're writing into — otherwise the daemon threads
        # could be mid-write when the file handle goes away. Buffered
        # tails of surreal.log / api.log often hold the crash cause.
        for t in self._drain_threads:
            try:
                t.join(timeout=2.0)
            except Exception as exc:
                log.debug("drain-thread join failed: %s", exc)
        for f in self._log_files:
            try:
                f.close()
            except Exception as exc:
                log.debug("log_file close failed: %s", exc)
        self._procs.clear()
        self._log_files.clear()
        self._drain_threads.clear()

    def _spawn(
        self,
        args: list[str],
        cwd: Path | None = None,
        name: str = "child",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        # PIPE without a reader deadlocks long-running children once the OS
        # pipe buffer fills (Surreal, uvicorn, Next all emit plenty of output).
        # In production we discard stdout (chatty + uninteresting for crash
        # post-mortem); in debug_mode we drain both streams to per-child
        # full-log files.
        #
        # v0.8.38 — ALSO capture stderr in non-debug mode and write the
        # last ~50 lines to a per-child `.tail` file. Cost: one drainer
        # thread + a tiny rolling file per sidecar. Benefit: when a
        # sidecar crashes (bad GGUF, OOM, port collision) the API can
        # surface the actual cause via /healthz/sidecars/{kind}/log
        # instead of just rendering a stale "down" badge. Pre-v0.8.38
        # users had to enable debug_mode + relaunch + hunt the log dir
        # — bad UX for the failure mode that matters most.
        if self.debug_mode:
            stdout: int = subprocess.PIPE
            stderr: int = subprocess.PIPE
        else:
            stdout = subprocess.DEVNULL
            # v0.8.38 — stderr captured into a tail-only drainer.
            stderr = subprocess.PIPE

        # v0.7.173 — Isolate each child into its own process group so
        # `stop_all` can kill the WHOLE subtree (including grandchildren)
        # in one signal. Previously this was a bare `subprocess.Popen`
        # and `stop_all` only sent SIGTERM to the immediate child —
        # grandchildren reparented to PID 1 and survived after the
        # .app window closed. Documented incident: the
        # `next-server (v16.2.6)` orphan zombies the user has seen
        # accumulating between launches (Next.js forks per-request
        # workers; `next-server` is itself a fork of the `node`
        # process we directly spawn). The v0.7.142 `reap_orphans`
        # was a startup sweep, not a shutdown one — so closing the
        # .app still leaked zombies until the next launch.
        #
        # `start_new_session=True` (POSIX) makes the child a process-
        # group leader. `stop_all` below now uses `os.killpg(pgid,
        # SIGTERM)` to kill the whole group atomically.
        # On Windows the equivalent is `creationflags=CREATE_NEW_PROCESS_GROUP`
        # plus `signal.CTRL_BREAK_EVENT`; only POSIX is supported here
        # currently — Windows launcher builds are a future-work item.
        # v0.7.205 — per-child env override. Was: `env=self.session_env`
        # for every spawn — which leaked PORT=<frontend_port> into
        # uvicorn-based children (llama_cpp.server, etc.) and
        # overrode their `--port` CLI arg. Merge any per-child
        # `extra_env` on top of the shared session_env so only the
        # Next.js spawn sees PORT.
        if extra_env:
            child_env = dict(self.session_env)
            child_env.update(extra_env)
        else:
            child_env = self.session_env

        popen_kwargs: dict = {
            "cwd": str(cwd) if cwd else None,
            "env": child_env,
            "stdout": stdout,
            "stderr": stderr,
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        else:
            # v0.7.185 — was just CREATE_NEW_PROCESS_GROUP. Added
            # CREATE_NO_WINDOW so each child doesn't pop a transient
            # console window (jarring UX from a packaged windowed
            # .app). The process-group flag remains so `taskkill /T`
            # in stop_all can target the whole subtree.
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )

        proc = subprocess.Popen(args, **popen_kwargs)
        self._procs.append(proc)

        if self.debug_mode and proc.stdout is not None and proc.stderr is not None:
            self._start_drainers(proc, name)
        elif proc.stderr is not None:
            # v0.8.38 — non-debug mode: tail-only stderr drainer for
            # the API's /healthz/sidecars/{kind}/log endpoint. stdout
            # is DEVNULL so there's nothing to drain on that side.
            self._start_tail_drainer(proc, name)

        return proc

    def _start_drainers(self, proc: subprocess.Popen, name: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{name}.log"
        log_file = open(log_path, "ab", buffering=0)
        self._log_files.append(log_file)

        # P2-HIGH-16 audit fix: scrub known secrets from subprocess output.
        # The Surreal binary, in particular, echoes its CLI flags (including
        # `--pass=...`) to stdout on startup. With debug_mode on, that would
        # land in surreal.log in plaintext.
        secret_pat = re.compile(
            rb"(?i)(--pass=|password[=:]|surreal_password[=:]|encryption_key[=:])"
            rb"([^\s\"']+)"
        )

        def _redact(b: bytes) -> bytes:
            return secret_pat.sub(rb"\1[REDACTED]", b)

        def drain(stream: IO[bytes], prefix: bytes) -> None:
            try:
                for line in iter(stream.readline, b""):
                    try:
                        log_file.write(prefix + _redact(line))
                    except Exception:
                        return
            except Exception:
                return

        for stream, prefix in ((proc.stdout, b"[out] "), (proc.stderr, b"[err] ")):
            t = threading.Thread(
                target=drain, args=(stream, prefix), name=f"drain-{name}", daemon=True
            )
            t.start()
            # v0.7.58 — track for join-before-log-close in stop_all
            self._drain_threads.append(t)

    def _start_tail_drainer(self, proc: subprocess.Popen, name: str) -> None:
        """v0.8.38 — non-debug-mode stderr tail capture.

        Drains the child's stderr into a `collections.deque(maxlen=50)`
        and writes the rolling tail to `{log_dir}/{name}.tail` on every
        new line. The file is small (≤ 50 lines × line width) and gives
        the API's `/healthz/sidecars/{kind}/log` endpoint a way to
        surface the actual crash cause when a sidecar dies (bad GGUF,
        OOM, port collision) — pre-v0.8.38 these failures landed only
        in DEVNULL.

        Secret redaction mirrors _start_drainers above so a child that
        echoes its CLI flags doesn't leak passwords/keys.
        """
        from collections import deque

        self.log_dir.mkdir(parents=True, exist_ok=True)
        tail_path = self.log_dir / f"{name}.tail"
        # Pre-create so the API can stat it without race.
        try:
            tail_path.touch(exist_ok=True)
        except Exception:
            # If we can't create the file (read-only FS, perms), just
            # skip the tail drainer — the rest of the launcher must
            # continue to function.
            return

        # Same secret-scrubber as the debug-mode drainer (v0.7.58 audit).
        secret_pat = re.compile(
            rb"(?i)(--pass=|password[=:]|surreal_password[=:]|encryption_key[=:])"
            rb"([^\s\"']+)"
        )

        def _redact(b: bytes) -> bytes:
            return secret_pat.sub(rb"\1[REDACTED]", b)

        tail = deque(maxlen=50)
        stderr_stream = proc.stderr  # type: IO[bytes] | None

        def drain_tail() -> None:
            if stderr_stream is None:
                return
            try:
                for line in iter(stderr_stream.readline, b""):
                    tail.append(_redact(line))
                    # Rewrite tail file on each new line. Cheap: ≤50
                    # lines of bytes; the cost is dominated by the
                    # IO syscall, not the join. Atomic-rewrite pattern
                    # avoids readers seeing a half-written file:
                    # write to a sibling then rename.
                    try:
                        tmp = tail_path.with_suffix(".tail.tmp")
                        with open(tmp, "wb") as f:
                            for ln in tail:
                                f.write(ln)
                        tmp.replace(tail_path)
                    except Exception:
                        # IO failure (disk full, perms changed) —
                        # keep draining the pipe so the child doesn't
                        # block, but skip the rewrite this iteration.
                        continue
            except Exception:
                # Stream closed / OSError — child likely exited. The
                # last tail file write reflects the final state, which
                # is what /healthz wants to surface.
                return

        t = threading.Thread(
            target=drain_tail,
            name=f"drain-tail-{name}",
            daemon=True,
        )
        t.start()
        self._drain_threads.append(t)

    def _spawn_surreal(self, port: int) -> None:
        ext = ".exe" if self.surreal_arch.startswith("windows") else ""
        binary = self.bin_dir / f"surreal-{self.surreal_arch}{ext}"
        data_dir = active_data_root() / "surreal_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        # Use --flag=value form so passwords/usernames that happen to start
        # with '-' (a real possibility from secrets.token_urlsafe which uses
        # base64url, where `-` is a valid char) aren't reparsed by clap as
        # a separate short flag like '-4'.
        # v0.7.185 — was f"file://{data_dir}". On POSIX `data_dir`
        # starts with `/`, producing a valid `file:///Users/.../...`
        # URL. On Windows `data_dir` is e.g. `C:\Users\Joe\.open-
        # notebook-plus\surreal_data`, so the f-string produces
        # `file://C:\Users\...` — NOT a valid file: URL. SurrealDB's
        # URL parser reads `C:` as the host, and storage init fails
        # silently or with a confusing error. `Path.as_uri()` is the
        # idiomatic cross-platform builder: returns
        # `file:///C:/Users/...` on Windows, `file:///Users/...` on
        # POSIX. Audit finding #2.
        self._spawn(
            [
                str(binary),
                "start",
                f"--user={self.cfg.surreal_user}",
                f"--pass={self.cfg.surreal_password}",
                f"--bind=127.0.0.1:{port}",
                data_dir.as_uri(),
            ],
            name="surreal",
        )

    def _spawn_api(self, port: int) -> None:
        # Use the venv python to run uvicorn directly — it's a real Python
        # interpreter with all upstream deps installed, so -m uvicorn works
        # without any internal dispatcher tricks.
        # cwd MUST be upstream_root so top-level api, commands, and canonical
        # deeper_notebook imports resolve consistently in the bundled runtime.
        args = [
            str(self.venv_python),
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        self._spawn(args, cwd=self.upstream_root, name="api")

    def _spawn_worker(self) -> None:
        # Use the venv python to call the surreal-commands worker module
        # directly — no console script or frozen-binary dispatcher needed.
        # cwd is upstream_root for the same reason as the API.
        #
        # v0.7.211 — Make `--max-tasks` explicit even though the default
        # is 5. Two reasons:
        #   1. Visibility — the v0.7.210 deep audit flagged this as
        #      "single worker, head-of-line blocking" because the spawn
        #      command had no concurrency flag. The default IS 5, but a
        #      future surreal-commands release could change that without
        #      us noticing. Pin the intent.
        #   2. Tunability — operators with constrained RAM (a 16 GB
        #      Mac running a 14B local model + 5 concurrent
        #      embed/insight/podcast jobs) can lower via
        #      DEEPER_NOTEBOOK_WORKER_MAX_TASKS env without code edits.
        max_tasks_raw = resolve_env("DEEPER_NOTEBOOK_WORKER_MAX_TASKS", "5")
        try:
            max_tasks = max(1, min(int(max_tasks_raw), 32))
        except ValueError:
            log.warning(
                "DEEPER_NOTEBOOK_WORKER_MAX_TASKS=%r is not an int; using default 5",
                max_tasks_raw,
            )
            max_tasks = 5
        args = [
            str(self.venv_python),
            "-m",
            "surreal_commands.cli.worker",
            "--import-modules",
            "commands",
            "--max-tasks",
            str(max_tasks),
        ]
        self._spawn(args, cwd=self.upstream_root, name="worker")

    def _maybe_repair_db_on_boot(self) -> None:
        """v0.8.67l — If a prior worker crash flagged live-query corruption,
        repair the DB now — BEFORE SurrealDB/API start, when nothing is
        connected. Backup-first, abort-safe, and ONE-SHOT (the flag is cleared
        after a single attempt so a repair that doesn't help can't cause a boot
        loop). Never raises: a repair failure just boots degraded with a clear
        log pointing at the manual script."""
        # Opt-out hook (set by the test conftest) so unit tests that drive
        # start_all with mocked subprocesses never touch the real data dir.
        if resolve_env("DEEPER_NOTEBOOK_DISABLE_DB_AUTOREPAIR"):
            return
        try:
            from desktop import db_repair

            data_home = active_data_root()
            if not db_repair.needs_repair(data_home):
                return
            ext = ".exe" if self.surreal_arch.startswith("windows") else ""
            log.warning(
                "db_repair: a prior worker crash flagged live-query corruption — "
                "running automatic backup-first repair before boot…"
            )
            try:
                repair_port = int(
                    resolve_env("DEEPER_NOTEBOOK_REPAIR_PORT", "18799") or 18799
                )
            except ValueError:
                repair_port = 18799
            ok = db_repair.auto_repair(
                surreal_bin=self.bin_dir / f"surreal-{self.surreal_arch}{ext}",
                data_dir=active_data_root() / "surreal_data",
                backup_dir=user_home() / "onp-backups",
                surreal_user=self.cfg.surreal_user,
                surreal_password=self.cfg.surreal_password,
                ts=time.strftime("%Y%m%d-%H%M%S"),
                port=repair_port,
                log=log,
            )
            # One-shot: clear regardless of outcome so a non-fixing repair never
            # re-triggers on the next boot.
            db_repair.clear_needs_repair(data_home)
            if not ok:
                log.warning(
                    "db_repair: automatic repair did not complete — booting anyway. "
                    "If source processing stays broken, quit and run "
                    "scripts/repair_desktop_db.sh."
                )
        except Exception as exc:
            log.error("db_repair: pre-start check failed (non-fatal): %s", exc)

    def _watch_worker_for_lq_corruption(self) -> None:
        """v0.8.67l — Briefly watch worker.log for the live-query "key already
        exists" crash. If seen, set the one-shot repair flag so the NEXT boot
        auto-heals — we do NOT repair mid-boot, because the API is already
        connected to the live DB. Runs in a daemon thread; never blocks boot."""
        if resolve_env("DEEPER_NOTEBOOK_DISABLE_DB_AUTOREPAIR"):
            return
        worker_log = self.log_dir / "worker.log"
        data_home = active_data_root()
        # v0.8.67l — only consider content appended AFTER this boot's worker
        # spawn. worker.log is append-only, so a stale crash from a PREVIOUS
        # (already-repaired) session would otherwise falsely re-flag a repair.
        try:
            start_offset = worker_log.stat().st_size
        except OSError:
            start_offset = 0

        def _watch() -> None:
            try:
                from desktop import db_repair
            except Exception:
                return
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                time.sleep(2.0)
                try:
                    with open(worker_log, "r", errors="ignore") as fh:
                        fh.seek(start_offset)
                        txt = fh.read()
                except Exception:
                    continue
                if db_repair.looks_like_lq_corruption(txt):
                    db_repair.set_needs_repair(data_home)
                    log.warning(
                        "db_repair: detected SurrealDB live-query corruption in the "
                        "worker (source processing is stuck). It will be repaired "
                        "AUTOMATICALLY the next time you open the app — quit (Cmd+Q) "
                        "and reopen."
                    )
                    return

        try:
            threading.Thread(
                target=_watch, name="lq-corruption-watch", daemon=True
            ).start()
        except Exception as exc:
            log.debug("db_repair: could not start worker watcher (%s)", exc)

    @staticmethod
    def _prune_old_exports(backup_dir: Path, keep: int) -> None:
        """v0.8.67m — Keep only the newest `keep` auto-export-*.surql files so
        scheduled backups don't grow without bound. Best-effort; never raises."""
        try:
            files = sorted(
                Path(backup_dir).glob("auto-export-*.surql"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for old in files[max(1, keep) :]:
            try:
                old.unlink()
            except OSError:
                pass

    def _start_periodic_export(self, surreal_port: int) -> None:
        """v0.8.67m — Periodically export the RUNNING SurrealDB to
        ~/onp-backups so a corruption or accidental delete is recoverable.
        Default every 24h, keep the newest 7; tunable via DEEPER_NOTEBOOK_AUTO_EXPORT_HOURS
        (0 disables) and DEEPER_NOTEBOOK_AUTO_EXPORT_KEEP. Sleeps the interval FIRST, so it
        never adds boot I/O and is inert in fast-finishing tests. Failures log
        and retry next interval — never crash the supervisor."""
        if resolve_env("DEEPER_NOTEBOOK_DISABLE_DB_AUTOREPAIR"):
            return
        try:
            hours = float(resolve_env("DEEPER_NOTEBOOK_AUTO_EXPORT_HOURS", "24") or 24)
        except ValueError:
            hours = 24.0
        if hours <= 0:
            return
        try:
            keep = int(resolve_env("DEEPER_NOTEBOOK_AUTO_EXPORT_KEEP", "7") or 7)
        except ValueError:
            keep = 7
        keep = max(1, keep)
        # v0.8.67o — do the FIRST export shortly after boot (default 10 min),
        # not after a full interval. A desktop app is usually quit within a day,
        # so sleeping the whole 24h interval FIRST (pre-v0.8.67o) meant most
        # sessions produced NO backup at all — the protection rarely fired.
        try:
            first_delay = float(
                resolve_env("DEEPER_NOTEBOOK_AUTO_EXPORT_FIRST_DELAY_SECS", "600")
                or 600
            )
        except ValueError:
            first_delay = 600.0
        if first_delay < 0:
            first_delay = 0.0

        ext = ".exe" if self.surreal_arch.startswith("windows") else ""
        binary = self.bin_dir / f"surreal-{self.surreal_arch}{ext}"
        backup_dir = user_home() / "onp-backups"
        user = self.cfg.surreal_user
        password = self.cfg.surreal_password

        def _loop() -> None:
            interval = hours * 3600.0
            delay = first_delay  # first export soon after boot, then every interval
            while True:
                time.sleep(delay)
                delay = interval
                try:
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    out = backup_dir / f"auto-export-{ts}.surql"
                    subprocess.run(
                        [
                            str(binary),
                            "export",
                            "--endpoint",
                            f"http://127.0.0.1:{surreal_port}",
                            "--username",
                            user,
                            "--password",
                            password,
                            "--namespace",
                            "open_notebook",
                            "--database",
                            "open_notebook",
                            str(out),
                        ],
                        check=True,
                        timeout=600,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if out.exists() and out.stat().st_size > 0:
                        log.info(
                            "auto-export: wrote %s (%d bytes)", out, out.stat().st_size
                        )
                        self._prune_old_exports(backup_dir, keep)
                    else:
                        log.warning("auto-export: produced an empty file; removing")
                        try:
                            out.unlink()
                        except OSError:
                            pass
                except Exception as exc:
                    log.warning("auto-export failed (retry next interval): %s", exc)

        try:
            threading.Thread(target=_loop, name="auto-export", daemon=True).start()
        except Exception as exc:
            log.debug("auto-export: could not start thread (%s)", exc)

    def _spawn_next(self, port: int, *, next_cwd: Path | None = None) -> None:
        node_bin = (
            self.bin_dir
            / f"node-{self.node_arch}"
            / ("node.exe" if self.node_arch.startswith("windows") else "bin/node")
        )
        # The standalone build produces server.js with everything inlined.
        #
        # v0.7.205 — pass `PORT=<frontend_port>` via per-child
        # `extra_env` instead of the shared session_env. Next.js's
        # standalone server reads PORT from env (no CLI flag for
        # port); the per-child override means only this spawn sees
        # it, not every uvicorn-based child.
        #
        # v0.7.143 — `next_cwd` defaults to the bundled frontend dir but
        # the caller may override with a writable copy if the bundle
        # itself is read-only (e.g., installed under /Applications).
        # See next_rewrites_patcher.patch_rewrites_for_api_port.
        if next_cwd is None:
            next_cwd = self.repo_root / "frontend"
        self._spawn(
            [str(node_bin), "server.js"],
            cwd=next_cwd,
            name="next",
            extra_env={"PORT": str(port)},
        )

    def _progress(self, step: str, status: str, message: str = "") -> None:
        if self.progress is not None:
            try:
                self.progress.publish(step, status, message)
            except Exception:
                pass

    def _try_spawn(self, step: str, fn, *args) -> None:
        """Wrap an optional/best-effort spawn in progress + logging.

        v0.6.5 — Previously each optional service had its own try/except
        that swallowed exceptions silently and only published "error" to
        the progress bus with no message. Anyone debugging "piper doesn't
        work on my machine" had nothing to go on. Now:
          - logger.warning logs the full exception (with traceback)
          - the progress event includes the str(exc) so the UI sees it
          - control flow is unchanged: failure here doesn't crash launcher
        """
        # v0.8.40 — remember the spawn args so the control plane's
        # restart_sidecar can re-invoke fn(*args) without re-deriving
        # ports/paths.
        kind = self._step_to_kind(step)
        heavyweight_mlx = bool(kind == "chat" and self.cfg.provider == "mlx")
        if kind is not None:
            reservation = self.resource_governor.reserve(
                kind,
                self._SIDECAR_RESERVATION_BYTES[kind],
                heavyweight_mlx=heavyweight_mlx,
            )
            if reservation != "reserved":
                self._progress(
                    step, reservation, "Local resource governor deferred spawn"
                )
                return
        if kind is not None and args:
            # Args is typically (port,) for sidecars. Store the int +
            # the step name so restart logs the right label.
            self._sidecar_spawn_args[kind] = (args[0] if args else 0, step)
        self._progress(step, "running")
        proc_count = len(self._procs)
        # v0.8.100 — per-sidecar milestone. `sidecars_up` was a single 5,947 ms
        # bucket covering embed/whisper/piper/chat/memory/openchronicle, which
        # only says "the sidecars are slow", not WHICH. Recording here rather
        # than at each call site means every sidecar — including any added
        # later — is attributed automatically. `step` is "supervisor.<name>",
        # so the milestone reads e.g. `sidecar_whisper`.
        _sidecar_started_at = time.monotonic()
        try:
            fn(*args)
            spawned = self._procs[proc_count:]
            if kind is not None and not spawned:
                self.resource_governor.release(kind)
                self._progress(step, "done")
                return
            if kind is not None and not self._sidecar_health_check(kind, spawned[-1]):
                raise RuntimeError("sidecar failed its post-spawn health check")
            # v0.8.40 — track the Popen by kind for restart lookup. The
            # spawn fn pushed it onto _procs already; grab the last one.
            if kind is not None:
                self._sidecar_procs[kind] = spawned[-1]
            self._progress(step, "done")
            self._record_sidecar_stage(step, _sidecar_started_at)
        except Exception as exc:
            if kind is not None:
                for proc in self._procs[proc_count:]:
                    self._cleanup_partial_sidecar(proc)
                self.resource_governor.release(kind)
            log.warning("%s spawn failed: %s", step, exc, exc_info=True)
            self._progress(step, "error", str(exc))
            # A sidecar that burns 30 s and THEN fails is the most expensive
            # case; attribute it rather than losing it to the exception path.
            self._record_sidecar_stage(step, _sidecar_started_at)

    # v0.8.40 — map launcher step names to API-side `kind` strings.
    # Must stay in sync with `_KIND_TO_SUPERVISOR` in
    # `api/routers/local_models.py`.
    _STEP_TO_KIND: dict[str, str] = {
        "supervisor.llamacpp_chat": "chat",
        "supervisor.llamacpp_embed": "embed",
        "supervisor.whisper": "whisper",
        "supervisor.piper": "piper",
        "supervisor.memory": "memory",
    }
    _SIDECAR_RESERVATION_BYTES: dict[str, int] = {
        "chat": 5 * 1024**3,
        "embed": 1024**3,
        "whisper": 2 * 1024**3,
        "piper": 512 * 1024**2,
        "memory": 512 * 1024**2,
    }

    def _step_to_kind(self, step: str) -> str | None:
        return self._STEP_TO_KIND.get(step)

    def _sidecar_health_check(self, _kind: str, proc: subprocess.Popen) -> bool:
        """A started child must still be alive before its reservation is kept."""
        return proc.poll() is None

    def _cleanup_partial_sidecar(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except Exception:
            pass
        if proc in self._procs:
            self._procs.remove(proc)

    def restart_sidecar(self, kind: str) -> tuple[bool, str]:
        """v0.8.40 — Kill the named sidecar's process group and respawn
        it with the same args. Called by the launcher control plane
        (`desktop/launcher_control.py:_ControlHandler.do_POST`) when
        the API receives POST /healthz/sidecars/{kind}/restart.

        Returns `(success, detail)`. Errors are returned as (False, msg)
        rather than raised so the control plane can serialize them.

        Steps:
          1. Look up the original spawn args from _sidecar_spawn_args.
             If missing, the sidecar was never spawned this session
             (e.g. chat skipped because no GGUF) → fail with a clear msg.
          2. If a current Popen exists, kill its process group (same
             pattern as stop_all uses) and reap.
          3. Re-invoke the original _spawn_<kind> function via the same
             _try_spawn path used at start-up — so any failure produces
             a `progress("error", ...)` event the UI can read.
          4. Return success with the new pid in the detail.

        Thread-safety: called from the launcher control plane's HTTP
        thread (separate from the main launcher thread which is
        usually idle waiting for stop_all). Popen + os.killpg are
        thread-safe; the dict mutations protected by the GIL.
        """
        import os as _os
        import signal as _signal

        if kind not in self._STEP_TO_KIND.values():
            return False, f"Unknown sidecar kind {kind!r}"

        if kind not in self._sidecar_spawn_args:
            return False, (
                f"Sidecar {kind!r} was never spawned this session "
                "(missing config? Try relaunching the app.)"
            )

        port, step = self._sidecar_spawn_args[kind]
        log.info("restart_sidecar(%r): killing old proc + respawning", kind)

        # Find the spawn function via name lookup. Mapping is
        # parallel-keyed to _STEP_TO_KIND so a future sidecar addition
        # only requires two table entries to wire up restart.
        spawn_fn_name = {
            "chat": "_spawn_llamacpp_chat",
            "embed": "_spawn_llamacpp_embed",
            "whisper": "_spawn_whisper",
            "piper": "_spawn_piper",
            "memory": "_spawn_memory_retriever",
        }.get(kind)
        if spawn_fn_name is None:
            return False, f"No spawn function registered for kind={kind!r}"
        spawn_fn = getattr(self, spawn_fn_name, None)
        if spawn_fn is None:
            return False, f"Spawn function {spawn_fn_name!r} missing on Supervisor"

        # Kill the existing Popen if any. Use the same SIGTERM →
        # SIGKILL pattern stop_all uses (process group) so any
        # grandchildren of llama-cpp / whisper-cpp / piper-cpp are
        # also reaped.
        old = self._sidecar_procs.get(kind)
        stopped = old is None or old.poll() is not None
        if old is not None and not stopped:
            try:
                pgid = _os.getpgid(old.pid)
                _os.killpg(pgid, _signal.SIGTERM)
            except (OSError, ProcessLookupError):
                # Already dead or never had a group — try a plain kill.
                try:
                    old.terminate()
                except Exception:
                    pass
            try:
                old.wait(timeout=5.0)
                stopped = True
            except subprocess.TimeoutExpired:
                try:
                    pgid = _os.getpgid(old.pid)
                    _os.killpg(pgid, _signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    return False, (
                        f"Sidecar {kind!r} could not be confirmed stopped; "
                        "keeping the existing process and reservation."
                    )
                try:
                    old.wait(timeout=2.0)
                    stopped = True
                except subprocess.TimeoutExpired:
                    stopped = old.poll() is not None
        if not stopped:
            return False, (
                f"Sidecar {kind!r} could not be confirmed stopped; "
                "keeping the existing process and reservation."
            )
        # Drop the dead Popen from both trackers so the next spawn
        # populates fresh entries.
        self._sidecar_procs.pop(kind, None)
        if old in self._procs:
            self._procs.remove(old)
        # The old child is no longer running, so its reservation must not
        # count against its own replacement. `_try_spawn` acquires a fresh
        # reservation and releases it again if no healthy replacement appears.
        self.resource_governor.release(kind)

        # Respawn via the same _try_spawn path used at boot — preserves
        # progress events + the v0.8.40 _sidecar_procs/spawn_args
        # tracking via the populator hook above.
        self._try_spawn(step, spawn_fn, port)

        # After _try_spawn, the new Popen (if any) is in _sidecar_procs.
        new = self._sidecar_procs.get(kind)
        if new is None or new.poll() is not None:
            return False, (
                f"Respawn of {kind!r} did not produce a live child. "
                "Check API logs and the sidecar tail file."
            )
        return True, f"Sidecar {kind!r} respawned (pid={new.pid})"

    def hot_swap_chat(self, new_path: str) -> tuple[bool, str]:
        """v0.8.40b — Swap the chat sidecar's GGUF without quitting
        the app.

        Steps:
          1. Validate `new_path` — must exist on disk and live under
             the configured model_dir (defense against path-traversal
             from the API caller — the API does its own check but we
             defense-in-depth here too).
          2. Update `self.chat_llm_path` so the next spawn reads it.
          3. Re-resolve `chat_llm_n_ctx` from the new GGUF's metadata
             (so subsequent restarts in this session use the right
             context length, even if the DEEPER_NOTEBOOK_LOCAL_N_CTX
             env var seen by the API stays at the OLD value — that
             mismatch is non-fatal but worth documenting; see
             v0.8.40b CHANGELOG entry).
          4. Restart the chat sidecar via `restart_sidecar("chat")`.

        Returns (ok, detail). Like restart_sidecar, failures are
        returned, never raised — control plane needs to serialize them.

        Known limitations (acceptable for v0.8.40b, deferred):
          - DEEPER_NOTEBOOK_LOCAL_N_CTX in the API subprocess env is
            NOT updated. If the new GGUF has a SMALLER n_ctx than the
            old, the router might still route prompts that fit the
            old context to local; the new sidecar then returns 400
            context_length_exceeded for those edge prompts. Common
            case (same family / same quant) works fine. A v0.8.40c
            could expose a control-plane "refresh env" endpoint
            that updates os.environ in the API process.
        """
        from pathlib import Path as _P

        if not new_path:
            return False, "Missing new_path"
        target = _P(new_path)
        if not target.exists():
            return False, f"File not found: {new_path}"
        is_gguf = target.is_file() and target.suffix.lower() == ".gguf"
        is_mlx = target.is_dir() and (target / "config.json").is_file()
        if not is_gguf and not is_mlx:
            return False, "new_path must be a .gguf file or an MLX model directory"
        if (
            self.cfg.model_dir not in target.parents
            and target.parent != self.cfg.model_dir
            and target != self.cfg.model_dir
        ):
            # Path-traversal guard — must live under model_dir.
            return False, (
                f"new_path must be inside the configured model_dir "
                f"({self.cfg.model_dir})"
            )

        if is_mlx:
            provider = getattr(self, "model_provider_runtime", None)
            if provider is None:
                try:
                    from desktop.providers.mlx import MlxProvider

                    provider = MlxProvider(
                        model_dir=Path(self.cfg.model_dir),
                        python_executable=self.venv_python,
                    )
                    self.model_provider_runtime = provider
                except Exception as exc:
                    return False, f"MLX provider initialization failed: {exc}"

            log.info("hot_swap_chat (mlx): target=%s", target)
            try:
                env = provider.start(str(target), validate=True, wait_for_ready=True)
            except Exception as exc:
                log.warning("hot_swap_chat (mlx) failed: %s", exc)
                return False, f"MLX server restart failed: {exc}"

            env_updates = {
                "OPENAI_COMPATIBLE_BASE_URL": env.OPENAI_COMPATIBLE_BASE_URL,
                "OPENAI_COMPATIBLE_API_KEY": env.OPENAI_COMPATIBLE_API_KEY,
                "DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL": env.DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL,
            }
            self.session_env.update(env_updates)
            try:
                self._push_env_to_api(env_updates)
            except Exception as exc:
                log.warning("hot_swap_chat: env-refresh push failed: %s", exc)

            return True, f"Chat model swapped to MLX model {target.name}."

        old_path = self.chat_llm_path
        # v0.8.42b — capture pre-swap n_ctx for full rollback. Pre-
        # v0.8.42b, on respawn failure only `chat_llm_path` was
        # restored — `chat_llm_n_ctx` kept the new GGUF's value,
        # giving a mismatched (path, n_ctx) pair on next retry.
        old_n_ctx = self.chat_llm_n_ctx
        log.info(
            "hot_swap_chat: %s → %s",
            old_path,
            target,
        )
        # Mutate the path BEFORE restart so the respawn picks it up.
        self.chat_llm_path = target

        # Re-resolve n_ctx for the new GGUF. The launcher's own
        # _spawn_llamacpp_chat reads chat_llm_n_ctx via env or
        # _resolve_chat_llm_n_ctx; we update the attribute here so
        # future restarts of THIS sidecar in THIS session use the
        # right value. The API's view of DEEPER_NOTEBOOK_LOCAL_N_CTX
        # is stale until app relaunch — documented limitation.
        try:
            self.chat_llm_n_ctx = self._resolve_chat_llm_n_ctx()
        except Exception as exc:
            log.warning(
                "hot_swap_chat: n_ctx re-resolve failed (%s); keeping old value %d",
                exc,
                self.chat_llm_n_ctx,
            )

        # Now restart — the spawn function reads self.chat_llm_path
        # which we just updated.
        ok, detail = self.restart_sidecar("chat")
        if not ok:
            # Roll back the path so subsequent attempts don't compound
            # the failure. The old sidecar is already dead at this
            # point (restart_sidecar killed it before respawn), so
            # we can't restore the old running state — but at least
            # the next user-triggered restart will use the old path.
            # v0.8.42b — ALSO restore n_ctx to its pre-swap value.
            # Pre-v0.8.42b only `chat_llm_path` was restored, so the
            # next retry would compute against an n_ctx that matched
            # the rolled-back-out GGUF. Full rollback of the
            # (path, n_ctx) pair keeps invariants tight.
            self.chat_llm_path = old_path
            self.chat_llm_n_ctx = old_n_ctx
            return False, f"Restart with new GGUF failed: {detail}"

        # v0.8.40d — Push the new n_ctx into the running API process so
        # the smart router (provision.py) sees it on the very next
        # chat turn. Closes the v0.8.40b "stale n_ctx" limitation.
        # Best-effort: a push failure does NOT undo the swap — the
        # sidecar is live with the new GGUF, the router just keeps
        # using the OLD n_ctx until app relaunch. That's the v0.8.40b
        # baseline behaviour, so we're never WORSE off here.
        self.session_env["DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL"] = str(target)
        try:
            self._push_env_to_api(
                {
                    "DEEPER_NOTEBOOK_LOCAL_N_CTX": str(self.chat_llm_n_ctx),
                    "DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL": str(target),
                }
            )
        except Exception as exc:
            log.warning(
                "hot_swap_chat: env-refresh push failed (router will "
                "keep using stale n_ctx until app relaunch): %s",
                exc,
            )

        return True, (
            f"Chat sidecar swapped to {target.name} (n_ctx={self.chat_llm_n_ctx}). "
            f"{detail}"
        )

    def _push_env_to_api(self, vars: dict[str, str]) -> None:
        """v0.8.40d — POST `vars` to the API's /system/env-refresh
        endpoint so it mutates os.environ in the running process.

        Auth: reuse `DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN` (same secret
        the API uses for its launcher-control-plane calls; symmetric
        trust boundary).

        Raises on any failure — caller wraps in try/except so a flaky
        API doesn't undo a successful sidecar swap.
        """
        import httpx as _httpx

        api_port = self.session_env.get("API_PORT")
        token = self.session_env.get(
            "DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN",
            "",
        )
        if not api_port or not token:
            raise RuntimeError(
                "API_PORT / control token unavailable in session_env",
            )
        url = f"http://127.0.0.1:{api_port}/api/system/env-refresh"
        # Sync httpx — we're already on a non-event-loop thread
        # (control plane HTTP handler thread → no async context here).
        # Tight timeouts because the local API should respond in ms.
        with _httpx.Client(
            timeout=_httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
        ) as client:
            resp = client.post(
                url,
                json={"vars": vars},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            body = resp.json()
            log.info(
                "env-refresh: updated=%s rejected=%s",
                body.get("updated", []),
                body.get("rejected", []),
            )

    def _spawn_llamacpp_embed(self, port: int) -> None:
        if self.nomic_embed_path is None or not self.nomic_embed_path.exists():
            return  # silently skip; embeddings just won't work this session
        args = [
            str(self.venv_python),
            "-m",
            "llama_cpp.server",
            "--model",
            str(self.nomic_embed_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--embedding",
            "true",
            # v0.8.67c — GPU-offload the embedder too (Metal on Apple Silicon).
            # Tiny model, but it keeps source-embedding fast and consistent with
            # the chat sidecar; CPU on non-macOS by default.
            "--n_gpu_layers",
            _n_gpu_layers("DEEPER_NOTEBOOK_EMBED_N_GPU_LAYERS"),
        ]
        self._spawn(args, cwd=self.upstream_root, name="llamacpp_embed")
        self._spawn_llamacpp_rerank()

    def _detect_reranker_model(self) -> Path | None:
        """Scan configured model directory for cross-encoder reranker GGUF."""
        raw = os.environ.get("DEEPER_NOTEBOOK_RERANKER_MODEL_PATH", "").strip()
        if raw and Path(raw).is_file():
            return Path(raw)
        model_dir = getattr(self.cfg, "model_dir", None)
        if model_dir and Path(model_dir).is_dir():
            for p in sorted(Path(model_dir).glob("*.gguf")):
                if "rerank" in p.name.lower():
                    return p
        if self.nomic_embed_path and self.nomic_embed_path.parent.is_dir():
            for p in sorted(self.nomic_embed_path.parent.glob("*.gguf")):
                if "rerank" in p.name.lower():
                    return p
        return None

    def _spawn_llamacpp_rerank(self, port: int | None = None) -> None:
        """Spawn llama_cpp.server in --rerank mode if a reranker model is detected."""
        reranker_model = self._detect_reranker_model()
        if reranker_model is None or not reranker_model.exists():
            return
        rerank_port = port if (port and port > 0) else find_free_ports(1)[0]
        args = [
            str(self.venv_python),
            "-m",
            "llama_cpp.server",
            "--model",
            str(reranker_model),
            "--host",
            "127.0.0.1",
            "--port",
            str(rerank_port),
            "--rerank",
            "true",
            "--n_gpu_layers",
            _n_gpu_layers("DEEPER_NOTEBOOK_RERANK_N_GPU_LAYERS"),
        ]
        self._spawn(args, cwd=self.upstream_root, name="llamacpp_rerank")
        self.session_env["DEEPER_NOTEBOOK_RERANKER_URL"] = f"http://127.0.0.1:{rerank_port}"
        try:
            self._push_env_to_api(
                {"DEEPER_NOTEBOOK_RERANKER_URL": self.session_env["DEEPER_NOTEBOOK_RERANKER_URL"]}
            )
        except Exception as exc:
            log.warning("Could not push DEEPER_NOTEBOOK_RERANKER_URL to API: %s", exc)

    def _spawn_whisper(self, port: int) -> None:
        if self.whisper_model_path is None:
            return
        args = [
            str(self.venv_python),
            "-m",
            "desktop_shims.whisper_shim",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--model",
            str(self.whisper_model_path),
        ]
        self._spawn(args, cwd=self.upstream_root, name="whisper")

    def _spawn_piper(self, port: int) -> None:
        if not self.piper_voices:
            return
        voice_args = []
        for name, path in self.piper_voices.items():
            if path.exists():
                voice_args.extend(["--voice", f"{name}={path}"])
        if not voice_args:
            return
        args = [
            str(self.venv_python),
            "-m",
            "desktop_shims.piper_shim",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ] + voice_args
        self._spawn(args, cwd=self.upstream_root, name="piper")

    @staticmethod
    def _detect_gguf_context_length(
        gguf_path: Path,
        *,
        fallback: int = 32768,
    ) -> int:
        """v0.7.206 — Read a GGUF file's `<arch>.context_length` metadata
        without loading the whole model into memory.

        Why this exists: previously the launcher capped `n_ctx` at a
        fixed 16384 default regardless of model capability. A user
        running Hermes-3 (131k native) hit `400 context_length_exceeded`
        after selecting 2-3 sources for a chat (21k tokens combined).
        Auto-detection means the cap matches what the model file
        actually advertises, capped by `DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX` for RAM
        safety.

        Returns `fallback` on any error — the launcher must never
        block startup on a metadata parse failure (could be a corrupt
        file, an exotic quant the parser doesn't recognise, etc.).
        """
        try:
            from gguf import GGUFReader  # type: ignore[import-not-found]
        except Exception as exc:
            log.debug(
                "gguf library not available for metadata probe; using fallback %d: %s",
                fallback,
                exc,
            )
            return fallback

        try:
            reader = GGUFReader(str(gguf_path))
            # Architecture is stored in `general.architecture`; the
            # per-arch metadata key is `<arch>.context_length`.
            arch_field = reader.get_field("general.architecture")
            if arch_field is None:
                return fallback
            # GGUFReader exposes string fields as a list of raw bytes
            # arrays per part; coerce defensively.
            arch_parts = getattr(arch_field, "parts", None) or []
            arch_data = getattr(arch_field, "data", None) or []
            if arch_parts and arch_data:
                arch_raw = arch_parts[arch_data[0]]
                arch = bytes(arch_raw).decode("utf-8", errors="ignore").strip()
            else:
                return fallback

            ctx_field = reader.get_field(f"{arch}.context_length")
            if ctx_field is None:
                return fallback
            ctx_data = getattr(ctx_field, "data", None) or []
            ctx_parts = getattr(ctx_field, "parts", None) or []
            if ctx_parts and ctx_data:
                val = ctx_parts[ctx_data[0]]
                # context_length is a uint32/uint64; coerce to int.
                ctx_int = int(val[0]) if hasattr(val, "__len__") else int(val)
                if ctx_int >= 512:
                    return ctx_int
        except Exception as exc:
            log.debug(
                "Failed to read GGUF context_length from %s: %s; using fallback %d",
                gguf_path,
                exc,
                fallback,
            )
        return fallback

    @staticmethod
    def _default_ctx_max() -> int:
        """v0.8.67i — RAM-aware default ceiling for the chat-LLM context
        window, used only when DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX is NOT explicitly set.

        A llama.cpp KV cache for an 8B model costs ~0.125 MiB/token, so a
        98304-token window ≈ 12 GiB. On Apple Silicon (unified memory)
        that is cheap on a 64 GB machine but ruinous on a 16 GB one — so we
        scale the ceiling to total physical RAM and keep the historical
        32768 default on smaller or non-darwin hosts. Tiers leave generous
        headroom for the model weights (~5 GiB), the embeddings sidecar,
        and the OS.

        Why this exists: pre-v0.8.67i the cap was hardcoded to 32768, so a
        large all-sources chat context (e.g. ~72K tokens for a 26-source
        notebook) failed with context_length_exceeded even on a 64 GB Mac
        whose model (Hermes-3, 131072 native) could easily hold it. An
        explicit DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX (or DEEPER_NOTEBOOK_CHAT_LLM_CTX) always wins
        over this default — see _resolve_chat_llm_n_ctx.
        """
        default = 32768
        if sys.platform != "darwin":
            return default
        try:
            names = os.sysconf_names
            if "SC_PHYS_PAGES" not in names or "SC_PAGE_SIZE" not in names:
                return default
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError):
            return default
        gib = total / (1024**3)
        if gib >= 56:
            return 98304
        if gib >= 40:
            return 65536
        if gib >= 28:
            return 49152
        return default

    # KV-cache cost for a typical 8B chat model: ~0.125 MiB per context token.
    _KV_MIB_PER_TOKEN = 0.125

    @staticmethod
    def _available_ram_bytes() -> "int | None":
        """v0.8.67l — Best-effort AVAILABLE physical RAM on darwin (via vm_stat).
        Returns None when it can't be determined, so callers skip the pressure
        backoff rather than guess. Counts free + inactive + speculative +
        purgeable pages (the memory the OS can hand to a new allocation)."""
        if sys.platform != "darwin":
            return None
        try:
            out = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=3
            ).stdout
        except Exception:
            return None
        m = re.search(r"page size of (\d+) bytes", out)
        page = int(m.group(1)) if m else 4096

        def _pages(label: str) -> int:
            mm = re.search(rf"{re.escape(label)}:\s+(\d+)\.", out)
            return int(mm.group(1)) if mm else 0

        pages = (
            _pages("Pages free")
            + _pages("Pages inactive")
            + _pages("Pages speculative")
            + _pages("Pages purgeable")
        )
        return pages * page if pages > 0 else None

    @staticmethod
    def _pressure_adjusted_ctx_max(tier: int, avail_bytes: "int | None") -> int:
        """v0.8.67l — Step the context ceiling DOWN through the tiers while its
        KV cache (~0.125 MiB/token) plus ~5 GiB for model weights + OS headroom
        won't fit in `avail_bytes`. Prevents launching the chat sidecar into a
        swap storm when the machine is already memory-saturated. No-op when
        `avail_bytes` is None/unknown or already roomy — so on a healthy machine
        the total-RAM tier from _default_ctx_max() is returned unchanged."""
        if not avail_bytes or avail_bytes <= 0:
            return tier
        headroom = 5 * 1024**3
        for cand in (98304, 65536, 49152, 32768):
            if cand > tier:
                continue
            kv = int(cand * Supervisor._KV_MIB_PER_TOKEN * 1024 * 1024)
            if kv + headroom <= avail_bytes:
                return cand
        return 32768  # floor — smallest window we offer

    def _resolve_chat_llm_n_ctx(self) -> int:
        """v0.8.7 — Resolve the chat-LLM n_ctx ONCE, before session_env
        is built, so DEEPER_NOTEBOOK_LOCAL_N_CTX can carry the actual
        ceiling the sidecar will use.

        Precedence (mirrors the original v0.7.206 logic that used to
        live inline in _spawn_llamacpp_chat):
          1. `DEEPER_NOTEBOOK_CHAT_LLM_CTX` explicit override (validated as int ≥ 512).
          2. GGUF metadata `<arch>.context_length`, capped at
             `DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX` (default 32768).
          3. The cap value if either the env or the GGUF read fails.

        Returns 0 only when no chat_llm_path is configured at all
        (memory writer + chat sidecar both skip in that case; the 0
        sentinel lets callers detect "no chat LLM" without separate
        flags). Any positive return is safe to pass through to
        `llama_cpp.server --n_ctx <N>`.
        """
        # v0.8.67i — RAM-aware default ceiling (was hardcoded 32768). An
        # explicit DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX still wins; otherwise scale to total
        # unified memory so capable Macs chat over large source selections
        # without the user setting any env var.
        # v0.8.67l — then step DOWN if AVAILABLE memory right now can't hold the
        # tier's KV cache + headroom (avoids a swap storm when launching while
        # the machine is already saturated). No-op on a healthy machine.
        _fallback = self._pressure_adjusted_ctx_max(
            self._default_ctx_max(), self._available_ram_bytes()
        )
        try:
            _env_ctx_max = resolve_env("DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX")
            ctx_max = int(_env_ctx_max) if _env_ctx_max else _fallback
            if ctx_max < 512:
                ctx_max = _fallback
        except ValueError:
            ctx_max = _fallback

        env_n_ctx = resolve_env("DEEPER_NOTEBOOK_CHAT_LLM_CTX")
        if env_n_ctx:
            # Explicit user override — validate but otherwise trust.
            try:
                n_ctx_int = int(env_n_ctx)
                if n_ctx_int < 512:
                    log.warning(
                        "DEEPER_NOTEBOOK_CHAT_LLM_CTX=%s too low (<512); using %d instead",
                        env_n_ctx,
                        ctx_max,
                    )
                    n_ctx_int = ctx_max
            except ValueError:
                log.warning(
                    "DEEPER_NOTEBOOK_CHAT_LLM_CTX=%r is not an int; using %d",
                    env_n_ctx,
                    ctx_max,
                )
                n_ctx_int = ctx_max
            return n_ctx_int

        # No explicit override — try to read the GGUF's native context
        # length and use min(native, ctx_max). If no chat_llm_path is
        # configured, fall back to ctx_max so DEEPER_NOTEBOOK_LOCAL_N_CTX
        # in session_env still gets a sane value (the chat sidecar
        # won't actually spawn, but the router won't crash trying to
        # cast a stale env value either).
        if self.chat_llm_path is None or not self.chat_llm_path.exists():
            return ctx_max

        n_ctx_int = self._detect_gguf_context_length(
            self.chat_llm_path, fallback=ctx_max
        )
        n_ctx_int = min(n_ctx_int, ctx_max)
        log.info(
            "llamacpp_chat: n_ctx=%d (auto-detected, capped at "
            "DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX=%d). Override with DEEPER_NOTEBOOK_CHAT_LLM_CTX.",
            n_ctx_int,
            ctx_max,
        )
        return n_ctx_int

    def _spawn_llamacpp_chat(self, port: int) -> None:
        """Second llama-server, this one serving a chat-capable GGUF.

        Needed by mem0's writer (extract_turn / summarize_session) for
        Hermes-3-style tool calling. ~5 GB RAM at runtime.
        """
        # v0.7.67 — log a clear warning when we skip rather than
        # silently returning. The previous comment said "memory writer
        # will simply no-op" — true, but the user opening the bundled
        # app with no chat GGUF will then wonder why "memory" features
        # never produce facts. A single WARNING line in the launcher
        # log identifies the cause immediately. Each cause is logged
        # distinctly so the user can act on it (drop a GGUF in the
        # configured path vs. download one).
        if self.chat_llm_path is None:
            log.warning(
                "Skipping llamacpp_chat: no chat GGUF configured "
                "(chat_llm_path is None). Memory writer (fact "
                "extraction + session summaries) will no-op. "
                "Configure a chat model via the launcher config to "
                "enable it."
            )
            return
        if not self.chat_llm_path.exists():
            log.warning(
                "Skipping llamacpp_chat: configured GGUF not found at "
                "%s. Memory writer will no-op. Download a chat-capable "
                "GGUF (e.g. Hermes-3, Qwen2.5-Instruct, Llama-3.2) to "
                "that path to enable it.",
                self.chat_llm_path,
            )
            return
        # v0.7.8 — n_ctx is configurable via env var. Previous hardcoded 8192
        # capped EVERY chat session at 8k tokens regardless of the model's
        # actual capability. Modern local models commonly support much more:
        # Qwen 2.5/3.x at 32k-131k, Hermes-3 at 131k, Mistral-7B at 32k,
        # Llama-3.2 at 131k.
        #
        # v0.7.206 — Two changes here, both motivated by a user-reported
        # 400 "context_length_exceeded" failure on local chat:
        #
        #   1. **Bump default 16384 → 32768.** The previous 16k default was
        #      set when gemma-2-9b / codellama-13b were the common local
        #      models (8k/16k native contexts). The actual install base now
        #      runs Hermes-3 / Qwen2.5 / Llama-3.2 — all of which natively
        #      support 32k–131k. The 16k cap was the SERVER side; a user
        #      chatting with 2-3 selected sources can easily exceed 21k
        #      tokens combined (system prompt + history + sources). 32k
        #      gives 11k of headroom over the v0.7.205 failure case while
        #      only doubling KV-cache RAM (~2 GB → ~4 GB for an 8B model).
        #
        #   2. **Auto-detect from GGUF metadata** when DEEPER_NOTEBOOK_CHAT_LLM_CTX is
        #      not explicitly set. The GGUF file's `llama.context_length`
        #      metadata field tells us the model's native max. Cap that at
        #      `DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX` (default 32768) to avoid runaway RAM
        #      on models that advertise 131k. Users who explicitly set
        #      DEEPER_NOTEBOOK_CHAT_LLM_CTX retain full control.
        #
        # Constrained-hardware users with low VRAM can lower via
        # `DEEPER_NOTEBOOK_CHAT_LLM_CTX=8192`; users on a Mac Studio with 64GB+ can
        # raise via `DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX=65536` (or set
        # `DEEPER_NOTEBOOK_CHAT_LLM_CTX=65536` to skip auto-detection entirely).
        # v0.8.7 — n_ctx is now resolved once in start_all() via
        # _resolve_chat_llm_n_ctx() and stored on self.chat_llm_n_ctx so
        # session_env can export it as DEEPER_NOTEBOOK_LOCAL_N_CTX before
        # any subprocess is spawned. The original v0.7.206 resolution
        # logic lives in _resolve_chat_llm_n_ctx; this method just
        # reads the cached result.
        n_ctx = str(self.chat_llm_n_ctx) if self.chat_llm_n_ctx > 0 else "32768"

        args = [
            str(self.venv_python),
            "-m",
            "llama_cpp.server",
            "--model",
            str(self.chat_llm_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--n_ctx",
            n_ctx,
            # v0.8.67c — offload the model to the GPU (Metal on Apple Silicon).
            # Without this, llama_cpp.server defaults to n_gpu_layers=0 and the
            # whole model runs on CPU — so slow the chat never returns a
            # completion (the silent-chatbot bug). -1 = all layers on macOS.
            "--n_gpu_layers",
            _n_gpu_layers("DEEPER_NOTEBOOK_CHAT_LLM_N_GPU_LAYERS"),
        ]

        # v0.8.3 — Speculative decoding via --model_draft. Originally
        # shipped as v0.8.2 Item A but wired to LlamaCppProvider in
        # desktop/providers/llamacpp.py — which auto_register's v0.7.193
        # fix had already deprecated as a production spawn path.
        # Operators following the v0.8.2 docs were setting the env vars
        # correctly but seeing no speedup because this spawn (the LIVE
        # one) never read them. Now wired here. Env var names preserved
        # for backward compat.
        #
        # Skipping rules (same as the original llamacpp.py guard):
        #   - Missing DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH = no flag,
        #     no speedup, same as today (sidecar default behavior).
        #   - Path doesn't exist or is <1MB (Git-LFS pointer / aborted
        #     download) = silently skip rather than crash; main model
        #     still loads, operator just doesn't get the speedup.
        #   - n_predict knob without a draft path = dropped silently
        #     (llama_cpp.server would reject a bare --n_predict_draft).
        _MIN_GGUF_BYTES = 1 * 1024 * 1024
        _draft_path_str = (
            resolve_env("DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH", "") or ""
        ).strip()
        if _draft_path_str:
            from pathlib import Path as _Path

            _draft_path = _Path(_draft_path_str)
            if _draft_path.is_file() and _draft_path.stat().st_size >= _MIN_GGUF_BYTES:
                args.extend(["--model_draft", str(_draft_path)])
                # Only emit --n_predict_draft when the draft model was
                # accepted above; bare n_predict without a draft model
                # would make llama_cpp.server reject the argv at parse.
                _draft_n_env = (
                    resolve_env(
                        "DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT",
                        "",
                    )
                    or ""
                ).strip()
                if _draft_n_env:
                    try:
                        _draft_n = int(_draft_n_env)
                        if _draft_n > 0:
                            args.extend(
                                [
                                    "--n_predict_draft",
                                    str(_draft_n),
                                ]
                            )
                    except ValueError:
                        # Stale or malformed env value — log and skip
                        # rather than crash the chat sidecar over a
                        # tuning knob.
                        log.warning(
                            "DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT=%r is "
                            "not an int; ignoring (--n_predict_draft "
                            "omitted; llama_cpp.server default applies)",
                            _draft_n_env,
                        )
                log.info(
                    "llamacpp_chat: speculative decoding enabled (--model_draft=%s)",
                    _draft_path,
                )
            else:
                log.warning(
                    "DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH=%s skipped: "
                    "file missing or <1MB (likely Git-LFS pointer or "
                    "aborted download). Chat sidecar starting without "
                    "speculative decoding.",
                    _draft_path,
                )

        self._spawn(args, cwd=self.upstream_root, name="llamacpp_chat")

    def _spawn_memory_retriever(self, port: int) -> None:
        args = [
            str(self.venv_python),
            "-m",
            "desktop_shims.memory_shim",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--surreal-url",
            self.session_env["SURREAL_URL"],
            "--embed-url",
            f"http://127.0.0.1:{self.embed_port}/v1" if self.embed_port else "",
            "--llm-url",
            f"http://127.0.0.1:{self.chat_llm_port}/v1" if self.chat_llm_port else "",
        ]
        self._spawn(args, cwd=self.upstream_root, name="memory")

    def _spawn_openchronicle_bridge(self, port: int) -> None:
        if not self.openchronicle_available:
            return
        # v0.7.197 — Honour OPENCHRONICLE_MCP_URL the same way the shim's
        # argparse default does. Before, the launcher hardcoded
        # `--mcp-url http://127.0.0.1:8742/mcp` on every spawn —
        # which OVERRODE the env-var default in
        # `openchronicle_shim.py`'s argparse. Users running
        # OpenChronicle on a non-default port (the documented use
        # case) couldn't reach it from ONP. The P1-MED-10 audit fix
        # in the shim was dead code as long as this launcher line
        # forced the default URL. Read the env now; fall back to the
        # same default the shim would have used.
        mcp_url = os.environ.get("OPENCHRONICLE_MCP_URL", "http://127.0.0.1:8742/mcp")
        args = [
            str(self.venv_python),
            "-m",
            "desktop_shims.openchronicle_shim",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mcp-url",
            mcp_url,
        ]
        self._spawn(args, cwd=self.upstream_root, name="openchronicle")
