"""P03 的完整协议场景：真实函数副作用、消息配对和模型调用统计。

本测试套件验证 P03 纵向贯穿场景（Vertical Slice）：
1. 正常工具回执驱动第二轮模型调用，产生真实副作用与累加 Token；
2. 损坏的 JSON/非法对象工具参数被捕获并转化为 observation 回执，允许大模型在后续轮次自纠错。
"""

import asyncio
import json
from copy import deepcopy

import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


class Model:
    """按序返回预设响应的测试 Mock 模型。"""
    model = "offline"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []  # 记录收到的全部请求历史

    def invoke(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return next(self.responses)

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)


def execute(agent, mode):
    """统一执行辅助函数，支持同步 (run) 与异步 (arun) 入口。"""
    if mode == "run":
        return agent.run("完成记录")
    return asyncio.run(agent.arun("完成记录"))


@pytest.mark.parametrize("mode", ["run", "arun"])
def test_tool_receipt_drives_second_model_request(tmp_path, monkeypatch, mode):
    """验证：工具回执驱动第二次模型调用，且副作用与消息序列完全符合契约。"""
    monkeypatch.chdir(tmp_path)
    writes = []
    # 注册一个具有真实副作用的本地记录函数
    registry = ToolRegistry()
    registry.register_function(lambda value: writes.append(value) or "已记录", name="record")
    
    # 模拟两次模型响应：第一次请求工具 record，第二次给出最终完成答复
    model = Model([
        LLMToolResponse(None, [ToolCall("c1", "record", '{"input":"evidence"}')],
                        "offline", usage={"total_tokens": 3}),
        LLMToolResponse("完成", [], "offline", usage={"total_tokens": 3}),
    ])
    agent = SimpleAgent("test", model, tool_registry=registry)
    
    # 断言 1：返回文本与副作用正确
    assert execute(agent, mode) == "完成"
    assert writes == ["evidence"]
    
    # 断言 2：调用统计（2次模型，1次工具，Token 累加为 6，状态为 completed）
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 1
    assert agent.last_run.status == "completed" and agent.last_run.usage["total_tokens"] == 6
    
    # 断言 3：第二次模型请求中准确包含了 role="tool" 且 tool_call_id="c1" 的回执
    assert len(model.requests[0]) == 1
    assert [item["role"] for item in model.requests[1]] == ["user", "assistant", "tool"]
    assert model.requests[1][-1]["tool_call_id"] == "c1"
    receipt = json.loads(model.requests[1][-1]["content"])
    assert receipt["status"] == "success" and receipt["data"]["output"] == "已记录"
    
    # 断言 4：长期历史形成原子完整的 4 条消息闭环
    assert [item.role for item in agent.get_history()] == ["user", "assistant", "tool", "assistant"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["run", "arun"])
def test_bad_tool_arguments_become_observations_before_continuing(mode):
    """验证：坏工具参数（非法 JSON 或非对象）被转化为错误观察，不崩溃并允许流程继续。"""
    seen = []
    registry = ToolRegistry()
    registry.register_function(lambda value: seen.append(value) or value, name="echo")
    
    # 模拟模型一次性返回了 3 个工具调用：前两个是坏参数（不完整 JSON、数组参数），第三个是合法参数
    model = Model([
        LLMToolResponse(None, [
            ToolCall("bad-json", "echo", "{"),
            ToolCall("bad-object", "echo", "[]"),
            ToolCall("good", "echo", '{"input":"ok"}'),
        ], "offline"),
        LLMToolResponse("corrected", [], "offline"),
    ])
    agent = SimpleAgent("test", model, tool_registry=registry)
    
    # 执行并断言：坏参数被优雅处理，合法工具正常执行，最终输出 corrected
    assert execute(agent, mode) == "corrected"
    assert seen == ["ok"]
    
    # 核验在第二次请求中，前两个回执的状态为 error，第三个为 success
    receipts = [message for message in model.requests[-1] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in receipts] == ["bad-json", "bad-object", "good"]
    assert [json.loads(message["content"])["status"] for message in receipts] == ["error", "error", "success"]
