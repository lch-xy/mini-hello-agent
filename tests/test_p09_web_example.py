"""P09：保留固定目标的当前原始集成定义。"""
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
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.delenv("HELLOAGENTS_DEMO_LIVE", raising=False)
    from examples.web.fastapi_sse_server import create_app, build_agent

    agents = []

    def factory(kind):
        agent = build_agent(kind)
        agents.append(agent)
        return agent

    client = TestClient(create_app(factory))
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
        assert events[0]["type"] == "agent_start"
        assert events[-1]["type"] == "agent_finish"
        assert events[-1]["data"]["status"] == "completed"
    assert agents[0] is not agents[1]
    assert (
        client.post("/agent/stream", json={"input": "", "agent_type": kind}).status_code
        == 422
    )
    assert client.get("/").status_code == 200

