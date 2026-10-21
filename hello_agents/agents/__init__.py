"""Agent 实现模块 - HelloAgents 原生 Agent 范式体系。

本模块汇集基于共享运行时（Shared Runtime）的四大核心 Agent 策略实现与工厂方法：
1. SimpleAgent: 基础 Native Tool Calling 工具调用 Agent；
2. ReActAgent: 结合思考（Thought）与行动（Action）及终态标记（Finish）的协同推理 Agent；
3. ReflectionAgent: 双阶段“执行-反思”（Generator-Critic）的自迭代修正 Agent；
4. PlanSolveAgent: 两阶段“规划-分步执行”（Planner-Executor）的复杂任务拆解 Agent；
5. create_agent / default_subagent_factory: 策略创建工厂与子代理衍生机制。
"""

from .simple_agent import SimpleAgent
from .react_agent import ReActAgent
from .reflection_agent import ReflectionAgent
from .plan_solve_agent import PlanSolveAgent

# 子代理与工厂机制
from .factory import create_agent, default_subagent_factory

__all__ = [
    "SimpleAgent",
    "ReActAgent",
    "ReflectionAgent",
    "PlanSolveAgent",
    # 子代理与创建工厂
    "create_agent",
    "default_subagent_factory",
]
