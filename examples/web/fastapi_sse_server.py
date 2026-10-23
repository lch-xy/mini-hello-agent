"""FastAPI Server-Sent Events (SSE) 服务端实现。

提供基于 HTTP 协议的 Agent 实时流式响应端点：
- 访问 GET `/` 获取原生 HTML/JS 前端测试控制台（sse_client.html）；
- 请求 POST `/agent/stream` 建立 SSE 事件流（media_type="text/event-stream"）；
- 默认采用离线预设响应，设置环境变量 `HELLOAGENTS_DEMO_LIVE=1` 并配置大模型环境变量后可直连真实模型。

关键架构与安全考量：
1. 请求作用域隔离：每次 HTTP 请求均独立创建全新的 Agent、ToolRegistry 和 LLM 会话，彻底杜绝并发竞态与会话污染；
2. 连接生命周期管理：通过 `contextlib.aclosing` 确保异步生成器在连接正常结束或客户端提前断开（disconnect）时优雅关闭；
3. 底层资源回收：在 `finally` 块中通过反射检测并安全执行 `agent.llm.aclose()` 回收底层 HTTP 连接池；
4. 异常防御脱敏：捕获所有异常并转换为标准脱敏的 `StreamEventType.ERROR` 事件，杜绝向前端暴露服务端路径、内部堆栈或大模型凭据。
"""

import os
from pathlib import Path
from contextlib import aclosing
from typing import Literal
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from hello_agents import (
    SimpleAgent,
    ReActAgent,
    ReflectionAgent,
    PlanSolveAgent,
    ToolRegistry,
)
from hello_agents.tools.builtin import CalculatorTool
from hello_agents.core.streaming import StreamEvent, StreamEventType
from examples._support import demo_config, demo_llm, response, tool_response


class AgentRequest(BaseModel):
    """前端流式请求参数模型。"""
    input: str = Field(min_length=1, max_length=10000, description="用户输入的提示词或任务指令")
    agent_type: Literal["simple", "react", "reflection", "plan"] = Field(
        default="simple", description="目标智能体架构类型"
    )


def build_agent(kind):
    """根据请求类型动态创建独立的智能体实例。

    针对不同架构类型（simple, react, reflection, plan）预装配对应的离线模型响应列表与必要工具。
    """
    # 每次请求独立创建 Agent、工具注册表和模型会话。
    fixtures = [response("离线演示回答：已收到任务。")]
    if kind == "reflection":
        fixtures.append(response("无需改进"))
    elif kind == "plan":
        fixtures.insert(0, tool_response("generate_plan", {"steps": ["回答用户问题"]}))
    registry = ToolRegistry()
    registry.register_tool(CalculatorTool())
    cls = dict(
        simple=SimpleAgent,
        react=ReActAgent,
        reflection=ReflectionAgent,
        plan=PlanSolveAgent,
    )[kind]
    return cls(
        "SSE-" + kind,
        demo_llm(fixtures, live=os.getenv("HELLOAGENTS_DEMO_LIVE") == "1"),
        tool_registry=registry,
        config=demo_config(),
    )


def create_app(agent_factory=build_agent):
    """构建并配置 FastAPI 应用实例，支持注入自定义 agent_factory 以便单元测试。"""
    app = FastAPI(title="HelloAgents SSE")

    @app.get("/")
    async def root():
        """提供前端静态演示页面。"""
        return FileResponse(Path(__file__).with_name("sse_client.html"))

    @app.post("/agent/stream")
    async def agent_stream(request: AgentRequest):
        """核心 SSE 流式端点：实时产出智能体思考、工具调用与文本片段事件。"""
        agent = agent_factory(request.agent_type)

        async def events():
            try:
                # 使用 aclosing 确保客户端中断或发生异常时，底层异步生成器能够得到关闭通知
                async with aclosing(agent.arun_stream(request.input)) as source:
                    async for event in source:
                        yield event.to_sse()
            except Exception:
                # 安全兜底：不把凭据、服务端路径或上游原始错误发送给浏览器，返回统一脱敏提示
                yield StreamEvent.create(
                    StreamEventType.ERROR,
                    agent.name,
                    message="运行失败，请检查服务端配置与日志。",
                ).to_sse()
            finally:
                # 无论正常结束还是异常中断，均显式回收 Agent 底层模型的异步连接资源
                close = getattr(type(agent.llm), "aclose", None)
                if close is not None:
                    await agent.llm.aclose()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

