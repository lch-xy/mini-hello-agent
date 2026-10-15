"""P13：保留当前阶段依赖已就绪的原始定义。"""
import asyncio
from dataclasses import replace
from copy import deepcopy
import json
import pytest
from hello_agents import SimpleAgent, Config, Message, ToolRegistry
from hello_agents.retrieval import RAGStore, RetrievalPipeline
from hello_agents.tools import RAGTool, ToolStatus
from hello_agents.context import ContextBuilder, ContextBuildResult, RetrievalContextProvider
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall


class CapturingLLM:
    """测试替身：捕获消息并返回预设文本/工具请求，不运行模型。"""

    model = "test-capture"

    def __init__(self, responses=()):
        self.messages = []
        self.schemas = []
        self.responses = iter(responses)

    def invoke(self, messages, **kwargs):
        assert "context_packets" not in kwargs
        self.messages.append(deepcopy(messages))
        return LLMResponse("完成", self.model)

    def invoke_with_tools(self, messages, tools, **kwargs):
        self.messages.append(deepcopy(messages))
        self.schemas.append(deepcopy(tools))
        return next(self.responses)

    def stream_invoke(self, messages, **kwargs):
        self.invoke(messages, **kwargs)
        yield "完成"

    async def astream_invoke(self, messages, **kwargs):
        self.invoke(messages, **kwargs)
        yield "完成"



def config():
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )



def test_native_tool_roundtrip_preserves_source_and_registry(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document("杭州博物馆需要预约", "fixture://notice", version="v2")
    registry = ToolRegistry()
    registry.register_tool(RAGTool(store))
    llm = CapturingLLM(
        [
            LLMToolResponse(
                None, [ToolCall("call-1", "rag_search", '{"query":"杭州预约"}')], "test"
            ),
            LLMToolResponse("请预约", [], "test"),
        ]
    )
    agent = SimpleAgent(
        "test",
        llm,
        config=config(),
        tool_registry=registry,
        context_builder=ContextBuilder(),
    )
    assert agent.run("安排杭州旅行") == "请预约"
    messages = llm.messages[1]
    assert messages[-2]["tool_calls"][0]["id"] == "call-1"
    assert messages[-1]["tool_call_id"] == "call-1"
    data = json.loads(messages[-1]["content"])
    assert data["status"] == "success"
    assert data["data"]["results"][0]["source"] == "fixture://notice"
    assert data["data"]["results"][0]["version"] == "v2"
    assert agent.last_context_diagnostics["tool_schema_tokens"] > 0



def test_custom_assembler_protocol_and_provider(tmp_path):
    class Assembler:
        def build_messages(self, messages, additional_packets=(), tool_schemas=()):
            return ContextBuildResult(
                messages
                + [{"role": "user", "content": p.content} for p in additional_packets],
                {"custom": True},
            )

    store = RAGStore(tmp_path / "rag.db")
    store.add_document("杭州旅行指南", "fixture://travel")
    llm = CapturingLLM()
    agent = SimpleAgent(
        "test",
        llm,
        config=config(),
        context_builder=Assembler(),
        context_providers=[RetrievalContextProvider(store)],
    )
    agent.run("杭州旅行")
    assert llm.messages[0][-1]["content"] == "杭州旅行指南"
    assert agent.last_context_diagnostics == {"custom": True}

