"""P03 的原始 ID、工具选择与异步模型覆写断言。"""

import asyncio
from copy import deepcopy
import pytest
from hello_agents import SimpleAgent
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
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
        result = self.invoke(messages, **kwargs)
        if result.content:
            yield result.content
        yield result


def response(text="", calls=(), reason="stop"):
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def test_duplicate_tool_ids_fail_before_any_execution():
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
    assert writes == []


def test_tool_choice_reaches_model_and_is_removed_for_summary():
    seen = []

    class Model(ScriptedLLM):
        def invoke_with_tools(self, messages, tools, **kwargs):
            seen.append(kwargs["tool_choice"])
            return self.invoke(messages, **kwargs)

        def invoke(self, messages, **kwargs):
            if len(self.messages) > 0:
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
    class Model(ScriptedLLM):
        def invoke(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke(self, messages, **kwargs):
            return response("async")

    agent = SimpleAgent("test", Model([]), config=config())
    assert asyncio.run(agent.arun("task")) == "async"


def test_native_async_tool_model_override():
    class Model(ScriptedLLM):
        def invoke_with_tools(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke_with_tools(self, messages, tools, **kwargs):
            return response("native")

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    agent = SimpleAgent("test", Model([]), config=config(), tool_registry=registry)
    assert asyncio.run(agent.arun("task")) == "native"

