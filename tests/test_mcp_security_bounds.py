"""RED-to-GREEN coverage for hostile MCP discovery and result payloads."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


@pytest.mark.asyncio
async def test_mcp_discovery_projects_only_bounded_valid_tool_specs(monkeypatch):
    import deeper_notebook.mcp.client as client_module

    class _Tool:
        def __init__(self, name, description="", schema=None):
            self.name = name
            self.description = description
            self.inputSchema = schema

    class _Session:
        async def list_tools(self):
            return type(
                "Result",
                (),
                {
                    "tools": [
                        _Tool("valid", "ok", {"type": "object", "properties": {}}),
                        _Tool(" " * 3, "blank name"),
                        _Tool("x" * (client_module._MAX_TOOL_NAME_CHARS + 1)),
                        _Tool(42, "not a string"),
                    ]
                    + [
                        _Tool(
                            f"extra-{index}",
                            "d" * (client_module._MAX_DESCRIPTION_CHARS + 1),
                            {"type": "object", "properties": {}},
                        )
                        for index in range(client_module._MAX_MCP_TOOLS + 10)
                    ],
                },
            )()

    @asynccontextmanager
    async def _open_session(url, headers=None, **_kwargs):  # accepts transport= (fa6a7213)
        yield _Session()

    monkeypatch.setattr(client_module, "_open_session", _open_session)
    tools = await client_module.MCPClient("http://127.0.0.1/mcp").list_tools_full()

    assert len(tools) <= client_module._MAX_MCP_TOOLS
    assert all(
        isinstance(tool["name"], str)
        and 0 < len(tool["name"]) <= client_module._MAX_TOOL_NAME_CHARS
        and len(tool["description"]) <= client_module._MAX_DESCRIPTION_CHARS
        and isinstance(tool["input_schema"], dict)
        for tool in tools
    )


@pytest.mark.asyncio
async def test_mcp_result_projection_bounds_text_binary_and_block_count(monkeypatch):
    import deeper_notebook.mcp.client as client_module

    class _Text:
        text = "t" * (client_module._MAX_TEXT_CHARS + 100)

    class _Image:
        data = "A" * (client_module._MAX_BINARY_CHARS + 100)
        mimeType = "image/png"

    class _Resource:
        class resource:
            uri = "u" * (client_module._MAX_URI_CHARS + 100)
            mimeType = "application/octet-stream"
            text = None
            blob = "B" * (client_module._MAX_BINARY_CHARS + 100)

    class _Result:
        content = [_Text(), _Image(), _Resource()] + [object()] * (
            client_module._MAX_CONTENT_BLOCKS + 10
        )

    class _Session:
        async def call_tool(self, name, arguments=None):
            return _Result()

    @asynccontextmanager
    async def _open_session(url, headers=None, **_kwargs):  # accepts transport= (fa6a7213)
        yield _Session()

    monkeypatch.setattr(client_module, "_open_session", _open_session)
    result = await client_module.MCPClient("http://127.0.0.1/mcp").call_tool(
        "example", {}
    )

    assert len(result["blocks"]) <= client_module._MAX_CONTENT_BLOCKS
    assert len(result["text"]) <= client_module._MAX_TEXT_CHARS * (
        client_module._MAX_CONTENT_BLOCKS + 1
    )
    for block in result["blocks"]:
        if "text" in block:
            assert len(block["text"]) <= client_module._MAX_TEXT_CHARS
        if "data" in block:
            assert len(block["data"]) <= client_module._MAX_BINARY_CHARS
        if "uri" in block:
            assert len(block["uri"]) <= client_module._MAX_URI_CHARS
        if block["type"] == "unknown":
            assert len(block["repr"]) <= client_module._MAX_REPR_CHARS


@pytest.mark.asyncio
async def test_mcp_bounds_do_not_materialize_hostile_lazy_iterables(monkeypatch):
    import deeper_notebook.mcp.client as client_module

    class _ExplodingList(list):
        def __iter__(self):
            for index in range(client_module._MAX_CONTENT_BLOCKS + 100):
                if index >= client_module._MAX_CONTENT_BLOCKS:
                    raise AssertionError("unbounded content traversal")
                yield object()

    class _Tool:
        name = "safe"
        description = ""
        inputSchema = {"type": "object", "properties": {}}

    class _ExplodingTools(list):
        def __iter__(self):
            for index in range(client_module._MAX_MCP_TOOLS + 100):
                if index >= client_module._MAX_MCP_TOOLS:
                    raise AssertionError("unbounded tool traversal")
                yield _Tool()

    class _Result:
        tools = _ExplodingTools()
        content = _ExplodingList()

    class _Session:
        async def list_tools(self):
            return _Result()

        async def call_tool(self, name, arguments=None):
            return _Result()

    @asynccontextmanager
    async def _open_session(url, headers=None, **_kwargs):  # accepts transport= (fa6a7213)
        yield _Session()

    monkeypatch.setattr(client_module, "_open_session", _open_session)
    client = client_module.MCPClient("http://127.0.0.1/mcp")
    assert len(await client.list_tools_full()) <= client_module._MAX_MCP_TOOLS
    result = await client.call_tool("safe", {})
    assert len(result["blocks"]) <= client_module._MAX_CONTENT_BLOCKS


@pytest.mark.asyncio
async def test_mcp_discovery_skips_individually_broken_tool_mapping(monkeypatch):
    import deeper_notebook.mcp.client as client_module

    class _BrokenTool(dict):
        def get(self, key, default=None):
            raise RuntimeError("malformed tool mapping")

    class _GoodTool:
        name = "survives"
        description = ""
        inputSchema = {"type": "object", "properties": {}}

    class _Session:
        async def list_tools(self):
            return type("Result", (), {"tools": [_BrokenTool(), _GoodTool()]})()

    @asynccontextmanager
    async def _open_session(url, headers=None, **_kwargs):  # accepts transport= (fa6a7213)
        yield _Session()

    monkeypatch.setattr(client_module, "_open_session", _open_session)
    tools = await client_module.MCPClient("http://127.0.0.1/mcp").list_tools_full()

    assert [tool["name"] for tool in tools] == ["survives"]


@pytest.mark.asyncio
async def test_mcp_result_projection_has_total_text_and_binary_budgets(monkeypatch):
    import deeper_notebook.mcp.client as client_module

    class _Text:
        text = "t" * client_module._MAX_TEXT_CHARS

    class _Image:
        data = "A" * client_module._MAX_BINARY_CHARS
        mimeType = "image/png"

    class _Result:
        content = [
            _item
            for _index in range(client_module._MAX_CONTENT_BLOCKS)
            for _item in (_Text(), _Image())
        ]

    class _Session:
        async def call_tool(self, name, arguments=None):
            return _Result()

    @asynccontextmanager
    async def _open_session(url, headers=None, **_kwargs):  # accepts transport= (fa6a7213)
        yield _Session()

    monkeypatch.setattr(client_module, "_open_session", _open_session)
    result = await client_module.MCPClient("http://127.0.0.1/mcp").call_tool(
        "bounded", {}
    )

    text_size = sum(
        len(block.get("text", ""))
        for block in result["blocks"]
        if block["type"] == "text"
    )
    binary_size = sum(
        len(block.get("data", ""))
        for block in result["blocks"]
        if block["type"] == "image"
    )
    assert text_size <= client_module._MAX_RESULT_TEXT_CHARS
    assert binary_size <= client_module._MAX_RESULT_BINARY_CHARS


@pytest.mark.asyncio
async def test_chat_discovery_bounds_servers_and_cache(monkeypatch):
    import deeper_notebook.graphs.chat as chat_module

    monkeypatch.setattr(chat_module, "_MAX_MCP_SERVERS", 2)
    monkeypatch.setattr(chat_module, "_TOOL_DISCOVERY_CACHE_MAX", 2)
    chat_module._clear_tool_discovery_cache()
    calls = {"n": 0}

    async def _list_tools_full(self):
        calls["n"] += 1
        return [
            {
                "name": "search",
                "description": "",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    monkeypatch.setattr(
        "deeper_notebook.mcp.client.MCPClient.list_tools_full", _list_tools_full
    )
    servers = [
        {"name": f"server-{index}", "url": f"http://127.0.0.1/{index}"}
        for index in range(10)
    ]
    await chat_module._resolve_chat_tools(force_servers=servers)

    assert calls["n"] <= chat_module._MAX_MCP_SERVERS
    assert (
        len(chat_module._tool_discovery_cache) <= chat_module._TOOL_DISCOVERY_CACHE_MAX
    )


@pytest.mark.asyncio
async def test_chat_discovery_is_fail_soft_for_malformed_plugin_data(monkeypatch):
    import deeper_notebook.graphs.chat as chat_module

    chat_module._clear_tool_discovery_cache()
    tools = await chat_module._resolve_chat_tools(
        force_servers=[
            None,
            {"name": "missing-url"},
            {"name": "valid", "url": "http://127.0.0.1/mcp"},
        ],
        force_tools_full=[
            None,
            {"name": "", "description": "bad", "input_schema": {}},
            {"name": "safe", "description": 123, "input_schema": "bad"},
        ],
    )

    assert [tool.name for tool in tools] == ["mcp_safe"]


@pytest.mark.asyncio
async def test_chat_discovery_is_fail_soft_when_registry_lookup_raises(monkeypatch):
    import deeper_notebook.graphs.chat as chat_module

    async def _raise_registry_error():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(
        "deeper_notebook.mcp.registry.list_enabled_servers",
        _raise_registry_error,
    )

    assert await chat_module._resolve_chat_tools() == []


@pytest.mark.asyncio
async def test_mcp_registry_bounds_and_skips_malformed_rows(monkeypatch):
    import deeper_notebook.mcp.registry as registry_module

    monkeypatch.setattr(registry_module, "_MAX_MCP_SERVERS", 2)

    async def _repo_query(query):
        return [
            None,
            {"name": "missing-url", "enabled": True},
            {
                "name": "first",
                "url": "http://127.0.0.1:8742/mcp",
                "enabled": True,
            },
            {
                "name": "disabled",
                "url": "http://127.0.0.1:8743/mcp",
                "enabled": False,
            },
            {
                "name": "second",
                "url": "https://example.test/mcp",
                "enabled": True,
            },
            {
                "name": "third",
                "url": "https://example.test/third",
                "enabled": True,
            },
        ]

    monkeypatch.setattr("deeper_notebook.database.repository.repo_query", _repo_query)
    servers = await registry_module.list_enabled_servers()

    assert [server["name"] for server in servers] == ["first", "second"]


@pytest.mark.asyncio
async def test_mcp_registry_does_not_materialize_hostile_mapping(monkeypatch):
    import deeper_notebook.mcp.registry as registry_module

    class _HostileRow(dict):
        def __iter__(self):
            raise AssertionError("unbounded registry row traversal")

    async def _repo_query(query):
        return [
            _HostileRow(
                name="safe",
                url="http://127.0.0.1:8742/mcp",
                enabled=True,
                priority=10,
            )
        ]

    monkeypatch.setattr("deeper_notebook.database.repository.repo_query", _repo_query)

    servers = await registry_module.list_enabled_servers()
    assert [server["name"] for server in servers] == ["safe"]


@pytest.mark.parametrize(
    ("env_name", "helper_name", "max_name"),
    [
        (
            "DEEPER_NOTEBOOK_MCP_RPC_TIMEOUT_SEC",
            "_rpc_timeout",
            "_MAX_RPC_TIMEOUT_SEC",
        ),
        (
            "DEEPER_NOTEBOOK_MCP_TOOL_TIMEOUT_SEC",
            "_mcp_tool_timeout_sec",
            "_MAX_MCP_TOOL_TIMEOUT_SEC",
        ),
    ],
)
def test_mcp_timeout_env_values_are_finite_and_bounded(
    monkeypatch, env_name, helper_name, max_name
):
    import deeper_notebook.graphs.chat as chat_module
    import deeper_notebook.mcp.client as client_module

    module = client_module if helper_name == "_rpc_timeout" else chat_module
    monkeypatch.setenv(env_name, "inf")
    assert getattr(module, helper_name)() == 30.0
    monkeypatch.setenv(env_name, "nan")
    assert getattr(module, helper_name)() == 30.0
    monkeypatch.setenv(env_name, "999999999")
    assert getattr(module, helper_name)() == getattr(module, max_name)


def test_agent_iteration_env_value_is_finite_and_bounded(monkeypatch):
    import deeper_notebook.graphs.chat as chat_module

    monkeypatch.setenv("DEEPER_NOTEBOOK_AGENT_MAX_ITERATIONS", "999999999")
    assert chat_module._agent_max_iterations() == chat_module._MAX_AGENT_ITERATIONS
    monkeypatch.setenv("DEEPER_NOTEBOOK_AGENT_MAX_ITERATIONS", "inf")
    assert chat_module._agent_max_iterations() == 4
