"""真实嵌套 Task：模型选择、过滤、共享工具身份与父消息边界。"""

import json

import pytest

from hello_agents import AgentComponents, Config, SimpleAgent, ToolRegistry
from hello_agents.agents.factory import default_subagent_factory
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.tools.builtin import ReadTool, TodoWriteTool


@pytest.mark.parametrize("use_light", [False, True])
def test_task_filters_child_names_and_keeps_model_ownership_and_shared_tool_identity(tmp_path, use_light):
    (tmp_path / "evidence.txt").write_text("local evidence", encoding="utf-8")
    writes, children = [], []
    registry = ToolRegistry()
    read = ReadTool(str(tmp_path), registry=registry)
    registry.register_tool(read)
    registry.register_function(lambda value: writes.append(value), name="Write")

    class Model:
        model = "offline"

        def __init__(self):
            self.parent_calls = self.child_calls = 0
            self.closed = False

        def invoke_with_tools(self, messages, tools, **kwargs):
            names = {tool["function"]["name"] for tool in tools}
            if "Task" in names:
                self.parent_calls += 1
                if self.parent_calls == 1:
                    return LLMToolResponse(None, [ToolCall("task", "Task", json.dumps({
                        "task": "read evidence", "agent_type": "simple",
                        "tool_filter": "readonly", "max_steps": 2,
                    }))], self.model)
                receipt = json.loads(messages[-1]["content"])
                assert receipt["status"] == "success" and "local evidence" in receipt["text"]
                return LLMToolResponse("parent done", [], self.model)
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

    def capture(kind):
        child = factory(kind)
        children.append(child)
        return child

    task.agent_factory = capture
    assert parent.run("delegate") == "parent done"
    assert main.parent_calls == 2
    assert (main.child_calls, light.child_calls) == ((0, 2) if use_light else (2, 0))
    child = children[0]
    assert child.get_history() == [] and child.tool_registry.get_tool("Read") is read
    assert not child.config.subagent_enabled and not child.config.auto_save_enabled
    assert child.tool_registry.read_metadata_cache is not registry.read_metadata_cache
    # 字典已复制，但共享 ReadTool 仍持有宿主 Registry 的反向引用。
    assert registry.get_read_metadata("evidence.txt") is not None
    assert read.registry is registry and writes == []
    calls = [call["function"]["name"] for message in parent.get_history()
             for call in message.metadata["model_message"].get("tool_calls", [])]
    assert calls == ["Task"]  # 子任务全量 Read 轨迹没有直接合入父历史。
    assert parent.last_run.model_calls == 2 and parent.last_run.tool_calls == 1
    assert not main.closed and not light.closed


def test_default_factory_can_reassemble_optional_tools_from_inherited_config(tmp_path):
    registry = ToolRegistry()
    registry.register_function(lambda value: "host", name="TodoWrite")
    config = Config(subagent_enabled=True, auto_save_enabled=True, todowrite_enabled=True,
                    todowrite_persistence_dir=str(tmp_path / "todos"))
    child = default_subagent_factory("simple", type("Model", (), {"model": "offline"})(), registry, config)
    assert not child.config.subagent_enabled and not child.config.auto_save_enabled
    assert isinstance(child.tool_registry.get_tool("TodoWrite"), TodoWriteTool)
    assert registry.get_tool("TodoWrite") is None
    assert registry.execute_tool("TodoWrite", "input").data["output"] == "host"
