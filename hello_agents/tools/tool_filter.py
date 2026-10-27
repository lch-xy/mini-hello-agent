"""工具过滤器模块

用于子代理（SubAgent）权限收敛与沙箱机制，控制不同职责的 Agent 实例能够访问哪些工具。
通过白名单、黑名单以及只读/完全访问预设策略，防止子代理执行未经授权的写操作或高危系统命令。
"""

from abc import ABC, abstractmethod
from typing import List, Set, Optional


class ToolFilter(ABC):
    """工具过滤器抽象基类

    定义在子代理执行环境中对工具集合进行权限筛选与准入判断的标准接口。
    """

    @abstractmethod
    def filter(self, all_tools: List[str]) -> List[str]:
        """对候选工具名称列表进行批量过滤筛选

        Args:
            all_tools: 当前环境中全部可用的工具名称列表

        Returns:
            过滤后被允许使用的工具名称列表
        """
        pass

    @abstractmethod
    def is_allowed(self, tool_name: str) -> bool:
        """检查指定名称的单个工具是否允许被调用

        Args:
            tool_name: 待判断的工具名称

        Returns:
            bool: True 表示允许使用，False 表示拒绝访问
        """
        pass


class ReadOnlyFilter(ToolFilter):
    """只读工具过滤器

    仅允许使用查询、检索、查看等无副作用的只读工具，
    适用于代码探索（explore）、任务规划（plan）与信息归纳（summary）等子任务。
    """

    # 预设的只读工具白名单集合（涵盖文件读取、代码搜索、RAG 检索等）
    READONLY_TOOLS: Set[str] = {
        "Read",
        "ReadTool",
        "LS",
        "LSTool",
        "Glob",
        "GlobTool",
        "Grep",
        "GrepTool",
        "Skill",
        "SkillTool",
        "rag",
        "rag_search",
        "rag_read",
        "rag_read_document",
        "memory_search",
        "MemorySearchTool",
        "SearchTool",
        "relation_lookup",
    }

    def __init__(self, additional_allowed: Optional[List[str]] = None):
        """初始化只读过滤器

        Args:
            additional_allowed: 额外允许放行的自定义只读工具名称列表
        """
        self.allowed_tools = self.READONLY_TOOLS.copy()
        if additional_allowed:
            self.allowed_tools.update(additional_allowed)

    def filter(self, all_tools: List[str]) -> List[str]:
        """批量过滤，仅保留属于允许白名单中的工具"""
        return [tool for tool in all_tools if self.is_allowed(tool)]

    def is_allowed(self, tool_name: str) -> bool:
        """检查工具是否在只读白名单集合中"""
        return tool_name in self.allowed_tools


class FullAccessFilter(ToolFilter):
    """完全访问过滤器

    允许使用绝大部分通用工具（包括读写操作），但明确拦截高危终端执行工具。
    适用于代码编写与重构（code）等需要写权限的子代理场景。
    """

    # 明确禁止的高风险命令执行工具黑名单
    DENIED_TOOLS: Set[str] = {
        "Bash",
        "BashTool",
        "Terminal",
        "TerminalTool",
        "Execute",
        "ExecuteTool",
    }

    def __init__(self, additional_denied: Optional[List[str]] = None):
        """初始化完全访问过滤器

        Args:
            additional_denied: 额外需要禁止的危险工具名称列表
        """
        self.denied_tools = self.DENIED_TOOLS.copy()
        if additional_denied:
            self.denied_tools.update(additional_denied)

    def filter(self, all_tools: List[str]) -> List[str]:
        """批量过滤，排除所有黑名单中的危险工具"""
        return [tool for tool in all_tools if self.is_allowed(tool)]

    def is_allowed(self, tool_name: str) -> bool:
        """检查工具是否不在黑名单中（不在黑名单即允许）"""
        return tool_name not in self.denied_tools


class CustomFilter(ToolFilter):
    """自定义工具过滤器

    支持用户通过白名单（whitelist）或黑名单（blacklist）模式精确指定放行或拦截规则。
    """

    def __init__(
        self,
        allowed: Optional[List[str]] = None,
        denied: Optional[List[str]] = None,
        mode: str = "whitelist",
    ):
        """初始化自定义过滤器

        Args:
            allowed: 允许的工具名称列表（白名单模式下使用）
            denied: 禁止的工具名称列表（黑名单模式下使用）
            mode: 过滤模式，必须为 'whitelist'（白名单）或 'blacklist'（黑名单）

        Raises:
            ValueError: mode 既非 'whitelist' 也非 'blacklist' 时抛出
        """
        self.allowed = set(allowed) if allowed else set()
        self.denied = set(denied) if denied else set()
        self.mode = mode

        if mode not in ("whitelist", "blacklist"):
            raise ValueError(
                f"Invalid mode: {mode}. Must be 'whitelist' or 'blacklist'"
            )

    def filter(self, all_tools: List[str]) -> List[str]:
        """根据当前配置的过滤模式（白名单或黑名单）批量筛选工具"""
        return [tool for tool in all_tools if self.is_allowed(tool)]

    def is_allowed(self, tool_name: str) -> bool:
        """检查单个工具在当前过滤模式下是否被允许调用"""
        if self.mode == "whitelist":
            return tool_name in self.allowed
        else:  # 黑名单模式
            return tool_name not in self.denied
