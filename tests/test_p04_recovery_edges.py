"""P04：从固定快照提取当前阶段原定义，断言保持原样。"""
import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from hello_agents import SimpleAgent, Config, ToolRegistry, AgentComponents
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.core.runtime import deadline
from hello_agents.core.llm_adapters import BaseLLMAdapter


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



def test_cancellation_keeps_completed_tool_receipt(tmp_path):
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x) or "receipt-1", name="write")
    llm = ScriptedLLM([response(calls=[ToolCall("a", "write", '{"input":"once"}')])])
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                if event.type == StreamEventType.TOOL_CALL_FINISH:
                    break
        finally:
            await stream.aclose()

    asyncio.run(main())
    assert writes == ["once"]
    assert any("receipt-1" in m.content for m in agent.get_history())
    assert agent.last_run.status == "cancelled"



def test_deadline_clears_only_its_own_cancellation_request():
    async def main():
        task = asyncio.current_task()
        before = task.cancelling() if hasattr(task, "cancelling") else None
        with pytest.raises(TimeoutError):
            async with deadline(0.001):
                await asyncio.sleep(10)
        if before is not None:
            assert task.cancelling() == before
        assert not task.cancelled()
        await asyncio.sleep(0)

    asyncio.run(main())

