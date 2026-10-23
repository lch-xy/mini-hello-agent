"""P09 Web SSE 示例服务端到端集成测试。

系统验证 FastAPI SSE 服务在各类型智能体下的核心端点契约：
1. 四类智能体架构（simple, react, reflection, plan）的完整流式事件生命周期（以 agent_start 开始，以 agent_finish 结束）；
2. 每次请求独立实例化验证：连续两次调用产生互不相同的 Agent 实例，确保无跨请求污染；
3. 输入参数边界校验：空字符串输入被 Pydantic 模型精确拦截并返回 HTTP 422；
4. 静态页面端点可访问性：GET / 正确返回 HTML 客户端。
"""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import subprocess
import sys
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, Config, AgentComponents, ToolRegistry
from hello_agents.core.session_store import SessionStore
from hello_agents.core.message import Message
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.observability import TraceLogger
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["simple", "react", "reflection", "plan"])
def test_sse_current_api(kind, monkeypatch):
    """验证四大类智能体通过 FastAPI SSE 服务流式运行的完整契约与隔离性。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    # 将工程根目录加入模块搜索路径，并清除真实在线环境变量
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.delenv("HELLOAGENTS_DEMO_LIVE", raising=False)
    from examples.web.fastapi_sse_server import create_app, build_agent

    agents = []

    def factory(kind):
        # 记录每次请求创建的 agent 实例
        agent = build_agent(kind)
        agents.append(agent)
        return agent

    client = TestClient(create_app(factory))
    # 连续发起两次请求，验证每次均能正常流式返回
    for _ in range(2):
        response = client.post(
            "/agent/stream", json={"input": "你好", "agent_type": kind}
        )
        assert response.status_code == 200
        events = [
            json.loads(line[5:])
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        # 验证首尾事件类型
        assert events[0]["type"] == "agent_start"
        assert events[-1]["type"] == "agent_finish"
        assert events[-1]["data"]["status"] == "completed"
    # 验证两次请求使用的是不同实例（对象引用不相同），保证请求间上下文隔离
    assert agents[0] is not agents[1]
    # 验证 input 为空字符串时被 Pydantic 参数校验拦截返回 422 Unprocessable Entity
    assert (
        client.post("/agent/stream", json={"input": "", "agent_type": kind}).status_code
        == 422
    )
    # 验证根路径静态页面访问正常
    assert client.get("/").status_code == 200


