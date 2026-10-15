"""P09：保留固定目标的当前原始集成定义。"""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import subprocess
import sys
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, Config, AgentComponents, ToolRegistry
from hello_agents.core.session_store import SessionStore
from hello_agents.core.message import Message
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.observability import TraceLogger


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



def test_trace_statistics_and_legacy_tool_event_fields(tmp_path):
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = ScriptedLLM(
        [response(calls=[ToolCall("c", "echo", '{"input":"x"}')]), response("done")]
    )
    cfg = config()
    cfg.trace_enabled = True
    cfg.trace_dir = str(tmp_path)
    agent = SimpleAgent("test", llm, config=cfg, tool_registry=registry)

    async def main():
        return [e async for e in agent.arun_stream("task")]

    events = asyncio.run(main())
    stats = agent.trace_logger._compute_stats()
    assert stats["model_calls"] == 2 and stats["tool_calls"] == {"echo": 1}
    call = next(e for e in events if e.type == StreamEventType.TOOL_CALL_START)
    assert call.data["tool_name"] == "echo" and call.data["tool_call_id"] == "c"

