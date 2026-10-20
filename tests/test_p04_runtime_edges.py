"""P04 运行时边缘与契约完整性测试套件。

验证流式运行时、适配器碎片拼接与超时边界保护：
1. 流式消费者在 AGENT_FINISH 提早停止并 aclose 时，历史记录被正常提交，Trace 跟踪日志文件安全关闭；
2. LLMResponse 数据类位置参数与思维链字段的向前兼容性；
3. OpenAI 流式工具碎片拼装：多段参数片段（arguments 字符串碎片）按 index 聚合，流式尾部 usage 正常解析，流与客户端双重释放；
4. 异步工具超时（tool_async_timeout）抛出 TimeoutError，状态标记为 failed，运行锁被安全释放；
5. 模型请求超时（llm_async_timeout）中止流并释放 Agent 状态；
6. 消费端延迟关闭（aclose）信号先达供应商生成器内部清理逻辑，保证连接安全回收。
"""
import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from hello_agents import SimpleAgent, Config, ToolRegistry, AgentComponents
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.core.runtime import deadline
from hello_agents.core.llm_adapters import BaseLLMAdapter


def config(**kwargs):
    """创建禁用非核心扩展功能的纯净测试配置。"""
    return Config(
        **dict(
            trace_enabled=False,
            session_enabled=False,
            skills_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
            **kwargs,
        )
    )



class ScriptedLLM:
    """按序返回预设响应的测试脚本化 LLM。"""
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        response = self.invoke(messages, **kwargs)
        if response.content:
            yield response.content
        yield response



def response(text="", calls=(), reason="stop"):
    """构建统一格式的 LLMToolResponse 辅助函数。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def execute(agent, mode):
    """多模式统一执行辅助函数，支持同步、流式与异步执行。"""
    if mode == "run":
        return agent.run("完成任务")
    if mode == "stream_run":
        return "".join(agent.stream_run("完成任务"))

    async def main():
        if mode == "arun":
            return await agent.arun("完成任务")
        events = [e async for e in agent.arun_stream("完成任务")]
        return events[-1].data["result"]

    return asyncio.run(main())



def test_history_committed_and_trace_closed_when_consumer_stops_at_finish(tmp_path):
    """验证：流式消费在 AGENT_FINISH 退出并关闭时，历史已提交且 Trace 日志被刷盘关闭。"""
    cfg = config()
    cfg.trace_enabled = True
    cfg.trace_dir = str(tmp_path)
    agent = SimpleAgent("test", ScriptedLLM([response("answer")]), config=cfg)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                # 遇到完成事件即停止消费
                if event.type == StreamEventType.AGENT_FINISH:
                    assert len(agent.get_history()) == 2
                    break
        finally:
            await stream.aclose()
        # 断言：Agent 处于非激活状态，Trace 日志文件句柄已关闭
        assert not agent._run_active and agent.trace_logger.jsonl_file.closed

    asyncio.run(main())



def test_response_positional_compatibility():
    """验证：LLMResponse 允许通过 5 个位置参数兼容传入 reasoning_content。"""
    assert LLMResponse("x", "m", {}, 1, "reasoning").reasoning_content == "reasoning"



def test_stream_adapter_assembles_fragments_and_usage_footer(monkeypatch):
    """验证：OpenAI 流式适配器正确按 index 拼装工具调用参数碎片，并提取末尾 usage。"""
    from hello_agents.core.llm_adapters import OpenAIAdapter

    calls = []

    def delta(text=None, fragments=None, reason=None):
        """构造模拟 OpenAI chunk 的命名空间辅助函数。"""
        return NS(
            usage=None,
            choices=[
                NS(finish_reason=reason, delta=NS(content=text, tool_calls=fragments))
            ],
        )

    class Stream:
        """模拟将工具调用 JSON 切割为碎片分发的大模型流式传输。"""
        closed = False

        def __aiter__(self):
            async def gen():
                # 碎片 1：发出函数名及部分参数 '{"input":'
                yield delta(
                    fragments=[
                        NS(
                            index=0,
                            id="c",
                            function=NS(name="write", arguments='{"input":'),
                        )
                    ]
                )
                # 碎片 2：发出剩余参数 '"ok"}'
                yield delta(
                    fragments=[
                        NS(index=0, id=None, function=NS(name=None, arguments='"ok"}'))
                    ],
                    reason="tool_calls",
                )
                # 碎片 3：尾部 usage 统计 chunk
                yield NS(choices=[], usage={"total_tokens": 17})

            return gen()

        async def close(self):
            self.closed = True

    stream = Stream()

    class Client:
        """模拟异步客户端。"""
        closed = False

        async def create(self, **kwargs):
            calls.append(kwargs)
            return stream

        async def close(self):
            self.closed = True

    client = Client()
    client.chat = NS(completions=client)
    adapter = OpenAIAdapter("fake", None, 30, "m")
    monkeypatch.setattr(adapter, "create_async_client", lambda: client)

    async def main():
        return [
            x
            async for x in adapter.astream_invoke_with_tools([], [], tool_choice="auto")
        ]

    # 获取终态产出的 LLMToolResponse 对象
    output = asyncio.run(main())[-1]
    
    # 断言 1：拼接后的 JSON 参数完整无误
    assert json.loads(output.tool_calls[0].arguments) == {"input": "ok"}
    # 断言 2：finish_reason 与 total_tokens 统计准确
    assert output.finish_reason == "tool_calls" and output.usage["total_tokens"] == 17
    # 断言 3：流与客户端均已完成双重释放
    assert stream.closed and client.closed
    # 断言 4：工具列表为空时 tool_choice 参数被自动剔除
    assert "tools" not in calls[0] and "tool_choice" not in calls[0]



def test_native_async_tool_timeout_is_reported():
    """验证：原生异步工具执行超时时抛出 TimeoutError，状态标记为 failed 并释放运行锁。"""
    async def slow(value):
        await asyncio.sleep(1)

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    agent = SimpleAgent(
        "test",
        ScriptedLLM([response(calls=[ToolCall("c", "slow", '{"input":"x"}')])]),
        config=config(tool_async_timeout=0.01),
        tool_registry=registry,
    )
    with pytest.raises(TimeoutError):
        agent.run("task")
    assert agent.last_run.status == "failed" and not agent._run_active



def test_model_timeout_cancels_stream_and_releases_agent():
    """验证：模型请求超时时中止流式执行，抛出 TimeoutError 并重置 Agent 激活状态。"""
    class SlowLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            await asyncio.sleep(1)
            yield response("late")

    agent = SimpleAgent("test", SlowLLM([]), config=config(llm_async_timeout=0.01))
    with pytest.raises(TimeoutError):
        execute(agent, "arun_stream")
    assert agent.last_run.status == "failed" and not agent._run_active



def test_stream_close_reaches_provider_before_return_and_ignores_consumer_delay():
    """验证：消费端 aclose 时，供应商底层流式生成器的清理优先于外部返回完成。"""
    closed = []

    class SlowConsumerLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            try:
                yield "part"
                yield response("part")
            finally:
                # 记录底层生成器被关闭的时机
                closed.append(True)

    async def main():
        agent = SimpleAgent(
            "test", SlowConsumerLLM([]), config=config(llm_async_timeout=0.01)
        )
        stream = agent.arun_stream("task")
        async for event in stream:
            if event.type == StreamEventType.LLM_CHUNK:
                await asyncio.sleep(0.03)
                await stream.aclose()
                # 断言：调用 aclose 时，服务商生成器的 finally 逻辑已执行完毕
                assert closed == [True]
                assert agent.last_run.status == "cancelled" and not agent._run_active
                break

    asyncio.run(main())

