"""P04 集成验收：入口、取消、回调和传输边界。"""

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
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def invoke(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return next(self.responses)

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        response = self.invoke(messages, **kwargs)
        if response.content:
            # 两个真实增量，最终响应只提交一次完整消息。
            yield response.content[:1]
            yield response.content[1:]
        yield response


@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_four_entries_preserve_one_write_and_one_final_message(mode):
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or value, name="write")
    model = Model([
        LLMToolResponse(None, [ToolCall("w1", "write", '{"input":"once"}')], "offline"),
        LLMToolResponse("完成", [], "offline"),
    ])
    agent = SimpleAgent("entry", model, tool_registry=registry)

    async def consume():
        if mode == "arun":
            return await agent.arun("task")
        events = [event async for event in agent.arun_stream("task")]
        assert "".join(e.data["chunk"] for e in events if e.type == StreamEventType.LLM_CHUNK) == "完成"
        return events[-1].data["result"]

    if mode == "run":
        answer = agent.run("task")
    elif mode == "stream_run":
        answer = "".join(agent.stream_run("task"))
    else:
        answer = asyncio.run(consume())
    assert answer == "完成" and writes == ["once"]
    assert model.requests[-1][-1]["tool_call_id"] == "w1"
    assert [m.role for m in agent.get_history()] == ["user", "assistant", "tool", "assistant"]
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 1
    assert agent.last_run.status == "completed" and not agent._run_active


@pytest.mark.asyncio
async def test_active_agent_and_sync_bridge_fail_without_corrupting_history():
    agent = SimpleAgent("guard", Model([LLMToolResponse("next", [], "offline")]))
    with pytest.raises(RuntimeError, match="事件循环"):
        agent.run("wrong entry")
    assert agent.get_history() == []
    source = agent.arun_stream("first")
    await anext(source)  # 启动本轮，但还未请求模型。
    try:
        with pytest.raises(RuntimeError, match="并发"):
            await agent.arun("second")
    finally:
        await source.aclose()
    assert not agent._run_active
    assert await agent.arun("next") == "next"


@pytest.mark.asyncio
async def test_hook_timeout_and_error_do_not_replace_execution_result():
    calls = []

    async def slow(event):
        calls.append(event.type.value)
        await asyncio.sleep(10)

    async def broken(event):
        calls.append(event.type.value)
        raise RuntimeError("observer failure")

    agent = SimpleAgent("hooks", Model([LLMToolResponse("answer", [], "offline")]),
                        config=Config(hook_timeout_seconds=0.01))
    assert await agent.arun("task", on_start=slow, on_finish=broken) == "answer"
    assert calls == ["agent_start", "agent_finish"]
    assert agent.last_run.status == "completed" and not agent._run_active
    assert agent.get_history()[-1].content == "answer"


@pytest.mark.asyncio
async def test_cancelling_model_wait_closes_source_and_allows_next_run():
    started, closed = asyncio.Event(), []

    class SlowModel(Model):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            try:
                started.set()
                await asyncio.sleep(10)
                yield LLMToolResponse("late", [], "offline")
            finally:
                closed.append(True)

    agent = SimpleAgent("cancel", SlowModel([]))

    async def consume():
        async for _ in agent.arun_stream("task"):
            pass

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
    assert agent.last_run.status == "cancelled" and not agent._run_active
    agent.llm = Model([LLMToolResponse("recovered", [], "offline")])
    assert await agent.arun("continue") == "recovered"


@pytest.mark.asyncio
async def test_cancelled_sync_tool_can_write_later_but_receipt_stays_unknown():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    writes = []

    def write(value):
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
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receipts = [m for m in agent.get_history() if m.role == "tool"]
        assert len(receipts) == 1
        assert json.loads(receipts[0].content)["execution_status"] == "unknown"
        assert writes == [] and not agent._run_active
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
    assert writes == ["once"]
    assert json.loads(receipts[0].content)["execution_status"] == "unknown"


@pytest.mark.asyncio
async def test_sse_frames_survive_arbitrary_byte_chunks_and_unicode():
    agent = SimpleAgent("sse", Model([LLMToolResponse("中文回答", [], "offline")]))
    wire = "".join([part async for part in stream_to_sse(agent.arun_stream("task"))]).encode("utf-8")
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending, frames = "", []
    # 传输分片故意切开 UTF-8 字符和 SSE 帧；消费者按空行组装完整事件。
    for offset in range(0, len(wire), 7):
        pending += decoder.decode(wire[offset:offset + 7])
        while "\n\n" in pending:
            frame, pending = pending.split("\n\n", 1)
            event_line, data_line = frame.split("\n", 1)
            payload = json.loads(data_line.removeprefix("data: "))
            assert event_line == "event: " + payload["type"]
            frames.append(payload)
    pending += decoder.decode(b"", final=True)
    assert pending == ""
    assert frames[0]["type"] == "agent_start" and frames[-1]["type"] == "agent_finish"
    assert "".join(f["data"]["chunk"] for f in frames if f["type"] == "llm_chunk") == "中文回答"
    assert frames[-1]["data"]["result"] == "中文回答"
