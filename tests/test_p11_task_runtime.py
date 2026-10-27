"""P11 运行时嵌套 Task 端到端全链路集成测试

测试重点：
1. 真实嵌套 Task 调用生命周期：模型路由选择（主模型 vs 轻量模型）、工具过滤权限隔离；
2. 共享工具身份与反向引用：验证工具实例在子代理中的共享身份与读元数据缓存独立性；
3. 父子消息边界：子任务内部工具执行调用轨迹不侵入父历史消息，仅作为 Task 摘要回执呈现；
4. 默认工厂（default_subagent_factory）按继承配置重组内置工具，不破坏宿主工具表。
"""

import json

import pytest

from hello_agents import AgentComponents, Config, SimpleAgent, ToolRegistry
from hello_agents.agents.factory import default_subagent_factory
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.tools.builtin import ReadTool, TodoWriteTool


@pytest.mark.parametrize("use_light", [False, True])
def test_task_filters_child_names_and_keeps_model_ownership_and_shared_tool_identity(tmp_path, use_light):
    """测试 Task 工具执行：验证模型路由、工具可见性过滤、共享工具同一性及父子历史边界

    验证要点：
    - 当 use_light 为 True 时，子代理调用由 subagent_llm（轻量模型）承担，主模型调用为 0；
    - 子代理受 readonly 策略约束，仅能感知 Read 工具，Write 工具被安全隐藏；
    - 共享的 ReadTool 实例引用同一物理对象，但子代理持有的 read_metadata_cache 为浅隔离拷贝；
    - 父 Agent 历史消息中仅记录唯一的 Task 工具调用，子代理的 Read 轨迹不泄漏到父历史。
    """
    (tmp_path / "evidence.txt").write_text("local evidence", encoding="utf-8")
    writes, children = [], []
    registry = ToolRegistry()
    read = ReadTool(str(tmp_path), registry=registry)
    registry.register_tool(read)
    registry.register_function(lambda value: writes.append(value), name="Write")

    class Model:
        """测试用模型替身，区分父 Agent 与子代理的交互契约"""
        model = "offline"

        def __init__(self):
            self.parent_calls = self.child_calls = 0
            self.closed = False

        def invoke_with_tools(self, messages, tools, **kwargs):
            names = {tool["function"]["name"] for tool in tools}
            if "Task" in names:
                # 宿主父 Agent 调用链路
                self.parent_calls += 1
                if self.parent_calls == 1:
                    # 第一次调用：返回委托子代理的 ToolCall
                    return LLMToolResponse(None, [ToolCall("task", "Task", json.dumps({
                        "task": "read evidence", "agent_type": "simple",
                        "tool_filter": "readonly", "max_steps": 2,
                    }))], self.model)
                # 第二次调用：接收子代理执行结果摘要
                receipt = json.loads(messages[-1]["content"])
                assert receipt["status"] == "success" and "local evidence" in receipt["text"]
                return LLMToolResponse("parent done", [], self.model)
            # 子代理调用链路：受 readonly 过滤后工具集合必须只有 Read
            self.child_calls += 1
            assert names == {"Read"}
            if self.child_calls == 1:
                return LLMToolResponse(None, [ToolCall("read", "Read", '{"path":"evidence.txt"}')], self.model)
            assert json.loads(messages[-1]["content"])["data"]["content"] == "local evidence"
            return LLMToolResponse("local evidence", [], self.model)

        def close(self):
            self.closed = True

    main, light = Model(), Model()
    config = Config(subagent_enabled=True, subagent_use_light_llm=use_light, subagent_max_steps=2)
    parent = SimpleAgent("parent", main, config=config, tool_registry=registry,
                         components=AgentComponents(subagent_llm=light))
    task = registry.get_tool("Task")
    factory = task.agent_factory

    # 包装工厂方法以捕获生成的子代理实例
    def capture(kind):
        child = factory(kind)
        children.append(child)
        return child

    task.agent_factory = capture
    # 执行主 Agent
    assert parent.run("delegate") == "parent done"
    assert main.parent_calls == 2
    # 模型归属断言：根据 use_light 正确路由到轻量模型或主模型
    assert (main.child_calls, light.child_calls) == ((0, 2) if use_light else (2, 0))
    child = children[0]
    # 子代理历史与工具对象断言
    assert child.get_history() == [] and child.tool_registry.get_tool("Read") is read
    # 子代理配置中禁止递归再创建子代理与自动保存
    assert not child.config.subagent_enabled and not child.config.auto_save_enabled
    # 工具缓存隔离断言
    assert child.tool_registry.read_metadata_cache is not registry.read_metadata_cache
    # 字典已复制，但共享 ReadTool 仍持有宿主 Registry 的反向引用。
    assert registry.get_read_metadata("evidence.txt") is not None
    assert read.registry is registry and writes == []
    # 消息边界断言：子任务全量 Read 轨迹没有直接合入父历史，父历史仅记录 Task 调用
    calls = [call["function"]["name"] for message in parent.get_history()
             for call in message.metadata["model_message"].get("tool_calls", [])]
    assert calls == ["Task"]
    assert parent.last_run.model_calls == 2 and parent.last_run.tool_calls == 1
    assert not main.closed and not light.closed


def test_default_factory_can_reassemble_optional_tools_from_inherited_config(tmp_path):
    """测试子代理默认工厂根据继承的配置项安全重组内置可选工具

    即使父注册表中已存在同名自定义函数，子代理根据其自身继承的 todowrite_enabled 配置，
    应在其隔离的 tool_registry 中挂载标准 TodoWriteTool 实例，而不污染宿主注册表。
    """
    registry = ToolRegistry()
    registry.register_function(lambda value: "host", name="TodoWrite")
    config = Config(subagent_enabled=True, auto_save_enabled=True, todowrite_enabled=True,
                    todowrite_persistence_dir=str(tmp_path / "todos"))
    child = default_subagent_factory("simple", type("Model", (), {"model": "offline"})(), registry, config)
    # 子代理禁用多级嵌套子代理与自动保存
    assert not child.config.subagent_enabled and not child.config.auto_save_enabled
    # 子代理注册表成功装配了标准的 TodoWriteTool
    assert isinstance(child.tool_registry.get_tool("TodoWrite"), TodoWriteTool)
    # 宿主注册表中原函数未被覆盖
    assert registry.get_tool("TodoWrite") is None
    assert registry.execute_tool("TodoWrite", "input").data["output"] == "host"
