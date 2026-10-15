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



def test_history_committed_and_trace_closed_when_consumer_stops_at_finish(tmp_path):
    cfg = config()
    cfg.trace_enabled = True
    cfg.trace_dir = str(tmp_path)
    agent = SimpleAgent("test", ScriptedLLM([response("answer")]), config=cfg)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                if event.type == StreamEventType.AGENT_FINISH:
                    assert len(agent.get_history()) == 2
                    break
        finally:
            await stream.aclose()
        assert not agent._run_active and agent.trace_logger.jsonl_file.closed

    asyncio.run(main())



def test_response_positional_compatibility():
    assert LLMResponse("x", "m", {}, 1, "reasoning").reasoning_content == "reasoning"



def test_stream_adapter_assembles_fragments_and_usage_footer(monkeypatch):
    from hello_agents.core.llm_adapters import OpenAIAdapter

    calls = []

    def delta(text=None, fragments=None, reason=None):
        return NS(
            usage=None,
            choices=[
                NS(finish_reason=reason, delta=NS(content=text, tool_calls=fragments))
            ],
        )

    class Stream:
        closed = False

        def __aiter__(self):
            async def gen():
                yield delta(
                    fragments=[
                        NS(
                            index=0,
                            id="c",
                            function=NS(name="write", arguments='{"input":'),
                        )
                    ]
                )
                yield delta(
                    fragments=[
                        NS(index=0, id=None, function=NS(name=None, arguments='"ok"}'))
                    ],
                    reason="tool_calls",
                )
                yield NS(choices=[], usage={"total_tokens": 17})

            return gen()

        async def close(self):
            self.closed = True

    stream = Stream()

    class Client:
        closed = False

        async def create(self, **kwargs):
            calls.append(kwargs)
            return stream

        async def close(self):
            self.closed = True

    client = Client()
    client.chat = NS(completions=client)
    adapter = OpenAIAdapter("fake", None, 30, "m")
    monkeypatch.setattr(adapter, "create_async_client", lambda: client)

    async def main():
        return [
            x
            async for x in adapter.astream_invoke_with_tools([], [], tool_choice="auto")
        ]

    output = asyncio.run(main())[-1]
    assert json.loads(output.tool_calls[0].arguments) == {"input": "ok"}
    assert output.finish_reason == "tool_calls" and output.usage["total_tokens"] == 17
    assert stream.closed and client.closed
    assert "tools" not in calls[0] and "tool_choice" not in calls[0]



def test_native_async_tool_timeout_is_reported():
    async def slow(value):
        await asyncio.sleep(1)

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    agent = SimpleAgent(
        "test",
        ScriptedLLM([response(calls=[ToolCall("c", "slow", '{"input":"x"}')])]),
        config=config(tool_async_timeout=0.01),
        tool_registry=registry,
    )
    with pytest.raises(TimeoutError):
        agent.run("task")
    assert agent.last_run.status == "failed" and not agent._run_active



def test_model_timeout_cancels_stream_and_releases_agent():
    class SlowLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            await asyncio.sleep(1)
            yield response("late")

    agent = SimpleAgent("test", SlowLLM([]), config=config(llm_async_timeout=0.01))
    with pytest.raises(TimeoutError):
        execute(agent, "arun_stream")
    assert agent.last_run.status == "failed" and not agent._run_active



def test_stream_close_reaches_provider_before_return_and_ignores_consumer_delay():
    closed = []

    class SlowConsumerLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            try:
                yield "part"
                yield response("part")
            finally:
                closed.append(True)

    async def main():
        agent = SimpleAgent(
            "test", SlowConsumerLLM([]), config=config(llm_async_timeout=0.01)
        )
        stream = agent.arun_stream("task")
        async for event in stream:
            if event.type == StreamEventType.LLM_CHUNK:
                await asyncio.sleep(0.03)
                await stream.aclose()
                assert closed == [True]
                assert agent.last_run.status == "cancelled" and not agent._run_active
                break

    asyncio.run(main())

