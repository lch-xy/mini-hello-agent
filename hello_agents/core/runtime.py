"""阻塞、异步与流式 Agent 入口共用同一工具循环。

本模块负责执行顺序与终止条件。Agent 提供提示词、上下文
选择与工具调度，展示层消费同一组事件。
"""

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from contextlib import aclosing, asynccontextmanager
from .llm_response import LLMToolResponse


@dataclass
class RunResult:
    """Agent 一次运行（Turn）的执行结果与统计数据。
    
    用于记录本次调用的最终答案、生命周期状态、停止原因以及 Token 消耗统计。
    与 HistoryManager（长期历史）分离，避免将中间运行状态与持久化历史混淆。
    """
    answer: str = ""                       # 最终交付给用户的文本答复
    status: str = "running"               # 运行状态：running, completed, max_iterations, output_limit, failed, cancelled 等
    stop_reason: str | None = None        # 模型最后一轮调用的 finish_reason（如 stop, length, tool_calls 等）
    model_calls: int = 0                  # 本次运行累计发起的 LLM 调用次数
    tool_calls: int = 0                   # 本次运行累计执行的工具函数次数
    usage: dict[str, Any] = field(default_factory=dict)  # Token 统计字典（prompt_tokens, completion_tokens, total_tokens）


class EmptyModelResponse(RuntimeError):
    """服务商既未返回可见文本，也未返回工具请求。
    
    属于模型供应商层面的严重异常或静默失败，框架禁止将其伪装为正常空字符串返回。
    """


def close_interrupted_turn(messages):
    """保留回执并关闭待处理请求，不将未知结果标记为执行失败。

    同步工具被取消后，仍可能在线程中完成执行。因此，缺少
    回执表示结果未知，不能据此直接重试写入操作。
    
    本函数扫描尚未闭合的 tool_calls，为其补齐 status="unknown" 的 tool 角色回执，
    并追加一条说明性 assistant 消息，使历史消息序列在遇到异常中断时依然符合
    大模型供应商“tool_calls 必须紧随对应 tool 回执”的强契约约束。
    """
    closed = []
    pending = {}  # 记录已发出但尚未取得回执的 tool_call_id

    def flush():
        """为所有挂起的工具调用补齐未知状态的防御性回执。"""
        for call_id in pending:
            closed.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "status": "error",
                            "execution_status": "unknown",
                            "text": "运行已中断，未取得此请求的执行回执。请先核实外部状态，不要直接重试有副作用的操作。",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        pending.clear()

    for message in messages:
        if message["role"] != "tool":
            flush()  # 遇到非 tool 消息，先将之前未闭合的工具调用补齐
        else:
            # 遇到 tool 消息，从挂起字典中移除已解决的调用 ID
            pending.pop(message.get("tool_call_id"), None)
        closed.append(message)
        # 扫描 assistant 消息中携带的新工具调用意图
        for call in message.get("tool_calls", []):
            pending[call["id"]] = call
            
    flush()  # 遍历结束后，收尾补齐剩余悬挂的工具请求
    closed.append(
        {"role": "assistant", "content": "本轮运行已中断，任务尚未确认完成。"}
    )
    return closed


def stopped_by_limit(reason):
    """判断模型是否由于达到了输出 Token 上限（截断）而停止。
    
    常见截断标识包括：length, max_tokens, max_output_tokens。
    """
    value = getattr(reason, "value", reason)
    return str(value).lower() in {"length", "max_tokens", "max_output_tokens"}


def sync_events(iterator):
    """在普通同步 Python 代码中消费异步事件源。
    
    将异步生成器（_events）桥接为普通的同步生成器：
    1. 检查运行环境，禁止在已有 running loop 的线程中嵌套调用（防死锁）；
    2. 创建临时独立的事件循环逐个消费 anext；
    3. 在 finally 块中通过 aclose() 和 shutdown_asyncgens() 确保生成器被完全回收，
       即使外部调用方提前 break，也能触发协程内部的清理逻辑。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # 避免在已运行 loop 的环境中（如 Jupyter、FastAPI）由于嵌套 loop 导致死锁
        raise RuntimeError(
            "同步入口不能在运行中的事件循环内调用，请使用 await arun 或 arun_stream"
        )
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                # 逐个驱动异步生成器的下一个事件
                yield loop.run_until_complete(anext(iterator))
            except StopAsyncIteration:
                return
    finally:
        # 彻底清理异步生成器资源与后台线程池
        loop.run_until_complete(iterator.aclose())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


@asynccontextmanager
async def deadline(seconds):
    """基于定时器句柄的异步超时上下文管理器。
    
    相比标准 wait_for，deadline 能在超时发生时向当前 Task 发起取消，
    并在退出时通过 uncancel 正确还原任务取消计数，防止异常被吞或生成器挂起。
    """
    task = asyncio.current_task()
    cancelling = task.cancelling() if hasattr(task, "cancelling") else 0
    expired = False

    def cancel():
        nonlocal expired
        expired = True
        task.cancel()

    # 注册未来到期的超时回调
    handle = asyncio.get_running_loop().call_later(seconds, cancel)
    try:
        yield
    except asyncio.CancelledError:
        # 如果是当前 deadline 超时引发的取消，转换为 TimeoutError 抛出
        if expired:
            if not hasattr(task, "uncancel") or task.uncancel() <= cancelling:
                raise TimeoutError("执行超过配置时限") from None
        raise
    finally:
        handle.cancel()  # 正常完成时取消定时器，防止资源泄漏


async def model_events(llm, messages, schemas, stream, kwargs):
    """将不同 LLM 实现与模式归一化为标准的内部异步事件流。
    
    负责调度协议层：
    1. 动态注入或剥离 tool_choice 参数；
    2. 支持流式带工具调用 (astream_invoke_with_tools)；
    3. 支持普通流式调用 (astream_invoke)；
    4. 支持非流式阻塞/异步调用 (invoke_with_tools / ainvoke)；
    5. 统一向外派发 {"kind": "chunk", "text": ...} 与 {"kind": "response", "response": ...}。
    """
    messages = deepcopy(messages)
    kwargs = dict(kwargs)
    if schemas:
        kwargs.setdefault("tool_choice", "auto")
    else:
        # 无可用工具时必须显式剥离 tool_choice，防止模型强行报错
        kwargs.pop("tool_choice", None)
        
    # 检查类本身：Mock 对象与透明记录包装器不能通过
    # __getattr__ 动态伪造的能力，静默绕过自身的 invoke 方法。
    native = getattr(type(llm), "astream_invoke_with_tools", None)
    
    # 场景 1：流式且模型原生支持带工具的流式调用
    if stream and native:
        final = None
        async with aclosing(
            llm.astream_invoke_with_tools(messages, schemas, **kwargs)
        ) as source:
            async for item in source:
                if isinstance(item, str):
                    yield {"kind": "chunk", "text": item}
                else:
                    final = item
        if final is None:
            raise EmptyModelResponse("流未返回完整模型响应")
        yield {"kind": "response", "response": final}
        
    # 场景 2：流式但无工具模式，使用普通流式调用
    elif stream and not schemas and getattr(type(llm), "astream_invoke", None):
        chunks = []
        async with aclosing(llm.astream_invoke(messages, **kwargs)) as source:
            async for chunk in source:
                chunks.append(chunk)
                yield {"kind": "chunk", "text": chunk}
        stats = getattr(llm, "last_call_stats", None)
        yield {
            "kind": "response",
            "response": LLMToolResponse(
                "".join(chunks),
                [],
                llm.model,
                usage=getattr(stats, "usage", {}) or {},
                finish_reason=getattr(stats, "finish_reason", None),
            ),
        }
        
    # 场景 3：非流式调用（优先使用原生异步方法，无异步则使用 to_thread 线程桥接）
    else:
        method = "invoke_with_tools" if schemas else "invoke"
        params = dict(messages=messages, **kwargs)
        if schemas:
            params["tools"] = schemas
        if getattr(type(llm), "a" + method, None):
            response = await getattr(llm, "a" + method)(**params)
        else:
            response = await asyncio.to_thread(getattr(llm, method), **params)
        if stream and getattr(response, "content", None):
            yield {"kind": "chunk", "text": response.content}
        yield {"kind": "response", "response": response}


async def turn_events(agent, messages, packets, *, stream, kwargs):
    """P03 核心工具循环状态机。
    
    负责调度模型调用、工具执行、超时管理、消息追加和循环终止条件判断。
    向外产生 step_start, model, tool_start, tool_finish, step_finish 等底层事件。
    """
    # 1. 提取工具定义 Schema（若启用了工具调用）
    schemas = agent._build_tool_schemas() if agent.enable_tool_calling else []
    result = agent.last_run
    
    # 2. 计算最大工具迭代轮数；无工具时上限为 1
    limit = agent.max_tool_iterations if schemas else 1
    
    # 循环上限为 limit + 1：多留出的 1 步用于在步数耗尽时剥离工具，发起最后总结调用
    for step in range(limit + (1 if schemas else 0)):
        # 判断工具预算是否已耗尽
        exhausted = step == limit
        
        # 保留最后一次总结调用，但将工具 schemas 清空，防止模型继续调用工具；
        # 同时标记状态为预算耗尽，不能据此认为任务已验证完成。
        active_schemas = [] if exhausted else schemas
        model_messages = agent._prepare_context(messages, packets, active_schemas)
        
        if getattr(agent, "_parallel_tools", False):
            yield {
                "kind": "step_start",
                "phase": "tool_loop",
                "step": step + 1,
                "max_steps": limit,
            }
            
        # 3. 带超时的模型调用
        response = None
        remaining = agent.config.llm_async_timeout
        async with aclosing(
            model_events(agent.llm, model_messages, active_schemas, stream, kwargs)
        ) as events:
            while True:
                started = asyncio.get_running_loop().time()
                try:
                    async with deadline(remaining):
                        event = await anext(events)
                except StopAsyncIteration:
                    break
                # 动态扣减已消耗的时延，确保整步不超时
                remaining -= asyncio.get_running_loop().time() - started
                if event["kind"] == "response":
                    response = event["response"]
                else:
                    yield dict(event, step=step + 1)
                    
        # 4. 统计与指标累加
        result.model_calls += 1
        usage = getattr(response, "usage", None) or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key in usage:
                result.usage[key] = result.usage.get(key, 0) + usage[key]
        result.stop_reason = getattr(response, "finish_reason", None)
        calls = getattr(response, "tool_calls", []) or []
        text = getattr(response, "content", None) or ""
        reasoning = getattr(response, "reasoning_content", None)
        private_fields = {"reasoning_content": reasoning} if isinstance(reasoning, str) and reasoning else {}
        
        # 派发模型响应完成事件
        yield {
            "kind": "model",
            "usage": usage,
            "step": result.model_calls,
            "content": text,
            "finish_reason": result.stop_reason,
            "tool_count": len(calls),
        }
        
        # 【终止条件 1】：模型输出被 Token 上限截断，立即停止，严禁执行残缺的工具参数
        if stopped_by_limit(result.stop_reason):
            result.answer, result.status = text, "output_limit"
            if text:
                messages.append({"role": "assistant", "content": text, **private_fields})
            return
            
        # 【终止条件 2】：无工具调用意图，或已达到步数上限（总结轮完毕）
        if not calls or exhausted:
            # 防御：服务商既未给回答也未给工具调用，属于空响应故障
            if not text.strip():
                raise EmptyModelResponse("模型没有返回公开回答或工具请求")
            result.answer = text
            result.status = "max_iterations" if exhausted else "completed"
            messages.append({"role": "assistant", "content": text, **private_fields})
            return  # 正常完成本轮循环
            
        # 【安全校验】：校验模型返回的工具调用 ID 必须非空且同批次唯一
        ids = [call.id for call in calls]
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(
            set(ids)
        ) != len(ids):
            raise ValueError("工具请求 ID 必须非空且在同一批次内唯一")
            
        # 将模型发出的工具调用意图记录入当前工作消息
        messages.append(
            {
                "role": "assistant",
                "content": text or None,
                **private_fields,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in calls
                ],
            }
        )
        
        # 场景 A：并行工具执行分支（若配置开启且无内置控制工具）
        if (
            getattr(agent, "_parallel_tools", False)
            and len(calls) > 1
            and not any(c.name in agent._builtin_tools for c in calls)
        ):
            for call in calls:
                yield {
                    "kind": "tool_start",
                    "name": call.name,
                    "id": call.id,
                    "arguments": call.arguments,
                    "step": step + 1,
                }

            def record(call_id, output):
                result.tool_calls += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output["content"],
                    }
                )

            outputs = await agent._execute_tools_async(
                calls, current_step=step + 1, on_result=record
            )
            for name, call_id, output in outputs:
                yield {
                    "kind": "tool_finish",
                    "name": name,
                    "id": call_id,
                    "result": output["content"],
                    "step": step + 1,
                }
            yield {
                "kind": "step_finish",
                "phase": "tool_loop",
                "step": step + 1,
                "tool_calls": len(calls),
            }
            continue  # 完成当前并行批次，进入下一轮迭代
            
        # 场景 B：串行执行每个工具调用（默认路径）
        finished = None
        for call in calls:
            yield {
                "kind": "tool_start",
                "name": call.name,
                "id": call.id,
                "arguments": call.arguments,
                "step": step + 1,
            }
            try:
                # 校验工具参数必须是合法的 JSON 对象
                args = json.loads(call.arguments)
                if not isinstance(args, dict):
                    raise ValueError("工具参数必须是 JSON 对象")
                if finished is not None:
                    # 如果前序内置工具已经提前宣布结束本轮
                    output = json.dumps(
                        {"status": "error", "text": "本轮已经结束，未执行此请求"},
                        ensure_ascii=False,
                    )
                elif call.name in getattr(agent, "_builtin_tools", set()):
                    # 处理内置框架控制工具（如结束或反思）
                    value = agent._handle_builtin_tool(call.name, args)
                    output = value["content"]
                    if value.get("finished"):
                        finished = value["final_answer"]
                else:
                    # 真正调度 P02 的工具注册表执行实际函数，受超时保护
                    async with deadline(agent.config.tool_async_timeout):
                        output = await agent._aexecute_tool_call(call.name, args)
            except (ValueError, TypeError) as exc:
                # 参数错误转为标准错误观察回传，给予模型在下一轮自纠错的机会
                output = json.dumps(
                    {"status": "error", "text": str(exc)}, ensure_ascii=False
                )
            result.tool_calls += 1
            # 将工具回执以 role: tool 追加，并精确绑定 tool_call_id
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )
            yield {
                "kind": "tool_finish",
                "name": call.name,
                "id": call.id,
                "result": output,
                "step": step + 1,
            }
            
        # 若内置工具提前产出最终答案，直接收尾返回
        if finished is not None:
            result.answer, result.status = finished, "completed"
            messages.append({"role": "assistant", "content": finished})
            if stream:
                yield {"kind": "chunk", "text": finished}
            return
            
        if getattr(agent, "_parallel_tools", False):
            yield {
                "kind": "step_finish",
                "phase": "tool_loop",
                "step": step + 1,
                "tool_calls": len(calls),
            }
