"""Phase 2 — MCP server client + chat-graph integration.

Task 9 additions (lines below the original 3 tests):
  * test_mcp_router_list_and_create  — GET /api/mcp returns [] initially;
    POST /api/mcp 201 with the new record.
  * test_mcp_router_duplicate_name_409 — second POST with the same name
    must return 409, not 500.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_mcp_tool_cache():
    """v0.8.12 — _resolve_chat_tools added a TTL cache for MCP
    list_tools_full discovery (keyed by server URL). Cross-test
    pollution: a test that registers `http://x` and then poisons it
    to empty would leave that empty result cached for any later test
    in this file using the same URL. The discovery_fails test poisons
    `http://x` to [] which then made test_call_model_with_messages_
    executes_mcp_tool_calls see an empty tool list (loop never enters,
    call_count==1 instead of 2). Clear before AND after every test
    in this file."""
    try:
        from deeper_notebook.graphs.chat import _clear_tool_discovery_cache
    except ImportError:
        yield
        return
    _clear_tool_discovery_cache()
    yield
    _clear_tool_discovery_cache()


def test_mcp_client_call_tool_handles_text_image_and_resource_blocks(monkeypatch):
    """v0.8.13 — MCPClient.call_tool must surface ALL content blocks
    from the MCP response, not just the first, and preserve type +
    mime type. Pre-v0.8.13 only the first block was returned and
    non-text content was either missing its mime type
    (ImageContent → no `mimeType` field on the return) or silently
    lost (EmbeddedResource → returned None)."""
    import asyncio

    from deeper_notebook.mcp.client import MCPClient

    # Fake content block shapes — mirror the public attrs from mcp's
    # TextContent / ImageContent / EmbeddedResource without needing
    # the real mcp package types.
    class _Text:
        def __init__(self, text):
            self.text = text

    class _Image:
        def __init__(self, data, mime):
            self.data, self.mimeType = data, mime

    class _Resource:
        def __init__(self, uri, mime, text=None, blob=None):
            class _R:
                pass

            r = _R()
            r.uri, r.mimeType, r.text, r.blob = uri, mime, text, blob
            self.resource = r

    class _Result:
        def __init__(self, blocks):
            self.content = blocks

    captured_session = {}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_tool(self, name, arguments=None):
            captured_session["name"] = name
            captured_session["arguments"] = arguments
            return _Result(
                [
                    _Text("Hello from search."),
                    _Image("AAAA" * 256, "image/png"),  # ~768 bytes
                    _Resource(
                        uri="file:///tmp/a.pdf",
                        mime="application/pdf",
                        blob="BBBB" * 1024,
                    ),
                    _Text("Trailing note."),
                ]
            )

    monkeypatch.setattr(
        "deeper_notebook.mcp.client._open_session",
        lambda url, headers=None, **_kwargs: _FakeSession(),
    )

    client = MCPClient(url="http://x")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.call_tool("search", {"q": "x"}))
    finally:
        loop.close()

    # Top-level text is the concatenation of all readable text
    assert "Hello from search." in result["text"]
    assert "Trailing note." in result["text"]
    # Image and resource summarised inline so the LLM at least sees
    # something arrived
    assert "[image: image/png" in result["text"]
    assert "[resource: file:///tmp/a.pdf" in result["text"]

    # Full block list preserved for the pill popover to render rich
    blocks = result["blocks"]
    assert len(blocks) == 4
    assert blocks[0] == {"type": "text", "text": "Hello from search."}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["mime_type"] == "image/png"
    assert blocks[1]["bytes"] > 0
    assert blocks[2]["type"] == "resource"
    assert blocks[2]["uri"] == "file:///tmp/a.pdf"
    assert blocks[2]["mime_type"] == "application/pdf"
    assert blocks[2]["bytes"] > 0
    assert blocks[3] == {"type": "text", "text": "Trailing note."}


def test_mcp_client_call_tool_empty_result_safe(monkeypatch):
    """v0.8.13 — empty content list must return text="" + blocks=[]
    rather than KeyError'ing on the chat-graph side."""
    import asyncio

    from deeper_notebook.mcp.client import MCPClient

    class _Result:
        content = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_tool(self, name, arguments=None):
            return _Result()

    monkeypatch.setattr(
        "deeper_notebook.mcp.client._open_session",
        lambda url, headers=None, **_kwargs: _FakeSession(),
    )

    client = MCPClient(url="http://x")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.call_tool("noop", {}))
    finally:
        loop.close()
    assert result["text"] == ""
    assert result["blocks"] == []


def test_mcp_client_lists_tools_via_streamable_http(monkeypatch):
    """Given a working streamable-http MCP server URL, the client
    must `list_tools()` and return the discovered tool names."""
    from deeper_notebook.mcp.client import MCPClient

    fake_tools = [
        {"name": "web_search", "description": "Search the web"},
        {"name": "fetch_url", "description": "Fetch a URL"},
    ]

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def list_tools(self):
            return type(
                "X",
                (),
                {
                    "tools": [
                        type(
                            "T",
                            (),
                            {"name": t["name"], "description": t["description"]},
                        )()
                        for t in fake_tools
                    ]
                },
            )()

    monkeypatch.setattr(
        "deeper_notebook.mcp.client._open_session",
        lambda url, headers=None, **_kwargs: FakeSession(),
    )
    client = MCPClient(url="http://127.0.0.1:8742/mcp")
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        names = loop.run_until_complete(client.list_tool_names())
    finally:
        loop.close()
    assert names == ["web_search", "fetch_url"]


def test_chat_graph_exposes_mcp_tools_when_enabled(monkeypatch):
    """v0.8.10 — when at least one MCP server is enabled, the chat
    graph's tool registry must contain `mcp_<remote_name>` for every
    tool the server exposes. Pre-v0.8.10 this asserted `mcp_search`
    and `mcp_fetch` (the hardcoded names), which silently broke any
    server whose actual tools were named differently — gbrain
    exposes `search`/`think`/`find_trajectory`, not `web_search`/
    `fetch_url`."""
    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        lambda: __import__("asyncio").Future(),
    )
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                # v0.8.10 — bypass network discovery for the unit test
                force_tool_names=["web_search", "fetch_url"],
            )
        )
    finally:
        loop.close()
    tool_names = [t.name for t in tools]
    # Names are prefixed with `mcp_` so the LLM sees them as MCP tools
    # and not as native LangChain tools (avoids confusion when the
    # graph also binds local tools in the future).
    assert "mcp_web_search" in tool_names
    assert "mcp_fetch_url" in tool_names


def test_chat_graph_binds_gbrain_style_tool_names(monkeypatch):
    """v0.8.10 — regression for the gbrain integration documented in
    v0.8.2 Item B. gbrain exposes `search`, `think`,
    `find_trajectory`, etc. Pre-v0.8.10 the chat would call
    `web_search` (which gbrain doesn't expose) and the tool would
    fail every turn. Post-fix, each discovered remote name becomes a
    `mcp_<remote_name>` LangChain Tool."""
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "gbrain",
                        "url": "http://localhost:8742/mcp",
                        "enabled": True,
                    }
                ],
                force_tool_names=["search", "think", "find_trajectory"],
            )
        )
    finally:
        loop.close()

    tool_names = [t.name for t in tools]
    assert "mcp_search" in tool_names
    assert "mcp_think" in tool_names
    assert "mcp_find_trajectory" in tool_names
    # And the v0.8.2 docs' promise that "mcp_search" works against
    # gbrain holds — because gbrain HAS a `search` tool that becomes
    # `mcp_search` after wrapping.


def test_chat_graph_builds_structured_tool_with_real_args_schema(monkeypatch):
    """v0.8.11 — `_resolve_chat_tools` must build `StructuredTool`s with
    a real `args_schema` Pydantic model derived from the MCP server's
    inputSchema. Pre-v0.8.11 it used plain `Tool` with no schema, so
    `bind_tools` told the LLM the function took a single `input: str`
    arg; the LLM had to guess the real arg names and routinely sent
    wrong/empty args. Post-fix, `bind_tools` sends the real arg names
    + types so the LLM formats calls correctly first try."""
    import asyncio

    from langchain_core.tools import StructuredTool

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search text"},
            "limit": {"type": "integer", "description": "Max results"},
        },
        "required": ["query"],
    }

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                force_tools_full=[
                    {
                        "name": "search",
                        "description": "Search the brain repo",
                        "input_schema": schema,
                    }
                ],
            )
        )
    finally:
        loop.close()

    assert len(tools) == 1
    tool = tools[0]
    # Must be a StructuredTool, not the old plain Tool — that's how
    # bind_tools learns about the real arg shape.
    assert isinstance(tool, StructuredTool), (
        f"v0.8.11: must be StructuredTool to carry args_schema; got {type(tool).__name__}"
    )
    assert tool.name == "mcp_search"
    assert tool.args_schema is not None
    # Pydantic v2 — model_fields exposes the declared fields.
    fields = tool.args_schema.model_fields
    assert "query" in fields, (
        f"v0.8.11: args_schema must include the server-declared 'query' "
        f"field; LangChain bind_tools needs it. Got fields: {list(fields)}"
    )
    assert "limit" in fields
    # 'query' is in required; field must NOT be Optional.
    # 'limit' is NOT required; field default should be None.
    assert fields["query"].is_required(), (
        "v0.8.11: required schema fields must be required on the model"
    )
    assert not fields["limit"].is_required(), (
        "v0.8.11: non-required schema fields must default to None"
    )


def test_resolve_chat_tools_handles_nullable_json_schema(monkeypatch):
    """v0.8.12 — JSON Schema's `"type": ["string", "null"]` nullable
    shape (real-world MCP servers use it) must build a valid optional
    Pydantic field. Pre-v0.8.12 the type_map.get(list) returned None
    and propagated as a broken field type."""
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    schema = {
        "type": "object",
        "properties": {
            # JSON-Schema-nullable string
            "query": {"type": ["string", "null"], "description": "Query or null"},
            # Plain optional string with explicit default
            "limit": {"type": "string", "default": "10"},
            # Only-null type (degenerate but legal)
            "stub": {"type": ["null"]},
        },
        "required": ["query"],  # required-but-nullable → still optional
    }

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                force_tools_full=[
                    {
                        "name": "nullable_search",
                        "description": "",
                        "input_schema": schema,
                    }
                ],
            )
        )
    finally:
        loop.close()

    assert len(tools) == 1
    fields = tools[0].args_schema.model_fields
    # Nullable required → optional in the model (key may be null)
    assert not fields["query"].is_required(), (
        "v0.8.12: ['string','null'] type makes the field optional even when in required"
    )
    # Default from JSON Schema is preserved
    assert fields["limit"].default == "10", (
        f"v0.8.12: JSON Schema default should propagate; got {fields['limit'].default!r}"
    )
    # ['null'] only → no error, stays optional
    assert "stub" in fields


def test_resolve_chat_tools_caches_discovery_across_calls(monkeypatch):
    """v0.8.12 — list_tools_full() must be cached for ~30s so each
    chat turn doesn't pay an MCP handshake. Pre-v0.8.12 every turn
    re-discovered tools (~50-500ms per turn). Cache key = server URL,
    TTL = 30s."""
    import asyncio

    from deeper_notebook.graphs.chat import (
        _clear_tool_discovery_cache,
        _resolve_chat_tools,
    )

    _clear_tool_discovery_cache()

    call_count = {"n": 0}

    async def fake_list_tools_full(self):
        call_count["n"] += 1
        return [
            {
                "name": "search",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_full,
    )

    async def fake_list_enabled():
        return [
            {
                "id": "mcp_server:1",
                "name": "test",
                "url": "http://CACHE-TEST-URL",
                "enabled": True,
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list_enabled,
    )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_resolve_chat_tools())
        loop.run_until_complete(_resolve_chat_tools())
        loop.run_until_complete(_resolve_chat_tools())
    finally:
        loop.close()
        _clear_tool_discovery_cache()  # don't leak into other tests

    assert call_count["n"] == 1, (
        f"v0.8.12: list_tools_full should be cached across turns; "
        f"got {call_count['n']} calls (expected 1 cold + 2 warm = 1 network call)"
    )


def test_resolve_chat_tools_negative_caches_discovery_failures(monkeypatch):
    """v0.8.12 — when discovery fails (server unreachable), cache the
    empty result too so the next turn doesn't retry. Otherwise a
    flaky/down MCP server adds discovery latency to every chat turn
    until the operator removes it. Operator who fixes the server
    sees recovery on the next turn after the TTL window expires."""
    import asyncio

    from deeper_notebook.graphs.chat import (
        _clear_tool_discovery_cache,
        _resolve_chat_tools,
    )

    _clear_tool_discovery_cache()

    call_count = {"n": 0}

    async def fake_list_tools_raise(self):
        call_count["n"] += 1
        raise ConnectionError("MCP server unreachable")

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_raise,
    )

    async def fake_list_enabled():
        return [
            {
                "id": "mcp_server:1",
                "name": "broken",
                "url": "http://BROKEN-URL",
                "enabled": True,
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list_enabled,
    )

    loop = asyncio.new_event_loop()
    try:
        t1 = loop.run_until_complete(_resolve_chat_tools())
        t2 = loop.run_until_complete(_resolve_chat_tools())
    finally:
        loop.close()
        _clear_tool_discovery_cache()

    assert t1 == [] and t2 == []
    assert call_count["n"] == 1, (
        f"v0.8.12: discovery failure must be cached negatively; "
        f"got {call_count['n']} retries (expected 1)"
    )


def test_bind_mcp_and_run_tool_loop_extracted_helper_works(monkeypatch):
    """v0.8.16 — the v0.8.9 in-node tool execution loop was extracted
    into `bind_mcp_and_run_tool_loop` so source_chat.py can reuse it.
    This test exercises the helper directly: fake model emits one
    tool_call, helper runs the tool, feeds ToolMessage back,
    re-invokes the model, returns (final_message, captures)."""
    import asyncio
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    from deeper_notebook.graphs.chat import bind_mcp_and_run_tool_loop

    async def fake_list_enabled():
        return [
            {
                "id": "mcp_server:1",
                "name": "test",
                "url": "http://EXTRACT-TEST-URL",
                "enabled": True,
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list_enabled,
    )

    async def fake_list_tools_full(self):
        return [
            {
                "name": "search",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_full,
    )

    async def fake_call_tool(self, name, args):
        return {"text": f"called {name} with {args}", "blocks": []}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    n = {"calls": 0}

    async def fake_ainvoke(payload):
        n["calls"] += 1
        if n["calls"] == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "mcp_search", "args": {"query": "x"}, "id": "c1"}],
            )
        return AIMessage(content="final answer [mcp:1]")

    fake_model = MagicMock()
    fake_model.ainvoke = fake_ainvoke
    fake_model.bind_tools = lambda tools: fake_model

    loop = asyncio.new_event_loop()
    try:
        final, captures = loop.run_until_complete(
            bind_mcp_and_run_tool_loop(
                fake_model,
                [HumanMessage(content="hi")],
            )
        )
    finally:
        loop.close()

    assert n["calls"] == 2, (
        f"v0.8.16: helper must drive the tool loop (got {n['calls']} model invocations)"
    )
    assert len(captures) == 1
    assert captures[0]["name"] == "search"
    assert final.content == "final answer [mcp:1]"


def test_source_chat_state_carries_mcp_tool_calls_field(monkeypatch):
    """v0.8.16 — SourceChatState gained `mcp_tool_calls: Optional[list]`
    so the source-chat router can include MCP captures in its stream.
    Cheap structural check — the v0.7.183 source_chat tests + the new
    bind_mcp_and_run_tool_loop integration give end-to-end coverage."""
    from deeper_notebook.graphs.source_chat import SourceChatState

    annotations = getattr(SourceChatState, "__annotations__", {})
    assert "mcp_tool_calls" in annotations, (
        "v0.8.16: SourceChatState must declare mcp_tool_calls so the "
        "source-chat router can surface MCP captures alongside the "
        "AI message — otherwise pill popovers are silently empty in "
        "source chat even though notebook chat populates them."
    )


def test_chat_graph_returns_empty_when_discovery_fails(monkeypatch):
    """v0.8.10 — fail-soft. If MCPClient.list_tool_names raises
    (server unreachable, transport error, auth refused), the chat
    must continue in degraded no-MCP mode rather than crashing the
    whole turn."""

    async def fake_list_enabled():
        return [
            {"id": "mcp_server:1", "name": "test", "url": "http://x", "enabled": True}
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list_enabled,
    )

    # v0.8.11 — discovery is now via list_tools_full (returns schemas).
    # Mock both methods so any future refactor still hits a raise.
    async def fake_list_tools_raise(self):
        raise ConnectionError("MCP server unreachable")

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_raise,
    )
    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tool_names",
        fake_list_tools_raise,
    )

    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(_resolve_chat_tools())
    finally:
        loop.close()
    assert tools == [], (
        "v0.8.10: discovery failure must degrade to empty tool list, "
        "not raise — the chat turn would otherwise abort entirely "
        "when the MCP server is briefly down"
    )


def test_mcp_registry_lists_enabled_servers(monkeypatch):
    """`list_enabled_servers()` returns only servers with
    `enabled=True`. Disabled servers are not used by the chat
    graph even if they're in the DB."""
    from deeper_notebook.mcp.registry import list_enabled_servers

    async def _fake_repo_query(q, params=None):
        return [
            {
                "id": "mcp_server:1",
                "name": "OpenChronicle",
                "url": "http://127.0.0.1:8742/mcp",
                "enabled": True,
            },
            {
                "id": "mcp_server:2",
                "name": "DuckDuckGo",
                "url": "http://127.0.0.1:8743/mcp",
                "enabled": False,
            },
        ]

    monkeypatch.setattr(
        "deeper_notebook.database.repository.repo_query",
        _fake_repo_query,
    )
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        servers = loop.run_until_complete(list_enabled_servers())
    finally:
        loop.close()
    assert len(servers) == 1
    assert servers[0]["name"] == "OpenChronicle"


# ---------------------------------------------------------------------------
# Task 9 — /api/mcp CRUD router
# Auth note: conftest.py sets DEEPER_NOTEBOOK_PASSWORD="" so the middleware
# skips auth for all tests — no Authorization header needed.
# ---------------------------------------------------------------------------


def test_mcp_router_list_and_create(monkeypatch):
    """GET /api/mcp returns [] when the table is empty.
    POST /api/mcp 201 creates a server and returns the new record."""
    from fastapi.testclient import TestClient

    from api.main import app

    _created_record = {
        "id": "mcp_server:abc123",
        "name": "TestServer",
        "url": "http://127.0.0.1:8742/mcp",
        "enabled": True,
    }

    async def _fake_repo_query(q, params=None):
        return []

    async def _fake_repo_create(table, data):
        # Simulate SurrealDB returning the inserted record dict.
        return {**data, "id": "mcp_server:abc123"}

    # Patch at the source so the router's lazy import picks it up.
    import deeper_notebook.database.repository as _repo

    monkeypatch.setattr(_repo, "repo_query", _fake_repo_query)
    monkeypatch.setattr(_repo, "repo_create", _fake_repo_create)

    client = TestClient(app)

    # List — empty
    r_list = client.get("/api/mcp")
    assert r_list.status_code == 200
    assert r_list.json() == []

    # Create
    payload = {
        "name": "TestServer",
        "url": "http://127.0.0.1:8742/mcp",
        "enabled": True,
    }
    r_create = client.post("/api/mcp", json=payload)
    assert r_create.status_code == 201, r_create.text
    body = r_create.json()
    assert body["name"] == "TestServer"
    assert "id" in body


def test_mcp_router_duplicate_name_409(monkeypatch):
    """POST /api/mcp with a duplicate name must return 409, not 500.

    The mcp_server_name_unique index in Migration 17 raises an exception
    whose message contains 'unique'. The router must catch that and raise
    HTTPException(409)."""
    from fastapi.testclient import TestClient

    from api.main import app

    async def _dup_repo_create(table, data):
        raise RuntimeError("Database error: unique index constraint violated")

    import deeper_notebook.database.repository as _repo

    monkeypatch.setattr(_repo, "repo_create", _dup_repo_create)

    client = TestClient(app)
    payload = {"name": "Dup", "url": "http://127.0.0.1:8742/mcp", "enabled": True}
    r = client.post("/api/mcp", json=payload)
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["detail"]


# ---------------------------------------------------------------------------
# v0.8.1 Item 5 — priority field + PATCH endpoint
# ---------------------------------------------------------------------------


def test_list_enabled_servers_sorts_by_priority(monkeypatch):
    """`list_enabled_servers()` returns rows sorted by priority ASC, then
    created ASC. Out-of-order rows from the DB are reordered by the SQL
    ORDER BY clause (tested by verifying the actual SELECT string the
    function passes to repo_query, plus the returned ordering).

    We provide rows already pre-ordered by the mock (mimicking SurrealDB's
    ORDER BY) to confirm that the Python-layer filter doesn't break it.
    The SQL clause is the source of truth; the integration test for a live
    DB is handled by the migration + manual QA."""
    from deeper_notebook.mcp.registry import list_enabled_servers

    # Rows as SurrealDB would return them after ORDER BY priority, created:
    # priority 10 → 50 → 100
    ordered_rows = [
        {
            "id": "mcp_server:3",
            "name": "FastOne",
            "url": "http://a/mcp",
            "enabled": True,
            "priority": 10,
            "created": "2026-01-01T00:00:00Z",
        },
        {
            "id": "mcp_server:1",
            "name": "MidOne",
            "url": "http://b/mcp",
            "enabled": True,
            "priority": 50,
            "created": "2026-01-02T00:00:00Z",
        },
        {
            "id": "mcp_server:2",
            "name": "SlowOne",
            "url": "http://c/mcp",
            "enabled": True,
            "priority": 100,
            "created": "2026-01-03T00:00:00Z",
        },
    ]

    async def _fake_repo_query(q, params=None):
        # Verify the query includes the ORDER BY clause.
        assert "ORDER BY priority ASC, created ASC" in q, (
            f"Expected ORDER BY in query but got: {q!r}"
        )
        return ordered_rows

    monkeypatch.setattr(
        "deeper_notebook.database.repository.repo_query",
        _fake_repo_query,
    )
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        servers = loop.run_until_complete(list_enabled_servers())
    finally:
        loop.close()

    assert len(servers) == 3
    assert servers[0]["name"] == "FastOne"
    assert servers[1]["name"] == "MidOne"
    assert servers[2]["name"] == "SlowOne"


def test_patch_mcp_server_updates_priority(monkeypatch):
    """PATCH /api/mcp/{id} with {priority: 5} must call repo_update with
    the correct arguments and return the updated record."""
    from fastapi.testclient import TestClient

    from api.main import app

    _updated = {
        "id": "mcp_server:p1",
        "name": "PriorityServer",
        "url": "http://x/mcp",
        "enabled": True,
        "priority": 5,
    }

    async def _fake_repo_update(table, id_, data):
        assert table == "mcp_server"
        assert "priority" in data
        assert data["priority"] == 5
        return [_updated]

    import deeper_notebook.database.repository as _repo

    monkeypatch.setattr(_repo, "repo_update", _fake_repo_update)

    client = TestClient(app)
    r = client.patch("/api/mcp/mcp_server:p1", json={"priority": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["priority"] == 5


def test_patch_mcp_server_rejects_empty_body(monkeypatch):
    """PATCH /api/mcp/{id} with an empty body {} must return 400."""
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    r = client.patch("/api/mcp/mcp_server:x1", json={})
    assert r.status_code == 400, r.text
    assert "No fields to update" in r.json()["detail"]


# ---------------------------------------------------------------------------
# v0.8.1 Item 3 — _resolve_chat_tools captures accumulator
# ---------------------------------------------------------------------------


def test_resolve_chat_tools_captures_calls(monkeypatch):
    """v0.8.10 — when captures is provided, the wrapped MCP tool appends
    one record per call with correct index, name, args, and text.
    Uses force_tool_names to bypass network discovery — the unit test
    pins the captures behavior, not the discovery surface."""
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    async def fake_call_tool(self, name, args):
        return {"text": "fake search result"}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    captures: list = []
    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                captures=captures,
                force_tool_names=["web_search"],
            )
        )
        # v0.8.10 — tool name is `mcp_<remote_name>` and the coroutine
        # accepts **kwargs (LangChain Tool .ainvoke unpacks dict args)
        mcp_search = next(t for t in tools if t.name == "mcp_web_search")
        loop.run_until_complete(mcp_search.coroutine(query="test query"))
    finally:
        loop.close()

    assert len(captures) == 1
    assert captures[0]["index"] == 1
    # Remote name preserved in captures (for the pill popover label),
    # not the mcp_ prefix — the popover shows what the server's tool
    # is actually called.
    assert captures[0]["name"] == "web_search"
    assert captures[0]["args"] == {"query": "test query"}
    assert captures[0]["text"] == "fake search result"


def test_resolve_chat_tools_increments_index_across_calls(monkeypatch):
    """v0.8.10 — calling the wrapped MCP tool twice yields index 1
    then 2 in captures. Same as above but pins the index increment."""
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    async def fake_call_tool(self, name, args):
        return {"text": "result"}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    captures: list = []
    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                captures=captures,
                force_tool_names=["web_search"],
            )
        )
        mcp_search = next(t for t in tools if t.name == "mcp_web_search")
        loop.run_until_complete(mcp_search.coroutine(query="first query"))
        loop.run_until_complete(mcp_search.coroutine(query="second query"))
    finally:
        loop.close()

    assert len(captures) == 2
    assert captures[0]["index"] == 1
    assert captures[1]["index"] == 2


def test_resolve_chat_tools_truncates_long_text(monkeypatch):
    """Text longer than 4000 chars is truncated to exactly 4000 chars."""
    import asyncio

    from deeper_notebook.graphs.chat import _resolve_chat_tools

    long_text = "x" * 10000

    async def fake_call_tool(self, name, args):
        return {"text": long_text}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    captures: list = []
    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(
            _resolve_chat_tools(
                force_servers=[
                    {
                        "id": "mcp_server:1",
                        "name": "test",
                        "url": "http://x",
                        "enabled": True,
                    }
                ],
                captures=captures,
                force_tool_names=["web_search"],
            )
        )
        mcp_search = next(t for t in tools if t.name == "mcp_web_search")
        loop.run_until_complete(mcp_search.coroutine(query="any query"))
    finally:
        loop.close()

    assert len(captures) == 1
    assert len(captures[0]["text"]) == 4000


# ---------------------------------------------------------------------------
# v0.8.9 CRITICAL — chat graph in-node tool execution loop
# ---------------------------------------------------------------------------


def test_call_model_with_messages_executes_mcp_tool_calls(monkeypatch):
    """v0.8.9 — pre-fix, the chat graph was START → agent → END with no
    ToolNode. `bind_tools(mcp_tools)` exposed the tools to the LLM but
    nothing actually executed any `tool_calls` the LLM emitted. So the
    v0.8.1 Item 3 mcp_captures accumulator was always empty, [mcp:N]
    pill markers in the LLM text were hallucinated, and citation pill
    popovers always showed the placeholder. This test pins the in-node
    tool execution loop: when the model emits a tool_call, the node
    must (a) invoke the matching tool, (b) feed the result back as a
    ToolMessage, (c) re-invoke the model, (d) populate mcp_captures
    with the executed call's payload."""
    import asyncio
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage

    # Mock MCP server registry → one enabled server
    async def fake_list():
        return [
            {"id": "mcp_server:1", "name": "test", "url": "http://x", "enabled": True}
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list,
    )

    # Mock MCPClient.call_tool → deterministic result
    async def fake_call_tool(self, name, args):
        return {"text": f"executed {name} with {args}"}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    # v0.8.10 / v0.8.11 — also stub list_tools_full since
    # _resolve_chat_tools now discovers tools (with input_schemas)
    # dynamically. Without this, the test would try a real network
    # call to "http://x".
    async def fake_list_tools_full(self):
        return [
            {
                "name": "web_search",
                "description": "Search the web",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_full,
    )

    # Fake model: first ainvoke returns an AIMessage with a tool_call,
    # second ainvoke returns a plain AIMessage with the final answer.
    call_count = {"n": 0}
    captured_payloads: list[list] = []

    async def fake_ainvoke(payload):
        captured_payloads.append(list(payload))
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call → emit a tool_call for the discovered tool
            # (the v0.8.10 wrapping makes it mcp_<remote_name> =
            # mcp_web_search since fake_list_tools returns ["web_search"]).
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "mcp_web_search",
                        "args": {"query": "test query"},
                        "id": "call_abc",
                    }
                ],
            )
        # Second call (after tool result fed back) → final answer
        return AIMessage(
            content="Found the answer based on the search [mcp:1].",
        )

    fake_model = MagicMock()
    fake_model.ainvoke = fake_ainvoke
    fake_model.bind_tools = lambda tools: fake_model  # passthrough

    # Stub the provision so the node uses our fake model
    import deeper_notebook.graphs.chat as chat_mod

    async def fake_provision(content, model_id, default_type, **kw):
        return fake_model

    monkeypatch.setattr(chat_mod, "provision_langchain_model", fake_provision)

    async def fake_recall_memory(query=None):
        return {"facts": [], "preferences": [], "episodes": []}

    monkeypatch.setattr(chat_mod, "recall_memory", fake_recall_memory)

    # Run the node
    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content="search for something")],
        "notebook": None,
        "context": None,
        "context_config": None,
        "model_override": "model:test",  # bypass smart router for clean test
        "selected_provider": None,
        "selected_model_id": None,
        "mcp_tool_calls": None,
    }
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            chat_mod.call_model_with_messages(state, {"configurable": {}})
        )
    finally:
        loop.close()

    # Model must have been invoked TWICE — once to get the tool_call,
    # once with the tool result fed back.
    assert call_count["n"] == 2, (
        f"v0.8.9: model.ainvoke should run twice (tool_call → tool result "
        f"→ final answer); ran {call_count['n']} time(s). Tool loop is broken."
    )

    # mcp_captures (returned as mcp_tool_calls) must be populated.
    captures = result.get("mcp_tool_calls")
    assert captures is not None and len(captures) == 1, (
        f"v0.8.9: mcp_tool_calls must contain the executed call; got {captures!r}. "
        f"This means the tool closure never fired — chat graph isn't executing "
        f"tools despite the bind_tools call."
    )
    assert (
        captures[0]["name"] == "web_search"
    )  # _search calls call_tool("web_search", ...)
    assert captures[0]["args"] == {"query": "test query"}
    assert "executed web_search" in captures[0]["text"]

    # The second model invocation must have seen a ToolMessage in the payload.
    from langchain_core.messages import ToolMessage

    second_payload = captured_payloads[1]
    tool_msgs = [m for m in second_payload if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1, (
        f"v0.8.9: second model.ainvoke payload must include a ToolMessage "
        f"with the tool result; got {[type(m).__name__ for m in second_payload]}"
    )
    assert tool_msgs[0].tool_call_id == "call_abc"


def test_call_model_bounds_tool_loop_iterations(monkeypatch):
    """v0.8.9 — runaway protection. If the model keeps emitting
    tool_calls forever (broken prompt, faulty fine-tune, etc.), the
    loop must terminate at MAX_TOOL_ITERATIONS instead of infinite-
    looping the API. Pin the bound at <=4 model invocations."""
    import asyncio
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    async def fake_list():
        return [
            {"id": "mcp_server:1", "name": "test", "url": "http://x", "enabled": True}
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        fake_list,
    )

    async def fake_call_tool(self, name, args):
        return {"text": "result"}

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.call_tool",
        fake_call_tool,
    )

    # v0.8.10 / v0.8.11 — stub list_tools_full discovery
    async def fake_list_tools_full(self):
        return [
            {
                "name": "web_search",
                "description": "Search the web",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full",
        fake_list_tools_full,
    )

    call_count = {"n": 0}

    async def fake_ainvoke(payload):
        call_count["n"] += 1
        # ALWAYS emit a tool_call → would loop forever without the bound
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "mcp_web_search",
                    "args": {"query": f"iter{call_count['n']}"},
                    "id": f"call_{call_count['n']}",
                }
            ],
        )

    fake_model = MagicMock()
    fake_model.ainvoke = fake_ainvoke
    fake_model.bind_tools = lambda tools: fake_model

    import deeper_notebook.graphs.chat as chat_mod

    async def fake_provision(content, model_id, default_type, **kw):
        return fake_model

    monkeypatch.setattr(chat_mod, "provision_langchain_model", fake_provision)

    async def fake_recall_memory(query=None):
        return {"facts": [], "preferences": [], "episodes": []}

    monkeypatch.setattr(chat_mod, "recall_memory", fake_recall_memory)

    state = {
        "messages": [HumanMessage(content="loop")],
        "notebook": None,
        "context": None,
        "context_config": None,
        "model_override": "model:test",
        "selected_provider": None,
        "selected_model_id": None,
        "mcp_tool_calls": None,
    }
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            chat_mod.call_model_with_messages(state, {"configurable": {}})
        )
    finally:
        loop.close()

    # 1 initial + up to MAX_TOOL_ITERATIONS=4 re-invocations = 5 max.
    # Test that the bound holds and we don't keep spinning forever.
    assert call_count["n"] <= 5, (
        f"v0.8.9: tool loop must terminate at MAX_TOOL_ITERATIONS; "
        f"got {call_count['n']} model invocations (runaway)"
    )
