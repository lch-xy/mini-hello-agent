"""真实回环 HTTP 连接中断，验证 SSE 关闭传播；不访问外部服务。"""

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
    source_closed, model_closed = asyncio.Event(), asyncio.Event()

    class Model:
        model = "offline"

        async def astream_invoke_with_tools(self, messages, tools, **kwargs):
            try:
                yield "partial"
                await asyncio.sleep(30)
                yield LLMToolResponse("late", [], self.model)
            finally:
                source_closed.set()

        async def aclose(self):
            model_closed.set()

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
        await asyncio.wait_for(ready(), timeout=3)
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            async with client.stream("POST", f"http://127.0.0.1:{port}/agent/stream",
                                     json={"input": "task"}) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:") and json.loads(line[5:])["type"] == "llm_chunk":
                        break
            # 离开流上下文关闭实际连接，服务端应收到 disconnect。
        await asyncio.wait_for(source_closed.wait(), timeout=3)
        await asyncio.wait_for(model_closed.wait(), timeout=3)
        assert agent.last_run.status == "cancelled" and not agent._run_active
    finally:
        server.should_exit = True
        await asyncio.wait_for(service, timeout=3)
        listener.close()
