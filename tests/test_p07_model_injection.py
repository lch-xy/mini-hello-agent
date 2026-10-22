"""P07 模型依赖注入测试。

验证智能体针对专门子任务（如历史记录摘要提取 summary_llm、轻量级子智能体调用 subagent_llm）
的模型解析与装配逻辑：
1. 默认回退（Fallback）：未显式注入专用模型时，自动复用智能体的主 LLM 实例；
2. 显式覆盖（Override）：通过 AgentComponents 显式注入独立模型实例时，优先使用注入的实例，实现模型分级治理。
"""

from unittest.mock import Mock
from hello_agents import SimpleAgent, AgentComponents

def test_injected_or_main_model_for_summary_and_subagent():
    """验证摘要模型与轻量级子智能体模型的默认回退与显式注入覆盖机制。"""
    # 模拟主模型与专用的外部独立模型
    main, other = Mock(model='main'), Mock(model='other')

    # 1. 默认情况：未注入专用组件时，均回退使用主 LLM 实例
    agent = SimpleAgent('test', main)
    assert agent._get_summary_llm() is main
    assert agent._create_light_llm() is main

    # 2. 显式注入：通过 AgentComponents 注入外部独立模型实例
    agent = SimpleAgent('test', main, components=AgentComponents(summary_llm=other, subagent_llm=other))
    assert agent._get_summary_llm() is other
    assert agent._create_light_llm() is other


