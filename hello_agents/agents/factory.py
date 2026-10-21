"""Agent 工厂模块。

提供按策略名称动态创建 Agent 实例的统一入口，并实现子代理（Subagent）的安全派生工厂：
1. create_agent: 根据策略类型（react / reflection / plan / simple）实例化对应的 Agent；
2. default_subagent_factory: 为多 Agent 协作场景提供沙箱化的子代理生成机制（工具白名单隔离、步数受限、禁止无节制递归嵌套）。
"""

from typing import Optional, TYPE_CHECKING
from ..core.agent import Agent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry


def create_agent(
    agent_type: str,
    name: str,
    llm: HelloAgentsLLM,
    tool_registry: Optional['ToolRegistry'] = None,
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None
) -> Agent:
    """根据指定的策略类型创建对应的 Agent 实例。
    
    采用按需局部导入（Lazy Import），避免在模块加载阶段引发循环引用。
    
    Args:
        agent_type: Agent 策略类型，大小写不敏感，支持：
            - "react": ReActAgent（推理-行动-观察循环策略）
            - "reflection": ReflectionAgent（生成-评估-修正自我反思策略）
            - "plan": PlanSolveAgent（任务规划与分步执行策略）
            - "simple": SimpleAgent（基础 Native Function Calling 策略）
        name: Agent 实例标识名称
        llm: 统一的 HelloAgentsLLM 客户端实例
        tool_registry: 可选的工具注册表（未提供时子类通常使用默认注册表）
        config: 全局或定制配置对象
        system_prompt: 自定义系统提示词（如未指定则由具体策略类提供缺省值）
        
    Returns:
        初始化完成的 Agent 策略实例
        
    Raises:
        ValueError: 当传入未受支持的 agent_type 时抛出明确异常
    """
    agent_type = agent_type.lower()
    
    if agent_type == "react":
        from .react_agent import ReActAgent
        return ReActAgent(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            config=config,
            system_prompt=system_prompt
        )
    
    elif agent_type == "reflection":
        from .reflection_agent import ReflectionAgent
        return ReflectionAgent(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            config=config,
            system_prompt=system_prompt
        )
    
    elif agent_type == "plan":
        from .plan_solve_agent import PlanSolveAgent
        return PlanSolveAgent(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            config=config,
            system_prompt=system_prompt
        )
    
    elif agent_type == "simple":
        from .simple_agent import SimpleAgent
        return SimpleAgent(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            config=config,
            system_prompt=system_prompt
        )
    
    else:
        raise ValueError(
            f"不支持的 agent_type: {agent_type}。"
            f"支持的类型: react, reflection, plan, simple"
        )


def default_subagent_factory(
    agent_type: str,
    llm: HelloAgentsLLM,
    tool_registry: Optional['ToolRegistry'] = None,
    config: Optional[Config] = None
) -> Agent:
    """默认子代理派生工厂函数。
    
    负责在父代理运行期间创建隔离的临时子代理，具有以下安全与沙箱约束：
    1. 递归保护：显式配置 subagent_enabled=False，严禁子代理无限衍生下一级子代理；
    2. 持久化隔离：禁用自动保存（auto_save_enabled=False），避免子代理的中间运行历史污染主持久化会话；
    3. 工具白名单收敛：使用 fork 剔除调度与任务管理等高阶工具（如 Task, Skill, TodoWrite, DevLog），
       防止子代理突破职责边界越权调用敏感工具；
    4. 步数硬约束：强制将子代理的最大步数/工具调用次数限定在 config.subagent_max_steps 内。
    
    Args:
        agent_type: 子代理的策略类型（如 "react", "simple" 等）
        llm: 共享或专用的 LLM 实例
        tool_registry: 父代理的工具注册表（将被克隆并收敛白名单）
        config: 配置对象
        
    Returns:
        配置好权限边界与执行预算的子代理实例
    """
    config = config or Config()
    # 规则 1 & 2：关闭子代理的再次分发能力与主会话自动持久化
    config = config.model_copy(update={"subagent_enabled": False, "auto_save_enabled": False})
    
    # 规则 3：克隆工具注册表并过滤掉高危管理型工具，收敛子代理权限
    if tool_registry is not None:
        tool_registry = tool_registry.fork(
            name for name in tool_registry.list_tools()
            if name not in {"Task", "Skill", "TodoWrite", "DevLog"}
        )
    
    # 构造唯一子代理名称与针对性系统提示词
    name = f"subagent-{agent_type}"
    system_prompt = _get_system_prompt_for_type(agent_type)
    
    # 实例化子代理
    subagent = create_agent(
        agent_type=agent_type,
        name=name,
        llm=llm,
        tool_registry=tool_registry,
        config=config,
        system_prompt=system_prompt
    )
    
    # 规则 4：注入子代理专用最大步数预算限制，防止失控死循环
    if hasattr(subagent, 'max_steps'):
        subagent.max_steps = config.subagent_max_steps
    if hasattr(subagent, 'max_tool_iterations'):
        subagent.max_tool_iterations = config.subagent_max_steps
    
    return subagent


def _get_system_prompt_for_type(agent_type: str) -> str:
    """根据 Agent 策略类型获取专门优化的缺省系统提示词。"""
    prompts = {
        "react": """你是一个高效的任务执行专家。

目标：快速完成指定的子任务。

规则：
- 使用可用工具高效完成任务
- 保持输出简洁明了
- 在规定步数内完成
""",
        "reflection": """你是一个反思型专家。

目标：深入分析问题并提供高质量的解决方案。

规则：
- 先给出初步方案
- 反思并改进方案
- 输出最终优化结果
""",
        "plan": """你是一个任务规划专家。

目标：将复杂任务分解为可执行的步骤。

规则：
- 分析任务需求
- 制定详细的执行计划
- 标注步骤依赖关系
""",
        "simple": """你是一个简洁高效的助手。

目标：直接回答问题或完成任务。

规则：
- 保持回答简洁
- 直接给出结果
- 避免冗余信息
"""
    }
    
    return prompts.get(agent_type.lower(), prompts["simple"])
    prompts = {
        "react": """你是一个高效的任务执行专家。

目标：快速完成指定的子任务。

规则：
- 使用可用工具高效完成任务
- 保持输出简洁明了
- 在规定步数内完成
""",
        "reflection": """你是一个反思型专家。

目标：深入分析问题并提供高质量的解决方案。

规则：
- 先给出初步方案
- 反思并改进方案
- 输出最终优化结果
""",
        "plan": """你是一个任务规划专家。

目标：将复杂任务分解为可执行的步骤。

规则：
- 分析任务需求
- 制定详细的执行计划
- 标注步骤依赖关系
""",
        "simple": """你是一个简洁高效的助手。

目标：直接回答问题或完成任务。

规则：
- 保持回答简洁
- 直接给出结果
- 避免冗余信息
"""
    }
    
    return prompts.get(agent_type.lower(), prompts["simple"])

