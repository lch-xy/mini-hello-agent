"""真实回环 HTTP 连接中断契约测试。

验证在本地回环网络环境下，当客户端主动提前中断 HTTP SSE 连接时：
1. 服务端收到 disconnect 信号并迅速向内层异步生成器传播关闭通知；
2. 正在等待长时间 LLM 产出的生成器被取消并在 finally 中触发清理（source_closed）；
3. 服务端在退出时调用模型的 aclose() 回收网络资源（model_closed）；
4. 智能体运行状态被正确置为 "cancelled"，且活跃标记 `_run_active` 恢复为 False。
"""

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

from hello_agents import SimpleAgent
from hello_agents.core.llm_response import LLMToolResponse
from examples.web.fastapi_sse_server import create_app


@pytest.mark.asyncio
async def test_loopback_disconnect_closes_provider_and_owned_model():
    """验证客户端主动断开 SSE 连接后，关闭信号能完整向上传播至模型流与底层连接。"""
    source_closed, model_closed = asyncio.Event(), asyncio.Event()

    class Model:
        model = "offline"

        async def astream_invoke_with_tools(self, messages, tools, **kwargs):
            try:
                # 产出第一个分块后休眠，模拟长耗时生成
                yield "partial"
                await asyncio.sleep(30)
                yield LLMToolResponse("late", [], self.model)
            finally:
                # 连接断开引发 GeneratorExit 或 CancelledError 时，必须触发此 cleanup
                source_closed.set()

        async def aclose(self):
            # 模型连接池显式关闭通知
            model_closed.set()

    # 初始化测试智能体与应用，使用随机可用端口绑定
    agent = SimpleAgent("disconnect", Model())
    app = create_app(lambda kind: agent)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    service = asyncio.create_task(server.serve(sockets=[listener]))

    async def ready():
        while not server.started:
            if service.done():
                await service
                raise RuntimeError("local server stopped before startup")
            await asyncio.sleep(0.01)

    try:
        # 等待服务器启动就绪
        await asyncio.wait_for(ready(), timeout=3)
        # 客户端发起流式请求并在接收到首个 chunk 后立即主动退出 context，断开底层 TCP 连接
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            async with client.stream("POST", f"http://127.0.0.1:{port}/agent/stream",
                                     json={"input": "task"}) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:") and json.loads(line[5:])["type"] == "llm_chunk":
                        break
            # 离开流上下文关闭实际连接，服务端应收到 disconnect。
        # 验证服务端正确传播了中断，使流生成器退出
        await asyncio.wait_for(source_closed.wait(), timeout=3)
        # 验证服务端正常调用了模型的 aclose
        await asyncio.wait_for(model_closed.wait(), timeout=3)
        # 验证智能体状态记录为已取消，且不再处于活跃运行状态
        assert agent.last_run.status == "cancelled" and not agent._run_active
    finally:
        # 停止 uvicorn 服务器并释放端口套接字
        server.should_exit = True
        await asyncio.wait_for(service, timeout=3)
        listener.close()

