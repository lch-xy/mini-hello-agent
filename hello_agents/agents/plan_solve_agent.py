from contextlib import aclosing

"""通过通用 Agent 运行机制完成“规划-分步执行”（Plan-and-Solve）策略。

核心架构与设计思想：
1. 职责拆分：
   - Planner（规划器）：利用模型 Function Calling 强契约输出严格格式化的步骤列表（generate_plan 工具）；
   - Executor（执行器）：将当前待执行子任务、完整计划及前序步骤历史拼装为上下文字符串进行针对性求解；
   - PlanSolveAgent：顶层策略协调者，继承自 SimpleAgent，无缝复用共享 Runtime 的流式事件分发与 Token 统计。
2. 边界保护与错误恢复：
   - 规划超限（stopped_by_limit）或格式错误直接抛错，杜绝在残缺计划上做无意义的后续执行；
   - 规划阶段消耗的 Token 显式累加并入 Agent 的运行总开销（last_run.usage）；
   - 步骤间状态原子同步：执行步骤产出的 assistant 增量消息在 finally 中回填至主历史。
"""

import asyncio
import json
from typing import Optional, List, Dict, TYPE_CHECKING
from .simple_agent import SimpleAgent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.components import AgentComponents
from ..core.runtime import turn_events, stopped_by_limit, deadline

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry


class Planner:
    """任务规划器：负责将复杂的初始问题拆解为按逻辑顺序排列的子任务步骤列表。"""

    def __init__(self, llm_client: HelloAgentsLLM, system_prompt: Optional[str] = None):
        self.llm_client = llm_client
        self.system_prompt = (
            system_prompt
            or """你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。"""
        )

    def plan(self, question: str, **kwargs) -> List[str]:
        """通过大模型 Function Calling 机制生成结构化的分步执行计划。

        Args:
            question: 待解决的目标问题/主任务
            **kwargs: 透传给 LLM 调用的参数

        Returns:
            步骤文本列表（非空字符串数组）
            
        Raises:
            ValueError: 当模型输出截断、未调用 generate_plan 或返回空步骤时抛出
        """

        # 定义严格约定的规划工具 Schema，强制大模型以结构化数组形式产出步骤
        plan_tool = {
            "type": "function",
            "function": {
                "name": "generate_plan",
                "description": "生成解决问题的分步计划",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "按顺序排列的执行步骤列表",
                        }
                    },
                    "required": ["steps"],
                },
            },
        }

        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": f"请调用 generate_plan 工具提交以下问题的执行计划，不要仅用文字描述：\n\n{question}",
            },
        ]

        kwargs = dict(kwargs)
        kwargs.pop("tool_choice", None)  # 剔除可能继承自业务配置的 tool_choice，避免干扰规划工具调用

        response = self.llm_client.invoke_with_tools(
            messages=messages,
            tools=[plan_tool],
            tool_choice="auto",
            **kwargs,
        )

        # 契约校验 1：如果规划输出因 token 上限被截断，拒绝执行不完整计划
        if stopped_by_limit(response.finish_reason):
            raise ValueError("计划输出超过模型限制，未执行不完整计划")
        # 契约校验 2：必须且仅能调用一次 generate_plan 工具
        if len(response.tool_calls or []) != 1 or response.tool_calls[0].name != "generate_plan":
            raise ValueError("模型未返回 generate_plan 请求")
            
        # 契约校验 3：反序列化并验证 steps 是否为非空有效字符串列表
        arguments = json.loads(response.tool_calls[0].arguments)
        steps = arguments.get("steps") if isinstance(arguments, dict) else None
        if (
            not isinstance(steps, list)
            or not steps
            or any(not isinstance(step, str) or not step.strip() for step in steps)
        ):
            raise ValueError("计划必须包含非空的步骤字符串列表")
            
        self.last_response = response  # 暂存模型响应以便父层统计 Token
        return steps


class Executor:
    """任务执行器：负责将上下文历史与当前步骤融合，驱动模型或工具逐项解决。"""

    def __init__(
        self,
        llm_client: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
    ):
        self.llm_client = llm_client
        self.system_prompt = (
            system_prompt
            or """你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
请专注于解决当前步骤，并输出该步骤的最终答案。"""
        )
        self.tool_registry = tool_registry
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_tool_iterations = max_tool_iterations

    def _format_plan(self, plan: List[str]) -> str:
        """格式化计划列表为编号文本。"""
        return "\n".join([f"{i}. {step}" for i, step in enumerate(plan, 1)])

    def _format_history(self, history: List[Dict[str, str]]) -> str:
        """格式化已完成步骤的历史轨迹。"""
        return "\n\n".join(
            [
                f"步骤 {i}: {h['step']}\n结果: {h['result']}"
                for i, h in enumerate(history, 1)
            ]
        )

    def step_prompt(self, question, plan, history, step):
        """为单一步骤生成包含任务全貌、执行进度与当前聚焦点的多维 Prompt。"""
        return (
            f"原始任务：{question}\n完整计划：\n{self._format_plan(plan)}\n"
            f"已完成的步骤：\n{self._format_history(history) or '无'}\n"
            f"当前步骤：{step}\n请执行当前步骤并给出结果。"
        )

    def execute(self, question, plan, **kwargs):
        """独立同步执行模式：依次遍历计划步骤并返回最终步骤的输出。"""
        if not plan:
            raise ValueError("执行计划不能为空")
        history = []
        for step in plan:
            result = self._execute_step(
                self.step_prompt(question, plan, history, step), **kwargs
            )
            history.append({"step": step, "result": result})
        return history[-1]["result"]

    def _execute_step(self, context, **kwargs):
        """针对单个步骤实例化独立的临时 SimpleAgent 进行隔离求解。"""
        # 使用不带全局持久化与副作用的轻量配置
        config = Config(
            trace_enabled=False,
            session_enabled=False,
            skills_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
        )
        agent = SimpleAgent(
            "executor",
            self.llm_client,
            self.system_prompt,
            config,
            tool_registry=self.tool_registry,
            enable_tool_calling=self.enable_tool_calling,
            max_tool_iterations=self.max_tool_iterations,
        )
        answer = agent.run(context, **kwargs)
        if agent.last_run.status != "completed":
            raise RuntimeError(f"步骤未完成：{agent.last_run.status}")
        return answer


class PlanSolveAgent(SimpleAgent):
    """规划-求解（Plan-and-Solve）策略 Agent。
    
    统一编排 Planner 与 Executor，流式输出 planning 与 execution 两大阶段的细粒度事件。
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        planner_prompt: Optional[str] = None,
        executor_prompt: Optional[str] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
        components: Optional[AgentComponents] = None,
    ):
        """初始化 PlanSolveAgent 实例。"""
        super().__init__(
            name,
            llm,
            system_prompt,
            config,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations,
            components=components,
        )

        self.planner = Planner(self.llm, planner_prompt)
        self.executor = Executor(
            self.llm,
            executor_prompt,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations,
        )

    async def _run_loop(self, messages, packets, stream, kwargs):
        """规划-求解策略的主运行事件循环。"""
        task = messages[-1]["content"]
        
        # ========== 阶段 1：规划（Planning） ==========
        yield {"kind": "step_start", "phase": "planning", "description": "生成执行计划"}
        # 为规划请求设置异步超时防护
        async with deadline(self.config.llm_async_timeout):
            plan = await asyncio.to_thread(self.planner.plan, task, **kwargs)
            
        self.last_run.model_calls += 1
        response = getattr(self.planner, "last_response", None)
        yield {
            "kind": "model",
            "usage": getattr(response, "usage", {}) or {},
            "step": self.last_run.model_calls,
            "content": "",
            "phase": "planning",
        }
        # 累加规划阶段消耗的 Token 统计到全局 last_run
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.last_run.usage[key] = self.last_run.usage.get(key, 0) + (
                getattr(response, "usage", {}) or {}
            ).get(key, 0)
            
        self._plan_total_steps = len(plan)
        yield {
            "kind": "step_finish",
            "phase": "planning",
            "plan": plan,
            "total_steps": len(plan),
        }
        
        # ========== 阶段 2：执行（Execution）分步推进 ==========
        history = []
        for index, step in enumerate(plan, 1):
            # 构造当前步骤的用户消息
            messages.append(
                {
                    "role": "user",
                    "content": self.executor.step_prompt(task, plan, history, step),
                }
            )
            # 临时注入执行器系统角色指令
            execution = [
                {"role": "system", "content": self.executor.system_prompt},
                *messages,
            ]
            boundary = len(execution)
            self.last_run.status = "running"
            yield {
                "kind": "step_start",
                "phase": "execution",
                "step": index,
                "total_steps": len(plan),
                "description": step,
            }
            try:
                # 委托给 turn_events 流式执行单步任务（支持工具调用）
                async with aclosing(
                    turn_events(self, execution, packets, stream=stream, kwargs=kwargs)
                ) as source:
                    async for event in source:
                        yield dict(
                            event, phase="execution", plan_step=index, step=index
                        )
            finally:
                # 将本轮生成的 assistant 与 tool 回执追加进主历史序列
                messages.extend(execution[boundary:])
                
            history.append({"step": step, "result": self.last_run.answer})
            yield {
                "kind": "step_finish",
                "phase": "execution",
                "step": index,
                "result": self.last_run.answer,
            }
            # 若单步执行出现非 completed 状态（如超限或异常），终止后续计划
            if self.last_run.status != "completed":
                return
