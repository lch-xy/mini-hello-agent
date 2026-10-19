"""P03 SimpleAgent 原始运行断言；其他策略与原生流式在后续接通。

本测试套件验证 P03 运行时的核心契约（Runtime Contract）：
1. 异常不吞咽并标为 failed；
2. 输出 Token 截断时绝不执行残缺参数的工具调用；
3. 模型空响应抛出专用异常 EmptyModelResponse；
4. 工具迭代上限后允许做最后总结，但状态标记为 max_iterations；
5. 多轮对话中，第二轮请求无损重放第一轮的原生工具调用与回执。
"""

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
    """创建关闭除核心工具循环外所有特性的静默配置。"""
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
    """可配置预设响应序列的 Mock LLM。"""
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []  # 记录所有接收到的历史请求

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
    """创建标准 LLMToolResponse 对象的辅助方法。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def execute(agent, mode):
    """多入口分发执行器，验证四种公开入口行为的一致性。"""
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
    """验证：当大模型抛出底层错误时，所有入口均向外抛出异常，状态置为 failed 且释放并发锁。"""
    agent = SimpleAgent(
        "test", ScriptedLLM([RuntimeError("model unavailable")]), config=config()
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        execute(agent, mode)
    assert agent.last_run.status == "failed"
    assert not agent._run_active  # 确保 finally 释放了并发锁


def test_output_limit_never_executes_incomplete_tool_request():
    """验证：当模型输出被 Token 上限截断（length）时，严禁执行不完整的工具参数。"""
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
    # 状态应为 output_limit，且注册的工具未被调用
    assert agent.last_run.status == "output_limit" and calls == []


def test_empty_model_response_is_failure():
    """验证：模型既无可见文本也无工具调用时，必须抛出 EmptyModelResponse。"""
    from hello_agents.core.runtime import EmptyModelResponse

    agent = SimpleAgent("test", ScriptedLLM([response()]), config=config())
    with pytest.raises(EmptyModelResponse):
        agent.run("task")
    assert agent.last_run.status == "failed"


def test_iteration_limit_is_distinct_from_completion():
    """验证：达到最大工具轮数上限后，允许最后一次总结调用，但状态必须标为 max_iterations。"""
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
    # 状态必须是 max_iterations，不能伪装为 completed
    assert agent.last_run.status == "max_iterations"


def test_next_turn_replays_native_tool_pair():
    """验证：第二轮对话时，大模型接收到的历史能够 100% 原生回放第一轮的工具调用与回执对。"""
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
    # 核验在第二轮对话发起的请求中，索引 1 和 2 完整保留了原生 tool_calls 与 tool_call_id
    assert llm.messages[-1][1]["tool_calls"][0]["id"] == "c"
    assert llm.messages[-1][2]["tool_call_id"] == "c"
