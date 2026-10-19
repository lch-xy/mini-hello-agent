"""Agent 组件的显式注入与默认装配，不提供全局容器或插件生命周期。"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING, Union

from .config import Config
from .message import Message

if TYPE_CHECKING:
    from .agent import Agent
    from ..skills.loader import Skill


class HistoryProtocol(Protocol):
    """历史管理器协议接口。
    
    采用 Protocol 鸭子类型定义，允许调用方通过普通类直接实现自定义历史管理，
    无需硬继承框架内部的 HistoryManager 类。
    """
    min_retain_rounds: int

    def append(self, message: Message) -> None: ...
    def get_history(self) -> List[Message]: ...
    def clear(self) -> None: ...
    def compress(self, summary: str) -> None: ...
    def estimate_rounds(self) -> int: ...
    def find_round_boundaries(self) -> List[int]: ...


class TokenCounterProtocol(Protocol):
    """Token 计数器协议接口。
    
    用于估算单条或多条消息的 Token 消耗，以及清理内部缓存。
    """
    def count_message(self, message: Message) -> int: ...
    def count_messages(self, messages: List[Message]) -> int: ...
    def clear_cache(self) -> None: ...


class TruncatorProtocol(Protocol):
    """工具观察截断器协议接口。
    
    负责检查单次工具调用的返回内容是否超出限制，并在超长时生成预览与落盘大文件。
    """
    def truncate(self, tool_name: str, output: str) -> Dict[str, Any]: ...


class SessionStoreProtocol(Protocol):
    """会话持久化存储协议接口。
    
    负责将 Agent 历史、配置、工具 Schema 哈希与元数据持久化到磁盘并恢复。
    """
    def save(
        self,
        agent_config: Dict[str, Any],
        history: List[Message],
        tool_schema_hash: str,
        read_cache: Dict[str, Dict],
        metadata: Dict[str, Any],
        session_name: Optional[str] = None,
    ) -> str: ...
    def load(self, filepath: str) -> Dict[str, Any]: ...
    def list_sessions(self) -> List[Dict[str, Any]]: ...
    def check_config_consistency(
        self,
        saved_config: Dict[str, Any],
        current_config: Dict[str, Any],
    ) -> Dict[str, Any]: ...
    def check_tool_schema_consistency(
        self, saved_hash: str, current_hash: str
    ) -> Dict[str, Any]: ...


class SkillLoaderProtocol(Protocol):
    """技能加载器协议接口。
    
    负责加载外部 Markdown 技能定义并生成描述注入给 Agent。
    """
    def get_descriptions(self) -> str: ...
    def get_skill(self, name: str) -> Optional["Skill"]: ...
    def list_skills(self) -> List[str]: ...


class _DefaultComponent(Enum):
    """标记枚举：表示沿用 Config 默认配置创建组件实例。"""
    VALUE = "use_config_default"


DEFAULT_COMPONENT = _DefaultComponent.VALUE


@dataclass
class AgentComponents:
    """构造时覆盖指定组件的入参载体；所有组件实例由宿主管理生命周期。

    必需组件（history_manager, token_counter, truncator）传入 None 表示创建默认实例。
    可选组件（session_store, skill_loader）：
    - DEFAULT_COMPONENT：根据 Config 配置决定是否创建；
    - None：显式禁用该组件；
    - 实例对象：显式注入自定义替换实现。
    """

    summary_llm: Any = None
    subagent_llm: Any = None
    history_manager: Optional[HistoryProtocol] = None
    token_counter: Optional[TokenCounterProtocol] = None
    truncator: Optional[TruncatorProtocol] = None
    session_store: Union[SessionStoreProtocol, None, _DefaultComponent] = (
        DEFAULT_COMPONENT
    )
    skill_loader: Union[SkillLoaderProtocol, None, _DefaultComponent] = (
        DEFAULT_COMPONENT
    )


@dataclass(frozen=True)
class ResolvedAgentComponents:
    """解析组装完毕的不可变组件集合。
    
    挂载在 Agent 实例上，供运行时各子模块访问。
    """
    history_manager: HistoryProtocol
    token_counter: TokenCounterProtocol
    truncator: TruncatorProtocol
    session_store: Optional[SessionStoreProtocol]
    skill_loader: Optional[SkillLoaderProtocol]


# 运行时鸭子类型检查契约清单：严格定义每个组件必须实现的 callable 方法名称
_METHODS = {
    "history_manager": (
        "append",
        "get_history",
        "clear",
        "compress",
        "estimate_rounds",
        "find_round_boundaries",
    ),
    "token_counter": ("count_message", "count_messages", "clear_cache"),
    "truncator": ("truncate",),
    "session_store": (
        "save",
        "load",
        "list_sessions",
        "check_config_consistency",
        "check_tool_schema_consistency",
    ),
    "skill_loader": ("get_descriptions", "get_skill", "list_skills"),
}


def build_agent_components(
    config: Config,
    model: str,
    components: Optional[AgentComponents] = None,
) -> ResolvedAgentComponents:
    """解析注入项，未指定的组件按 Config 装配；不覆盖传入实例状态。
    
    装配顺序：
    1. 校验传入的 components 类型；
    2. 校验自定义注入对象是否完整实现了 Protocol 所需的方法（防无效注入）；
    3. 按需懒加载默认实现（HistoryManager, TokenCounter, ObservationTruncator 等）；
    4. 返回组装完毕的 ResolvedAgentComponents 容器。
    """
    if components is not None and not isinstance(components, AgentComponents):
        raise TypeError("components 必须是 AgentComponents 或 None")
    requested = components if components is not None else AgentComponents()

    # 先校验宿主注入对象，避免在发现无效注入前已经误创建了默认存储目录。
    for name, methods in _METHODS.items():
        value = getattr(requested, name)
        if value is None:
            continue
        if name in {"session_store", "skill_loader"} and value is DEFAULT_COMPONENT:
            continue
        missing = [
            method for method in methods if not callable(getattr(value, method, None))
        ]
        if missing:
            raise TypeError(f"组件 {name} 缺少方法：{', '.join(missing)}")
        if name == "history_manager":
            retained = getattr(value, "min_retain_rounds", None)
            if type(retained) is not int or retained < 1:
                raise ValueError("history_manager.min_retain_rounds 必须是正整数")

    from ..context.history import HistoryManager
    from ..context.token_counter import TokenCounter
    from ..context.truncator import ObservationTruncator
    from ..skills.loader import SkillLoader
    from .session_store import SessionStore

    # 解析核心组件：若未注入则实例化默认组件
    history = requested.history_manager
    if history is None:
        history = HistoryManager(config.min_retain_rounds, config.compression_threshold)
        
    counter = requested.token_counter
    if counter is None:
        counter = TokenCounter(model=model)
        
    truncator = requested.truncator
    if truncator is None:
        truncator = ObservationTruncator(
            max_lines=config.tool_output_max_lines,
            max_bytes=config.tool_output_max_bytes,
            truncate_direction=config.tool_output_truncate_direction,
            output_dir=config.tool_output_dir,
        )
        
    # 解析可选组件：若为 DEFAULT_COMPONENT 则根据配置开关决定是否实例化
    session = requested.session_store
    if session is DEFAULT_COMPONENT:
        session = SessionStore(config.session_dir) if config.session_enabled else None
        
    skills = requested.skill_loader
    if skills is DEFAULT_COMPONENT:
        skills = SkillLoader(config.skills_dir) if config.skills_enabled else None
        
    return ResolvedAgentComponents(history, counter, truncator, session, skills)


def register_default_tools(agent: "Agent") -> None:
    """按显式配置装配可选工具；显式禁用组件后不自动创建替代实例。
    
    根据 Config 中的开关（skills_auto_register, subagent_enabled 等），
    自动向 Agent 的 tool_registry 注册内置能力工具。
    """
    if agent.tool_registry is None:
        return
    existing = set(agent.tool_registry.list_tools())
    
    # 自动注册技能加载工具
    if (
        agent.skill_loader is not None
        and agent.config.skills_auto_register
        and "Skill" not in existing
    ):
        from ..tools.builtin.skill_tool import SkillTool

        agent.tool_registry.register_tool(SkillTool(skill_loader=agent.skill_loader))
        
    # 自动注册子代理任务派发工具
    if agent.config.subagent_enabled and "Task" not in existing:
        agent._register_task_tool()
        
    # 自动注册代办事项工具
    if agent.config.todowrite_enabled and "TodoWrite" not in existing:
        agent._register_todowrite_tool()
        
    # 自动注册开发日志记录工具
    if agent.config.devlog_enabled and "DevLog" not in existing:
        agent._register_devlog_tool()
