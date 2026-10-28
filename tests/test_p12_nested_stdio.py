"""P12 实际 stdio 管道传输与嵌套参数 Agent 端到端往返契约测试

测试重点：
1. 启动真实的独立 FastMCP stdio 子进程，提供包含复杂嵌套结构（`dict[str, list[int]]`）的工具；
2. 验证 MCPTool 正确保留嵌套参数 Schema 并挂载到 ToolRegistry；
3. 验证 SimpleAgent.arun 与真实 MCP 子进程的双向通信与结果回执注入；
4. 验证本地 Schema 拦截非法嵌套参数（类型错误），不触发对 MCP 进程的二次调用；
5. 验证 async with 退出后子进程资源正常关闭，动态工具全部注销。
"""

import json
import sys

import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.tools import MCPConnection
from hello_agents.core.llm_response import ToolCall
from examples.agents.runtime_features import ScriptedLLM, response


@pytest.mark.asyncio
async def test_nested_schema_actual_stdio_agent_roundtrip(tmp_path):
    """测试真实 stdio 传输下的嵌套参数传递与 Agent 完整调用链路

    流程：
    1. 在临时目录动态创建并启动一个包含 nested_total 工具的 MCP 服务脚本；
    2. 使用 MCPConnection.stdio 通过标准输入输出管道建立通信；
    3. 注册至 ToolRegistry，验证嵌套 Schema 的正确映射；
    4. 执行 Agent 异步循环，验证回执中的 structuredContent 结果；
    5. 测试非法入参在本地被拦截且远端计数器不增加。
    """
    server = tmp_path / "nested_server.py"
    counter = tmp_path / "calls.txt"
    # 创建带调用计数器的测试 MCP 服务端脚本
    server.write_text('''from pathlib import Path
import sys
from mcp.server import MCPServer
server = MCPServer("nested fixture")
counter = Path(sys.argv[1])
@server.tool(structured_output=True)
def nested_total(payload: dict[str, list[int]]) -> dict[str, int]:
    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
    return {"total": sum(sum(values) for values in payload.values())}
server.run("stdio")
''', encoding="utf-8")
    registry = ToolRegistry()
    # 模拟 LLM 预设响应
    model = ScriptedLLM([
        response(calls=[ToolCall("nested", "remote_nested_total", json.dumps({
            "payload": {"first": [1, 2], "second": [3]},
        }))]),
        response("total 6"),
    ])
    # 建立 stdio 管道连接
    async with MCPConnection.stdio(sys.executable, [str(server), str(counter)], prefix="remote_") as connection:
        connection.register_tools(registry)
        # 验证嵌套参数的模式映射正确保留
        schema = registry.get_tool("remote_nested_total").to_openai_schema()["function"]["parameters"]
        assert schema["properties"]["payload"]["additionalProperties"]["items"]["type"] == "integer"
        
        # 启动 Agent 进行实际异步调用
        agent = SimpleAgent("nested", model, tool_registry=registry)
        assert await agent.arun("sum nested values") == "total 6"
        
        # 验证工具调用回执
        receipt = next(m for m in model.requests[-1]["messages"] if m.get("tool_call_id") == "nested")
        assert json.loads(receipt["content"])["data"]["mcp"]["structuredContent"]["total"] == 6
        
        # 验证非法结构在本地校验被拦截为 INVALID_PARAM，且远程计数器保持为 1（未发生无效调用）
        invalid = await registry.aexecute_tool("remote_nested_total", {"payload": {"bad": ["x"]}})
        assert invalid.error_info["code"] == "INVALID_PARAM" and counter.read_text() == "1"
        
    # 验证上下文退出后工具注销干净
    assert registry.list_tools() == []
