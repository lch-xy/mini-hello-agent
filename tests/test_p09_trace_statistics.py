"""链路追踪统计计算与工具调用流式事件契约测试。

验证两项核心运行指标与数据契约：
1. 追踪统计指标聚合（`_compute_stats`）：准确统计运行全生命周期中的模型调用次数（model_calls）以及各工具的细分调用频次字典（tool_calls）；
2. 流式事件字段规范：验证 TOOL_CALL_START 事件中包含标准的 tool_name 与 tool_call_id 字段。
"""
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
    """构建单元测试用纯隔离 Config 实例。"""
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
    """可编程的离线模型测试替身，按序返回预设的响应。"""
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
    """构造离线模型响应对象。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def test_trace_statistics_and_legacy_tool_event_fields(tmp_path):
    """验证 TraceLogger 的调用统计分析逻辑与流式 TOOL_CALL_START 事件结构。"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    # 模拟两轮交互：第 1 轮返回工具调用 echo，第 2 轮返回终态文本 "done"
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
    # 1. 验证统计计算结果：模型交互 2 次，工具 echo 执行 1 次
    stats = agent.trace_logger._compute_stats()
    assert stats["model_calls"] == 2 and stats["tool_calls"] == {"echo": 1}
    # 2. 验证产生的流式事件包含标准的工具调用标识
    call = next(e for e in events if e.type == StreamEventType.TOOL_CALL_START)
    assert call.data["tool_name"] == "echo" and call.data["tool_call_id"] == "c"


