"""P03 的完整协议场景：真实函数副作用、消息配对和模型调用统计。"""

import asyncio
import json
from copy import deepcopy

import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


class Model:
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def invoke(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return next(self.responses)

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)


def execute(agent, mode):
    if mode == "run":
        return agent.run("完成记录")
    return asyncio.run(agent.arun("完成记录"))


@pytest.mark.parametrize("mode", ["run", "arun"])
def test_tool_receipt_drives_second_model_request(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or "已记录", name="record")
    model = Model([
        LLMToolResponse(None, [ToolCall("c1", "record", '{"input":"evidence"}')],
                        "offline", usage={"total_tokens": 3}),
        LLMToolResponse("完成", [], "offline", usage={"total_tokens": 3}),
    ])
    agent = SimpleAgent("test", model, tool_registry=registry)
    assert execute(agent, mode) == "完成"
    assert writes == ["evidence"]
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 1
    assert agent.last_run.status == "completed" and agent.last_run.usage["total_tokens"] == 6
    assert len(model.requests[0]) == 1
    assert [item["role"] for item in model.requests[1]] == ["user", "assistant", "tool"]
    assert model.requests[1][-1]["tool_call_id"] == "c1"
    receipt = json.loads(model.requests[1][-1]["content"])
    assert receipt["status"] == "success" and receipt["data"]["output"] == "已记录"
    assert [item.role for item in agent.get_history()] == ["user", "assistant", "tool", "assistant"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["run", "arun"])
def test_bad_tool_arguments_become_observations_before_continuing(mode):
    seen = []
    registry = ToolRegistry()
    registry.register_function(lambda value: seen.append(value) or value, name="echo")
    model = Model([
        LLMToolResponse(None, [
            ToolCall("bad-json", "echo", "{"),
            ToolCall("bad-object", "echo", "[]"),
            ToolCall("good", "echo", '{"input":"ok"}'),
        ], "offline"),
        LLMToolResponse("corrected", [], "offline"),
    ])
    agent = SimpleAgent("test", model, tool_registry=registry)
    assert execute(agent, mode) == "corrected"
    assert seen == ["ok"]
    receipts = [message for message in model.requests[-1] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in receipts] == ["bad-json", "bad-object", "good"]
    assert [json.loads(message["content"])["status"] for message in receipts] == ["error", "error", "success"]
