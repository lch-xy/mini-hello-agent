"""MCP 桥接层多传输方式（stdio 与 HTTP）综合集成测试

测试覆盖：
1. 实际 stdio 管道下的工具模式生成、正常调用返回、参数类型拦截、远端业务报错与权限策略（AllowlistPolicy）拦截；
2. 工具名称冲突拦截与退出清理时保留宿主替换工具（replace=True）；
3. 断开重连代际隔离（generation check）：重连后旧的 fork 工具副本不会被误激活；
4. 真实 Streamable HTTP 传输协议连接、工具注册与执行全生命周期。
"""

import asyncio
from pathlib import Path
import socket
import subprocess
import sys

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from hello_agents import ToolRegistry
from hello_agents.tools import MCPConnection, AllowlistPolicy

# 引用示例目录中的实际 MCP 服务脚本
SERVER = Path(__file__).resolve().parents[1] / "examples" / "tools" / "mcp_server.py"


@pytest.mark.asyncio
async def test_actual_stdio_schema_results_lifecycle_and_policy():
    """测试实际 stdio 模式下的工具生命周期、Schema 提取、参数校验、执行报错及权限控制"""
    r = ToolRegistry()
    connection = MCPConnection.stdio(sys.executable, [str(SERVER)], prefix="trip_")
    async with connection:
        # 验证工具注册成功并带上前缀
        assert connection.register_tools(r) == ("trip_route_info",)
        tool = r.get_tool("trip_route_info")
        # 验证参数 Schema 映射
        assert (
            tool.to_openai_schema()["function"]["parameters"]["properties"]["place"][
                "type"
            ]
            == "string"
        )
        # 1. 正常执行：结构化结果提取
        good = await r.aexecute_tool("trip_route_info", {"place": "museum"})
        assert good.data["mcp"]["structuredContent"]["minutes"] == 25
        # 2. 参数类型不符：本地 schema 校验拦截为 INVALID_PARAM
        bad = await r.aexecute_tool("trip_route_info", {"place": 5})
        assert bad.error_info["code"] == "INVALID_PARAM"
        # 3. 远端业务报错：服务抛出 ValueError 时封装为 EXECUTION_ERROR
        remote_error = await r.aexecute_tool("trip_route_info", {"place": "missing"})
        assert remote_error.error_info["code"] == "EXECUTION_ERROR"
        # 4. 权限策略拦截：空白名单拒绝调用
        r.policy = AllowlistPolicy([])
        assert (await r.aexecute_tool("trip_route_info", {"place": "lake"})).error_info[
            "code"
        ] == "PERMISSION_DENIED"
    # 验证退出上下文后工具被自动注销
    assert r.list_tools() == []
    # 验证在已关闭的连接上调用工具会抛出 RuntimeError
    with pytest.raises(RuntimeError, match="closed"):
        await tool.arun({"place": "museum"})


@pytest.mark.asyncio
async def test_collision_and_host_replacement_survives_cleanup():
    """测试名称冲突检测与退出清理行为：

    - 若注册表中已存在同名工具，抛出 ValueError；
    - 若宿主后来以 replace=True 替换了同名工具，连接退出时不应误注销宿主替换的新工具。
    """
    r = ToolRegistry()
    r.register_function(lambda x: x, name="route_info")
    async with MCPConnection.stdio(sys.executable, [str(SERVER)]) as connection:
        # 验证同名冲突抛出异常
        with pytest.raises(ValueError, match="collision"):
            connection.register_tools(r)
        r.unregister("route_info")
        connection.register_tools(r)
        # 宿主覆盖替换工具
        r.register_function(lambda x: x, name="route_info", replace=True)
    # 验证宿主的新工具依然完好保留，未被注销
    assert r.list_tools() == ["route_info"]


@pytest.mark.asyncio
async def test_reconnect_does_not_reactivate_old_fork_tools():
    """测试断开重连代际隔离（generation check）：

    旧连接上 fork 出的工具副本，在连接关闭并重新建立后，
    由于代际标识（_generation）不匹配，严禁被重新激活。
    """
    r = ToolRegistry()
    c = MCPConnection.stdio(sys.executable, [str(SERVER)])
    async with c:
        c.register_tools(r)
        child = r.fork()
    c.allowed = frozenset()
    async with c:
        assert c.register_tools(r) == ()
        # 验证旧代的工具执行时报错代际已关闭
        result = await child.aexecute_tool("route_info", {"place": "museum"})
        assert result.error_info["code"] == "INTERNAL_ERROR"
        assert "closed connection generation" in result.text
        assert not result.data.get("mcp")


@pytest.mark.asyncio
async def test_actual_streamable_http():
    """测试真实 HTTP 协议（streamable-http）传输的连接、工具注册与执行全生命周期"""
    pytest.importorskip("uvicorn")
    # 动态探测绑定空闲端口
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # 启动 MCP HTTP 子进程
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # 轮询探测端口就绪状态
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("MCP HTTP service exited before becoming ready")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("MCP HTTP service did not start")
        # 通过 HTTP URL 建立 MCP 连接
        async with MCPConnection(
            f"http://127.0.0.1:{port}/mcp", tools=["route_info"]
        ) as c:
            r = ToolRegistry()
            c.register_tools(r)
            # 执行工具调用验证
            result = await r.aexecute_tool("route_info", {"place": "lake"})
            assert result.data["mcp"]["structuredContent"]["walking_km"] == 4
    finally:
        # 清理释放子进程资源
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
