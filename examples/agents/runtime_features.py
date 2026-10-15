"""P09 Web 共用离线模型契约；完整 RAG/Memory 组合示例在后续阶段恢复。"""

from copy import deepcopy
from hello_agents.core.llm_response import LLMToolResponse


class ScriptedLLM:
    """明确的离线测试替身：按序返回给定响应，同时捕获实际模型输入。"""

    model = "offline-protocol-fixture"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed_streams = 0

    def invoke(self, messages, **kwargs):
        self.requests.append(
            {"messages": deepcopy(messages), "kwargs": deepcopy(kwargs)}
        )
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, tools=tools, **kwargs)

    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        try:
            response = self.invoke_with_tools(messages, tools, **kwargs)
            if response.content:
                yield response.content
            yield response
        finally:
            self.closed_streams += 1



def response(text="", calls=(), reason="stop"):
    return LLMToolResponse(text, list(calls), ScriptedLLM.model, finish_reason=reason)

