"""加载知识、记录进度与决策通过真实工具回执进入共享 Runtime。"""

import json
from pathlib import Path

from hello_agents import AgentComponents, Config, SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.skills import SkillLoader


def test_skill_todo_devlog_roundtrip_keeps_host_prompt_and_does_not_run_resources(tmp_path):
    directory = tmp_path / "skills" / "design"
    scripts = directory / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "resource-was-executed"
    (directory / "SKILL.md").write_text(
        "---\nname: design\ndescription: 设计规则\n---\n先明确 $ARGUMENTS 的职责。", encoding="utf-8"
    )
    (scripts / "resource.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n", encoding="utf-8"
    )
    loader, registry = SkillLoader(directory.parent), ToolRegistry()
    assert loader.skills_cache == {}

    class Model:
        model = "offline"

        def __init__(self):
            self.calls = 0

        def invoke_with_tools(self, messages, tools, **kwargs):
            self.calls += 1
            assert messages[0] == {"role": "system", "content": "host rules"}
            if self.calls == 1:
                schema = next(t for t in tools if t["function"]["name"] == "Skill")
                assert "design: 设计规则" in schema["function"]["description"]
                calls = [
                    ToolCall("skill", "Skill", '{"skill":"design","args":"Runtime"}'),
                    ToolCall("todo", "TodoWrite", json.dumps({"todos": [
                        {"content": "核查职责", "status": "in_progress"}
                    ]})),
                    ToolCall("log", "DevLog", json.dumps({
                        "action": "append", "category": "decision", "content": "复用共享循环"
                    })),
                ]
                return LLMToolResponse(None, calls, self.model)
            receipts = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
            assert "先明确 Runtime 的职责" in receipts["skill"]["text"]
            assert "resource.py" in receipts["skill"]["text"]
            assert receipts["todo"]["data"]["stats"]["in_progress"] == 1
            assert receipts["log"]["status"] == "success"
            return LLMToolResponse("已加载并记录", [], self.model)

    config = Config(todowrite_enabled=True, devlog_enabled=True,
                    todowrite_persistence_dir=str(tmp_path / "todos"),
                    devlog_persistence_dir=str(tmp_path / "devlogs"))
    agent = SimpleAgent("knowledge", Model(), system_prompt="host rules", config=config,
                        tool_registry=registry, components=AgentComponents(skill_loader=loader))
    assert agent.run("设计 Runtime") == "已加载并记录"
    assert agent.system_prompt == "host rules" and not marker.exists()
    assert loader.get_skill("design") is loader.skills_cache["design"]
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 3
    assert list((tmp_path / "todos").glob("*.json"))
    assert list((tmp_path / "devlogs").glob("*.json"))
