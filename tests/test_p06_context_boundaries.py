"""P06 上下文边界、预算隔离与动态刷新测试套件。

验证上下文构建与历史管理的边界契约：
1. 原生工具字段计数与缓存：tool_calls 结构体参与 Token 统计，且支持计数缓存与显式清理；
2. 预算超限强传播：智能摘要模型触发 BudgetExceeded 时直接向上抛出，严禁降级为简易计数摘要掩盖预算危机；
3. Provider 动态刷新与历史纯净性：工具调用产生副作用后，下一轮模型请求动态刷新 Provider 获取最新资料，且资料绝不渗漏污染长期持久化历史；
4. 相关性自主评估：Builder 基于局部词项/MMR 算法重新评估资料，不轻信外部传入的静态 relevance_score；
5. 指令型资料安全注入：作为参考引用注入 user 消息，不破坏底层已建立的 assistant-tool 消息配对闭环；
6. 窗口硬超限防御：当最少保留轮次限制导致历史无法进一步压缩时，ContextBuilder 显式抛出 ContextBudgetExceeded。
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from hello_agents import AgentComponents, Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBudgetExceeded, ContextBuilder, ContextConfig, ContextPacket, TokenCounter
from hello_agents.core.budget import BudgetExceeded
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


def test_native_tool_fields_affect_counter_cache_and_clear(monkeypatch):
    """验证：原生 tool_calls 结构体准确参与 Token 估算，并验证计数字典缓存与清空机制。"""
    monkeypatch.setattr(TokenCounter, "_get_encoding", lambda self: None)
    counter = TokenCounter()
    plain = Message("", "assistant")
    # 构造携带 400 字符超大工具调用参数的 assistant 消息
    native = Message("", "assistant", metadata={"model_message": {"tool_calls": [{
        "id": "c", "function": {"name": "lookup", "arguments": "x" * 400}
    }]}})
    
    # 断言 1：携带工具调用的消息 Token 数明显大于纯空消息
    assert counter.count_message(native) > counter.count_message(plain)
    # 断言 2：二次读取命中缓存，缓存容量为 2
    cached = counter.count_message(native)
    assert counter.count_message(native) == cached and counter.get_cache_size() == 2
    # 断言 3：缓存清空后可重新计算并取得相同结果
    counter.clear_cache()
    assert counter.get_cache_size() == 0
    assert counter.count_message(native) == cached


def test_summary_budget_failure_propagates_without_count_only_fallback():
    """验证：智能摘要抛出 BudgetExceeded 时直接向上冒泡，不执行简易纯计数降级。"""
    summary = Mock()
    summary.invoke.side_effect = BudgetExceeded("limit")
    agent = SimpleAgent("summary", Mock(), config=Config(min_retain_rounds=1),
                        components=AgentComponents(summary_llm=summary))
    for role, text in [("user", "old"), ("assistant", "old answer"), ("user", "latest")]:
        agent.history_manager.append(Message(text, role))
    # 期望直接抛出 BudgetExceeded 异常
    with pytest.raises(BudgetExceeded):
        agent._generate_smart_summary(agent.get_history())
    summary.invoke.assert_called_once()


def test_each_model_request_refreshes_provider_after_real_tool_execution():
    """验证：真实工具执行后下一轮请求动态重新从 Provider 拉取资料，且资料不写入长期历史。"""
    current = [ContextPacket("待撤回约束：ONLY_FIRST_REQUEST", metadata={
        "id": "constraint", "source": "user:1", "version": "v1", "required": True,
    })]
    queries, requests = [], []

    class Provider:
        """动态上下文提供者：按需返回当前受托资料列表。"""
        def get_context(self, query):
            queries.append(query)
            return list(current)

    class Model:
        model = "offline"

        def invoke_with_tools(self, messages, tools, **kwargs):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                # 第一轮：模型决定调用 retract 工具撤回约束
                return LLMToolResponse(None, [ToolCall("r", "retract", '{"input":"constraint"}')], self.model)
            # 第二轮：返回最终完成答复
            return LLMToolResponse("已撤回", [], self.model)

    registry = ToolRegistry()
    registry.register_function(lambda value: current.clear() or "已撤回", name="retract")
    agent = SimpleAgent("refresh", Model(), tool_registry=registry,
                        context_builder=ContextBuilder(), context_providers=[Provider()])
    
    assert agent.run("撤回约束") == "已撤回"
    # 断言 1：Provider 在两次模型调用前均被重新查询
    assert queries == ["撤回约束", "撤回约束"]
    # 断言 2：第一轮请求包含该约束资料，第二轮因工具副作用清空而不再包含
    assert any("ONLY_FIRST_REQUEST" in (m.get("content") or "") for m in requests[0])
    assert all("ONLY_FIRST_REQUEST" not in (m.get("content") or "") for m in requests[1])
    # 断言 3：工具调用与回执严格配对闭合（tool_call_id='r'）
    assert requests[1][-2]["tool_calls"][0]["id"] == requests[1][-1]["tool_call_id"] == "r"
    # 断言 4：动态注入的参考资料只属于本次单次请求，未悄悄沉淀进长期历史
    assert all("ONLY_FIRST_REQUEST" not in m.content for m in agent.get_history())


def test_local_relevance_and_diversity_do_not_trust_packet_score():
    """验证：ContextBuilder 自主进行词项与多样性 (MMR) 重排，不采信外部附带的静态评分。"""
    builder = ContextBuilder(ContextConfig(max_tokens=2000, mmr_lambda=0.2))
    packets = [
        ContextPacket("travel budget alpha", metadata={"id": "a"}, relevance_score=0),
        ContextPacket("travel budget alpha", metadata={"id": "b"}, relevance_score=1),
        ContextPacket("travel budget beta", metadata={"id": "c"}, relevance_score=0),
        ContextPacket("unrelated", metadata={"id": "d"}, relevance_score=1),  # 外部伪造的高分无关项
    ]
    result = builder.build_messages([{"role": "user", "content": "travel budget"}], packets)
    # 断言：基于局部算法正确选中 a, c, b 并剔除 d（reason='relevance'）
    assert [item["id"] for item in result.diagnostics["selected"]] == ["a", "c", "b"]
    assert result.diagnostics["excluded"] == [{
        "id": "d", "source": None, "relevance": 0, "reason": "relevance",
    }]


def test_instruction_packet_is_required_reference_without_changing_base_pairs():
    """验证：指令型 ContextPacket 注入为 user 参考资料，不破坏原有的 assistant-tool 配对。"""
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
    # 断言：基准消息序列前序不变，资料作为参考注入到最新 user 消息前，不切断 assistant-tool 配对
    assert result.messages[:-2] == base[:-1]
    assert result.messages[-2]["role"] == "user"
    assert "fixture://doc" in result.messages[-2]["content"]
    assert result.messages[-1] == base[-1]
    assert len(base) == 5 and result.diagnostics["selected"][0]["reason"] == "required"


def test_builder_rejects_large_base_when_history_retention_prevents_compression():
    """验证：当保留轮次约束阻止继续压缩导致基础历史超出预算时，显式抛出 ContextBudgetExceeded。"""
    model = Mock(model="offline")
    agent = SimpleAgent("budget", model,
                        config=Config(min_retain_rounds=5, context_window=100,
                                      compression_threshold=0.5, enable_smart_compression=False),
                        context_builder=ContextBuilder(ContextConfig(max_tokens=250)))
    agent.add_message(Message("x " * 2000, "user"))
    assert agent.history_manager.estimate_rounds() == 1
    assert agent.get_history()[0].role == "user"
    # 期望抛出超限异常，阻止向模型发起请求
    with pytest.raises(ContextBudgetExceeded):
        agent.run("current")
    model.invoke.assert_not_called()
    assert agent.last_run.status == "failed" and not agent._run_active
