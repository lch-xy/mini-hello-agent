"""状态机与异常恢复边缘场景集成测试（Stateful Failures & Edge Cases）。

系统验证在单轮成功运行中无法覆盖的严苛边缘故障恢复机制：
1. 会话保存的临时文件生命周期与序列化失败的原子回滚；
2. 路径穿越（../）安全性防护：会话名称仅作为文件前缀，严禁传入包含路径分隔符的相对或绝对路径；
3. 损坏快照恢复失败时的状态保护：加载失败时不破坏内存中原有的历史消息与工具读缓存；
4. 工具异常容错性：并发调用中的畸形参数被作为工具 Observation 结果优雅回传模型，而非引发智能体崩溃；
5. 重复工具调用 ID（duplicate tool_call_id）在执行前即被拦截并报错，绝不发生脏写；
6. 工具路由选择（tool_choice）的作用域隔离：仅作用于业务工具执行，不污染摘要生成与规划工具；
7. 异步流中断与超时取消：流被提前中断或超时，保留已完成工具的回执，未完成工具标记为 unknown，智能体标记为 cancelled；
8. deadline 超时上下文精确清理自身取消请求，不产生外部任务取消的副作用污染；
9. 多线程并发保存会话时各自拥有独立的临时文件，杜绝跨线程临时文件冲突。
"""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent
from hello_agents.core.config import Config
from hello_agents.core.components import AgentComponents
from hello_agents.core.session_store import SessionStore
from hello_agents.core.message import Message
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.core.runtime import deadline
from hello_agents.tools import ToolRegistry


def config(**kwargs):
    """构建单元测试用零副作用配置实例。"""
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
    """可编程的离线模型测试替身，按序返回预设的响应列表。"""
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
        result = self.invoke(messages, **kwargs)
        if result.content:
            yield result.content
        yield result


def response(text="", calls=(), reason="stop"):
    """构造离线模型响应对象。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)


def save(store, name="session", history=(), metadata=None):
    """会话保存测试辅助函数。"""
    return store.save({}, list(history), "hash", {}, metadata or {}, name)


def test_session_save_preserves_unrelated_temp_file(tmp_path):
    """验证会话保存过程严格只管理自己的临时文件，绝不误删同目录下已存在的同名 .tmp 文件。"""
    store = SessionStore(str(tmp_path))
    other = tmp_path / "session.json.tmp"
    other.write_text("user data", encoding="utf8")
    save(store)
    # 验证外部既有的临时文件保持完好
    assert other.read_text(encoding="utf8") == "user data"


def test_failed_session_serialization_leaves_no_partial_file(tmp_path):
    """验证会话序列化失败时（如包含无法 JSON 序列化的对象），原文件保持不变且无临时文件残留。"""
    store = SessionStore(str(tmp_path))
    target = Path(save(store))
    original = target.read_bytes()
    # 传入不可序列化的 object()，预期抛出 TypeError
    with pytest.raises(TypeError):
        save(store, metadata={"bad": object()})
    # 验证目标文件字节完全保持原样，且无孤儿 .tmp 文件
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("name", ["../elsewhere", "nested/file", r"nested\file"])
def test_session_name_is_a_name_not_a_path(tmp_path, name):
    """验证路径穿越防御：会话名称中包含路径分隔符时，立即抛出 ValueError 拒绝。"""
    with pytest.raises(ValueError):
        save(SessionStore(str(tmp_path)), name)


def test_bad_restored_message_preserves_current_history(tmp_path):
    """验证快照中消息数据损坏时，加载失败抛出异常，且智能体现有的内存历史消息不被破坏。"""
    store = SessionStore(str(tmp_path))
    agent = SimpleAgent(
        "test",
        ScriptedLLM([]),
        config=config(),
        components=AgentComponents(session_store=store),
    )
    agent.add_message(Message("original", "user"))
    # 人为构造损坏的快照文件（包含非法的 role 字段）
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "history": [
                    {"role": "user", "content": "new"},
                    {"role": "invalid", "content": "broken"},
                ]
            }
        ),
        encoding="utf8",
    )
    with pytest.raises(ValueError):
        agent.load_session(str(path), check_consistency=False)
    # 验证恢复失败后，内存历史仍然是原始消息
    assert [m.content for m in agent.get_history()] == ["original"]


def test_restore_empty_cache_removes_previous_session_cache(tmp_path):
    """验证恢复一个读缓存为空的会话时，能够干净地清空智能体原有的读元数据缓存。"""
    store = SessionStore(str(tmp_path))
    registry = ToolRegistry()
    registry.cache_read_metadata("old", {"version": 1})
    agent = SimpleAgent(
        "test",
        ScriptedLLM([]),
        config=config(),
        tool_registry=registry,
        components=AgentComponents(session_store=store),
    )
    agent.load_session(save(store), check_consistency=False)
    # 验证缓存已被重置清空
    assert registry.read_metadata_cache == {}



def test_parallel_malformed_arguments_are_observations_not_crashes():
    """验证并行调用中畸形参数被作为工具 Observation 结果优雅反馈给模型，而非崩溃。"""
    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    # 第 1 轮模型返回一个非法参数 "[]" 与一个合法参数；第 2 轮模型收到错误后返回纠正响应 "corrected"
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("a", "echo", "[]"),
                    ToolCall("b", "echo", '{"input":"ok"}'),
                ]
            ),
            response("corrected"),
        ]
    )
    agent = ReActAgent("test", llm, config=config(), tool_registry=registry)
    assert agent.run("task") == "corrected"
    # 验证错误信息被组装进输入历史由模型自主消费纠正
    assert "error" in llm.messages[-1][-2]["content"]


def test_duplicate_tool_ids_fail_before_any_execution():
    """验证模型返回重复的 ToolCall ID 时在执行前即被拦截，杜绝脏写。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x), name="write")
    # 模型错误地返回了具有相同 ID "same" 的两个写操作
    llm = ScriptedLLM(
        [
            response(
                calls=[
                    ToolCall("same", "write", '{"input":"a"}'),
                    ToolCall("same", "write", '{"input":"b"}'),
                ]
            ),
            response("done"),
        ]
    )
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)
    # 预期抛出重复 ID 异常
    with pytest.raises(ValueError, match="ID"):
        agent.run("task")
    # 核心保证：在执行任何工具之前已被阻断，writes 列表保持为空
    assert writes == []


def test_tool_choice_reaches_model_and_is_removed_for_summary():
    """验证用户指定的 tool_choice 仅作用于工具调用阶段，在后续摘要阶段自动剥离。"""
    seen = []

    class Model(ScriptedLLM):
        def invoke_with_tools(self, messages, tools, **kwargs):
            seen.append(kwargs["tool_choice"])
            return self.invoke(messages, **kwargs)

        def invoke(self, messages, **kwargs):
            if len(self.messages) > 0:
                # 第二轮总结阶段不应包含 tool_choice 约束
                assert "tool_choice" not in kwargs
            return super().invoke(messages, **kwargs)

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = Model(
        [response(calls=[ToolCall("a", "echo", '{"input":"ok"}')]), response("summary")]
    )
    agent = SimpleAgent(
        "test", llm, config=config(), tool_registry=registry, max_tool_iterations=1
    )
    assert agent.run("task", tool_choice="required") == "summary" and seen == [
        "required"
    ]


def test_nonstream_async_model_override_is_used():
    """验证智能体在异步非流式调用下支持使用 ainvoke 模型接口。"""
    class Model(ScriptedLLM):
        def invoke(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke(self, messages, **kwargs):
            return response("async")

    agent = SimpleAgent("test", Model([]), config=config())
    assert asyncio.run(agent.arun("task")) == "async"


def test_cancellation_keeps_completed_tool_receipt(tmp_path):
    """验证流式调用被提前中断取消时，保留已执行完毕的工具回执并置状态为 cancelled。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x) or "receipt-1", name="write")
    llm = ScriptedLLM([response(calls=[ToolCall("a", "write", '{"input":"once"}')])])
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                # 在收到工具执行完成事件后立即跳出流并关闭
                if event.type == StreamEventType.TOOL_CALL_FINISH:
                    break
        finally:
            await stream.aclose()

    asyncio.run(main())
    # 验证工具已执行完成并留下了回执
    assert writes == ["once"]
    assert any("receipt-1" in m.content for m in agent.get_history())
    # 验证状态正确更新为 cancelled
    assert agent.last_run.status == "cancelled"


def test_auto_save_contains_current_run_statistics(tmp_path):
    """验证触发自动保存的快照中准确记录了当次运行的步数统计。"""
    store = SessionStore(str(tmp_path))
    cfg = config()
    cfg.auto_save_enabled = True
    cfg.auto_save_interval = 1
    agent = SimpleAgent(
        "test",
        ScriptedLLM([response("done")]),
        config=cfg,
        components=AgentComponents(session_store=store),
    )
    agent.run("task")
    saved = store.load(str(tmp_path / "session-auto.json"))
    assert saved["metadata"]["total_steps"] == 1


def assert_closed(history):
    """辅助断言函数：验证历史记录中所有的 ToolCall 均已与对应的 tool role 回执消息严格成对闭合。"""
    pending = set()
    for item in history:
        native = item.metadata["model_message"]
        if native["role"] == "tool":
            assert native["tool_call_id"] in pending
            pending.remove(native["tool_call_id"])
        else:
            assert not pending
            pending = {c["id"] for c in native.get("tool_calls", [])}
    assert not pending


@pytest.mark.parametrize(
    "cls", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
def test_interrupted_batch_can_save_restore_and_continue_without_repeating_write(
    tmp_path, cls
):
    """验证中断的批量工具调用在保存恢复后，能够闭合协议并继续执行，绝不重复调用已执行的写操作。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x) or "receipt-42", name="write")
    calls = [
        response(
            calls=[
                ToolCall("w", "write", '{"input":"one"}'),
                ToolCall("v", "write", '{"input":"two"}'),
            ]
        )
    ]
    if cls is PlanSolveAgent:
        calls.insert(
            0, response(calls=[ToolCall("p", "generate_plan", '{"steps":["write"]}')])
        )
    store = SessionStore(str(tmp_path))
    agent = cls(
        "test",
        ScriptedLLM(calls),
        config=config(),
        tool_registry=registry,
        components=AgentComponents(session_store=store),
    )

    async def interrupt():
        source = agent.arun_stream("write once")
        try:
            async for event in source:
                if event.type == StreamEventType.TOOL_CALL_FINISH:
                    break
        finally:
            await source.aclose()

    asyncio.run(interrupt())
    # ReAct 策略并行执行批处理；其他智能体在第 2 个写操作前中断
    assert writes == (["one", "two"] if cls is ReActAgent else ["one"])
    assert_closed(agent.get_history())

    # 保存中断状态并恢复到新智能体中
    path = agent.save_session("interrupted")
    restored = SimpleAgent(
        "restored",
        ScriptedLLM([response("received")]),
        config=config(),
        tool_registry=registry,
        components=AgentComponents(session_store=store),
    )
    restored.load_session(path, check_consistency=False)
    before = list(writes)
    # 继续执行任务，断言不会重复触发写工具
    assert restored.run("report existing receipts only") == "received"
    assert writes == before
    assert "receipt-42" in json.dumps(restored.llm.messages, ensure_ascii=False)
    assert_closed(restored.get_history())


def test_parallel_timeout_retains_successful_sibling_and_closes_unknown_result():
    """验证并行工具执行超时时，快速成功的兄弟工具结果完整保留，慢工具被标记为 unknown 状态。"""
    registry = ToolRegistry()

    async def operation(value):
        if value == "slow":
            await asyncio.sleep(10)
        return "receipt-fast"

    registry.register_function(operation, name="operation")
    agent = ReActAgent(
        "test",
        ScriptedLLM(
            [
                response(
                    calls=[
                        ToolCall("a", "operation", '{"input":"fast"}'),
                        ToolCall("b", "operation", '{"input":"slow"}'),
                    ]
                )
            ]
        ),
        config=config(tool_async_timeout=0.02),
        tool_registry=registry,
    )
    with pytest.raises(TimeoutError):
        agent.run("task")
    assert agent.last_run.status == "failed"
    assert agent.last_run.tool_calls == 1
    assert_closed(agent.get_history())
    receipts = {
        m.metadata["model_message"]["tool_call_id"]: m.content
        for m in agent.get_history()
        if m.role == "tool"
    }
    # 验证成功的 fast 工具回执保留，慢的 slow 工具被记录为 unknown 闭合协议
    assert "receipt-fast" in receipts["a"]
    assert json.loads(receipts["b"])["execution_status"] == "unknown"


def test_active_stream_rejects_session_restore(tmp_path):
    """验证当智能体当前正处于活跃流式运行中时，调用 load_session 抛出 RuntimeError 拒绝。"""
    store = SessionStore(str(tmp_path))
    agent = SimpleAgent(
        "test",
        ScriptedLLM([response("done")]),
        config=config(),
        components=AgentComponents(session_store=store),
    )
    path = save(store)

    async def main():
        source = agent.arun_stream("task")
        await anext(source)
        try:
            with pytest.raises(RuntimeError, match="运行中"):
                agent.load_session(path)
        finally:
            await source.aclose()

    asyncio.run(main())
    assert not agent._run_active


def test_native_async_tool_model_override():
    """验证支持使用原生 ainvoke_with_tools 异步工具调用模型方法。"""
    class Model(ScriptedLLM):
        def invoke_with_tools(self, *args, **kwargs):
            raise AssertionError("sync entry used")

        async def ainvoke_with_tools(self, messages, tools, **kwargs):
            return response("native")

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    agent = SimpleAgent("test", Model([]), config=config(), tool_registry=registry)
    assert asyncio.run(agent.arun("task")) == "native"


@pytest.mark.parametrize(
    "name", ["../other", r"nested\file", "/absolute", "", "C:other"]
)
def test_session_delete_rejects_paths(tmp_path, name):
    """验证删除会话时防御非法路径穿越参数，确保只能删除合法名称的快照。"""
    with pytest.raises(ValueError):
        SessionStore(str(tmp_path)).delete(name)


def test_plan_agent_forced_business_tool_does_not_override_planning_tool():
    """验证 PlanSolveAgent 在规划阶段使用专用工具，不受用户指定的业务 tool_choice 影响。"""
    seen = []

    class Model(ScriptedLLM):
        def invoke_with_tools(self, messages, tools, **kwargs):
            seen.append(kwargs["tool_choice"])
            return super().invoke_with_tools(messages, tools, **kwargs)

    registry = ToolRegistry()
    registry.register_function(lambda x: x, name="echo")
    llm = Model(
        [
            response(calls=[ToolCall("p", "generate_plan", '{"steps":["do"]}')]),
            response(calls=[ToolCall("e", "echo", '{"input":"ok"}')]),
            response("done"),
        ]
    )
    agent = PlanSolveAgent(
        "test", llm, config=config(), tool_registry=registry, max_tool_iterations=1
    )
    assert agent.run("task", tool_choice="required") == "done"
    # 第 1 阶段规划强制使用 "auto"，第 2 阶段业务执行才应用 "required"
    assert seen == [
        "auto",
        "required",
    ]


def test_concurrent_session_saves_never_share_a_temporary_file(tmp_path):
    """验证多线程并发保存会话时，各自独占创建不同名字的临时文件，杜绝写入损坏。"""
    store = SessionStore(str(tmp_path))

    def write(index):
        return save(store, metadata={"index": index, "payload": str(index) * 10000})

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(write, range(16)))
    # 目标文件名保持唯一
    assert len(set(paths)) == 1
    # 最终落盘内容是某一次完整写入的结果，无半写损坏
    final = store.load(paths[0])["metadata"]
    assert final["payload"] == str(final["index"]) * 10000
    assert not list(tmp_path.glob("*.tmp"))


def test_deadline_clears_only_its_own_cancellation_request():
    """验证 deadline 超时上下文精确清理自身的取消请求，不污染外层协程任务状态。"""
    async def main():
        task = asyncio.current_task()
        before = task.cancelling() if hasattr(task, "cancelling") else None
        with pytest.raises(TimeoutError):
            async with deadline(0.001):
                await asyncio.sleep(10)
        if before is not None:
            assert task.cancelling() == before
        assert not task.cancelled()
        await asyncio.sleep(0)

    asyncio.run(main())


def test_cancel_between_plan_steps_is_not_reported_as_completed():
    """验证 PlanSolveAgent 在多个执行步骤之间被取消时，运行状态标记为 cancelled 而非 completed。"""
    model = ScriptedLLM(
        [
            response(calls=[ToolCall("p", "generate_plan", '{"steps":["one","two"]}')]),
            response("first"),
        ]
    )
    agent = PlanSolveAgent("test", model, config=config())

    async def main():
        source = agent.arun_stream("task")
        try:
            async for event in source:
                # 在第 1 个步骤完成后提前中断
                if (
                    event.type == StreamEventType.STEP_FINISH
                    and event.data.get("phase") == "execution"
                ):
                    break
        finally:
            await source.aclose()

    asyncio.run(main())
    assert agent.last_run.status == "cancelled"
    assert len(model.messages) == 2

