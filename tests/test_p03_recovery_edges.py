"""P03 的原始 ID、工具选择与异步模型覆写断言。

本测试套件验证 P03 的边界防御与调用路由：
1. 同批次重复工具 ID 在产生任何副作用前立即报错并阻断；
2. tool_choice 参数在正常轮次送达模型，在步数耗尽的总结轮中被安全剥离；
3. 非流式与带工具调用优先使用原生异步方法 (ainvoke / ainvoke_with_tools)。
"""

import asyncio
from copy import deepcopy
import pytest
from hello_agents import SimpleAgent
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.tools import ToolRegistry


def config(**kwargs):
    """创建测试专用的静默配置。"""
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
    """支持预设响应的脚本模型。"""
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
    """构造响应对象的辅助函数。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def test_duplicate_tool_ids_fail_before_any_execution():
    """验证：同批次重复的工具调用 ID 在执行任何工具前触发 ValueError，且无任何副作用产生。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x), name="write")
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("same", "write", '{"input":"a"}'),
                    ToolCall("same", "write", '{"input":"b"}'),
                ]
            ),
            response("done"),
        ]
    )
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)
    with pytest.raises(ValueError, match="ID"):
        agent.run("task")
    # 断言：由于在执行前被阻断，writes 列表必须保持为空
    assert writes == []


def test_tool_choice_reaches_model_and_is_removed_for_summary():
    """验证：tool_choice 参数在第一轮正常透传，在总结轮被正确剥离，防止大模型继续强行调用工具。"""
    seen = []

    class Model(ScriptedLLM):
        def invoke_with_tools(self, messages, tools, **kwargs):
            seen.append(kwargs["tool_choice"])
            return self.invoke(messages, **kwargs)

        def invoke(self, messages, **kwargs):
            if len(self.messages) > 0:
                # 步数耗尽的总结轮次中，tool_choice 必须被移除
                assert "tool_choice" not in kwargs
            return super().invoke(messages, **kwargs)

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = Model(
        [response(calls=[ToolCall("a", "echo", '{"input":"ok"}')]), response("summary")]
    )
    agent = SimpleAgent(
        "test", llm, config=config(), tool_registry=registry, max_tool_iterations=1
    )
    assert agent.run("task", tool_choice="required") == "summary" and seen == [
        "required"
    ]


def test_nonstream_async_model_override_is_used():
    """验证：当大模型定义了 ainvoke 时，异步入口 arun 必须直接调用 ainvoke 而非同步 invoke。"""
    class Model(ScriptedLLM):
        def invoke(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke(self, messages, **kwargs):
            return response("async")

    agent = SimpleAgent("test", Model([]), config=config())
    assert asyncio.run(agent.arun("task")) == "async"


def test_native_async_tool_model_override():
    """验证：当大模型定义了 ainvoke_with_tools 时，优先调用该原生异步工具方法。"""
    class Model(ScriptedLLM):
        def invoke_with_tools(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke_with_tools(self, messages, tools, **kwargs):
            return response("native")

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    agent = SimpleAgent("test", Model([]), config=config(), tool_registry=registry)
    assert asyncio.run(agent.arun("task")) == "native"
