"""策略变化不应破坏执行、停止和资源边界。"""

import asyncio
import json
from copy import deepcopy

import pytest

from hello_agents import PlanSolveAgent, ReActAgent, ReflectionAgent, ToolRegistry
from hello_agents.agents.factory import create_agent
from hello_agents.agents.plan_solve_agent import Executor
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType


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


def answer(text, usage=1):
    return LLMToolResponse(text, [], "offline", usage={"total_tokens": usage})


def call(name, parameters, identity="c"):
    return ToolCall(identity, name, json.dumps(parameters, ensure_ascii=False))


def test_react_mixed_control_batch_is_serial_and_finish_blocks_later_write():
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or value, name="write")
    model = Model([LLMToolResponse(None, [
        call("Thought", {"reasoning": "先记录再完成"}, "t"),
        call("write", {"input": "before"}, "w1"),
        call("Finish", {"answer": "done"}, "f"),
        call("write", {"input": "after"}, "w2"),
    ], "offline")])
    agent = ReActAgent("react", model, tool_registry=registry)

    async def forbidden_parallel(*args, **kwargs):
        raise AssertionError("control batch entered business parallel path")

    agent._execute_tools_async = forbidden_parallel
    assert agent.run("task") == "done" and writes == ["before"]
    receipts = [m.metadata["model_message"] for m in agent.get_history() if m.role == "tool"]
    assert [m["tool_call_id"] for m in receipts] == ["t", "w1", "f", "w2"]
    assert json.loads(receipts[-1]["content"])["status"] == "error"
    assert agent.last_run.model_calls == 1 and agent.last_run.status == "completed"


@pytest.mark.parametrize("name", ["Thought", "Finish"])
def test_react_reserved_names_are_rejected_before_model_request(name):
    registry = ToolRegistry()
    registry.register_function(lambda value: value, name=name)
    model = Model([])
    agent = ReActAgent("react", model, tool_registry=registry)
    with pytest.raises(ValueError, match="保留名称"):
        agent.run("task")
    assert model.requests == []


@pytest.mark.parametrize("feedback", ["无需改进", "No need for improvement"])
def test_reflection_returns_execution_and_resets_trajectory_per_task(feedback):
    model = Model([answer("first", 2), answer(feedback, 3), answer("second", 5), answer(feedback, 7)])
    agent = ReflectionAgent("reflection", model, max_iterations=1)
    assert agent.run("task one") == "first"
    assert agent.last_run.model_calls == 2 and agent.last_run.usage["total_tokens"] == 5
    assert agent.run("task two") == "second"
    assert agent.last_run.usage["total_tokens"] == 12
    assert agent.memory.records == [
        {"type": "execution", "content": "second"},
        {"type": "reflection", "content": feedback},
    ]


def test_zero_reflections_and_incomplete_execution_do_not_start_review():
    model = Model([answer("one")])
    agent = ReflectionAgent("zero", model, max_iterations=0)
    assert agent.run("task") == "one" and len(model.requests) == 1
    limited = Model([LLMToolResponse("partial", [], "offline", finish_reason="length")])
    agent = ReflectionAgent("limited", limited)
    assert agent.run("task") == "partial"
    assert agent.last_run.status == "output_limit" and len(limited.requests) == 1


def test_refinement_accumulates_usage_and_has_distinct_phase_events():
    model = Model([answer("draft", 2), answer("需要补充依据", 3), answer("improved", 5)])
    agent = ReflectionAgent("refine", model, max_iterations=1)

    async def collect():
        return [event async for event in agent.arun_stream("task")]

    events = asyncio.run(collect())
    assert agent.last_run.answer == "improved" and agent.last_run.usage["total_tokens"] == 10
    assert agent.last_run.model_calls == 3
    assert [e.data["phase"] for e in events if e.type == StreamEventType.STEP_START] == [
        "initial_execution", "reflection", "refinement",
    ]
    assert any(e.type == StreamEventType.THINKING for e in events)


@pytest.mark.parametrize("steps", [[], "one", [""], ["  "], [1], {"step": "one"}])
def test_invalid_plan_fails_before_business_side_effect(steps):
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value), name="write")
    agent = PlanSolveAgent("plan", Model([
        LLMToolResponse(None, [call("generate_plan", {"steps": steps})], "offline")
    ]), tool_registry=registry)
    with pytest.raises(ValueError, match="计划"):
        agent.run("task")
    assert writes == [] and agent.last_run.status == "failed"


def test_plan_preserves_caller_role_step_context_and_usage():
    model = Model([
        LLMToolResponse(None, [call("generate_plan", {"steps": ["collect", "report"]})],
                        "offline", usage={"total_tokens": 2}),
        answer("fact", 3), answer("report", 5),
    ])
    agent = PlanSolveAgent("plan", model, system_prompt="caller rules", executor_prompt="executor rules")
    assert agent.run("task") == "report"
    assert agent.last_run.model_calls == 3 and agent.last_run.usage["total_tokens"] == 10
    assert model.requests[1][0]["content"] == "executor rules"
    assert model.requests[1][1]["content"] == "caller rules"
    assert "collect" in model.requests[-1][-1]["content"]
    assert "fact" in model.requests[-1][-1]["content"]


def test_executor_borrows_registry_and_rejects_incomplete_step():
    registry = ToolRegistry()
    registry.register_function(lambda value: value, name="echo")
    executor = Executor(Model([answer("step one"), answer("step two")]), tool_registry=registry)
    assert executor.execute("task", ["one", "two"]) == "step two"
    assert registry.list_tools() == ["echo"]
    executor = Executor(Model([
        LLMToolResponse("partial", [], "offline", finish_reason="length")
    ]), tool_registry=registry)
    with pytest.raises(RuntimeError, match="未完成"):
        executor.execute("task", ["one", "must not start"])
    assert len(executor.llm_client.requests) == 1


@pytest.mark.parametrize("kind,expected", [("react", ReActAgent), ("reflection", ReflectionAgent), ("plan", PlanSolveAgent)])
def test_factory_passes_shared_dependencies(kind, expected):
    registry, model, config = ToolRegistry(), Model([]), Config()
    agent = create_agent(kind, "name", model, registry, config, "caller rules")
    assert isinstance(agent, expected)
    assert agent.llm is model and agent.tool_registry is registry
    assert agent.config is config and agent.system_prompt == "caller rules"
