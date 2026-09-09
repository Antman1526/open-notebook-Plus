"""v0.8.116 — Heartbeat + idle-timeout wrapper for streaming responses.

The three streaming endpoints (``/chat/stream``, ``/search/ask``,
``/sources/{id}/chat/sessions/{sid}/messages``) yield one frame per model
event and nothing in between. While a local model is loading or running a
long prefill the wire goes completely silent, so the client cannot tell
"still thinking" from "the daemon died". The frontend guard added in
v0.8.115 (``readWithIdleTimeout``) has to be generous (minutes) for that
reason, and it cannot see the case where the API process is alive but the
model call itself is hung.

This wrapper sits between the endpoint's generator and ``StreamingResponse``
and adds two things:

* **Heartbeat frames** every ``interval`` seconds of silence. The frame text
  is supplied by the caller so each wire format stays self-consistent
  (an SSE comment line for the ``data:`` streams, a typed JSON line for
  NDJSON). Any bytes reset the client-side idle timer, so the frontend
  default can drop to well under a minute.
* **Server-side idle limit.** If the source produces nothing for
  ``idle_timeout`` seconds the wrapper emits the caller's error frame and
  stops, which also cancels the source so the model call is torn down
  instead of leaking. ``0`` disables the limit.

Cancellation semantics match the pre-wrapper behaviour: when the consumer
(``StreamingResponse``) closes this generator early because the client went
away, the pump task is cancelled and the source generator is closed, so the
source's ``finally`` blocks (session-lock release, memory extraction) still
run exactly as they did when ``StreamingResponse`` closed the source
directly. The queue is bounded so back-pressure is preserved: the source
cannot run arbitrarily far ahead of the network write.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from deeper_notebook.environment import resolve_env

DEFAULT_HEARTBEAT_SEC = 10.0
DEFAULT_IDLE_TIMEOUT_SEC = 300.0

# Wire-format heartbeat frames. SSE consumers only parse ``data:`` lines, so
# a comment line is invisible to them by construction; the NDJSON consumer
# is taught to skip ``{"type":"heartbeat"}`` explicitly.
SSE_HEARTBEAT_FRAME = ": heartbeat\n\n"
NDJSON_HEARTBEAT_FRAME = '{"type": "heartbeat"}\n'

_QUEUE_MAXSIZE = 256


def _read_seconds(name: str, default: float) -> float:
    raw = (resolve_env(name, str(default)) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    if value < 0:
        return default
    return value


def resolve_heartbeat_interval_sec() -> float:
    """``DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC`` (default 10; ``0`` disables)."""
    return _read_seconds("DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC", DEFAULT_HEARTBEAT_SEC)


def resolve_stream_idle_timeout_sec() -> float:
    """``DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC`` (default 300; ``0`` disables)."""
    return _read_seconds("DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC", DEFAULT_IDLE_TIMEOUT_SEC)


def idle_timeout_message(idle_seconds: float) -> str:
    """User-facing, provider-agnostic text for the idle-timeout error frame."""
    return (
        f"The model produced no output for {int(idle_seconds)}s. "
        "Check that your local model server (Ollama, llama.cpp, MLX) is "
        "still running, then try again."
    )


async def with_keepalive(
    source: AsyncIterator[str],
    *,
    heartbeat_frame: str,
    make_timeout_frame: Callable[[float], str],
    interval: float | None = None,
    idle_timeout: float | None = None,
) -> AsyncGenerator[str, None]:
    """Yield ``source``'s frames, inserting heartbeats during silence and
    terminating with an error frame if silence exceeds ``idle_timeout``.

    ``interval`` / ``idle_timeout`` default to the environment-resolved
    values. ``interval == 0`` passes frames through with no heartbeat and
    no idle limit (the wrapper still owns cancellation).
    """
    interval = resolve_heartbeat_interval_sec() if interval is None else interval
    idle_timeout = (
        resolve_stream_idle_timeout_sec() if idle_timeout is None else idle_timeout
    )

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    async def _pump() -> None:
        try:
            async for frame in source:
                await queue.put(("frame", frame))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — forwarded to the consumer
            await queue.put(("error", exc))
            return
        await queue.put(("done", None))

    pump_task = asyncio.create_task(_pump())
    last_frame_at = time.monotonic()

    try:
        while True:
            if interval <= 0:
                kind, payload = await queue.get()
            else:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    silent_for = time.monotonic() - last_frame_at
                    if idle_timeout > 0 and silent_for >= idle_timeout:
                        yield make_timeout_frame(silent_for)
                        return
                    yield heartbeat_frame
                    continue

            if kind == "frame":
                last_frame_at = time.monotonic()
                yield payload
            elif kind == "done":
                return
            else:
                raise payload
    finally:
        if not pump_task.done():
            pump_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pump_task
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with suppress(Exception):
                await aclose()
