"""真实文件副作用与 Agent/Policy 集成，全部使用临时目录。"""

import json
import os
from pathlib import Path

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.tools import AllowlistPolicy
from hello_agents.tools.builtin import EditTool, ReadTool, WriteTool


def test_runtime_reads_then_edits_with_explicit_mtime_and_utf8_receipt(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("old", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_tool(ReadTool(str(tmp_path), registry=registry))
    registry.register_tool(EditTool(str(tmp_path), registry=registry))

    class Model:
        model = "offline"

        def __init__(self):
            self.calls = 0

        def invoke_with_tools(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                call = ToolCall("read", "Read", '{"path":"note.txt"}')
            elif self.calls == 2:
                receipt = json.loads(messages[-1]["content"])["data"]
                assert receipt["content"] == "old" and receipt["file_size_bytes"] == 3
                assert registry.get_read_metadata("note.txt")["file_mtime_ms"] == receipt["file_mtime_ms"]
                call = ToolCall("edit", "Edit", json.dumps({
                    "path": "note.txt", "old_string": "old", "new_string": "世界",
                    "file_mtime_ms": receipt["file_mtime_ms"],
                }))
            else:
                receipt = json.loads(messages[-1]["content"])["data"]
                assert receipt["changed_bytes"] == len("世界".encode("utf-8")) - 3
                assert (tmp_path / receipt["backup_path"]).read_text(encoding="utf-8") == "old"
                return LLMToolResponse("已编辑", [], self.model)
            return LLMToolResponse(None, [call], self.model)

    agent = SimpleAgent("files", Model(), tool_registry=registry)
    assert agent.run("read then edit") == "已编辑"
    assert path.read_text(encoding="utf-8") == "世界"
    assert agent.last_run.model_calls == 3 and agent.last_run.tool_calls == 2
    assert [m.metadata["model_message"]["tool_call_id"] for m in agent.get_history() if m.role == "tool"] == ["read", "edit"]


def test_read_cache_does_not_silently_enforce_write_lock_without_parameter(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("original", encoding="utf-8")
    registry = ToolRegistry()
    read, write = ReadTool(str(tmp_path), registry=registry), WriteTool(str(tmp_path), registry=registry)
    old_mtime = read.run({"path": "note.txt"}).data["file_mtime_ms"]
    path.write_text("external", encoding="utf-8")
    changed_seconds = (old_mtime + 2000) / 1000
    os.utime(path, (changed_seconds, changed_seconds))
    rejected = write.run({"path": "note.txt", "content": "protected", "file_mtime_ms": old_mtime})
    assert rejected.error_info["code"] == "CONFLICT"
    assert path.read_text(encoding="utf-8") == "external"
    allowed = write.run({"path": "note.txt", "content": "unprotected"})
    assert allowed.status.value == "success"
    assert path.read_text(encoding="utf-8") == "unprotected"


def test_registry_policy_denies_write_before_any_filesystem_mutation(tmp_path):
    registry = ToolRegistry(policy=AllowlistPolicy({"Read"}))
    registry.register_tool(WriteTool(str(tmp_path)))
    result = registry.execute_tool("Write", {"path": "nested/new.txt", "content": "data"})
    assert result.error_info["code"] == "PERMISSION_DENIED"
    assert list(tmp_path.iterdir()) == []


def test_atomic_replacement_preserves_existing_file_mode(tmp_path):
    path = tmp_path / "run.sh"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o750)
    response = WriteTool(str(tmp_path)).run({"path": "run.sh", "content": "new"})
    assert response.status.value == "success"
    assert Path(path).stat().st_mode & 0o777 == 0o750
