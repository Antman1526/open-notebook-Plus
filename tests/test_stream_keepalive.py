"""v0.8.116 — Tests for api.utils.stream_keepalive.with_keepalive."""

from __future__ import annotations

import asyncio

import pytest

from api.utils.stream_keepalive import (
    NDJSON_HEARTBEAT_FRAME,
    SSE_HEARTBEAT_FRAME,
    idle_timeout_message,
    with_keepalive,
)


def _timeout_frame(seconds: float) -> str:
    return f"TIMEOUT:{int(seconds)}\n"


async def _collect(agen) -> list[str]:
    out: list[str] = []
    async for frame in agen:
        out.append(frame)
    return out


async def test_passthrough_without_heartbeats_when_source_is_fast():
    async def source():
        for i in range(3):
            yield f"frame{i}\n"

    frames = await _collect(
        with_keepalive(
            source(),
            heartbeat_frame=SSE_HEARTBEAT_FRAME,
            make_timeout_frame=_timeout_frame,
            interval=0.5,
            idle_timeout=5,
        )
    )
    assert frames == ["frame0\n", "frame1\n", "frame2\n"]


async def test_heartbeat_emitted_during_silence_then_source_resumes():
    async def source():
        yield "a\n"
        await asyncio.sleep(0.35)
        yield "b\n"

    frames = await _collect(
        with_keepalive(
            source(),
            heartbeat_frame=NDJSON_HEARTBEAT_FRAME,
            make_timeout_frame=_timeout_frame,
            interval=0.1,
            idle_timeout=5,
        )
    )
    assert frames[0] == "a\n"
    assert frames[-1] == "b\n"
    heartbeats = [f for f in frames if f == NDJSON_HEARTBEAT_FRAME]
    # 0.35s of silence at a 0.1s interval → at least 2 heartbeats, and the
    # data frames must be preserved in order around them.
    assert len(heartbeats) >= 2
    assert [f for f in frames if f != NDJSON_HEARTBEAT_FRAME] == ["a\n", "b\n"]


async def test_idle_timeout_emits_error_frame_and_cancels_source():
    cleaned_up = asyncio.Event()

    async def source():
        try:
            yield "start\n"
            await asyncio.sleep(60)  # never produces again
            yield "unreachable\n"
        finally:
            cleaned_up.set()

    frames = await _collect(
        with_keepalive(
            source(),
            heartbeat_frame=SSE_HEARTBEAT_FRAME,
            make_timeout_frame=_timeout_frame,
            interval=0.05,
            idle_timeout=0.2,
        )
    )
    assert frames[0] == "start\n"
    assert frames[-1].startswith("TIMEOUT:")
    assert "unreachable\n" not in frames
    assert SSE_HEARTBEAT_FRAME in frames
    # The model call behind the source must be torn down, not leaked.
    await asyncio.wait_for(cleaned_up.wait(), timeout=1)


async def test_idle_timeout_disabled_with_zero_keeps_heartbeating():
    async def source():
        yield "a\n"
        await asyncio.sleep(0.3)
        yield "b\n"

    frames = await _collect(
        with_keepalive(
            source(),
            heartbeat_frame=SSE_HEARTBEAT_FRAME,
            make_timeout_frame=_timeout_frame,
            interval=0.05,
            idle_timeout=0,
        )
    )
    assert frames[-1] == "b\n"
    assert not any(f.startswith("TIMEOUT:") for f in frames)
    assert frames.count(SSE_HEARTBEAT_FRAME) >= 3


async def test_consumer_closing_early_cancels_source():
    """StreamingResponse closes the wrapper when the client disconnects;
    the source's finally (session-lock release etc.) must still run."""
    cleaned_up = asyncio.Event()

    async def source():
        try:
            yield "first\n"
            await asyncio.sleep(60)
            yield "never\n"
        finally:
            cleaned_up.set()

    wrapped = with_keepalive(
        source(),
        heartbeat_frame=SSE_HEARTBEAT_FRAME,
        make_timeout_frame=_timeout_frame,
        interval=0.05,
        idle_timeout=0,
    )
    first = await wrapped.__anext__()
    assert first == "first\n"
    await wrapped.aclose()
    await asyncio.wait_for(cleaned_up.wait(), timeout=1)


async def test_source_exception_propagates_to_consumer():
    async def source():
        yield "ok\n"
        raise RuntimeError("boom")

    wrapped = with_keepalive(
        source(),
        heartbeat_frame=SSE_HEARTBEAT_FRAME,
        make_timeout_frame=_timeout_frame,
        interval=0.05,
        idle_timeout=0,
    )
    assert await wrapped.__anext__() == "ok\n"
    with pytest.raises(RuntimeError, match="boom"):
        await wrapped.__anext__()


async def test_interval_zero_is_plain_passthrough():
    async def source():
        yield "x\n"
        await asyncio.sleep(0.15)
        yield "y\n"

    frames = await _collect(
        with_keepalive(
            source(),
            heartbeat_frame=SSE_HEARTBEAT_FRAME,
            make_timeout_frame=_timeout_frame,
            interval=0,
            idle_timeout=0.05,
        )
    )
    assert frames == ["x\n", "y\n"]


def test_idle_timeout_message_names_local_daemons():
    msg = idle_timeout_message(42.7)
    assert "42s" in msg
    assert "Ollama" in msg
