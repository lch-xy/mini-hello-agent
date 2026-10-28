"""P12 MCP 目录协商契约、多模态返回与事件循环边界测试

测试重点：
1. 服务端返回非法目录格式时（如重复名称、循环分页游标、缺失白名单工具、名称非法字符、超长名称、非本地引用 $ref），
   连接安全关闭并不在本地注册表留下任何残留；
2. 完整保留嵌套 $defs JSON Schema，支持文本与图像多模态返回数据结构，校验入参并拒绝跨事件循环执行；
3. 远端超时异常（TimeoutError）不进行自动重试，确保写安全性。
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import mcp
import pytest

from hello_agents import ToolRegistry
from hello_agents.tools import MCPConnection


def definition(name="read", schema=None):
    """辅助工厂：构造符合 MCP 协议规范的工具定义结构"""
    return {"name": name, "description": "read", "inputSchema": schema if schema is not None else {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    }}


class Client:
    """测试用 MCP 客户端替身，模拟分页 list_tools 与 call_tool 响应"""
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
    ([{"tools": [definition(), definition()]}], {}),  # 重复工具名
    ([{"tools": [], "nextCursor": "repeat"}, {"tools": [], "nextCursor": "repeat"}], {}),  # 分页游标死循环
    ([{"tools": [definition()]}], {"tools": ["missing"]}),  # 请求的工具不存在于目录
    ([{"tools": [definition("name.with.dot")]}], {}),  # 工具名称包含非法点号
    ([{"tools": [definition("x" * 64)]}], {"prefix": "prefix_"}),  # 带前缀后名称总长超出 64 字符
    ([{"tools": [definition(schema={"$ref": "https://example.invalid/remote-schema"})]}], {}),  # 包含非本地 URL 模式引用
])
async def test_invalid_catalog_closes_connection_without_registration(monkeypatch, pages, options):
    """验证当远端目录协商发生任何错误时，连接立即抛出 ValueError 并关闭客户端，不挂载任何工具"""
    client = Client(pages)
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: client)
    connection = MCPConnection("https://example.invalid/not-contacted", **options)
    with pytest.raises(ValueError):
        async with connection:
            raise AssertionError("invalid catalog became usable")
    assert client.closed and connection.client is None and connection._catalog == []


@pytest.mark.asyncio
async def test_nested_schema_preserves_multimodal_result_and_rejects_wrong_loop(monkeypatch):
    """测试嵌套 Schema 完整性、多模态返回封装、本地 Schema 校验以及跨事件循环写保护

    - 嵌套的 $defs 与本地引用直接映射为 OpenAI parameters；
    - 支持包含文本与图像等混合内容的返回；
    - 传入非法参数类型被本地校验拦截为 INVALID_PARAM，不产生远端 RPC 调用；
    - 同步 run 被拒绝；跨线程/新事件循环调用被拒绝为 INTERNAL_ERROR。
    """
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
        # 验证导出的参数 Schema 与定义完全一致
        assert tool.to_openai_schema()["function"]["parameters"] == schema
        # 验证成功调用与原始多模态数据保留
        result = await registry.aexecute_tool("read", {"query": "fact"})
        assert result.text == "evidence" and result.data["mcp"] == raw
        # 验证非法入参在本地被 Draft202012Validator 拦截，不产生远端调用
        bad = await registry.aexecute_tool("read", {"query": 12})
        assert bad.error_info["code"] == "INVALID_PARAM" and client.calls == 1
        # 验证同步 run 会抛出异常提示需要 await
        with pytest.raises(RuntimeError, match="require await"):
            tool.run({"query": "fact"})
        # 验证在另一个线程的事件循环中执行会被安全拒绝
        fork = registry.fork()
        result = await asyncio.to_thread(lambda: asyncio.run(fork.aexecute_tool("read", {"query": "fact"})))
        assert result.error_info["code"] == "INTERNAL_ERROR"
        assert "another event loop" in result.text and client.calls == 1


@pytest.mark.asyncio
async def test_remote_timeout_is_not_automatically_retried(monkeypatch):
    """验证远端调用超时异常（TimeoutError）绝不自动发起二次重试

    对于写操作，超时可能意味着远端已在处理，自动重试会导致重复执行的副作用。
    """
    client = Client([{"tools": [definition()]}], TimeoutError("unknown remote outcome"))
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: client)
    async with MCPConnection("https://example.invalid/not-contacted") as connection:
        registry = ToolRegistry()
        connection.register_tools(registry)
        with pytest.raises(TimeoutError):
            await registry.get_tool("read").arun({"query": "write intent"})
        # 严格验证仅执行了 1 次远程调用，没有产生自动重试
        assert client.calls == 1
