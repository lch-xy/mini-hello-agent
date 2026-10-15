"""HelloAgents 复刻：当前接通 P01–P05 模型、工具、流式 Runtime 与四种策略。"""

# 包的公共入口：调用者可以直接 from hello_agents import HelloAgentsLLM，
# 不必知道内部文件位置。这里仅导出当前阶段已经实现的模型与工具对象。
from .core.budget import RunBudget, BudgetExceeded
from .core.config import Config
from .core.exceptions import HelloAgentsException
from .core.llm import HelloAgentsLLM
from .core.message import Message
from .core.components import AgentComponents, DEFAULT_COMPONENT
from .agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent

from .tools.registry import ToolRegistry, global_registry
from .tools.builtin.calculator import CalculatorTool, calculate

# __all__ 声明公开名称，影响 import *；它不会禁止访问其他内部模块。
__all__ = [
    "RunBudget", "BudgetExceeded",
    "HelloAgentsLLM", "Config", "Message", "HelloAgentsException",
    "ToolRegistry", "global_registry", "CalculatorTool", "calculate",
    "SimpleAgent", "ReActAgent", "ReflectionAgent", "PlanSolveAgent",
    "AgentComponents", "DEFAULT_COMPONENT",
]
