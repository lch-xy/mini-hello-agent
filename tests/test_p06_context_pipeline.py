"""P06 上下文流水线（Context Pipeline）装配与诊断契约测试套件。

验证 ContextBuilder 在实际请求流水线中的集成行为：
1. 中文证据检索匹配、角色编排与诊断跟踪（选中来源元数据、未选中原因）；
2. 预算防守机制：优先保障核心任务、工具 Schema 与必须项（required=True），非必须项按预算排除（reason='budget'）；
3. 四大执行入口（run / arun / stream_run / arun_stream）对外部 context_packets 与历史摘要的透明接收与格式转换；
4. 严格错误防守：未配置 context_builder 时禁止静默吞没 context_packets 传参。
"""
import asyncio
from copy import deepcopy
import json
import pytest
from hello_agents import Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBuilder, ContextConfig, ContextPacket, ContextBudgetExceeded, ContextBuildResult
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall


class CapturingLLM:
    """测试替身：捕获组装好的消息序列与工具 Schema，不发起真实网络调用。"""

    model = "test-capture"

    def __init__(self, responses=()):
        self.messages = []
        self.schemas = []
        self.responses = iter(responses)

    def invoke(self, messages, **kwargs):
        # 契约断言：context_packets 属于上下文层内部参数，绝不应泄露至底层模型调用 kwargs 中
        assert "context_packets" not in kwargs
        self.messages.append(deepcopy(messages))
        return LLMResponse("完成", self.model)

    def invoke_with_tools(self, messages, tools, **kwargs):
        self.messages.append(deepcopy(messages))
        self.schemas.append(deepcopy(tools))
        return next(self.responses)

    def stream_invoke(self, messages, **kwargs):
        self.invoke(messages, **kwargs)
        yield "完成"

    async def astream_invoke(self, messages, **kwargs):
        self.invoke(messages, **kwargs)
        yield "完成"



def config():
    """构造禁用其他扩展模块的轻量测试配置。"""
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )



def test_chinese_selection_source_roles_and_diagnostics():
    """验证：中文相关性筛选、资料角色插入位置（最新 user 消息前）与诊断元数据。"""
    builder = ContextBuilder(ContextConfig(max_tokens=1500))
    evidence = ContextPacket(
        "杭州旅行每天步行不超过五公里", metadata={"id": "source-1", "source": "user:1"}
    )
    irrelevant = ContextPacket("zzzz unrelated", metadata={"id": "other"})
    base = [
        {"role": "system", "content": "遵守任务约束"},
        {"role": "user", "content": "安排杭州旅行"},
    ]
    result = builder.build_messages(base, (p for p in (evidence, irrelevant)))
    
    # 断言 1：首尾系统指令与最新用户输入位置保持不变
    assert result.messages[0] == base[0] and result.messages[-1] == base[-1]
    # 断言 2：注入的参考资料使用 user 角色且包含来源标识
    assert result.messages[1]["role"] == "user"
    assert "source-1" in result.messages[1]["content"]
    # 断言 3：诊断信息准确记录选中项与被排除项原因
    assert result.diagnostics["selected"][0]["id"] == "source-1"
    assert result.diagnostics["excluded"][0]["reason"] == "relevance"
    assert result.diagnostics["is_estimate"] is True
    assert len(base) == 2



def test_budget_protects_task_schema_and_required_packets():
    """验证：预算优先级策略：超大工具 Schema 与必须项超限抛出异常，普通项平滑丢弃。"""
    base = [{"role": "system", "content": "规则"}, {"role": "user", "content": "旅行"}]
    builder = ContextBuilder(ContextConfig(max_tokens=250))
    
    # 校验 1：当工具定义本身超出预算时，必须抛出 ContextBudgetExceeded
    with pytest.raises(ContextBudgetExceeded) as exc:
        builder.build_messages(base, tool_schemas=[{"description": "x " * 500}])
    assert exc.value.diagnostics["tool_schema_tokens"] > 0
    
    # 校验 2：当必选资料包 (required=True) 超出预算时抛出异常
    with pytest.raises(ContextBudgetExceeded):
        builder.build_messages(
            base, [ContextPacket("x " * 500, metadata={"required": True})]
        )
        
    # 校验 3：普通资料包超出预算时不抛错，而是平滑被排除（reason='budget'）
    result = builder.build_messages(base, [ContextPacket("旅行" * 1000)])
    assert result.messages == base
    assert result.diagnostics["excluded"][0]["reason"] == "budget"



@pytest.mark.parametrize("mode", ["run", "arun", "stream_run", "arun_stream"])
def test_all_simple_agent_paths_receive_context_and_summary(mode):
    """验证：SimpleAgent 的 4 类入口均能无缝接收 context_packets 并将 summary 转为模型可用格式。"""
    llm = CapturingLLM()
    agent = SimpleAgent("test", llm, config=config(), context_builder=ContextBuilder())
    agent.add_message(Message("历史决定：住在西湖附近", "summary"))
    kwargs = {"context_packets": (p for p in [ContextPacket("杭州旅行预算1500元")])}
    
    if mode == "run":
        agent.run("安排杭州旅行", **kwargs)
    elif mode == "arun":
        asyncio.run(agent.arun("安排杭州旅行", **kwargs))
    elif mode == "stream_run":
        list(agent.stream_run("安排杭州旅行", **kwargs))
    else:
        async def consume():
            return [
                event async for event in agent.arun_stream("安排杭州旅行", **kwargs)
            ]
        asyncio.run(consume())

    sent = json.dumps(llm.messages[0], ensure_ascii=False)
    # 断言 1：参考资料与历史摘要均已编码注入最终请求
    assert "1500" in sent and "历史摘要" in sent
    # 断言 2：summary 虚拟角色已转译为标准大模型角色（不向 LLM 发送原生 summary 角色）
    assert all(m["role"] != "summary" for m in llm.messages[0])
    # 断言 3：上下文诊断信息成功沉淀在 Agent 属性中
    assert agent.last_context_diagnostics["estimated_tokens"] > 0



def test_no_silent_context_ignored_and_default_unchanged():
    """验证：未启用 context_builder 时传入资料包显式报错，杜绝隐式静默丢弃。"""
    llm = CapturingLLM()
    agent = SimpleAgent("test", llm, config=config())
    # 未配置 context_builder 时报错
    with pytest.raises(ValueError):
        agent.run("hello", context_packets=[ContextPacket("world")])
    # 常规调用无损向后兼容
    agent.run("hello")
    assert llm.messages == [[{"role": "user", "content": "hello"}]]

