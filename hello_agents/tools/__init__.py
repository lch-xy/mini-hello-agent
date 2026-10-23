"""P02：工具描述、注册和执行边界；后续内置工具按阶段接入。"""

from .base import Tool, ToolParameter, tool_action
from .registry import ToolRegistry, global_registry
from .response import ToolResponse, ToolStatus
from .errors import ToolErrorCode
from .permissions import PermissionDecision, ToolPolicy, AllowlistPolicy
from .builtin.calculator import CalculatorTool

# P08 引入的标准内置文件操作工具（支持乐观锁冲突检测、原子写入与自动备份）
from .builtin.file_tools import ReadTool, WriteTool, EditTool, MultiEditTool

__all__ = [
    "Tool", "ToolParameter", "tool_action", "ToolRegistry", "global_registry",
    "ToolResponse", "ToolStatus", "ToolErrorCode", "PermissionDecision",
    "ToolPolicy", "AllowlistPolicy", "CalculatorTool",
    "ReadTool", "WriteTool", "EditTool", "MultiEditTool",
]
