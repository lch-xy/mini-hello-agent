"""P05：保留固定目标原定义与断言。"""
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
    from hello_agents.agents.plan_solve_agent import Planner
    from hello_agents.core.llm_response import LLMToolResponse,ToolCall
    llm=Mock()
    llm.invoke_with_tools.return_value=LLMToolResponse(None,[ToolCall('p','generate_plan','{"steps":["one"]}')],'test')
    assert Planner(llm).plan('task')==['one']
    assert llm.invoke_with_tools.call_args.kwargs['tool_choice']=='auto'

