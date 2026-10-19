"""工具执行策略与 Agent 默认行为测试。

本测试套件验证：
1. Agent 默认行为不产生多余副作用或存储文件；
2. 权限策略在普通函数、fork 子注册表以及异步调用中均能有效拦截未授权工具；
3. 策略判定过程对参数进行安全深拷贝，防止外部并发修改已被授权的参数 payload。
"""

import asyncio
import pytest

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.tools import AllowlistPolicy, PermissionDecision, CalculatorTool
from hello_agents.core.llm_response import LLMResponse


class MinimalLLM:
    """最小化测试 LLM，用于核验默认闭环。"""
    model = "fixture"
    provider = "fixture"

    def invoke(self, messages, **kwargs):
        return LLMResponse("done", self.model)

    invoke_with_tools = invoke


def test_default_agent_is_a_minimal_loop(tmp_path, monkeypatch):
    """验证：默认配置下的 Agent 执行不产生任何临时文件，且未配置的持久化组件均为 None。"""
    monkeypatch.chdir(tmp_path)
    tools = ToolRegistry()
    tools.register_tool(CalculatorTool())
    names = tools.list_tools()
    agent = SimpleAgent("minimal", MinimalLLM(), tool_registry=tools)
    assert agent.run("hello") == "done"
    assert tools.list_tools() == names
    assert agent.session_store is None and agent.skill_loader is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_policy_covers_functions_forks_and_no_execution(asynchronous):
    """验证：白名单策略能精准拦截未授权的普通函数，并在 fork 子表中继续生效且不触发熔断计数。"""
    calls = []
    registry = ToolRegistry(policy=AllowlistPolicy(["read"]))
    registry.register_function(lambda x: calls.append(x), name="write")
    child = registry.fork()
    if asynchronous:
        result = asyncio.run(child.aexecute_tool("write", "payload"))
    else:
        result = child.execute_tool("write", "payload")
    # 断言权限被拒绝，真实函数没有被调用，熔断器未开启
    assert result.error_info["code"] == "PERMISSION_DENIED"
    assert calls == [] and not registry.circuit_breaker.is_open("write")


def test_policy_failure_is_closed_and_arguments_are_copied():
    """验证：策略检查函数自身崩溃时默认阻断执行（安全降级），且策略内部清空入参不影响外部原始字典。"""
    def invalid(name, arguments):
        raise RuntimeError("broken")

    r = ToolRegistry(policy=invalid)
    r.register_tool(CalculatorTool())
    assert (
        r.execute_tool("calculator", {"expression": "2+3"}).error_info["code"]
        == "PERMISSION_DENIED"
    )

    def allow(name, arguments):
        arguments.clear()  # 试图篡改或清空参数
        return PermissionDecision(True)

    r.policy = allow
    args = {"expression": "2+3"}
    r.execute_tool("calculator", args)
    # 原始参数必须被完整保护，不被策略函数篡改
    assert args == {"expression": "2+3"}


@pytest.mark.asyncio
async def test_caller_cannot_change_authorized_payload_during_execution():
    """验证：调用方在工具执行异步等待期间修改原始 payload，不会影响工具实际收到的参数（快照隔离）。"""
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def remote_action(arguments):
        entered.set()
        await release.wait()
        seen.append(arguments["target"])

    registry = ToolRegistry(
        policy=lambda name, args: PermissionDecision(args["target"] == "safe")
    )
    registry.register_function(remote_action)
    payload = {"target": "safe"}
    task = asyncio.create_task(registry.aexecute_tool("remote_action", payload))
    await entered.wait()
    # 策略通过后、异步执行未结束前，外部试图将参数篡改为 denied
    payload["target"] = "denied"
    release.set()
    await task
    # 工具实际消费的参数仍然是策略通过时的快照 "safe"
    assert seen == ["safe"]
