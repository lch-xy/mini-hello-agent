"""在共享工具与事件运行机制上提供简洁的 Agent 接口。"""

import asyncio
import json
from copy import deepcopy
from contextlib import aclosing
from typing import Optional, List, TYPE_CHECKING
from ..core.agent import Agent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.components import AgentComponents
from ..core.message import Message
from ..core.lifecycle import EventType
from ..core.streaming import StreamEvent, StreamEventType
from ..core.budget import BudgetExceeded
from ..core.runtime import RunResult, sync_events, turn_events, close_interrupted_turn
from ..context.providers import ContextAssembler, ContextProvider

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry


class SimpleAgent(Agent):
    """基于共享 Runtime 实现的直观 Agent 入口。
    
    run、arun 与流式事件入口（stream_run, arun_stream）共用相同的底层执行语义。
    负责管理当前会话的运行生命周期、长期历史提交、多入口转换以及并发防护。
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
        context_builder: Optional[ContextAssembler] = None,
        context_providers: Optional[List[ContextProvider]] = None,
        components: Optional[AgentComponents] = None,
    ):
        """
        初始化 SimpleAgent 实例。

        Args:
            name: Agent 唯一标识名称
            llm: 模型层适配器实例（实现 P01 协议契约）
            system_prompt: 角色系统提示词
            config: 运行时配置对象
            tool_registry: 工具注册表（可选，提供且启用时开启 Function Calling）
            enable_tool_calling: 是否启用工具调用能力
            max_tool_iterations: 一轮运行中允许调用工具的最大迭代步数（正整数）
            context_builder: 上下文装配器（用于动态注入提示词包和外部知识）
            context_providers: 外部上下文提供者列表
            components: 显式组件覆盖；未指定的部分沿用 Config 默认装配
        """
        # 将配置与核心组件传递给父类 Agent 进行依赖解析与注入
        super().__init__(
            name,
            llm,
            system_prompt,
            config,
            tool_registry=tool_registry,
            components=components,
        )
        # 仅当显式提供了工具注册表且参数为 True 时才开启工具调用能力
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_tool_iterations = max_tool_iterations
        self.context_builder = context_builder
        self.context_providers = list(context_providers or [])
        if self.context_providers and context_builder is None:
            raise ValueError("使用 context_providers 需要显式配置 context_builder")
        self.last_context_diagnostics = None

        if type(max_tool_iterations) is not int or max_tool_iterations < 1:
            raise ValueError("max_tool_iterations 必须是正整数")
            
        self.last_run = None                # 记录最近一次运行的结果指标 (RunResult)
        self._run_active = False            # 会话并发锁：同一 Agent 实例禁止并发调用
        self._messages_since_save = 0       # 距离上次自动保存积累的消息数

    def run(self, input_text, **kwargs):
        """同步阻塞执行入口。
        
        通过 sync_events 消费底层的异步事件生成器，并在执行完成后返回最终文本答复。
        """
        for _ in sync_events(self._events(input_text, stream=False, **kwargs)):
            pass
        return self.last_run.answer

    def stream_run(self, input_text, **kwargs):
        """同步流式生成器入口。
        
        通过 sync_events 逐个产出大模型吐出的文本分片（LLM_CHUNK）。
        """
        for event in sync_events(self._events(input_text, stream=True, **kwargs)):
            if event.type == StreamEventType.LLM_CHUNK:
                yield event.data["chunk"]

    async def arun(
        self,
        input_text,
        *,
        on_start=None,
        on_step=None,
        on_finish=None,
        on_error=None,
        on_tool_call=None,
        **kwargs,
    ):
        """异步执行入口。
        
        异步遍历内部 _events 生成器，支持生命周期回调，返回最终文本答复。
        """
        async for _ in self._events(
            input_text,
            stream=False,
            on_start=on_start,
            on_step=on_step,
            on_finish=on_finish,
            on_error=on_error,
            on_tool_call=on_tool_call,
            **kwargs,
        ):
            pass
        return self.last_run.answer

    async def arun_stream(
        self,
        input_text,
        *,
        on_start=None,
        on_step=None,
        on_finish=None,
        on_error=None,
        on_tool_call=None,
        **kwargs,
    ):
        """异步流式入口。
        
        异步产生 StreamEvent 公开事件流（包含 Chunk、工具调用开始/结束、步骤变化等）。
        """
        async with aclosing(
            self._events(
                input_text,
                stream=True,
                on_start=on_start,
                on_step=on_step,
                on_finish=on_finish,
                on_error=on_error,
                on_tool_call=on_tool_call,
                **kwargs,
            )
        ) as events:
            async for event in events:
                yield event

    async def _run_loop(self, messages, packets, stream, kwargs):
        """运行策略循环的模板扩展点。
        
        默认直接委托给 P03 核心 Runtime 的 turn_events。
        后续阶段的复杂策略（如 ReAct、Reflection、PlanSolve）可通过覆写此方法拓展执行逻辑。
        """
        async with aclosing(
            turn_events(self, messages, packets, stream=stream, kwargs=kwargs)
        ) as source:
            async for event in source:
                yield event

    def _build_messages(self, input_text):
        """将长期历史与当前输入组装为本次调用的在途工作消息序列 (messages)。
        
        设计关键点：
        1. 优先从历史 Message 的 metadata["model_message"] 读取深拷贝的原生模型结构，
           确保上一轮模型返回的 tool_calls 及服务商字段（如 DeepSeek reasoning_content）
           在跨轮重放时不丢失；
        2. 历史摘要（summary）角色降级为带前缀的参考文本，不提升为系统指令。
        """
        messages = (
            [{"role": "system", "content": self.system_prompt}]
            if self.system_prompt
            else []
        )
        for msg in self._history:
            native = (msg.metadata or {}).get("model_message")
            if native is not None:
                # 必须深拷贝，避免本轮对话对消息对象的修改反向污染长期历史
                messages.append(deepcopy(native))
            else:
                messages.append(
                    {
                        "role": "user" if msg.role == "summary" else msg.role,
                        "content": (
                            "[历史摘要，供参考]\n" + msg.content
                            if msg.role == "summary"
                            else msg.content
                        ),
                    }
                )
        # 将用户当前轮次的输入作为末尾 user 消息追加
        messages.append({"role": "user", "content": input_text})
        return messages

    def _commit_messages(self, messages, *, compress=True):
        """将本次运行确认成功的消息原子写入持久化历史管理器。
        
        在压缩、会话保存或发出 AGENT_FINISH 事件前执行。
        保证运行中途崩溃不会留下半截未决状态，保持历史状态一致性。
        """
        for item in messages:
            self.history_manager.append(
                Message(
                    item.get("content") or "",
                    item["role"],
                    metadata={"model_message": deepcopy(item)},
                )
            )
        # 重新评估历史 Token 消耗
        self._history_token_count = self.token_counter.count_messages(
            self.get_history()
        )
        # 检查是否触发历史自动压缩
        if compress and self._should_compress():
            self._compress_history()
        self._messages_since_save += len(messages)
        # 检查是否满足自动持久化会话的步长间隔
        if (
            self.config.auto_save_enabled
            and self.session_store is not None
            and self._messages_since_save >= self.config.auto_save_interval
        ):
            self._auto_save()
            self._messages_since_save = 0

    async def _aexecute_tool_call(self, name, arguments):
        """调度工具执行并对超长结果进行安全截断。
        
        流程：
        1. 类型转换：将字典参数转换为目标工具期望的类型；
        2. 执行工具：委托 ToolRegistry 执行受策略与熔断保护的函数；
        3. 结果截断：通过 truncator 检查输出大小，超大输出生成本地预览并落盘，
           防止单次工具输出撑爆模型的上下文窗口 (Context Window)。
        """
        parameters = (
            self._convert_parameter_types(name, arguments)
            if self.tool_registry.get_tool(name)
            else arguments.get("input", "")
        )
        response = await self.tool_registry.aexecute_tool(name, parameters)
        text = response.to_model_text()
        truncated = self.truncator.truncate(name, text)
        if truncated.get("truncated"):
            return json.dumps(
                {
                    "status": "partial",
                    "text": "工具结果过长，以下为预览",
                    "data": {
                        "preview": truncated["preview"],
                        "full_output_path": truncated.get("full_output_path"),
                    },
                },
                ensure_ascii=False,
            )
        return text

    async def _events(
        self,
        input_text,
        *,
        stream,
        on_start=None,
        on_step=None,
        on_finish=None,
        on_error=None,
        on_tool_call=None,
        **kwargs,
    ):
        """Agent 任务运行的核心生命周期中枢。
        
        负责：
        1. 并发加锁与结果状态初始化；
        2. 会话追踪记录仪 (TraceLogger) 启动；
        3. 驱动策略循环 (_run_loop) 并将内部事件转换为公开 StreamEvent；
        4. 运行成功后的原子历史提交 (_commit_messages)；
        5. 异常捕获、取消中断与 finally 收尾防护 (close_interrupted_turn)。
        """
        # 【并发互斥防御】：同一个 Agent 实例持有可变的历史状态，严禁并发重入
        if self._run_active:
            raise RuntimeError(
                "同一个 Agent 不能并发修改会话，请为独立任务创建独立实例"
            )
        self._run_active = True
        self.last_run = RunResult()
        logger = self.trace_logger
        messages = None
        committed = False
        try:
            # 开启执行追踪日志（若配置开启）
            if self.config.trace_enabled and (
                logger is None or logger.jsonl_file.closed
            ):
                from ..observability import TraceLogger

                logger = self.trace_logger = TraceLogger(
                    self.config.trace_dir,
                    sanitize=self.config.trace_sanitize,
                    html_include_raw_response=self.config.trace_html_include_raw_response,
                )
                logger.log_event("session_start", {"agent_name": self.name})
                
            packets = self._context_packets(kwargs)
            # 组装本次调用的工作消息
            messages = self._build_messages(input_text)
            start = len(messages) - 1  # 标记本次运行新增消息的起始偏移量
            
            # 触发启动事件
            await self._emit_event(
                EventType.AGENT_START, on_start, input_text=input_text
            )
            yield StreamEvent.create(
                StreamEventType.AGENT_START, self.name, input_text=input_text
            )
            
            # 驱动底层核心 Runtime 循环
            async with aclosing(
                self._run_loop(messages, packets, stream, kwargs)
            ) as source:
                async for event in source:
                    kind = event["kind"]
                    # 规范化工具事件字段
                    if kind in ("tool_start", "tool_finish"):
                        event = dict(
                            event, tool_name=event["name"], tool_call_id=event["id"]
                        )
                    if kind == "tool_start":
                        try:
                            event["args"] = json.loads(event["arguments"])
                        except (TypeError, ValueError):
                            event["args"] = None
                            
                    # 写入 Trace 追踪日志
                    if logger and kind != "chunk":
                        trace_kind = {
                            "model": "model_output",
                            "tool_start": "tool_call",
                            "tool_finish": "tool_result",
                        }.get(kind, kind)
                        logger.log_event(trace_kind, event, step=event.get("step"))
                        
                    # 事件转换：模型输出 Chunk
                    if kind == "chunk":
                        yield StreamEvent.create(
                            (
                                StreamEventType.THINKING
                                if event.get("phase") == "reflection"
                                else StreamEventType.LLM_CHUNK
                            ),
                            self.name,
                            chunk=event["text"],
                            **{
                                k: v
                                for k, v in event.items()
                                if k not in {"kind", "text"}
                            },
                        )
                    elif kind == "model":
                        if not getattr(self, "_parallel_tools", False):
                            await self._emit_event(
                                EventType.STEP_FINISH, on_step, **event
                            )
                    # 事件转换：步骤开始与结束
                    elif kind in ("step_start", "step_finish"):
                        event_type = (
                            StreamEventType.STEP_START
                            if kind == "step_start"
                            else StreamEventType.STEP_FINISH
                        )
                        if event.get("phase") == "tool_loop":
                            await self._emit_event(
                                (
                                    EventType.STEP_START
                                    if kind == "step_start"
                                    else EventType.STEP_FINISH
                                ),
                                on_step,
                                **event,
                            )
                        yield StreamEvent.create(event_type, self.name, **event)
                    # 事件转换：工具执行开始
                    elif kind == "tool_start":
                        await self._emit_event(
                            EventType.TOOL_CALL, on_tool_call, **event
                        )
                        yield StreamEvent.create(
                            StreamEventType.TOOL_CALL_START, self.name, **event
                        )
                    # 事件转换：工具执行结束
                    elif kind == "tool_finish":
                        yield StreamEvent.create(
                            StreamEventType.TOOL_CALL_FINISH, self.name, **event
                        )
                        
            # 更新会话元数据（总步数与 Token 统计）
            self._session_metadata.update(
                total_steps=self.last_run.model_calls,
                total_tokens=self.last_run.usage.get("total_tokens", 0),
            )
            
            # 【原子提交】：整轮运行成功，将本轮完整消息（user->assistant->tool->assistant）提交入历史
            committed = True
            self._commit_messages(messages[start:])
            
            # 派发最终完成事件
            await self._emit_event(
                EventType.AGENT_FINISH,
                on_finish,
                result=self.last_run.answer,
                status=self.last_run.status,
                total_steps=self.last_run.model_calls,
                total_tokens=self.last_run.usage.get("total_tokens", 0),
            )
            yield StreamEvent.create(
                StreamEventType.AGENT_FINISH,
                self.name,
                result=self.last_run.answer,
                status=self.last_run.status,
                stop_reason=self.last_run.stop_reason,
                total_steps=getattr(
                    self, "_plan_total_steps", self.last_run.model_calls
                ),
                total_tokens=self.last_run.usage.get("total_tokens", 0),
                max_steps_reached=self.last_run.status == "max_iterations",
                **(
                    {"total_iterations": self._reflection_iterations}
                    if hasattr(self, "_reflection_iterations")
                    else {}
                ),
            )
        except (asyncio.CancelledError, GeneratorExit):
            # 处理外部取消或生成器提前退出
            if not committed:
                self.last_run.status = "cancelled"
            raise
        except Exception as exc:
            # 处理未捕获异常：区分预算耗尽与常规错误
            self.last_run.status = "budget_exhausted" if isinstance(exc, BudgetExceeded) else "failed"
            if logger:
                logger.log_event(
                    "error", {"message": str(exc), "error_type": type(exc).__name__}
                )
            await self._emit_event(
                EventType.AGENT_ERROR,
                on_error,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            yield StreamEvent.create(
                StreamEventType.ERROR,
                self.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        finally:
            try:
                # 【防护收尾】：如果运行被异常或取消打断（尚未提交历史），
                # 必须调用 close_interrupted_turn 为所有孤立未决的工具调用补齐 unknown 回执，
                # 防止下次请求大模型时报 400 格式错误，同时不自动重试有副作用的操作。
                if messages is not None and not committed:
                    self._session_metadata.update(
                        total_steps=self.last_run.model_calls,
                        total_tokens=self.last_run.usage.get("total_tokens", 0),
                    )
                    self._commit_messages(
                        close_interrupted_turn(messages[start:]), compress=False
                    )
            finally:
                try:
                    if logger:
                        logger.log_event(
                            "session_end",
                            {
                                "status": self.last_run.status,
                                "final_answer": self.last_run.answer,
                                "total_steps": self.last_run.model_calls,
                                "usage": self.last_run.usage,
                            },
                        )
                        logger.finalize()
                finally:
                    # 最终务必释放会话并发锁
                    self._run_active = False

    def _context_packets(self, kwargs):
        """提取附加上下文数据包。"""
        packets = list(kwargs.pop("context_packets", None) or [])
        if packets and self.context_builder is None:
            raise ValueError("使用 context_packets 需要显式配置 context_builder")
        return packets

    def _prepare_context(self, messages, packets, tool_schemas=None):
        """在调用模型前，调用 context_builder 整合外部上下文提供者的补充数据。"""
        if self.context_builder is None:
            return messages
        query = next(
            (
                message.get("content") or ""
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        gathered = list(packets)
        for provider in self.context_providers:
            gathered.extend(provider.get_context(query))
        result = self.context_builder.build_messages(
            messages, additional_packets=gathered, tool_schemas=tool_schemas
        )
        self.last_context_diagnostics = result.diagnostics
        return result.messages

    def add_tool(self, tool, auto_expand: bool = True) -> None:
        """
        添加工具到 Agent（便利方法）。

        Args:
            tool: Tool 实例或带 @tool_action 的对象
            auto_expand: 是否自动展开可展开的工具（默认 True）
        """
        if not self.tool_registry:
            from ..tools.registry import ToolRegistry

            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True

        self.tool_registry.register_tool(tool, auto_expand=auto_expand)

    def remove_tool(self, tool_name: str) -> bool:
        """从 Agent 移除指定名称的工具。"""
        if self.tool_registry and tool_name in self.tool_registry.list_tools():
            self.tool_registry.unregister(tool_name)
            return True
        return False

    def list_tools(self) -> list:
        """列出当前 Agent 可用的所有工具名称列表。"""
        if self.tool_registry:
            return self.tool_registry.list_tools()
        return []

    def has_tools(self) -> bool:
        """检查当前 Agent 是否具备可用的工具。"""
        return self.enable_tool_calling and self.tool_registry is not None
