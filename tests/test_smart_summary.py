"""智能摘要（Smart Summary）与简易摘要回退测试套件。

验证 Agent 历史压缩时的摘要生成与 Token 计数契约：
1. 简易摘要：在未配置摘要模型或异常时生成按角色计数的确定性基础摘要；
2. 智能摘要边界范围：严格仅将保留轮次边界（min_retain_rounds）之前的早期历史送入 summary_llm，
   最近轮次的消息绝不混入摘要输入；
3. 异常降级契约：智能摘要模型发生普通运行时错误时，显式安全回退至简易计数摘要；
4. 真实模型服务冒烟验证；
5. Token 计数器基本计数、缓存复用与历史增量追踪机制。
"""

from unittest.mock import Mock
import os
import pytest
from hello_agents import SimpleAgent, HelloAgentsLLM, Config, Message, AgentComponents


def make_agent():
    """构造用于摘要测试的离线配置 SimpleAgent 实例。"""
    return SimpleAgent(
        "摘要机制",
        Mock(model="offline-summary"),
        config=Config(
            min_retain_rounds=1,
            context_window=8000,
            trace_enabled=False,
            session_enabled=False,
            skills_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
        ),
    )


def history():
    """构造包含 2 轮（4 条）问答消息的标准测试历史。"""
    return [
        Message("预算1500元", "user"),
        Message("已记录预算", "assistant"),
        Message("只安排室内活动", "user"),
        Message("继续核对预约", "assistant"),
    ]


def test_simple_summary_generation():
    """验证：基于消息计数的简易摘要生成逻辑，生成用户、助手及总消息数统计。"""
    agent = make_agent()
    for message in history():
        agent.history_manager.append(message)
    summary = agent._generate_simple_summary(agent.get_history())
    assert "用户消息：2 条" in summary
    assert "助手消息：2 条" in summary
    assert "总消息数：4 条" in summary


def test_smart_summary_uses_only_history_before_retention_boundary():
    """验证：智能摘要仅提取保留轮次边界之前的早期消息，最近 min_retain_rounds 轮不进入摘要输入。"""
    agent = make_agent()
    summary_llm = Mock()
    summary_llm.invoke.return_value = "已确认预算1500元。"
    agent._component_options = AgentComponents(summary_llm=summary_llm)
    for message in history():
        agent.history_manager.append(message)
    summary = agent._generate_smart_summary(agent.get_history())
    
    # 断言 1：成功生成智能摘要
    assert "已确认预算1500元" in summary
    messages = summary_llm.invoke.call_args.args[0]
    # 断言 2：第 1 轮消息（需压缩的早期消息）被传入摘要模型
    assert "预算1500元" in messages[-1]["content"]
    # 断言 3：第 2 轮消息（最新保留轮次）严格未传入摘要模型
    assert "只安排室内活动" not in messages[-1]["content"]
    summary_llm.invoke.assert_called_once()


def test_smart_summary_failure_has_explicit_simple_fallback():
    """验证：智能摘要调用失败时安全降级为简易计数摘要，保证主流程不因辅助摘要报错而崩溃。"""
    agent = make_agent()
    summary_llm = Mock()
    summary_llm.invoke.side_effect = RuntimeError("local summary failure")
    agent._component_options = AgentComponents(summary_llm=summary_llm)
    messages = history()
    for message in messages:
        agent.history_manager.append(message)
    # 断言：智能摘要异常后直接返回简易摘要结果
    assert agent._generate_smart_summary(messages) == agent._generate_simple_summary(
        messages
    )
    summary_llm.invoke.assert_called_once()


@pytest.mark.live
def test_live_smart_summary(live_llm_config):
    """验证：真实 LLM 服务调用下智能摘要的生成与上下文关键词捕获。"""
    agent = make_agent()
    settings = dict(live_llm_config)
    settings["model"] = os.getenv("LLM_SUMMARY_MODEL", settings["model"])
    agent._component_options = AgentComponents(summary_llm=HelloAgentsLLM(**settings))
    for message in history():
        agent.history_manager.append(message)
    summary = agent._generate_smart_summary(agent.get_history())
    assert "历史摘要" in summary
    assert "1500" in summary


def test_token_counter_basic_and_incremental():
    """验证：TokenCounter 单消息计数、重复计数缓存命中以及 Agent 历史增量累计与清空。"""
    agent = make_agent()
    first, second = Message("First message", "user"), Message(
        "Second message", "assistant"
    )
    # 基础单消息计数与缓存一致性
    assert agent.token_counter.count_message(first) > 0
    assert agent.token_counter.count_message(
        first
    ) == agent.token_counter.count_message(first)
    assert agent._history_token_count == 0
    
    # 逐步追加消息，验证增量累计
    agent.add_message(first)
    count = agent._history_token_count
    agent.add_message(second)
    assert agent._history_token_count > count > 0
    
    # 清空历史，验证计数归零
    agent.clear_history()
    assert agent._history_token_count == 0
