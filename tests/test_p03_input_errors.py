"""P03 的原始空输入与模型错误传播断言。

本测试套件验证 P03 运行时的输入处理与异常传播机制：
1. 用户输入空字符串（""）能够忠实送达模型层，不被静默吞咽或篡改；
2. 模型发生的底层异常（RuntimeError）直接向外抛出，状态置为 failed，不伪装成空成功。
"""

from unittest.mock import Mock
import pytest
from hello_agents import Config, HelloAgentsLLM, SimpleAgent
from hello_agents.core.llm_response import LLMToolResponse


def quiet_config():
    """创建禁用非核心特性的测试静默配置。"""
    return Config(
        trace_enabled=False,
        session_enabled=False,
        skills_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def test_empty_input_reaches_model_without_swallowing_errors():
    """验证：空字符串输入能够正常送达大模型作为末尾 user 消息，不被丢弃。"""
    llm = Mock(model="offline")
    llm.invoke.return_value = LLMToolResponse("请提供任务。", [], "offline")
    agent = SimpleAgent("输入检查", llm, config=quiet_config())
    assert agent.run("") == "请提供任务。"
    # 断言末尾的 user 消息内容确实为空字符串
    assert llm.invoke.call_args.kwargs["messages"][-1]["content"] == ""


def test_model_failure_propagates():
    """验证：当模型层调用抛出异常时，向外完整传播异常，且 last_run.status 记录为 failed。"""
    llm = Mock(model="offline")
    llm.invoke.side_effect = RuntimeError("local failure")
    agent = SimpleAgent("失败检查", llm, config=quiet_config())
    with pytest.raises(RuntimeError, match="local failure"):
        agent.run("任务")
    assert agent.last_run.status == "failed"
