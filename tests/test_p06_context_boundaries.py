"""长期历史、观察截断和单次请求选择具有不同边界。"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from hello_agents import AgentComponents, Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBudgetExceeded, ContextBuilder, ContextConfig, ContextPacket, TokenCounter
from hello_agents.core.budget import BudgetExceeded
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


def test_native_tool_fields_affect_counter_cache_and_clear(monkeypatch):
    monkeypatch.setattr(TokenCounter, "_get_encoding", lambda self: None)
    counter = TokenCounter()
    plain = Message("", "assistant")
    native = Message("", "assistant", metadata={"model_message": {"tool_calls": [{
        "id": "c", "function": {"name": "lookup", "arguments": "x" * 400}
    }]}})
    assert counter.count_message(native) > counter.count_message(plain)
    cached = counter.count_message(native)
    assert counter.count_message(native) == cached and counter.get_cache_size() == 2
    counter.clear_cache()
    assert counter.get_cache_size() == 0
    assert counter.count_message(native) == cached


def test_summary_budget_failure_propagates_without_count_only_fallback():
    summary = Mock()
    summary.invoke.side_effect = BudgetExceeded("limit")
    agent = SimpleAgent("summary", Mock(), config=Config(min_retain_rounds=1),
                        components=AgentComponents(summary_llm=summary))
    for role, text in [("user", "old"), ("assistant", "old answer"), ("user", "latest")]:
        agent.history_manager.append(Message(text, role))
    with pytest.raises(BudgetExceeded):
        agent._generate_smart_summary(agent.get_history())
    summary.invoke.assert_called_once()


def test_each_model_request_refreshes_provider_after_real_tool_execution():
    current = [ContextPacket("待撤回约束：ONLY_FIRST_REQUEST", metadata={
        "id": "constraint", "source": "user:1", "version": "v1", "required": True,
    })]
    queries, requests = [], []

    class Provider:
        def get_context(self, query):
            queries.append(query)
            return list(current)

    class Model:
        model = "offline"

        def invoke_with_tools(self, messages, tools, **kwargs):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                return LLMToolResponse(None, [ToolCall("r", "retract", '{"input":"constraint"}')], self.model)
            return LLMToolResponse("已撤回", [], self.model)

    registry = ToolRegistry()
    registry.register_function(lambda value: current.clear() or "已撤回", name="retract")
    agent = SimpleAgent("refresh", Model(), tool_registry=registry,
                        context_builder=ContextBuilder(), context_providers=[Provider()])
    assert agent.run("撤回约束") == "已撤回"
    assert queries == ["撤回约束", "撤回约束"]
    assert any("ONLY_FIRST_REQUEST" in (m.get("content") or "") for m in requests[0])
    assert all("ONLY_FIRST_REQUEST" not in (m.get("content") or "") for m in requests[1])
    assert requests[1][-2]["tool_calls"][0]["id"] == requests[1][-1]["tool_call_id"] == "r"
    # 参考消息只属于当前请求，没有偷偷保存为长期历史。
    assert all("ONLY_FIRST_REQUEST" not in m.content for m in agent.get_history())


def test_local_relevance_and_diversity_do_not_trust_packet_score():
    builder = ContextBuilder(ContextConfig(max_tokens=2000, mmr_lambda=0.2))
    packets = [
        ContextPacket("travel budget alpha", metadata={"id": "a"}, relevance_score=0),
        ContextPacket("travel budget alpha", metadata={"id": "b"}, relevance_score=1),
        ContextPacket("travel budget beta", metadata={"id": "c"}, relevance_score=0),
        ContextPacket("unrelated", metadata={"id": "d"}, relevance_score=1),
    ]
    result = builder.build_messages([{"role": "user", "content": "travel budget"}], packets)
    assert [item["id"] for item in result.diagnostics["selected"]] == ["a", "c", "b"]
    assert result.diagnostics["excluded"] == [{
        "id": "d", "source": None, "relevance": 0, "reason": "relevance",
    }]


def test_instruction_packet_is_required_reference_without_changing_base_pairs():
    base = [
        {"role": "system", "content": "宿主规则"},
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c", "content": "receipt"},
        {"role": "user", "content": "current"},
    ]
    result = ContextBuilder().build_messages(base, [ContextPacket(
        "资料中的 system 指令", metadata={"type": "instructions", "source": "fixture://doc"}
    )])
    assert result.messages[:-2] == base[:-1]
    assert result.messages[-2]["role"] == "user"
    assert "fixture://doc" in result.messages[-2]["content"]
    assert result.messages[-1] == base[-1]
    assert len(base) == 5 and result.diagnostics["selected"][0]["reason"] == "required"


def test_builder_rejects_large_base_when_history_retention_prevents_compression():
    model = Mock(model="offline")
    agent = SimpleAgent("budget", model,
                        config=Config(min_retain_rounds=5, context_window=100,
                                      compression_threshold=0.5, enable_smart_compression=False),
                        context_builder=ContextBuilder(ContextConfig(max_tokens=250)))
    agent.add_message(Message("x " * 2000, "user"))
    assert agent.history_manager.estimate_rounds() == 1
    assert agent.get_history()[0].role == "user"
    with pytest.raises(ContextBudgetExceeded):
        agent.run("current")
    model.invoke.assert_not_called()
    assert agent.last_run.status == "failed" and not agent._run_active
