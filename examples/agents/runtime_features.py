"""P09 Web 共用离线模型替身契约模块。

用于在无需依赖外部在线 LLM API 的情况下，严格按照协议预设执行并验证：
1. 模型消息调用的捕获与回放（`invoke`, `invoke_with_tools`）；
2. 异步流式工具调用生成器（`astream_invoke_with_tools`）与连接关闭计数追踪（`closed_streams`）；
3. 异常模拟与错误传播机制。
"""

from copy import deepcopy
from hello_agents.core.llm_response import LLMToolResponse


class ScriptedLLM:
    """离线可编程模型替身桩：按序返回给定的预设响应，并记录调用过程的完整输入上下文。"""

    model = "offline-protocol-fixture"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed_streams = 0

    def invoke(self, messages, **kwargs):
        """同步调用：深拷贝记录输入消息并弹出下一个预设响应（若预设为异常则抛出）。"""
        self.requests.append(
            {"messages": deepcopy(messages), "kwargs": deepcopy(kwargs)}
        )
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def invoke_with_tools(self, messages, tools, **kwargs):
        """带工具定义的模型调用，委托给 invoke 统一处理。"""
        return self.invoke(messages, tools=tools, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        """异步流式工具调用：先产出文本分块（chunk），再产出终态响应对象，并在流结束或中断时递增关闭计数。"""
        try:
            response = self.invoke_with_tools(messages, tools, **kwargs)
            if response.content:
                yield response.content
            yield response
        finally:
            self.closed_streams += 1



def response(text="", calls=(), reason="stop"):
    """便捷构造测试用 LLMToolResponse 对象。"""
    return LLMToolResponse(text, list(calls), ScriptedLLM.model, finish_reason=reason)


