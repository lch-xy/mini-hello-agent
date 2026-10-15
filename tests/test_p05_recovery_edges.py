"""P05：保留固定目标原定义与断言。"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, Config, ToolRegistry
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType


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
        result = self.invoke(messages, **kwargs)
        if result.content:
            yield result.content
        yield result



def response(text="", calls=(), reason="stop"):
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def test_parallel_malformed_arguments_are_observations_not_crashes():
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("a", "echo", "[]"),
                    ToolCall("b", "echo", '{"input":"ok"}'),
                ]
            ),
            response("corrected"),
        ]
    )
    agent = ReActAgent("test", llm, config=config(), tool_registry=registry)
    assert agent.run("task") == "corrected"
    assert "error" in llm.messages[-1][-2]["content"]



def assert_closed(history):
    pending = set()
    for item in history:
        native = item.metadata["model_message"]
        if native["role"] == "tool":
            assert native["tool_call_id"] in pending
            pending.remove(native["tool_call_id"])
        else:
            assert not pending
            pending = {c["id"] for c in native.get("tool_calls", [])}
    assert not pending



def test_parallel_timeout_retains_successful_sibling_and_closes_unknown_result():
    registry = ToolRegistry()

    async def operation(value):
        if value == "slow":
            await asyncio.sleep(10)
        return "receipt-fast"

    registry.register_function(operation, name="operation")
    agent = ReActAgent(
        "test",
        ScriptedLLM(
            [
                response(
                    calls=[
                        ToolCall("a", "operation", '{"input":"fast"}'),
                        ToolCall("b", "operation", '{"input":"slow"}'),
                    ]
                )
            ]
        ),
        config=config(tool_async_timeout=0.02),
        tool_registry=registry,
    )
    with pytest.raises(TimeoutError):
        agent.run("task")
    assert agent.last_run.status == "failed"
    assert agent.last_run.tool_calls == 1
    assert_closed(agent.get_history())
    receipts = {
        m.metadata["model_message"]["tool_call_id"]: m.content
        for m in agent.get_history()
        if m.role == "tool"
    }
    assert "receipt-fast" in receipts["a"]
    assert json.loads(receipts["b"])["execution_status"] == "unknown"



def test_plan_agent_forced_business_tool_does_not_override_planning_tool():
    seen = []

    class Model(ScriptedLLM):
        def invoke_with_tools(self, messages, tools, **kwargs):
            seen.append(kwargs["tool_choice"])
            return super().invoke_with_tools(messages, tools, **kwargs)

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = Model(
        [
            response(calls=[ToolCall("p", "generate_plan", '{"steps":["do"]}')]),
            response(calls=[ToolCall("e", "echo", '{"input":"ok"}')]),
            response("done"),
        ]
    )
    agent = PlanSolveAgent(
        "test", llm, config=config(), tool_registry=registry, max_tool_iterations=1
    )
    assert agent.run("task", tool_choice="required") == "done"
    assert seen == [
        "auto",
        "required",
    ]



def test_cancel_between_plan_steps_is_not_reported_as_completed():
    model = ScriptedLLM(
        [
            response(calls=[ToolCall("p", "generate_plan", '{"steps":["one","two"]}')]),
            response("first"),
        ]
    )
    agent = PlanSolveAgent("test", model, config=config())

    async def main():
        source = agent.arun_stream("task")
        try:
            async for event in source:
                if (
                    event.type == StreamEventType.STEP_FINISH
                    and event.data.get("phase") == "execution"
                ):
                    break
        finally:
            await source.aclose()

    asyncio.run(main())
    assert agent.last_run.status == "cancelled"
    assert len(model.messages) == 2

