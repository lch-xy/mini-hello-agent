"""P10：当前依赖已就绪的固定目标原定义。"""
from pathlib import Path
import json
from hello_agents.tools.errors import ToolErrorCode


def test_todo_clear_persists(tmp_path):
    from hello_agents.tools.builtin import TodoWriteTool

    tool = TodoWriteTool(persistence_dir=str(tmp_path))
    tool.run({"todos": [{"content": "旅行预算", "status": "pending"}]})
    assert tool.current_todos.todos
    tool.run({"action": "clear"})
    loaded = TodoWriteTool(persistence_dir=str(tmp_path))
    assert loaded.current_todos.todos == []



def test_devlog_schema_and_invalid_action(tmp_path):
    from hello_agents.tools.builtin import DevLogTool
    from hello_agents.tools.errors import ToolErrorCode

    tool = DevLogTool(
        session_id="test", agent_name="test", persistence_dir=str(tmp_path)
    )
    schema = tool.to_openai_schema()["function"]["parameters"]
    assert schema["properties"]["action"]["enum"] == [
        "append",
        "read",
        "summary",
        "clear",
    ]
    result = tool.run({"action": "not-an-action"})
    assert result.error_info["code"] == ToolErrorCode.INVALID_PARAM

