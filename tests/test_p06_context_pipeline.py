"""P06 当前可运行的原始上下文契约，保留原定义。"""
import asyncio
from copy import deepcopy
import json
import pytest
from hello_agents import Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBuilder, ContextConfig, ContextPacket, ContextBudgetExceeded, ContextBuildResult
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



def test_chinese_selection_source_roles_and_diagnostics():
    builder = ContextBuilder(ContextConfig(max_tokens=1500))
    evidence = ContextPacket(
        "杭州旅行每天步行不超过五公里", metadata={"id": "source-1", "source": "user:1"}
    )
    irrelevant = ContextPacket("zzzz unrelated", metadata={"id": "other"})
    base = [
        {"role": "system", "content": "遵守任务约束"},
        {"role": "user", "content": "安排杭州旅行"},
    ]
    result = builder.build_messages(base, (p for p in (evidence, irrelevant)))
    assert result.messages[0] == base[0] and result.messages[-1] == base[-1]
    assert result.messages[1]["role"] == "user"
    assert "source-1" in result.messages[1]["content"]
    assert result.diagnostics["selected"][0]["id"] == "source-1"
    assert result.diagnostics["excluded"][0]["reason"] == "relevance"
    assert result.diagnostics["is_estimate"] is True
    assert len(base) == 2



def test_budget_protects_task_schema_and_required_packets():
    base = [{"role": "system", "content": "规则"}, {"role": "user", "content": "旅行"}]
    builder = ContextBuilder(ContextConfig(max_tokens=250))
    with pytest.raises(ContextBudgetExceeded) as exc:
        builder.build_messages(base, tool_schemas=[{"description": "x " * 500}])
    assert exc.value.diagnostics["tool_schema_tokens"] > 0
    with pytest.raises(ContextBudgetExceeded):
        builder.build_messages(
            base, [ContextPacket("x " * 500, metadata={"required": True})]
        )
    result = builder.build_messages(base, [ContextPacket("旅行" * 1000)])
    assert result.messages == base
    assert result.diagnostics["excluded"][0]["reason"] == "budget"



@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_all_simple_agent_paths_receive_context_and_summary(mode):
    llm = CapturingLLM()
    agent = SimpleAgent("test", llm, config=config(), context_builder=ContextBuilder())
    agent.add_message(Message("历史决定：住在西湖附近", "summary"))
    kwargs = {"context_packets": (p for p in [ContextPacket("杭州旅行预算1500元")])}
    if mode == "run":
        agent.run("安排杭州旅行", **kwargs)
    elif mode == "arun":
        asyncio.run(agent.arun("安排杭州旅行", **kwargs))
    elif mode == "stream_run":
        list(agent.stream_run("安排杭州旅行", **kwargs))
    else:

        async def consume():
            return [
                event async for event in agent.arun_stream("安排杭州旅行", **kwargs)
            ]

        asyncio.run(consume())
    sent = json.dumps(llm.messages[0], ensure_ascii=False)
    assert "1500" in sent and "历史摘要" in sent
    assert all(m["role"] != "summary" for m in llm.messages[0])
    assert agent.last_context_diagnostics["estimated_tokens"] > 0



def test_no_silent_context_ignored_and_default_unchanged():
    llm = CapturingLLM()
    agent = SimpleAgent("test", llm, config=config())
    with pytest.raises(ValueError):
        agent.run("hello", context_packets=[ContextPacket("world")])
    agent.run("hello")
    assert llm.messages == [[{"role": "user", "content": "hello"}]]

