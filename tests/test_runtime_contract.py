"""统一运行时协议契约测试套件

核心契约目标：
所有入口（同步 run、异步 arun、流式 stream_run、异步事件流 arun_stream）必须遵循完全一致的协议行为，
包括工具调用成对流转（tool_calls 与 tool 回执配对）、错误捕获与状态标记、流取消与资源关闭路径、
子代理工厂隔离、工具截断与并发预算控制。
"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent
from hello_agents.core.config import Config
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.tools import ToolRegistry


def config(**kwargs):
    """构建所有高级特性默认关闭的基础测试 Config 对象"""
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
    """测试用预置脚本化 LLM，按顺序返回指定的响应或抛出指定异常"""
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
    """辅助函数：构造测试用的 LLMToolResponse 对象"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def execute(agent, mode):
    """统一调度辅助函数：根据模式参数分别通过 run/arun/stream_run/arun_stream 触发运行"""
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


@pytest.mark.parametrize(
    "cls", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_all_entry_points_really_dispatch_tools_and_keep_pairs(cls, mode):
    """验证四大 Agent 在全部 4 个运行入口下均能真正分发工具调用，并保持消息成对记录"""
    writes = []
    registry = ToolRegistry()

    async def record(value):
        writes.append(value)
        return value

    registry.register_function(record, name="record")
    calls = [
        response(
            calls=[ToolCall("c1", "record", '{"input":"evidence"}')],
            reason="tool_calls",
        ),
        response("完成"),
    ]
    if cls is PlanSolveAgent:
        calls.insert(
            0,
            response(
                calls=[ToolCall("plan1", "generate_plan", '{"steps":["执行记录"]}')]
            ),
        )
    if cls is ReflectionAgent:
        calls.append(response("无需改进"))
    llm = ScriptedLLM(calls)
    agent = cls("test", llm, config=config(), tool_registry=registry)
    answer = execute(agent, mode)
    assert "完成" in answer
    assert writes == ["evidence"]
    assert agent.last_run.status == "completed"
    assert any(
        m.metadata["model_message"].get("tool_call_id") == "c1"
        for m in agent.get_history()
    )


@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_errors_are_not_empty_success(mode):
    """验证模型异常在所有入口均被真实抛出，运行状态标记为 failed，不可误作成功空返回"""
    agent = SimpleAgent(
        "test", ScriptedLLM([RuntimeError("model unavailable")]), config=config()
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        execute(agent, mode)
    assert agent.last_run.status == "failed"
    assert not agent._run_active


def test_output_limit_never_executes_incomplete_tool_request():
    """验证模型输出超限（finish_reason='length'）时，绝不执行截断的不完整工具参数"""
    calls = []
    registry = ToolRegistry()
    registry.register_function(lambda value: calls.append(value), name="write")
    agent = SimpleAgent(
        "test",
        ScriptedLLM(
            [response(calls=[ToolCall("c", "write", '{"input":')], reason="length")]
        ),
        config=config(),
        tool_registry=registry,
    )
    assert agent.run("write") == ""
    assert agent.last_run.status == "output_limit" and calls == []


def test_history_committed_and_trace_closed_when_consumer_stops_at_finish(tmp_path):
    """验证当消费端在 AGENT_FINISH 事件处提前退出时，历史记录已落盘且 trace 文件正常关闭"""
    cfg = config()
    cfg.trace_enabled = True
    cfg.trace_dir = str(tmp_path)
    agent = SimpleAgent("test", ScriptedLLM([response("answer")]), config=cfg)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                if event.type == StreamEventType.AGENT_FINISH:
                    assert len(agent.get_history()) == 2
                    break
        finally:
            await stream.aclose()
        assert not agent._run_active and agent.trace_logger.jsonl_file.closed

    asyncio.run(main())


def test_react_finish_returned_by_text_stream():
    """验证 ReActAgent 通过 stream_run 能正确将 Finish 工具返回的最终答案完整输出"""
    agent = ReActAgent(
        "test",
        ScriptedLLM([response(calls=[ToolCall("f", "Finish", '{"answer":"终点"}')])]),
        config=config(),
    )
    assert "".join(agent.stream_run("task")) == "终点"


def test_planner_rejects_bad_plan_without_executing():
    """验证 PlanSolveAgent 收到非法规划格式时拒绝执行并标记 failed"""
    agent = PlanSolveAgent(
        "test",
        ScriptedLLM(
            [response(calls=[ToolCall("p", "generate_plan", '{"steps":"bad"}')])]
        ),
        config=config(),
    )
    with pytest.raises(ValueError, match="步骤"):
        agent.run("task")
    assert agent.last_run.status == "failed"


def test_response_positional_compatibility():
    """验证 LLMResponse 类的位置参数兼容性及 reasoning_content 属性"""
    assert LLMResponse("x", "m", {}, 1, "reasoning").reasoning_content == "reasoning"


def test_factory_simple_keeps_registry():
    """验证工厂方法创建 simple agent 时保留原始 tool_registry 引用"""
    from hello_agents.agents.factory import create_agent

    registry = ToolRegistry()
    assert (
        create_agent(
            "simple", "test", ScriptedLLM([]), registry, config()
        ).tool_registry
        is registry
    )


def test_stream_adapter_assembles_fragments_and_usage_footer(monkeypatch):
    """测试流式适配器正确拼接分散的 tool_calls JSON 片段与 Token 使用量页脚"""
    from hello_agents.core.llm_adapters import OpenAIAdapter

    calls = []

    def delta(text=None, fragments=None, reason=None):
        return NS(
            usage=None,
            choices=[
                NS(finish_reason=reason, delta=NS(content=text, tool_calls=fragments))
            ],
        )

    class Stream:
        closed = False

        def __aiter__(self):
            async def gen():
                # 分段产出工具参数 JSON 片段
                yield delta(
                    fragments=[
                        NS(
                            index=0,
                            id="c",
                            function=NS(name="write", arguments='{"input":'),
                        )
                    ]
                )
                yield delta(
                    fragments=[
                        NS(index=0, id=None, function=NS(name=None, arguments='"ok"}'))
                    ],
                    reason="tool_calls",
                )
                yield NS(choices=[], usage={"total_tokens": 17})

            return gen()

        async def close(self):
            self.closed = True

    stream = Stream()

    class Client:
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

    output = asyncio.run(main())[-1]
    # 验证拼接后的工具参数为完整合法的 JSON
    assert json.loads(output.tool_calls[0].arguments) == {"input": "ok"}
    assert output.finish_reason == "tool_calls" and output.usage["total_tokens"] == 17
    assert stream.closed and client.closed
    assert "tools" not in calls[0] and "tool_choice" not in calls[0]


@pytest.mark.parametrize("direction", ["head", "tail", "head_tail"])
def test_truncation_limits_utf8_bytes_and_preserves_direction(tmp_path, direction):
    """测试工具输出截断器严格受控于 UTF-8 字节预算，并符合指定截断方向"""
    from hello_agents.context.truncator import ObservationTruncator

    tool = ObservationTruncator(
        max_bytes=40,
        max_lines=3,
        truncate_direction=direction,
        output_dir=str(tmp_path),
    )
    value = "开头" + "长" * 100 + "结尾"
    result = tool.truncate("source", value)
    assert len(result["preview"].encode("utf8")) <= 40
    assert (
        json.loads(
            __import__("pathlib")
            .Path(result["full_output_path"])
            .read_text(encoding="utf8")
        )["output"]
        == value
    )
    if direction != "tail":
        assert result["preview"].startswith("开头")
    if direction != "head":
        assert result["preview"].endswith("结尾")


def test_subagent_factory_keeps_parent_registry_and_applies_budget(tmp_path):
    """测试子代理工厂保持父注册表安全独立，并应用 subagent_max_steps 迭代预算"""
    from hello_agents.agents.factory import default_subagent_factory

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="read_data")
    cfg = config()
    cfg.subagent_max_steps = 7
    child = default_subagent_factory("simple", ScriptedLLM([]), registry, cfg)
    assert child.tool_registry is not registry
    assert child.max_tool_iterations == 7 and not child.config.subagent_enabled
    # 子代理注销工具不影响父级注册表
    child.tool_registry.unregister("read_data")
    assert registry.list_tools() == ["read_data"]


def test_subagent_filter_also_removes_functions_without_touching_parent():
    """测试子代理工具过滤器对普通注册函数也能安全屏蔽，且父注册表不受影响"""
    from hello_agents.tools.tool_filter import ReadOnlyFilter

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="Write")
    llm = ScriptedLLM([response("done")])
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)
    assert agent.run_as_subagent("task", ReadOnlyFilter())["success"]
    assert registry.list_tools() == ["Write"] and agent.tool_registry is registry


def test_react_parallel_batch_respects_concurrency_limit():
    """测试 ReActAgent 并发执行工具时受 max_concurrent_tools 并发数限制"""
    active = peak = 0

    async def slow(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return value

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    llm = ScriptedLLM(
        [
            response(
                calls=[ToolCall(str(i), "slow", '{"input":"x"}') for i in range(4)]
            ),
            response("done"),
        ]
    )
    agent = ReActAgent(
        "test", llm, config=config(max_concurrent_tools=2), tool_registry=registry
    )
    assert agent.run("task") == "done" and peak == 2


def test_native_async_tool_timeout_is_reported():
    """测试异步工具执行超时时正确抛出 TimeoutError 并设置 failed 状态"""
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
    """测试 LLM 响应超时抛出 TimeoutError，取消流并释放 Agent 运行锁"""
    class SlowLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            await asyncio.sleep(1)
            yield response("late")

    agent = SimpleAgent("test", SlowLLM([]), config=config(llm_async_timeout=0.01))
    with pytest.raises(TimeoutError):
        execute(agent, "arun_stream")
    assert agent.last_run.status == "failed" and not agent._run_active


def test_empty_model_response_is_failure():
    """测试模型返回空白内容时抛出 EmptyModelResponse 且状态标记为 failed"""
    from hello_agents.core.runtime import EmptyModelResponse

    agent = SimpleAgent("test", ScriptedLLM([response()]), config=config())
    with pytest.raises(EmptyModelResponse):
        agent.run("task")
    assert agent.last_run.status == "failed"


def test_iteration_limit_is_distinct_from_completion():
    """测试工具迭代步数达上限时状态明确标记为 max_iterations 而非 completed"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    agent = SimpleAgent(
        "test",
        ScriptedLLM(
            [
                response(calls=[ToolCall("c", "echo", '{"input":"x"}')]),
                response("尚未完成"),
            ]
        ),
        config=config(),
        tool_registry=registry,
        max_tool_iterations=1,
    )
    assert agent.run("task") == "尚未完成"
    assert agent.last_run.status == "max_iterations"


def test_next_turn_replays_native_tool_pair():
    """测试后续轮次对话中原生工具调用与其回执对（tool_calls 与 tool）完整重放"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = ScriptedLLM(
        [
            response(calls=[ToolCall("c", "echo", '{"input":"x"}')]),
            response("done"),
            response("again"),
        ]
    )
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)
    agent.run("task")
    agent.run("next")
    assert llm.messages[-1][1]["tool_calls"][0]["id"] == "c"
    assert llm.messages[-1][2]["tool_call_id"] == "c"


def test_parallel_timeout_does_not_start_queued_write_after_failure():
    """测试并发执行中某工具超时失败后，队列中尚未启动的后续工具严禁被错误触发"""
    writes = []

    async def slow(value):
        await asyncio.sleep(1)

    async def write(value):
        writes.append(value)

    registry = ToolRegistry()
    registry.register_function(slow, name="slow")
    registry.register_function(write, name="write")
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("s", "slow", '{"input":"x"}'),
                    ToolCall("w", "write", '{"input":"x"}'),
                ]
            )
        ]
    )
    agent = ReActAgent(
        "test",
        llm,
        config=config(max_concurrent_tools=1, tool_async_timeout=0.01),
        tool_registry=registry,
    )

    async def main():
        with pytest.raises(TimeoutError):
            await agent.arun("task")
        await asyncio.sleep(0.03)
        assert writes == []

    asyncio.run(main())


def test_stream_close_reaches_provider_before_return_and_ignores_consumer_delay():
    """测试消费者关闭流时立即通知 Provider 完成底层资源清理，状态标记为 cancelled"""
    closed = []

    class SlowConsumerLLM(ScriptedLLM):
        async def astream_invoke_with_tools(self, *args, **kwargs):
            try:
                yield "part"
                yield response("part")
            finally:
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
                assert closed == [True]
                assert agent.last_run.status == "cancelled" and not agent._run_active
                break

    asyncio.run(main())


def test_bad_subagent_filter_does_not_destroy_history():
    """测试当传入异常的工具过滤器时，异常被抛出且父级历史消息完好无损"""
    from hello_agents.core.message import Message

    class BadFilter:
        def filter(self, names):
            raise ValueError("bad filter")

    agent = SimpleAgent(
        "test", ScriptedLLM([]), config=config(), tool_registry=ToolRegistry()
    )
    agent.add_message(Message("before", "user"))
    with pytest.raises(ValueError, match="bad filter"):
        agent.run_as_subagent("task", BadFilter())
    assert [m.content for m in agent.get_history()] == ["before"]


def test_trace_statistics_and_legacy_tool_event_fields(tmp_path):
    """测试 Trace 日志统计汇总（模型调用数、各工具调用频次）及流事件字段兼容性"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = ScriptedLLM(
        [response(calls=[ToolCall("c", "echo", '{"input":"x"}')]), response("done")]
    )
    cfg = config()
    cfg.trace_enabled = True
    cfg.trace_dir = str(tmp_path)
    agent = SimpleAgent("test", llm, config=cfg, tool_registry=registry)

    async def main():
        return [e async for e in agent.arun_stream("task")]

    events = asyncio.run(main())
    stats = agent.trace_logger._compute_stats()
    assert stats["model_calls"] == 2 and stats["tool_calls"] == {"echo": 1}
    call = next(e for e in events if e.type == StreamEventType.TOOL_CALL_START)
    assert call.data["tool_name"] == "echo" and call.data["tool_call_id"] == "c"
