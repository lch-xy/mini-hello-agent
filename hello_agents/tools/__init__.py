"""P02：工具描述、注册和执行边界；后续内置工具按阶段接入。"""

from .base import Tool, ToolParameter, tool_action
from .registry import ToolRegistry, global_registry
from .response import ToolResponse, ToolStatus
from .errors import ToolErrorCode
from .permissions import PermissionDecision, ToolPolicy, AllowlistPolicy
from .builtin.calculator import CalculatorTool

# P02/P03 内置文件操作工具
from .builtin.file_tools import ReadTool, WriteTool, EditTool, MultiEditTool

# P10 进度管理与知识加载工具
from .builtin.todowrite_tool import TodoWriteTool, TodoItem, TodoList
from .builtin.devlog_tool import DevLogTool, DevLogEntry, DevLogStore, CATEGORIES
from .builtin.skill_tool import SkillTool

__all__ = [
    "Tool", "ToolParameter", "tool_action", "ToolRegistry", "global_registry",
    "ToolResponse", "ToolStatus", "ToolErrorCode", "PermissionDecision",
    "ToolPolicy", "AllowlistPolicy", "CalculatorTool",
    "ReadTool", "WriteTool", "EditTool", "MultiEditTool",
    "TodoWriteTool", "TodoItem", "TodoList", "DevLogTool", "DevLogEntry",
    "DevLogStore", "CATEGORIES", "SkillTool",
]
