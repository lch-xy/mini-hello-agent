"""Web 包装层的请求生命周期、模型资源释放与异常终态测试。

验证两项核心 Web 运行保证：
1. 资源回收确定性：无论请求是成功完成还是由于上游模型异常失败，服务端均会在 finally 阶段调用模型的 aclose() 回收网络资源，且重置智能体的 _run_active 标记；
2. 异常脱敏安全保护：当底层模型抛出包含内部细节的异常时，对外仅输出统一脱敏的错误提示，严禁向客户端泄露内部异常信息。
"""

import json

import pytest
from fastapi.testclient import TestClient

from hello_agents import SimpleAgent
from hello_agents.core.llm_response import LLMToolResponse
from examples.web.fastapi_sse_server import create_app


@pytest.mark.parametrize("fail", [False, True])
def test_web_closes_owned_model_after_success_or_failure(fail):
    """验证在成功或失败两种分支下，Web 服务层均能保证调用模型 aclose 并输出合规事件。"""
    class Model:
        model = "offline"
        closed = False

        def invoke(self, messages, **kwargs):
            # 若测试失败分支，模拟抛出带有敏感信息的内部运行时异常
            if fail:
                raise RuntimeError("synthetic-upstream-detail")
            return LLMToolResponse("done", [], self.model)

        async def aclose(self):
            # 记录模型关闭操作被调用
            self.closed = True

    model = Model()
    agent = SimpleAgent("web", model)
    # 使用 FastAPI TestClient 测试 SSE 请求
    with TestClient(create_app(lambda kind: agent)) as client:
        response = client.post("/agent/stream", json={"input": "task"})
    # 解析响应中的 SSE 事件
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]

    # 1. 核心断言：无论成功与否，HTTP 状态均为 200（SSE 流正常建立），模型均被关闭，智能体不再活跃
    assert response.status_code == 200 and model.closed and not agent._run_active

    if fail:
        # 2. 失败分支：最后一个事件必须为脱敏的 error 事件，且不产出 agent_finish
        assert events[-1]["type"] == "error"
        assert events[-1]["data"]["message"] == "运行失败，请检查服务端配置与日志。"
        assert not any(event["type"] == "agent_finish" for event in events)
    else:
        # 3. 成功分支：最后一个事件必须为正常的 agent_finish 完成事件
        assert events[-1]["type"] == "agent_finish"
        assert events[-1]["data"]["status"] == "completed"

