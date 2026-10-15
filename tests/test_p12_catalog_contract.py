"""目录协商失败、原始结果和事件循环边界；真实传输另由原测试覆盖。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import mcp
import pytest

from hello_agents import ToolRegistry
from hello_agents.tools import MCPConnection


def definition(name="read", schema=None):
    return {"name": name, "description": "read", "inputSchema": schema if schema is not None else {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    }}


class Client:
    def __init__(self, pages, result=None):
        self.pages = iter(pages)
        self.result = result
        self.closed = False
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def list_tools(self, cursor=None):
        page = next(self.pages)
        return SimpleNamespace(model_dump=lambda **kwargs: deepcopy(page))

    async def call_tool(self, name, parameters):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return SimpleNamespace(model_dump=lambda **kwargs: deepcopy(self.result))


@pytest.mark.asyncio
@pytest.mark.parametrize("pages,options", [
    ([{"tools": [definition(), definition()]}], {}),
    ([{"tools": [], "nextCursor": "repeat"}, {"tools": [], "nextCursor": "repeat"}], {}),
    ([{"tools": [definition()]}], {"tools": ["missing"]}),
    ([{"tools": [definition("name.with.dot")]}], {}),
    ([{"tools": [definition("x" * 64)]}], {"prefix": "prefix_"}),
    ([{"tools": [definition(schema={"$ref": "https://example.invalid/remote-schema"})]}], {}),
])
async def test_invalid_catalog_closes_connection_without_registration(monkeypatch, pages, options):
    client = Client(pages)
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: client)
    connection = MCPConnection("https://example.invalid/not-contacted", **options)
    with pytest.raises(ValueError):
        async with connection:
            raise AssertionError("invalid catalog became usable")
    assert client.closed and connection.client is None and connection._catalog == []


@pytest.mark.asyncio
async def test_nested_schema_preserves_multimodal_result_and_rejects_wrong_loop(monkeypatch):
    schema = {"type": "object", "$defs": {"Text": {"type": "string"}},
              "properties": {"query": {"$ref": "#/$defs/Text"}}, "required": ["query"]}
    raw = {"content": [{"type": "text", "text": "evidence"},
                       {"type": "image", "data": "AA==", "mimeType": "image/png"}],
           "structuredContent": {"source": "fixture"}, "isError": False}
    client = Client([{"tools": [definition(schema=schema)]}], raw)
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: client)
    registry = ToolRegistry()
    async with MCPConnection("https://example.invalid/not-contacted") as connection:
        connection.register_tools(registry)
        tool = registry.get_tool("read")
        assert tool.to_openai_schema()["function"]["parameters"] == schema
        result = await registry.aexecute_tool("read", {"query": "fact"})
        assert result.text == "evidence" and result.data["mcp"] == raw
        bad = await registry.aexecute_tool("read", {"query": 12})
        assert bad.error_info["code"] == "INVALID_PARAM" and client.calls == 1
        with pytest.raises(RuntimeError, match="require await"):
            tool.run({"query": "fact"})
        fork = registry.fork()
        result = await asyncio.to_thread(lambda: asyncio.run(fork.aexecute_tool("read", {"query": "fact"})))
        assert result.error_info["code"] == "INTERNAL_ERROR"
        assert "another event loop" in result.text and client.calls == 1


@pytest.mark.asyncio
async def test_remote_timeout_is_not_automatically_retried(monkeypatch):
    client = Client([{"tools": [definition()]}], TimeoutError("unknown remote outcome"))
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: client)
    async with MCPConnection("https://example.invalid/not-contacted") as connection:
        registry = ToolRegistry()
        connection.register_tools(registry)
        with pytest.raises(TimeoutError):
            await registry.get_tool("read").arun({"query": "write intent"})
        assert client.calls == 1
