"""HelloAgents 复刻：当前接通 P01 模型、P02 工具与 P03 Runtime。"""

# 包的公共入口：调用者可以直接 from hello_agents import HelloAgentsLLM，
# 不必知道内部文件位置。这里仅导出当前阶段已经实现的模型与工具对象。
from .core.config import Config
from .core.exceptions import HelloAgentsException
from .core.llm import HelloAgentsLLM
from .core.message import Message
from .core.components import AgentComponents, DEFAULT_COMPONENT
from .agents.simple_agent import SimpleAgent

from .tools.registry import ToolRegistry, global_registry
from .tools.builtin.calculator import CalculatorTool, calculate

# __all__ 声明公开名称，影响 import *；它不会禁止访问其他内部模块。
__all__ = [
    "HelloAgentsLLM", "Config", "Message", "HelloAgentsException",
    "ToolRegistry", "global_registry", "CalculatorTool", "calculate",
    "SimpleAgent", "AgentComponents", "DEFAULT_COMPONENT",
]
