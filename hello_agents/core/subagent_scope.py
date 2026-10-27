"""通过临时会话状态，将空闲 Agent 复用为子任务执行器。

该上下文管理器（subagent_scope）负责在复用现有 Agent 作为子代理时，
对父级状态进行完全隔离与自动还原，保证历史记录、会话持久化与控制状态互不污染。
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

# 内部哨兵对象，用于标记父级对象原本不存在的属性
_MISSING = object()

# 需要在子代理作用域内暂存并在退出时严格还原的状态字段集合
_STATE = (
    "tool_registry", "session_store", "_history_token_count", "_session_metadata",
    "_messages_since_save", "_start_time", "max_steps", "max_tool_iterations",
    "last_run", "last_context_diagnostics",
)


@contextmanager
def subagent_scope(agent, tool_filter=None, max_steps=None):
    """子代理临时作用域上下文管理器

    始终恢复父 Agent 状态，结果处理失败或发生中断时也不例外。

    特性与约束：
    1. 活跃状态检查：正在运行中的 Agent 禁止被复用为子代理，防止并发冲突；
    2. 上下文隔离：进入时清空历史与 Token 计数，退出时无条件恢复父级历史消息；
    3. 工具分流过滤：复制注册名称与读取元数据；工具实例及其外部修改仍然共享；
    4. 会话持久化禁用：在子代理作用域内强制将 session_store 置空，避免子任务写入父会话；
    5. 步数与预算覆盖：若指定 max_steps 则在作用域内临时覆盖，退出时自动还原。

    Args:
        agent: 被复用的父 Agent 实例
        tool_filter: 可选的工具过滤器（ToolFilter 实例）
        max_steps: 可选的子代理最大步数限制覆盖值
    """
    # 1. 状态检查：防止对正处于运行循环中的 Agent 进行并发复用
    if getattr(agent, "_run_active", False):
        raise RuntimeError("运行中的 Agent 不能复用为子代理，请创建独立实例")
    # 2. 参数校验：步数覆盖值必须为正整数
    if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
        raise ValueError("max_steps_override 必须是正整数")

    # 3. 备份父级原始历史消息快照与关键状态字典
    history = list(agent.history_manager.get_history())
    state = {name: getattr(agent, name, _MISSING) for name in _STATE}
    registry = agent.tool_registry

    # 4. 修改状态前，先校验过滤器并构造隔离的工具注册表映射
    if registry is not None:
        names = tool_filter.filter(registry.list_tools()) if tool_filter is not None else None
        registry = registry.fork(names)
    metadata = deepcopy(agent._session_metadata)

    try:
        # 5. 注入子代理临时状态：隔离的工具注册表、禁用会话持久化、重置计时与消息数
        agent.tool_registry = registry
        agent.session_store = None
        agent._session_metadata = metadata
        agent._start_time = datetime.now()
        agent._messages_since_save = 0
        agent.history_manager.clear()
        agent._history_token_count = 0
        # 6. 覆盖最大步数与工具迭代限制
        if max_steps is not None:
            for name in ("max_steps", "max_tool_iterations"):
                if state[name] is not _MISSING:
                    setattr(agent, name, max_steps)
        yield
    finally:
        # 7. 退出作用域时恢复父 Agent 历史记录（两层 try/finally 确保无论如何均恢复状态）
        try:
            agent.history_manager.clear()
            for message in history:
                agent.history_manager.append(message)
        finally:
            # 8. 即使注入的历史组件发生故障，也要严格恢复全部控制配置与状态字段
            for name, value in state.items():
                if value is _MISSING:
                    if hasattr(agent, name):
                        delattr(agent, name)
                else:
                    setattr(agent, name, value)
