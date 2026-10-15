"""P03 的原始空输入与模型错误传播断言。"""

from unittest.mock import Mock
import pytest
from hello_agents import Config, HelloAgentsLLM, SimpleAgent
from hello_agents.core.llm_response import LLMToolResponse

def quiet_config():
    return Config(
        trace_enabled=False,
        session_enabled=False,
        skills_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def test_empty_input_reaches_model_without_swallowing_errors():
    llm = Mock(model="offline")
    llm.invoke.return_value = LLMToolResponse("请提供任务。", [], "offline")
    agent = SimpleAgent("输入检查", llm, config=quiet_config())
    assert agent.run("") == "请提供任务。"
    assert llm.invoke.call_args.kwargs["messages"][-1]["content"] == ""


def test_model_failure_propagates():
    llm = Mock(model="offline")
    llm.invoke.side_effect = RuntimeError("local failure")
    agent = SimpleAgent("失败检查", llm, config=quiet_config())
    with pytest.raises(RuntimeError, match="local failure"):
        agent.run("任务")
    assert agent.last_run.status == "failed"

