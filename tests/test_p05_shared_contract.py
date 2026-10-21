"""P05 共享运行时（Shared Runtime）契约测试套件。

验证四类 Agent 策略在统一运行时框架下的强契约一致性：
1. 统一执行矩阵：四种 Agent（Simple / ReAct / Reflection / PlanSolve）与四种入口（run / arun / stream_run / arun_stream）
   下，工具调用副作用、消息配对闭环与状态记录均保持绝对一致；
2. ReAct 内置 Finish 伪工具的流式文本透传；
3. PlanSolveAgent 对坏计划的即时熔断拒绝；
4. create_agent 工厂对工具注册表引用的正确传递；
5. ReActAgent 并发工具执行严格受控于 max_concurrent_tools 并发数；
6. 队列超时熔断：当并发队列中前序任务超时失败时，后续排队中的写操作工具绝不被错误拉起。
"""
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
    """构造轻量纯净的测试配置。"""
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
    """按序返回预设响应的测试脚本化 LLM。"""
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
    """构造统一格式的 LLMToolResponse 辅助函数。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def execute(agent, mode):
    """多模式统一执行辅助函数，支持同步、流式与异步执行。"""
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
    """验证：4种Agent类与4种入口模式构成的4x4矩阵中，工具调度、副作用与消息配对完全符合强契约。"""
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
    # 为特定策略补充前置规划或后置反思响应
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
    
    # 断言 1：正确返回最终答案且真实工具副作用发生了一次
    assert "完成" in answer
    assert writes == ["evidence"]
    # 断言 2：运行状态记为 completed
    assert agent.last_run.status == "completed"
    # 断言 3：历史中必定存在与 tool_call_id="c1" 对应配对的 tool 角色回执
    assert any(
        m.metadata["model_message"].get("tool_call_id") == "c1"
        for m in agent.get_history()
    )



def test_react_finish_returned_by_text_stream():
    """验证：ReActAgent 通过 Finish 伪工具返回的答案能够被同步文本流 (stream_run) 完整输出。"""
    agent = ReActAgent(
        "test",
        ScriptedLLM([response(calls=[ToolCall("f", "Finish", '{"answer":"终点"}')])]),
        config=config(),
    )
    assert "".join(agent.stream_run("task")) == "终点"



def test_planner_rejects_bad_plan_without_executing():
    """验证：当规划器生成非法格式的计划时，立即抛错中断，不盲目启动分步执行。"""
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
    """验证：create_agent 工厂正确透传调用方传入的外部工具注册表引用。"""
    from hello_agents.agents.factory import create_agent

    registry = ToolRegistry()
    assert (
        create_agent(
            "simple", "test", ScriptedLLM([]), registry, config()
        ).tool_registry
        is registry
    )



def test_react_parallel_batch_respects_concurrency_limit():
    """验证：ReActAgent 批量执行并发工具时，瞬时并发数严格遵循 max_concurrent_tools 约束。"""
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
    # 一次性请求 4 个并发工具调用
    llm = ScriptedLLM(
        [
            response(
                calls=[ToolCall(str(i), "slow", '{"input":"x"}') for i in range(4)]
            ),
            response("done"),
        ]
    )
    # 配置信号量上限为 2
    agent = ReActAgent(
        "test", llm, config=config(max_concurrent_tools=2), tool_registry=registry
    )
    # 断言：峰值并发数恰好等于 2，未发生超额并发
    assert agent.run("task") == "done" and peak == 2



def test_parallel_timeout_does_not_start_queued_write_after_failure():
    """验证：当前序并发任务超时失败时，排队等待中的写操作工具被协同取消，绝不启动执行。"""
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
    # 设置最大并发为 1（串行排队）且超时为 0.01 秒
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
        # 断言：后排的 write 工具被熔断取消，副作用列表为空
        assert writes == []

    asyncio.run(main())

