"""原生 Agent 循环调用实际 MCP 子进程端到端示例

展示如何通过 MCPConnection.stdio 启动 MCP 子进程服务，
将其远端工具动态注册至 ToolRegistry，并在 Agent 异步运行中执行工具调用与结果回执校验，
最后在退出 async with 作用域时自动完成工具注销与进程清理。
"""

import asyncio
from pathlib import Path
import sys

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.tools import MCPConnection
from examples._support import demo_llm, tool_response, response


async def main():
    """主执行逻辑：模拟 Agent 与真实 MCP stdio 子进程的交互"""
    # 构造演示模型，依次模拟工具调用响应与最终答复
    llm = demo_llm(
        [
            tool_response("travel_route_info", {"place": "museum"}),
            response("The museum route takes 25 minutes, with 1 km of walking."),
        ]
    )
    registry = ToolRegistry()
    server = Path(__file__).with_name("mcp_server.py")

    # 1. 以 stdio 模式异步连接本地 MCP 服务进程，并配置工具前缀与白名单
    async with MCPConnection.stdio(
        sys.executable,
        ["-X", "utf8", str(server)],
        prefix="travel_",
        tools=["route_info"],
    ) as connection:
        # 2. 将 MCP 工具注册进 ToolRegistry
        connection.register_tools(registry)
        agent = SimpleAgent("travel", llm, tool_registry=registry)

        # 3. 异步触发 Agent 执行循环
        print(
            await agent.arun(
                "Use travel_route_info to check the museum route. Report minutes and walking distance."
            )
        )

        # 4. 验证工具调用回执中包含 MCP 服务端返回的真实数据
        receipts = [m for m in agent._history if m.role == "tool"]
        assert receipts and "example-fixture" in receipts[0].content

    # 5. 退出 async with 作用域后，验证 MCP 动态注册的工具已被自动注销干净
    assert registry.list_tools() == []
    print("MCP tool receipt verified; connection closed and tools unregistered.")


if __name__ == "__main__":
    asyncio.run(main())
