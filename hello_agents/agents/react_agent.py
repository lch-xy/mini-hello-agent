"""在共享 Agent 运行机制上实现 ReAct（Reasoning + Acting）提示与工具协同策略。

核心设计思想：
1. 继承自 SimpleAgent，无缝复用底层的消息管理、流式运行循环、状态机以及工具并发调度器；
2. 将 ReAct 中的“思考”（Thought）和“完成”（Finish）转化为模型可见的内置伪工具（Pseudo Tools），
   使得遵循 Function Calling 协议的模型能够以统一的工具调用形式交替进行思考、行动和结论输出；
3. 保留字隔离：禁止用户注册名为 Thought 或 Finish 的业务工具，避免命名污染与策略失控；
4. 并行与超时保护：业务工具继续受 tool_async_timeout 与 max_concurrent_tools 约束。
"""

import asyncio
import json
from typing import Any, Dict, List, Optional
from .simple_agent import SimpleAgent
from ..tools.registry import ToolRegistry
from ..core.runtime import deadline
from ..core.llm import HelloAgentsLLM
from ..core.config import Config

DEFAULT_REACT_SYSTEM_PROMPT = """你是一个通过工具解决任务的助手。
按需调用业务工具获取信息或执行操作。Thought 可记录简短的行动说明；
取得足够依据后用 Finish 返回最终答案。不要把计划当成已完成的操作。"""


class ReActAgent(SimpleAgent):
    """ReAct 策略 Agent。
    
    统一所有运行入口（run, arun, stream_run, arun_stream），依托 Thought 和 Finish
    内置伪工具表达思考演进与最终决策。
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        tool_registry: Optional[ToolRegistry] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        components=None,
    ):
        """初始化 ReActAgent 实例。
        
        Args:
            name: Agent 名称标识
            llm: 语言模型客户端
            tool_registry: 业务工具注册表
            system_prompt: 自定义提示词，缺省为 DEFAULT_REACT_SYSTEM_PROMPT
            config: 运行时配置
            max_steps: 最大允许的工具执行轮数（步数限制），等同于 max_tool_iterations
            components: 运行时组件集合（如历史管理、追踪记录等）
        """
        super().__init__(
            name,
            llm,
            system_prompt or DEFAULT_REACT_SYSTEM_PROMPT,
            config,
            tool_registry=(
                tool_registry if tool_registry is not None else ToolRegistry()
            ),
            max_tool_iterations=max_steps,
            components=components,
        )
        self.max_steps = max_steps
        self._builtin_tools = {"Thought", "Finish"}  # ReAct 策略保留的内置工具集合
        self._parallel_tools = True

    @property
    def max_steps(self) -> int:
        """最大执行步数（映射到底层 SimpleAgent 的 max_tool_iterations）。"""
        return self.max_tool_iterations

    @max_steps.setter
    def max_steps(self, value: int) -> None:
        """设置最大执行步数并进行正整数类型校验。"""
        if type(value) is not int or value < 1:
            raise ValueError("max_steps 必须是正整数")
        self.max_tool_iterations = value

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema 列表。
        
        在基类业务工具列表的基础上，向模型注入 Thought（思考）和 Finish（终态结论）
        两个系统保留工具定义，使模型在同一个 Function Calling 空间内协同表达推理与行动。
        """
        schemas = []

        # 1. 注入内置工具：Thought（记录决策推理过程，不中断执行流程）
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "Thought",
                    "description": "分析问题，制定策略，记录简短的行动说明。在需要思考时调用此工具。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reasoning": {
                                "type": "string",
                                "description": "简短的行动计划或结论",
                            }
                        },
                        "required": ["reasoning"],
                    },
                },
            }
        )

        # 2. 注入内置工具：Finish（携带最终回答并标记运行闭环完成）
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "Finish",
                    "description": "当你有足够信息得出结论时，使用此工具返回最终答案。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string", "description": "最终答案"}
                        },
                        "required": ["answer"],
                    },
                },
            }
        )

        # 3. 校验并追加用户注册的业务工具（防御性检测名称冲突）
        if self.tool_registry:
            user_tool_schemas = super()._build_tool_schemas()
            if any(
                s["function"]["name"] in self._builtin_tools for s in user_tool_schemas
            ):
                raise ValueError("用户工具不能使用 ReAct 保留名称 Thought 或 Finish")
            schemas.extend(user_tool_schemas)

        return schemas

    def _handle_builtin_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """本地拦截并处理 ReAct 内置保留工具调用，无需发起外部网络或实际函数调用。"""
        if tool_name == "Thought":
            reasoning = arguments.get("reasoning", "")
            return {"content": f"推理: {reasoning}", "finished": False}
        elif tool_name == "Finish":
            answer = arguments.get("answer", "")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Finish.answer 必须是非空字符串")
            return {
                "content": f"最终答案: {answer}",
                "finished": True,
                "final_answer": answer,
            }
        else:
            return {"content": f"未知的内置工具: {tool_name}", "finished": False}

    async def _execute_tools_async(
        self, tool_calls, current_step=1, on_result=None, **kwargs
    ):
        """异步并发执行工具调用批次，并在本地拦截 Thought/Finish 伪工具。
        
        1. 使用 asyncio.Semaphore 限制全局最大并发数；
        2. 若某任务抛出严重 BaseException，设置 failed 信号并取消同批次其它在跑任务；
        3. 业务工具通过 deadline 加上超时保护，内置伪工具直接走 _handle_builtin_tool 快速响应。
        """
        semaphore = asyncio.Semaphore(self.config.max_concurrent_tools)
        failed = asyncio.Event()

        async def one(call):
            """单工具调用执行子协程。"""
            async with semaphore:
                if failed.is_set():
                    raise asyncio.CancelledError()
                try:
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("工具参数必须是 JSON 对象")
                    # 内置工具直接在本地内存处理
                    if call.name in self._builtin_tools:
                        result = self._handle_builtin_tool(call.name, arguments)
                    else:
                        # 外部业务工具包装异步超时限制
                        async with deadline(self.config.tool_async_timeout):
                            result = {
                                "content": await self._aexecute_tool_call(
                                    call.name, arguments
                                ),
                                "finished": False,
                            }
                except (ValueError, TypeError) as exc:
                    # 参数反序列化或格式错误封装为 error 状态回执，允许大模型在下一轮自我纠正
                    result = {
                        "content": json.dumps(
                            {"status": "error", "text": str(exc)}, ensure_ascii=False
                        ),
                        "finished": False,
                    }
                except BaseException:
                    failed.set()
                    raise
                if on_result is not None:
                    on_result(call.id, result)
                return call.name, call.id, result

        tasks = [asyncio.create_task(one(call)) for call in tool_calls]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            # 异常发生时优雅清理尚未完成的兄弟协程
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

