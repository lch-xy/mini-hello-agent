"""P05 规划器（Planner）模型服务商兼容性测试套件。

验证 Planner 在调用底层的 Function Calling 时保持最大兼容性：
1. 使用通用的 tool_choice='auto' 发起工具请求，不强行依赖特定模型厂商的强制工具绑定模式（forced tool_choice）；
2. 确保对各种开源或闭源大模型提供一致的规划计划生成能力。
"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, Config, ToolRegistry
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType


def test_planner_auto_does_not_assume_forced_tool_support():
    """验证：Planner 调用 invoke_with_tools 时使用 tool_choice='auto'，不假设服务商支持强制工具调用。"""
    from hello_agents.agents.plan_solve_agent import Planner
    from hello_agents.core.llm_response import LLMToolResponse, ToolCall
    
    llm = Mock()
    llm.invoke_with_tools.return_value = LLMToolResponse(
        None, [ToolCall('p', 'generate_plan', '{"steps":["one"]}')], 'test'
    )
    
    # 断言 1：正确解析返回的计划步骤列表
    assert Planner(llm).plan('task') == ['one']
    # 断言 2：传给模型的参数中 tool_choice 严格为 'auto'
    assert llm.invoke_with_tools.call_args.kwargs['tool_choice'] == 'auto'

