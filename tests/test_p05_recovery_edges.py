"""P05 策略异常恢复与边缘边界测试套件。

验证复杂策略在异常与中断场景下的系统容错能力：
1. 并发坏参数容错：损坏的工具参数转化为 error 观察回执，避免进程崩溃并驱动模型纠错；
2. 并发超时与兄弟调用回执隔离：单工具超时时，已成功的兄弟工具回执被保留，超时的工具补齐 unknown 回执；
3. 规划阶段工具策略保护：外层配置的 forced tool_choice 仅作用于业务执行，不污染规划器的 generate_plan 工具调用；
4. 规划步骤间取消：在多步骤执行过程中途被取消时，状态正确置为 cancelled，杜绝虚假标记为 completed。
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
        result = self.invoke(messages, **kwargs)
        if result.content:
            yield result.content
        yield result



def response(text="", calls=(), reason="stop"):
    """构造统一的 LLMToolResponse 响应。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def test_parallel_malformed_arguments_are_observations_not_crashes():
    """验证：并发工具调用中传入非法 JSON 结构时，转化为 error 状态观察回执而非崩溃。"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    # 工具 a 传入数组而非字典对象，工具 b 传入合法参数
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
    # 断言：模型收到 error 观察后在下一轮自愈纠偏，返回 corrected
    assert agent.run("task") == "corrected"
    assert "error" in llm.messages[-1][-2]["content"]



def assert_closed(history):
    """断言辅助函数：核验历史序列中每个 tool_call 均严格有对应的 tool 回执闭合。"""
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
    """验证：并发工具超时时，已成功的兄弟工具保留正常回执，超时工具补齐 unknown 防御性回执。"""
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
    
    # 期望捕获超时异常
    with pytest.raises(TimeoutError):
        agent.run("task")
        
    # 断言 1：运行状态记为 failed，记录了 1 次成功的工具调用
    assert agent.last_run.status == "failed"
    assert agent.last_run.tool_calls == 1
    # 断言 2：历史消息序列保持严格闭合（大模型供应商契约）
    assert_closed(agent.get_history())
    
    # 断言 3：工具 a 保留成功回执，超时的工具 b 被填充为 unknown 状态
    receipts = {
        m.metadata["model_message"]["tool_call_id"]: m.content
        for m in agent.get_history()
        if m.role == "tool"
    }
    assert "receipt-fast" in receipts["a"]
    assert json.loads(receipts["b"])["execution_status"] == "unknown"



def test_plan_agent_forced_business_tool_does_not_override_planning_tool():
    """验证：用户为业务执行指定的 tool_choice='required' 不会覆盖规划阶段的 'auto' 策略。"""
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
    # 断言：第一轮（规划）使用 auto，后续轮次（业务步骤执行）使用 required
    assert seen == [
        "auto",
        "required",
    ]



def test_cancel_between_plan_steps_is_not_reported_as_completed():
    """验证：在分步执行阶段之间被消费端取消时，运行状态为 cancelled 而非 completed。"""
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
                # 第一步执行完成（STEP_FINISH）后立刻中断消费并关闭流
                if (
                    event.type == StreamEventType.STEP_FINISH
                    and event.data.get("phase") == "execution"
                ):
                    break
        finally:
            await source.aclose()

    asyncio.run(main())
    # 断言：状态明确流转为 cancelled，两步计划中仅发起了两轮请求（规划 + 步骤一）
    assert agent.last_run.status == "cancelled"
    assert len(model.messages) == 2

