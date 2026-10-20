"""P04 异常恢复与边界生命周期测试。

验证流式执行中的异常边缘状态恢复与上下文清理：
1. 流式迭代在工具执行完成（TOOL_CALL_FINISH）后被取消时，已产生的工具执行回执必须被完整保留在历史中，运行状态标为 cancelled；
2. deadline 超时上下文管理器契约：超时引发 TimeoutError 并仅清除自身的取消请求标记，不污染调用方外层 Task 的取消状态。
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
        result = self.invoke(messages, **kwargs)
        if result.content:
            yield result.content
        yield result



def response(text="", calls=(), reason="stop"):
    """快速构建 LLMToolResponse 对象的工厂函数。"""
    return LLMToolResponse(text, list(calls), "offline", finish_reason=reason)



def test_cancellation_keeps_completed_tool_receipt(tmp_path):
    """验证：流式消费在工具完成后取消时，已完成的工具回执保留在历史中。"""
    writes = []
    registry = ToolRegistry()
    registry.register_function(lambda x: writes.append(x) or "receipt-1", name="write")
    llm = ScriptedLLM([response(calls=[ToolCall("a", "write", '{"input":"once"}')])])
    agent = SimpleAgent("test", llm, config=config(), tool_registry=registry)

    async def main():
        stream = agent.arun_stream("task")
        try:
            async for event in stream:
                # 收到工具执行完成事件后立即跳出并关闭流（触发取消）
                if event.type == StreamEventType.TOOL_CALL_FINISH:
                    break
        finally:
            await stream.aclose()

    asyncio.run(main())
    # 断言 1：工具副作用已执行
    assert writes == ["once"]
    # 断言 2：历史消息中保留了工具返回的 receipt-1 回执
    assert any("receipt-1" in m.content for m in agent.get_history())
    # 断言 3：运行状态被正确记为 cancelled
    assert agent.last_run.status == "cancelled"



def test_deadline_clears_only_its_own_cancellation_request():
    """验证：deadline 超时退出时只清除自身引发的取消请求，外层任务不处于 cancelled 状态。"""
    async def main():
        task = asyncio.current_task()
        before = task.cancelling() if hasattr(task, "cancelling") else None
        
        # 触发 1 毫秒超时
        with pytest.raises(TimeoutError):
            async with deadline(0.001):
                await asyncio.sleep(10)
                
        # 断言：若支持 cancelling 计数，验证计数已恢复；且任务本身未被标记为 cancelled
        if before is not None:
            assert task.cancelling() == before
        assert not task.cancelled()
        await asyncio.sleep(0)

    asyncio.run(main())

