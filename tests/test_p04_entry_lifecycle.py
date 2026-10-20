"""P04 集成验收：入口、取消、回调和传输边界。

本测试套件验证 P04 核心运行生命周期契约：
1. 四大运行入口（run, arun, stream_run, arun_stream）的执行等价性：确保副作用只发生一次，最终历史与统计指标完全一致；
2. 并发防重入与同步桥接保护：在事件循环中调用同步 run 或并发调用已激活 Agent 时立即报错，不破坏历史状态；
3. Hook 异常与超时隔离：回调函数的超时或抛错绝不篡改或中断 Agent 主流程结果；
4. 模型等待取消：取消等待模型请求时显式关闭数据源，状态流转为 cancelled，并释放运行锁允许后续调用；
5. 同步工具取消后效保护：后台线程工具被取消时，补齐 execution_status="unknown" 的防御性回执；
6. SSE 传输分片与 Unicode 边界：验证网络任意字节切片（如 7 字节断点）及多字节 UTF-8 字符流能被客户端无损重组。
"""

import asyncio
import codecs
import json
import threading
from copy import deepcopy

import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType, stream_to_sse


class Model:
    """按序产出预设响应的测试 Mock 模型。"""
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []  # 记录收到的全部请求历史

    def invoke(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return next(self.responses)

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        response = self.invoke(messages, **kwargs)
        if response.content:
            # 模拟拆分成两个增量 chunk 产出，最后一次产出完整响应对象
            yield response.content[:1]
            yield response.content[1:]
        yield response


@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_four_entries_preserve_one_write_and_one_final_message(mode):
    """验证：四种入口方式下，工具副作用仅发生一次，历史记录与状态统计完全一致。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or value, name="write")
    # 模拟两轮响应：第一轮发起工具调用 write，第二轮返回最终文本 "完成"
    model = Model([
        LLMToolResponse(None, [ToolCall("w1", "write", '{"input":"once"}')], "offline"),
        LLMToolResponse("完成", [], "offline"),
    ])
    agent = SimpleAgent("entry", model, tool_registry=registry)

    async def consume():
        if mode == "arun":
            return await agent.arun("task")
        # 收集异步流式事件并拼接打字机分块
        events = [event async for event in agent.arun_stream("task")]
        assert "".join(e.data["chunk"] for e in events if e.type == StreamEventType.LLM_CHUNK) == "完成"
        return events[-1].data["result"]

    # 针对不同模式分别驱动执行
    if mode == "run":
        answer = agent.run("task")
    elif mode == "stream_run":
        answer = "".join(agent.stream_run("task"))
    else:
        answer = asyncio.run(consume())

    # 断言 1：返回值与工具副作用均只发生了一次
    assert answer == "完成" and writes == ["once"]
    # 断言 2：第二轮模型请求精准携带了 tool_call_id="w1" 的执行回执
    assert model.requests[-1][-1]["tool_call_id"] == "w1"
    # 断言 3：持久化历史形成完整的四元组 [user, assistant, tool, assistant]
    assert [m.role for m in agent.get_history()] == ["user", "assistant", "tool", "assistant"]
    # 断言 4：调用统计符合预期（2次模型，1次工具，状态 completed，运行锁解除）
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 1
    assert agent.last_run.status == "completed" and not agent._run_active


@pytest.mark.asyncio
async def test_active_agent_and_sync_bridge_fail_without_corrupting_history():
    """验证：事件循环内滥用同步 run 或并发调用激活 Agent 时直接抛错，且不污染历史。"""
    agent = SimpleAgent("guard", Model([LLMToolResponse("next", [], "offline")]))
    
    # 检查点 1：禁止在运行中的事件循环直接同步调用 run（应使用 arun）
    with pytest.raises(RuntimeError, match="事件循环"):
        agent.run("wrong entry")
    assert agent.get_history() == []

    # 检查点 2：单 Agent 实例防重入保护（禁止并发启动两轮对话）
    source = agent.arun_stream("first")
    await anext(source)  # 启动本轮流式运行，此时处于 active 状态
    try:
        with pytest.raises(RuntimeError, match="并发"):
            await agent.arun("second")
    finally:
        # 关闭未完成的流，重置 active 状态
        await source.aclose()

    # 断言 3：流释放后锁状态恢复，后续调用可正常完成
    assert not agent._run_active
    assert await agent.arun("next") == "next"


@pytest.mark.asyncio
async def test_hook_timeout_and_error_do_not_replace_execution_result():
    """验证：观察者 Hook 的超时与异常不会中断 Agent 执行，亦不替换正常返回结果。"""
    calls = []

    async def slow(event):
        """模拟耗时超过配置上限的钩子。"""
        calls.append(event.type.value)
        await asyncio.sleep(10)

    async def broken(event):
        """模拟抛出严重异常的钩子。"""
        calls.append(event.type.value)
        raise RuntimeError("observer failure")

    # 配置 hook_timeout_seconds=0.01 秒
    agent = SimpleAgent("hooks", Model([LLMToolResponse("answer", [], "offline")]),
                        config=Config(hook_timeout_seconds=0.01))
    
    # 执行并断言：主流程未被中断，返回模型答案
    assert await agent.arun("task", on_start=slow, on_finish=broken) == "answer"
    # 断言：两项钩子均被触发
    assert calls == ["agent_start", "agent_finish"]
    assert agent.last_run.status == "completed" and not agent._run_active
    assert agent.get_history()[-1].content == "answer"


@pytest.mark.asyncio
async def test_cancelling_model_wait_closes_source_and_allows_next_run():
    """验证：取消等待中的流式请求会强制关闭数据源、标记 cancelled 并释放运行锁。"""
    started, closed = asyncio.Event(), []

    class SlowModel(Model):
        """模拟模型响应耗时较长。"""
        async def astream_invoke_with_tools(self, *args, **kwargs):
            try:
                started.set()
                await asyncio.sleep(10)
                yield LLMToolResponse("late", [], "offline")
            finally:
                # 记录底层生成器是否被最终关闭
                closed.append(True)

    agent = SimpleAgent("cancel", SlowModel([]))

    async def consume():
        async for _ in agent.arun_stream("task"):
            pass

    # 启动异步任务并在请求发出后取消
    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task

    # 断言：数据源被关闭，状态标记为 cancelled，运行锁释放
    assert closed == [True]
    assert agent.last_run.status == "cancelled" and not agent._run_active
    
    # 替换为可用模型，验证恢复后下一次调用正常
    agent.llm = Model([LLMToolResponse("recovered", [], "offline")])
    assert await agent.arun("continue") == "recovered"


@pytest.mark.asyncio
async def test_cancelled_sync_tool_can_write_later_but_receipt_stays_unknown():
    """验证：被取消的同步线程工具补齐 unknown 回执，防止破坏历史消息序列一致性。"""
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    writes = []

    def write(value):
        """模拟在独立线程中执行并在受控时机完成副作用的工具。"""
        started.set()
        try:
            if not release.wait(2):
                raise TimeoutError("test did not release worker")
            writes.append(value)
            return "late receipt"
        finally:
            finished.set()

    registry = ToolRegistry()
    registry.register_function(write, name="write")
    agent = SimpleAgent("thread", Model([
        LLMToolResponse(None, [ToolCall("late", "write", '{"input":"once"}')], "offline")
    ]), tool_registry=registry)
    
    task = asyncio.create_task(agent.arun("task"))
    try:
        # 等待工具线程启动后取消主协程
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        
        # 断言 1：历史记录中已注入防御性的 unknown 回执，且取消时副作用尚未写入
        receipts = [m for m in agent.get_history() if m.role == "tool"]
        assert len(receipts) == 1
        assert json.loads(receipts[0].content)["execution_status"] == "unknown"
        assert writes == [] and not agent._run_active
    finally:
        # 释放后台线程让其跑完
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        
    # 断言 2：后台线程最终完成了写入，但已持久化的 unknown 回执不受后效修改
    assert writes == ["once"]
    assert json.loads(receipts[0].content)["execution_status"] == "unknown"


@pytest.mark.asyncio
async def test_sse_frames_survive_arbitrary_byte_chunks_and_unicode():
    """验证：SSE 流经受任意字节切片与 Unicode 多字节字符拆分时可被完整无损反序列化。"""
    agent = SimpleAgent("sse", Model([LLMToolResponse("中文回答", [], "offline")]))
    # 获取原始 SSE 字节流
    wire = "".join([part async for part in stream_to_sse(agent.arun_stream("task"))]).encode("utf-8")
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending, frames = "", []
    
    # 模拟网络层极端拆包：按固定 7 个字节的碎片强制分片（故意跨越中文字符与 \\n\\n 帧边界）
    for offset in range(0, len(wire), 7):
        pending += decoder.decode(wire[offset:offset + 7])
        while "\n\n" in pending:
            frame, pending = pending.split("\n\n", 1)
            event_line, data_line = frame.split("\n", 1)
            payload = json.loads(data_line.removeprefix("data: "))
            assert event_line == "event: " + payload["type"]
            frames.append(payload)
    pending += decoder.decode(b"", final=True)
    
    # 断言：缓冲区无残留，首尾事件符合生命周期，中文文本完整无损拼接
    assert pending == ""
    assert frames[0]["type"] == "agent_start" and frames[-1]["type"] == "agent_finish"
    assert "".join(f["data"]["chunk"] for f in frames if f["type"] == "llm_chunk") == "中文回答"
    assert frames[-1]["data"]["result"] == "中文回答"
