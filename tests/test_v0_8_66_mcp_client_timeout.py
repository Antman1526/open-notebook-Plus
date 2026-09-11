"""v0.8.66 — MCP client RPC timeout (MCP-1) + optional auth headers (MCP-4)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

import deeper_notebook.mcp.client as cm


@pytest.mark.parametrize(
    "val,expected",
    [
        (None, 30.0),
        ("5", 5.0),
        ("0", 30.0),
        ("-1", 30.0),
        ("x", 30.0),
        ("", 30.0),
    ],
)
def test_rpc_timeout_parsing(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("DEEPER_NOTEBOOK_MCP_RPC_TIMEOUT_SEC", raising=False)
    else:
        monkeypatch.setenv("DEEPER_NOTEBOOK_MCP_RPC_TIMEOUT_SEC", val)
    assert cm._rpc_timeout() == expected


def test_env_headers_parsing(monkeypatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_MCP_AUTH_HEADER", raising=False)
    assert cm._env_headers() is None
    monkeypatch.setenv(
        "DEEPER_NOTEBOOK_MCP_AUTH_HEADER", "Authorization: Bearer abc123"
    )
    assert cm._env_headers() == {"Authorization": "Bearer abc123"}
    monkeypatch.setenv("DEEPER_NOTEBOOK_MCP_AUTH_HEADER", "no-colon-here")
    assert cm._env_headers() is None


@pytest.mark.asyncio
async def test_rpc_timeout_bounds_a_hung_server(monkeypatch):
    """A server that never responds must NOT pin the caller — the RPC timeout
    cancels it. (Pre-MCP-1: discovery/test could hang ~300s.)"""
    monkeypatch.setenv("DEEPER_NOTEBOOK_MCP_RPC_TIMEOUT_SEC", "0.2")

    @asynccontextmanager
    async def _hung_session(url, headers=None, **_kwargs):
        await asyncio.sleep(10)  # never returns within the timeout
        yield None  # pragma: no cover

    monkeypatch.setattr(cm, "_open_session", _hung_session)
    client = cm.MCPClient(url="http://127.0.0.1:9/mcp")
    with pytest.raises(asyncio.TimeoutError):
        await client.list_tool_names()


@pytest.mark.asyncio
async def test_headers_threaded_to_session(monkeypatch):
    """An explicit MCPClient(headers=...) (or the env header) reaches
    _open_session."""
    monkeypatch.delenv("DEEPER_NOTEBOOK_MCP_AUTH_HEADER", raising=False)
    seen = {}

    @asynccontextmanager
    async def _capture(url, headers=None, **_kwargs):
        seen["headers"] = headers

        class _S:
            async def list_tools(self_):
                return type("R", (), {"tools": []})()

        yield _S()

    monkeypatch.setattr(cm, "_open_session", _capture)
    client = cm.MCPClient(url="http://x", headers={"Authorization": "Bearer T"})
    await client.list_tool_names()
    assert seen["headers"] == {"Authorization": "Bearer T"}
