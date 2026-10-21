from contextlib import aclosing

"""基于共享运行时机制的自省反思（Reflection）策略 Agent。

核心设计思想：
1. 双阶段/三阶段循环：初次生成（Execution）→ 批判性评审（Reflection）→ 针对性优化（Refinement）；
2. 状态与轨迹显式追踪：使用 Memory 短期记忆结构存储每轮代码/文本尝试与评审意见；
3. 早停机制（Early Stopping）：当评审反馈命中“无需改进”时立即跳出循环，避免无效调用；
4. 全面复用共享 Runtime：每一阶段均作为一次独立的 turn_events 执行，完整继承 Token 统计与工具调用能力。
"""

from typing import Optional, List, Dict, Any, TYPE_CHECKING
from .simple_agent import SimpleAgent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.components import AgentComponents
from ..core.runtime import turn_events

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry


class Memory:
    """短期反思记忆模块，用于存储并回放 Agent 的多轮尝试轨迹与评审反馈。"""

    def __init__(self):
        self.records: List[Dict[str, Any]] = []

    def add_record(self, record_type: str, content: str):
        """向记忆中追加一条记录（type 可为 'execution' 或 'reflection'）。"""
        self.records.append({"type": record_type, "content": content})

    def get_trajectory(self) -> str:
        """将历次执行尝试与评审反馈格式化为可读的连贯历史文本。"""
        trajectory = ""
        for record in self.records:
            if record["type"] == "execution":
                trajectory += f"--- 上一轮尝试 (代码) ---\n{record['content']}\n\n"
            elif record["type"] == "reflection":
                trajectory += f"--- 评审员反馈 ---\n{record['content']}\n\n"
        return trajectory.strip()

    def get_last_execution(self) -> str:
        """倒序查找并返回最近一次的执行产出内容。"""
        for record in reversed(self.records):
            if record["type"] == "execution":
                return record["content"]
        return ""


class ReflectionAgent(SimpleAgent):
    """自省反思策略 Agent。
    
    在主循环中自动推进初次解答、评审诊断与增量优化，直至达到最大反思轮数或评审通过。
    """
    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_iterations: int = 3,
        tool_registry: Optional["ToolRegistry"] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
        components: Optional[AgentComponents] = None,
    ):
        """初始化 ReflectionAgent 实例。
        
        Args:
            name: Agent 实例标识
            llm: 模型客户端
            system_prompt: 提示词（定义角色与反思基准）
            config: 全局配置对象
            max_iterations: 最大反思重试轮数（必须为非负整数）
            tool_registry: 工具注册表
            enable_tool_calling: 是否允许各阶段调用工具
            max_tool_iterations: 每阶段内部允许的最大工具迭代次数
            components: 运行时组件装配
        """
        # 默认 system_prompt 注入自我反思的角色定义与早停提示
        default_system_prompt = """你是一个具有自我反思能力的AI助手。你的工作流程是：
1. 首先尝试完成用户的任务
2. 然后反思你的回答，找出可能的问题或改进空间
3. 根据反思结果优化你的回答
4. 如果回答已经很好，在反思时回复"无需改进"

请始终保持批判性思维，追求更高质量的输出。"""

        super().__init__(
            name,
            llm,
            system_prompt or default_system_prompt,
            config,
            tool_registry=tool_registry,
            max_tool_iterations=max_tool_iterations,
            enable_tool_calling=enable_tool_calling,
            components=components,
        )
        self.max_iterations = max_iterations
        self.memory = Memory()                       # 本次任务的反思轨迹存储
        self._reflection_iterations = 0             # 当前反思迭代轮次计数
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_tool_iterations = max_tool_iterations

        if type(max_iterations) is not int or max_iterations < 0:
            raise ValueError("max_iterations 必须是非负整数")

    async def _run_loop(self, messages, packets, stream, kwargs):
        """反思策略核心主循环：重写 SimpleAgent 的运行调度，编排三阶段状态跃迁。"""
        task = messages[-1]["content"]
        self.memory = Memory()
        self._reflection_iterations = 0

        # ========== 阶段 1：初次执行（Initial Execution） ==========
        async with aclosing(
            self._phase(messages, packets, stream, kwargs, "execution")
        ) as source:
            async for event in source:
                yield event
        self.memory.add_record("execution", self.last_run.answer)
        if self.last_run.status != "completed":
            return

        # ========== 阶段 2 & 3：反思与优化循环（Reflection & Refinement） ==========
        for iteration in range(1, self.max_iterations + 1):
            self._reflection_iterations = iteration
            
            # --- 2.1 发起批判性反思评审 ---
            messages.append(
                {
                    "role": "user",
                    "content": self._build_reflection_prompt(
                        task, self.memory.get_last_execution()
                    ),
                }
            )
            async with aclosing(
                self._phase(messages, packets, stream, kwargs, "reflection")
            ) as source:
                async for event in source:
                    yield event
            feedback = self.last_run.answer
            self.memory.add_record("reflection", feedback)
            if self.last_run.status != "completed":
                return

            # --- 2.2 早停判定：命中无需改进则保留上一版执行结果并正常退出 ---
            if "无需改进" in feedback or "no need for improvement" in feedback.lower():
                self.last_run.answer = self.memory.get_last_execution()
                messages.append({"role": "assistant", "content": self.last_run.answer})
                return

            # --- 2.3 根据反馈执行针对性修改优化（Refinement） ---
            messages.append(
                {
                    "role": "user",
                    "content": self._build_refinement_prompt(
                        task, self.memory.get_last_execution(), feedback
                    ),
                }
            )
            async with aclosing(
                self._phase(messages, packets, stream, kwargs, "refinement")
            ) as source:
                async for event in source:
                    yield event
            self.memory.add_record("execution", self.last_run.answer)
            if self.last_run.status != "completed":
                return

    async def _phase(self, messages, packets, stream, kwargs, phase):
        """单阶段执行包装函数：封装 step_start / step_finish 事件与 phase 元数据。"""
        self.last_run.status = "running"
        stage = "initial_execution" if phase == "execution" else phase
        details = (
            {} if phase == "execution" else {"iteration": self._reflection_iterations}
        )
        yield {"kind": "step_start", "phase": stage, **details}
        
        # 委托给底层通用 turn_events 执行单轮模型与工具交互
        async with aclosing(
            turn_events(self, messages, packets, stream=stream, kwargs=kwargs)
        ) as source:
            async for event in source:
                yield dict(event, phase=phase, **details)
                
        result = {"reflection": self.last_run.answer} if phase == "reflection" else {}
        yield {
            "kind": "step_finish",
            "phase": stage,
            "result": self.last_run.answer,
            **details,
            **result,
        }

    def _build_reflection_prompt(self, task, result):
        """构建反思评审提示词，明确引导大模型挑刺或回复早停短语。"""
        return f"请评审回答，提出具体改进意见；若已满足任务，只回答“无需改进”。\n任务：{task}\n回答：{result}"

    def _build_refinement_prompt(self, task, result, feedback):
        """构建优化改进提示词，将上一轮结果与评审意见注入上下文。"""
        return f"请根据反馈改进回答。\n任务：{task}\n上一轮回答：{result}\n反馈：{feedback}"
