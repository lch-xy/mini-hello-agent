"""P03 SimpleAgent 原始运行断言；其他策略与原生流式在后续接通。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from hello_agents import SimpleAgent
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.tools import ToolRegistry

def config(**kwargs):
    return Config(
        **dict(
            trace_enabled=False,
            session_enabled=False,
            skills_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
            **kwargs,
        )
    )


class ScriptedLLM:
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        response = self.invoke(messages, **kwargs)
        if response.content:
            yield response.content
        yield response


def response(text="", calls=(), reason="stop"):
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def execute(agent, mode):
    if mode == "run":
        return agent.run("完成任务")
    if mode == "stream_run":
        return "".join(agent.stream_run("完成任务"))

    async def main():
        if mode == "arun":
            return await agent.arun("完成任务")
        events = [e async for e in agent.arun_stream("完成任务")]
        return events[-1].data["result"]

    return asyncio.run(main())


@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_errors_are_not_empty_success(mode):
    agent = SimpleAgent(
        "test", ScriptedLLM([RuntimeError("model unavailable")]), config=config()
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        execute(agent, mode)
    assert agent.last_run.status == "failed"
    assert not agent._run_active


def test_output_limit_never_executes_incomplete_tool_request():
    calls = []
    registry = ToolRegistry()
    registry.register_function(lambda value: calls.append(value), name="write")
    agent = SimpleAgent(
        "test",
        ScriptedLLM(
            [response(calls=[ToolCall("c", "write", '{"input":')], reason="length")]
        ),
        config=config(),
        tool_registry=registry,
    )
    assert agent.run("write") == ""
    assert agent.last_run.status == "output_limit" and calls == []


def test_empty_model_response_is_failure():
    from hello_agents.core.runtime import EmptyModelResponse

    agent = SimpleAgent("test", ScriptedLLM([response()]), config=config())
    with pytest.raises(EmptyModelResponse):
        agent.run("task")
    assert agent.last_run.status == "failed"


def test_iteration_limit_is_distinct_from_completion():
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    agent = SimpleAgent(
        "test",
        ScriptedLLM(
            [
                response(calls=[ToolCall("c", "echo", '{"input":"x"}')]),
                response("尚未完成"),
            ]
        ),
        config=config(),
        tool_registry=registry,
        max_tool_iterations=1,
    )
    assert agent.run("task") == "尚未完成"
    assert agent.last_run.status == "max_iterations"


def test_next_turn_replays_native_tool_pair():
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = ScriptedLLM(
        [
            response(calls=[ToolCall("c", "echo", '{"input":"x"}')]),
            response("done"),
            response("again"),
        ]
    )
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)
    agent.run("task")
    agent.run("next")
    assert llm.messages[-1][1]["tool_calls"][0]["id"] == "c"
    assert llm.messages[-1][2]["tool_call_id"] == "c"

