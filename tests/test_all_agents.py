"""四类 Agent 的真实服务冒烟测试与本地输入/异常边界检查。

本测试套件覆盖：
1. LLM 基础实例初始化与模型名校验；
2. SimpleAgent、ReActAgent、ReflectionAgent、PlanSolveAgent 四类 Agent 的统一问答契约；
3. 工具调用端到端冒烟（基于 CalculatorTool）；
4. 多轮对话跨 Turn 记忆连贯性；
5. 空字符串输入透传（不静默丢弃）与模型底层异常的向上冒泡传播。
"""

from unittest.mock import Mock
import pytest
from hello_agents import (
    Config,
    HelloAgentsLLM,
    SimpleAgent,
    ReActAgent,
    ReflectionAgent,
    PlanSolveAgent,
    ToolRegistry,
)
from hello_agents.core.llm_response import LLMToolResponse
from hello_agents.tools.builtin import CalculatorTool


def quiet_config():
    """构造关闭各类文件 IO 与子代理的轻量测试配置。"""
    return Config(
        trace_enabled=False,
        session_enabled=False,
        skills_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def test_llm_initialization():
    """验证：HelloAgentsLLM 在离线配置下正确完成属性赋值与模型名初始化。"""
    llm = HelloAgentsLLM(
        model="offline-constructor",
        api_key="test-key",
        base_url="http://127.0.0.1:9/v1",
        provider="openai",
    )
    assert llm.model == "offline-constructor"


@pytest.mark.parametrize(
    "agent_type", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
@pytest.mark.live
def test_live_agent_answer(agent_type, live_llm_config):
    """验证：四种策略类型的 Agent 均能在真实 LLM 服务下成功执行并产出非空答复。"""
    agent = agent_type(
        "服务冒烟", HelloAgentsLLM(**live_llm_config), config=quiet_config()
    )
    answer = agent.run("用一句中文说明什么是递归。")
    # 断言：返回有效文本、运行终态为 completed，且至少发起过 1 次模型调用
    assert isinstance(answer, str) and answer.strip()
    assert agent.last_run.status == "completed"
    assert agent.last_run.model_calls >= 1


@pytest.mark.live
def test_live_calculator(live_llm_config):
    """验证：Agent 能够按指令准确调用外部计算器工具并据此合成正确答案。"""
    registry = ToolRegistry()
    registry.register_tool(CalculatorTool())
    agent = SimpleAgent(
        "工具冒烟",
        HelloAgentsLLM(**live_llm_config),
        tool_registry=registry,
        config=quiet_config(),
    )
    answer = agent.run("必须调用计算器计算 256 * 789，再报告精确结果。")
    # 断言：计算结果正确，工具调用计数 >= 1
    assert "201984" in answer.replace(",", "").replace(" ", "")
    assert agent.last_run.tool_calls >= 1
    assert agent.last_run.status == "completed"


@pytest.mark.live
def test_live_multi_turn(live_llm_config):
    """验证：Agent 跨轮次对话时，能够准确在上下文历史中保持记忆连续性。"""
    agent = SimpleAgent(
        "会话冒烟", HelloAgentsLLM(**live_llm_config), config=quiet_config()
    )
    assert agent.run("记住本次项目的代号是青鹭。只需确认。")
    assert "青鹭" in agent.run("本次项目的代号是什么？")


def test_empty_input_reaches_model_without_swallowing_errors():
    """验证：输入空字符串时不被前端拦截丢弃，而是作为合法 user 消息如实提交给底层模型。"""
    llm = Mock(model="offline")
    llm.invoke.return_value = LLMToolResponse("请提供任务。", [], "offline")
    agent = SimpleAgent("输入检查", llm, config=quiet_config())
    assert agent.run("") == "请提供任务。"
    # 断言：传给模型的最后一条消息内容确为原样空字符串
    assert llm.invoke.call_args.kwargs["messages"][-1]["content"] == ""


def test_model_failure_propagates():
    """验证：模型调用失败抛出异常时，Agent 正确记录状态为 failed 并向上抛出异常。"""
    llm = Mock(model="offline")
    llm.invoke.side_effect = RuntimeError("local failure")
    agent = SimpleAgent("失败检查", llm, config=quiet_config())
    with pytest.raises(RuntimeError, match="local failure"):
        agent.run("任务")
    assert agent.last_run.status == "failed"
