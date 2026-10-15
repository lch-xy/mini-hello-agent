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



@pytest.mark.parametrize(
    "cls", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_all_entry_points_really_dispatch_tools_and_keep_pairs(cls, mode):
    writes = []
    registry = ToolRegistry()

    async def record(value):
        writes.append(value)
        return value

    registry.register_function(record, name="record")
    calls = [
        response(
            calls=[ToolCall("c1", "record", '{"input":"evidence"}')],
            reason="tool_calls",
        ),
        response("完成"),
    ]
    if cls is PlanSolveAgent:
        calls.insert(
            0,
            response(
                calls=[ToolCall("plan1", "generate_plan", '{"steps":["执行记录"]}')]
            ),
        )
    if cls is ReflectionAgent:
        calls.append(response("无需改进"))
    llm = ScriptedLLM(calls)
    agent = cls("test", llm, config=config(), tool_registry=registry)
    answer = execute(agent, mode)
    assert "完成" in answer
    assert writes == ["evidence"]
    assert agent.last_run.status == "completed"
    assert any(
        m.metadata["model_message"].get("tool_call_id") == "c1"
        for m in agent.get_history()
    )



def test_react_finish_returned_by_text_stream():
    agent = ReActAgent(
        "test",
        ScriptedLLM([response(calls=[ToolCall("f", "Finish", '{"answer":"终点"}')])]),
        config=config(),
    )
    assert "".join(agent.stream_run("task")) == "终点"



def test_planner_rejects_bad_plan_without_executing():
    agent = PlanSolveAgent(
        "test",
        ScriptedLLM(
            [response(calls=[ToolCall("p", "generate_plan", '{"steps":"bad"}')])]
        ),
        config=config(),
    )
    with pytest.raises(ValueError, match="步骤"):
        agent.run("task")
    assert agent.last_run.status == "failed"



def test_factory_simple_keeps_registry():
    from hello_agents.agents.factory import create_agent

    registry = ToolRegistry()
    assert (
        create_agent(
            "simple", "test", ScriptedLLM([]), registry, config()
        ).tool_registry
        is registry
    )



def test_react_parallel_batch_respects_concurrency_limit():
    active = peak = 0

    async def slow(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return value

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    llm = ScriptedLLM(
        [
            response(
                calls=[ToolCall(str(i), "slow", '{"input":"x"}') for i in range(4)]
            ),
            response("done"),
        ]
    )
    agent = ReActAgent(
        "test", llm, config=config(max_concurrent_tools=2), tool_registry=registry
    )
    assert agent.run("task") == "done" and peak == 2



def test_parallel_timeout_does_not_start_queued_write_after_failure():
    writes = []

    async def slow(value):
        await asyncio.sleep(1)

    async def write(value):
        writes.append(value)

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    registry.register_function(write, name="write")
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("s", "slow", '{"input":"x"}'),
                    ToolCall("w", "write", '{"input":"x"}'),
                ]
            )
        ]
    )
    agent = ReActAgent(
        "test",
        llm,
        config=config(max_concurrent_tools=1, tool_async_timeout=0.01),
        tool_registry=registry,
    )

    async def main():
        with pytest.raises(TimeoutError):
            await agent.arun("task")
        await asyncio.sleep(0.03)
        assert writes == []

    asyncio.run(main())

