"""Phase 1 — Active health probes for each local-model sidecar.

Distinct from /healthz/deep (which is the API's own readiness):
this module probes the local llama-cpp / whisper / piper / memory
shims to verify they actually respond, not just that their port
is bound.
"""

from __future__ import annotations

import socket
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, NotRequired, TypedDict

import httpx


class HealthResult(TypedDict):
    name: str
    credential_id: NotRequired[str]
    status: Literal["healthy", "unhealthy", "not_configured", "unknown"]
    detail: str | None
    latency_ms: float | None
    runtime: NotRequired[str]
    endpoint: NotRequired[str]
    probe_path: NotRequired[str]


# Phase 1 Task 2 — bounded probe budgets so a wedged sidecar can't
# hang the launch-time health sweep. Same structured-Timeout shape
# as credentials_service uses for discovery probes.
_PROBE_TIMEOUT = httpx.Timeout(
    connect=2.0,
    read=5.0,
    write=2.0,
    pool=2.0,
)
# v0.8.85 — Ollama gets a longer read budget. A large or freshly-upgraded
# store makes /api/tags legitimately take 10-15s while the server is fine
# (measured live: HTTP 200 in 9.8s during a store inventory); a 5s read
# timeout flapped the health badge on every slow scan.
_OLLAMA_PROBE_TIMEOUT = httpx.Timeout(
    connect=2.0,
    read=20.0,
    write=2.0,
    pool=2.0,
)
_MAX_CONCURRENT_PROBES = 4


def probe_local_model(
    *,
    name: str,
    kind: str,
    base_url: str,
) -> HealthResult:
    """Probe a single local sidecar. Returns a HealthResult dict.

    Phase 1 Task 1: detects port-0 placeholders.
    Phase 1 Task 2: live HTTP probe for openai_compatible kind.
    """
    if ":0/" in base_url or base_url.endswith(":0"):
        return {
            "name": name,
            "status": "not_configured",
            "detail": "port not allocated this session",
            "latency_ms": None,
            "runtime": _runtime_label(name=name, kind=kind),
            "endpoint": base_url.rstrip("/"),
        }
    if kind == "openai_compatible":
        return _probe_openai_compatible(name=name, base_url=base_url)
    if kind == "ollama":
        return _probe_ollama(name=name, base_url=base_url)
    return {
        "name": name,
        "status": "unknown",
        "detail": f"no probe for kind={kind!r}",
        "latency_ms": None,
        "runtime": _runtime_label(name=name, kind=kind),
        "endpoint": base_url.rstrip("/"),
    }


def _runtime_label(*, name: str, kind: str) -> str:
    lower_name = name.lower()
    if kind == "ollama":
        return "ollama"
    if "lmstudio" in lower_name or "lm studio" in lower_name:
        return "LM Studio"
    if "mlx" in lower_name or "osaurus" in lower_name:
        return "MLX"
    if "llama.cpp" in lower_name or "gguf" in lower_name:
        return "llama.cpp"
    if kind == "openai_compatible":
        return "OpenAI-compatible"
    return kind


def _port_accepts_connection(base_url: str, timeout: float = 2.0) -> bool:
    """v0.8.84 — bare TCP reachability, for servers whose HTTP probe path
    wedges while the server itself keeps working (mlx-lm 0.31's /v1/models)."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_openai_compatible(*, name: str, base_url: str) -> HealthResult:
    """Hit `{base_url}/models` — the standard OpenAI-compatible
    discovery endpoint. Returns healthy with latency + first few
    model names on 200; unhealthy with the status code or
    connect-error detail otherwise."""
    url = f"{base_url.rstrip('/')}/models"
    start = time.monotonic()
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            resp = client.get(url)
            latency_ms = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                # v0.8.84 — mlx-lm 0.31's server answers GET /v1/models with
                # HTTP 200 and an EMPTY body (verified live: 0 bytes both
                # before and after a successful chat completion on the same
                # server). A 200 from the endpoint proves the server is up,
                # which is what this probe measures — parse the body
                # best-effort instead of letting JSONDecodeError mark a
                # working server unhealthy.
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                models = [m.get("id", "?") for m in data.get("data", [])]
                detail = ", ".join(models[:3]) if models else "no models listed"
                return {
                    "name": name,
                    "status": "healthy",
                    "detail": detail,
                    "latency_ms": latency_ms,
                    "runtime": _runtime_label(
                        name=name,
                        kind="openai_compatible",
                    ),
                    "endpoint": base_url.rstrip("/"),
                    "probe_path": "/models",
                }
            return {
                "name": name,
                "status": "unhealthy",
                "detail": f"HTTP {resp.status_code}",
                "latency_ms": latency_ms,
                "runtime": _runtime_label(
                    name=name,
                    kind="openai_compatible",
                ),
                "endpoint": base_url.rstrip("/"),
                "probe_path": "/models",
            }
    except httpx.ConnectError as exc:
        return {
            "name": name,
            "status": "unhealthy",
            "detail": f"connect refused: {exc}",
            "latency_ms": None,
            "runtime": _runtime_label(name=name, kind="openai_compatible"),
            "endpoint": base_url.rstrip("/"),
            "probe_path": "/models",
        }
    except httpx.TimeoutException:
        # v0.8.84 — mlx-lm 0.31's server stops completing GET /v1/models after
        # its first chat completion (measured live: instant 200 before any
        # generation, then an indefinite hang on the same endpoint while
        # completions keep working). A read timeout therefore does not mean
        # the server is down. Fall back to the question this probe actually
        # asks — is anything accepting connections on that port?
        if _port_accepts_connection(base_url):
            return {
                "name": name,
                "status": "healthy",
                "detail": "endpoint reachable (models list timed out)",
                "latency_ms": None,
                "runtime": _runtime_label(name=name, kind="openai_compatible"),
                "endpoint": base_url.rstrip("/"),
                "probe_path": "/models",
            }
        return {
            "name": name,
            "status": "unhealthy",
            "detail": "timed out and port not accepting connections",
            "latency_ms": None,
            "runtime": _runtime_label(name=name, kind="openai_compatible"),
            "endpoint": base_url.rstrip("/"),
            "probe_path": "/models",
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "unhealthy",
            "detail": f"{type(exc).__name__}: {exc}",
            "latency_ms": None,
            "runtime": _runtime_label(name=name, kind="openai_compatible"),
            "endpoint": base_url.rstrip("/"),
            "probe_path": "/models",
        }


def _probe_ollama(*, name: str, base_url: str) -> HealthResult:
    """Hit Ollama's local `/api/tags` endpoint and summarize installed
    models. Ollama is not OpenAI-compatible by default, so probing
    `/models` would falsely report a healthy local runtime as down."""
    clean_base = base_url.rstrip("/")
    url = f"{clean_base}/api/tags"
    start = time.monotonic()
    try:
        with httpx.Client(timeout=_OLLAMA_PROBE_TIMEOUT) as client:
            resp = client.get(url)
            latency_ms = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "?") for m in data.get("models", [])]
                detail = ", ".join(models[:3]) if models else "no models listed"
                return {
                    "name": name,
                    "status": "healthy",
                    "detail": detail,
                    "latency_ms": latency_ms,
                    "runtime": "ollama",
                    "endpoint": clean_base,
                    "probe_path": "/api/tags",
                }
            return {
                "name": name,
                "status": "unhealthy",
                "detail": f"HTTP {resp.status_code}",
                "latency_ms": latency_ms,
                "runtime": "ollama",
                "endpoint": clean_base,
                "probe_path": "/api/tags",
            }
    except httpx.ConnectError as exc:
        return {
            "name": name,
            "status": "unhealthy",
            "detail": f"connect refused: {exc}",
            "latency_ms": None,
            "runtime": "ollama",
            "endpoint": clean_base,
            "probe_path": "/api/tags",
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "unhealthy",
            "detail": f"{type(exc).__name__}: {exc}",
            "latency_ms": None,
            "runtime": "ollama",
            "endpoint": clean_base,
            "probe_path": "/api/tags",
        }


def probe_all_local_models(credentials: list[dict]) -> list[HealthResult]:
    """Probe every local-sidecar credential with bounded concurrency.

    `ThreadPoolExecutor.map` preserves input order, so the frontend gets stable
    rows while slow/dead runtimes no longer serialize the entire sweep.
    """
    if not credentials:
        return []

    def _probe(cred: dict) -> HealthResult:
        result = probe_local_model(
            name=cred["name"],
            kind=cred["kind"],
            base_url=cred["base_url"],
        )
        credential_id = cred.get("credential_id")
        if isinstance(credential_id, str) and credential_id:
            result["credential_id"] = credential_id
        return result

    max_workers = min(len(credentials), _MAX_CONCURRENT_PROBES)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_probe, credentials))
