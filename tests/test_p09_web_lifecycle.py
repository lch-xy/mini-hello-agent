"""Web 包装层的请求所有权与错误终态。"""

import json

import pytest
from fastapi.testclient import TestClient

from hello_agents import SimpleAgent
from hello_agents.core.llm_response import LLMToolResponse
from examples.web.fastapi_sse_server import create_app


@pytest.mark.parametrize("fail", [False, True])
def test_web_closes_owned_model_after_success_or_failure(fail):
    class Model:
        model = "offline"
        closed = False

        def invoke(self, messages, **kwargs):
            if fail:
                raise RuntimeError("synthetic-upstream-detail")
            return LLMToolResponse("done", [], self.model)

        async def aclose(self):
            self.closed = True

    model = Model()
    agent = SimpleAgent("web", model)
    with TestClient(create_app(lambda kind: agent)) as client:
        response = client.post("/agent/stream", json={"input": "task"})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert response.status_code == 200 and model.closed and not agent._run_active
    if fail:
        assert events[-1]["type"] == "error"
        assert events[-1]["data"]["message"] == "运行失败，请检查服务端配置与日志。"
        assert not any(event["type"] == "agent_finish" for event in events)
    else:
        assert events[-1]["type"] == "agent_finish"
        assert events[-1]["data"]["status"] == "completed"
