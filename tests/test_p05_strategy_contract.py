"""P05 各策略特性与执行边界契约测试套件。

验证策略专有行为、控制流拦截与生命周期边界：
1. ReActAgent 混合控制批次调度：Thought/Finish 控制流与业务工具混杂时串行化执行，且 Finish 阻止后续写操作；
2. ReActAgent 保留名称保护：拦截用户注册 Thought/Finish 命名工具；
3. ReflectionAgent 记忆重置与早停：每轮新任务独立重置 Memory 轨迹，评审命中“无需改进”时早停；
4. ReflectionAgent 零轮反思与异常截断防守：输出超限时直接阻断评审；
5. ReflectionAgent 三阶段流式事件与 Usage 累计；
6. PlanSolveAgent 非法计划即时熔断拒绝，确保副作用零泄漏；
7. PlanSolveAgent 保持调用方系统提示词与步骤上下文编排；
8. Executor 单步执行失败阻断；
9. create_agent 工厂依赖注入完整性。
"""

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
    """测试用 Mock 模型：按顺序返回预设响应。"""
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
    """构建携带文本和 Token 统计的测试响应。"""
    return LLMToolResponse(text, [], "offline", usage={"total_tokens": usage})


def call(name, parameters, identity="c"):
    """构建携带 JSON 序列化参数的测试 ToolCall。"""
    return ToolCall(identity, name, json.dumps(parameters, ensure_ascii=False))


def test_react_mixed_control_batch_is_serial_and_finish_blocks_later_write():
    """验证：ReAct 混合控制流批次按序执行，且 Finish 工具成功拦截后续副作用。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or value, name="write")
    # 模拟在单批工具调用中混杂：Thought、write(before)、Finish、write(after)
    model = Model([LLMToolResponse(None, [
        call("Thought", {"reasoning": "先记录再完成"}, "t"),
        call("write", {"input": "before"}, "w1"),
        call("Finish", {"answer": "done"}, "f"),
        call("write", {"input": "after"}, "w2"),
    ], "offline")])
    agent = ReActAgent("react", model, tool_registry=registry)

    async def forbidden_parallel(*args, **kwargs):
        raise AssertionError("control batch entered business parallel path")

    # 替换并行执行器以验证混合批次走受控的同步串行逻辑
    agent._execute_tools_async = forbidden_parallel
    # 断言 1：返回最终答案 done，仅前半段的 before 成功写入
    assert agent.run("task") == "done" and writes == ["before"]
    
    # 断言 2：四个工具调用均获得对应回执，但排在 Finish 之后的 w2 被标记为 error
    receipts = [m.metadata["model_message"] for m in agent.get_history() if m.role == "tool"]
    assert [m["tool_call_id"] for m in receipts] == ["t", "w1", "f", "w2"]
    assert json.loads(receipts[-1]["content"])["status"] == "error"
    assert agent.last_run.model_calls == 1 and agent.last_run.status == "completed"


@pytest.mark.parametrize("name", ["Thought", "Finish"])
def test_react_reserved_names_are_rejected_before_model_request(name):
    """验证：用户工具试图使用 ReAct 策略保留字命名时，在请求模型前即被抛错拦截。"""
    registry = ToolRegistry()
    registry.register_function(lambda value: value, name=name)
    model = Model([])
    agent = ReActAgent("react", model, tool_registry=registry)
    with pytest.raises(ValueError, match="保留名称"):
        agent.run("task")
    # 断言：模型从未接收到任何请求
    assert model.requests == []


@pytest.mark.parametrize("feedback", ["无需改进", "No need for improvement"])
def test_reflection_returns_execution_and_resets_trajectory_per_task(feedback):
    """验证：反思命中早停短语时采纳上一版产出，并在后续新任务中独立重置记忆轨迹。"""
    model = Model([answer("first", 2), answer(feedback, 3), answer("second", 5), answer(feedback, 7)])
    agent = ReflectionAgent("reflection", model, max_iterations=1)
    
    # 任务 1：产出 first，评审通过
    assert agent.run("task one") == "first"
    assert agent.last_run.model_calls == 2 and agent.last_run.usage["total_tokens"] == 5
    
    # 任务 2：独立执行，不残留任务 1 的记忆
    assert agent.run("task two") == "second"
    assert agent.last_run.usage["total_tokens"] == 12
    # 断言：Memory 仅包含任务 2 的执行与反思记录
    assert agent.memory.records == [
        {"type": "execution", "content": "second"},
        {"type": "reflection", "content": feedback},
    ]


def test_zero_reflections_and_incomplete_execution_do_not_start_review():
    """验证：配置零轮反思或初次执行因长度截断未完成时，均不开启评审步骤。"""
    model = Model([answer("one")])
    agent = ReflectionAgent("zero", model, max_iterations=0)
    assert agent.run("task") == "one" and len(model.requests) == 1
    
    # 初次执行由于 Token 限制异常截断
    limited = Model([LLMToolResponse("partial", [], "offline", finish_reason="length")])
    agent = ReflectionAgent("limited", limited)
    assert agent.run("task") == "partial"
    assert agent.last_run.status == "output_limit" and len(limited.requests) == 1


def test_refinement_accumulates_usage_and_has_distinct_phase_events():
    """验证：反思优化完整流程准确定位三阶段事件并累积所有阶段的 Token 消耗。"""
    model = Model([answer("draft", 2), answer("需要补充依据", 3), answer("improved", 5)])
    agent = ReflectionAgent("refine", model, max_iterations=1)

    async def collect():
        return [event async for event in agent.arun_stream("task")]

    events = asyncio.run(collect())
    # 断言 1：最终答案为优化版，Token 消耗为 2 + 3 + 5 = 10
    assert agent.last_run.answer == "improved" and agent.last_run.usage["total_tokens"] == 10
    assert agent.last_run.model_calls == 3
    # 断言 2：流式事件严格按序产出 initial_execution、reflection 与 refinement 三阶段标记
    assert [e.data["phase"] for e in events if e.type == StreamEventType.STEP_START] == [
        "initial_execution", "reflection", "refinement",
    ]
    assert any(e.type == StreamEventType.THINKING for e in events)


@pytest.mark.parametrize("steps", [[], "one", [""], ["  "], [1], {"step": "one"}])
def test_invalid_plan_fails_before_business_side_effect(steps):
    """验证：规划阶段返回空列表、非列表或无效字符时，在产生业务工具副作用前抛错熔断。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value), name="write")
    agent = PlanSolveAgent("plan", Model([
        LLMToolResponse(None, [call("generate_plan", {"steps": steps})], "offline")
    ]), tool_registry=registry)
    with pytest.raises(ValueError, match="计划"):
        agent.run("task")
    # 断言：业务写操作工具零调用
    assert writes == [] and agent.last_run.status == "failed"


def test_plan_preserves_caller_role_step_context_and_usage():
    """验证：PlanSolveAgent 完整保留调用方系统指令、执行器专用指令与跨步骤历史上下文。"""
    model = Model([
        LLMToolResponse(None, [call("generate_plan", {"steps": ["collect", "report"]})],
                        "offline", usage={"total_tokens": 2}),
        answer("fact", 3), answer("report", 5),
    ])
    agent = PlanSolveAgent("plan", model, system_prompt="caller rules", executor_prompt="executor rules")
    assert agent.run("task") == "report"
    # 断言 1：累计 Token 为 2 + 3 + 5 = 10
    assert agent.last_run.model_calls == 3 and agent.last_run.usage["total_tokens"] == 10
    # 断言 2：执行请求中融合了 executor rules 与 caller rules
    assert model.requests[1][0]["content"] == "executor rules"
    assert model.requests[1][1]["content"] == "caller rules"
    # 断言 3：第二步执行请求包含了第一步的执行结果
    assert "collect" in model.requests[-1][-1]["content"]
    assert "fact" in model.requests[-1][-1]["content"]


def test_executor_borrows_registry_and_rejects_incomplete_step():
    """验证：Executor 单步执行借用外部工具注册表但不污染，并在单步未完成时抛出异常。"""
    registry = ToolRegistry()
    registry.register_function(lambda value: value, name="echo")
    executor = Executor(Model([answer("step one"), answer("step two")]), tool_registry=registry)
    assert executor.execute("task", ["one", "two"]) == "step two"
    assert registry.list_tools() == ["echo"]
    
    # 模拟单步因输出截断未完成
    executor = Executor(Model([
        LLMToolResponse("partial", [], "offline", finish_reason="length")
    ]), tool_registry=registry)
    with pytest.raises(RuntimeError, match="未完成"):
        executor.execute("task", ["one", "must not start"])
    assert len(executor.llm_client.requests) == 1


@pytest.mark.parametrize("kind,expected", [("react", ReActAgent), ("reflection", ReflectionAgent), ("plan", PlanSolveAgent)])
def test_factory_passes_shared_dependencies(kind, expected):
    """验证：create_agent 正确将 model、registry、config 及系统指令注入具体策略实例。"""
    registry, model, config = ToolRegistry(), Model([]), Config()
    agent = create_agent(kind, "name", model, registry, config, "caller rules")
    assert isinstance(agent, expected)
    assert agent.llm is model and agent.tool_registry is registry
    assert agent.config is config and agent.system_prompt == "caller rules"
