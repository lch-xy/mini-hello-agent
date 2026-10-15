"""实际 stdio 传输中保留嵌套参数，并通过真实 Agent 往返。"""

import json
import sys

import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.tools import MCPConnection
from hello_agents.core.llm_response import ToolCall
from examples.agents.runtime_features import ScriptedLLM, response


@pytest.mark.asyncio
async def test_nested_schema_actual_stdio_agent_roundtrip(tmp_path):
    server = tmp_path / "nested_server.py"
    counter = tmp_path / "calls.txt"
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
    model = ScriptedLLM([
        response(calls=[ToolCall("nested", "remote_nested_total", json.dumps({
            "payload": {"first": [1, 2], "second": [3]},
        }))]),
        response("total 6"),
    ])
    async with MCPConnection.stdio(sys.executable, [str(server), str(counter)], prefix="remote_") as connection:
        connection.register_tools(registry)
        schema = registry.get_tool("remote_nested_total").to_openai_schema()["function"]["parameters"]
        assert schema["properties"]["payload"]["additionalProperties"]["items"]["type"] == "integer"
        agent = SimpleAgent("nested", model, tool_registry=registry)
        assert await agent.arun("sum nested values") == "total 6"
        receipt = next(m for m in model.requests[-1]["messages"] if m.get("tool_call_id") == "nested")
        assert json.loads(receipt["content"])["data"]["mcp"]["structuredContent"]["total"] == 6
        invalid = await registry.aexecute_tool("remote_nested_total", {"payload": {"bad": ["x"]}})
        assert invalid.error_info["code"] == "INVALID_PARAM" and counter.read_text() == "1"
    assert registry.list_tools() == []
