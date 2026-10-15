"""P11：固定目标的 Task 参数与过滤契约。"""
import pytest
from hello_agents.tools.builtin import TaskTool
from hello_agents.tools.tool_filter import ReadOnlyFilter

@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_filter": "readonyl"},
        {"tool_filter": None},
        {"agent_type": []},
        {"agent_type": "unknown"},
        {"max_steps": 0},
        {"max_steps": True},
        {"task": "  "},
        {"task": 12},
    ],
)
def test_task_invalid_parameters_rejected_before_factory(overrides):
    calls = []
    task = TaskTool(lambda kind: calls.append(kind))
    result = task.run({"task": "搜索资料", **overrides})
    assert result.error_info["code"] == "INVALID_PARAM"
    assert calls == []



def test_readonly_filter_includes_evidence_without_memory_mutations():
    reads = [
        "rag",
        "rag_search",
        "rag_read",
        "rag_read_document",
        "memory_search",
        "relation_lookup",
    ]
    writes = [
        "memory",
        "memory_add",
        "memory_revise",
        "memory_retract",
        "Write",
        "Bash",
    ]
    assert ReadOnlyFilter().filter(reads + writes) == reads



def test_task_explicit_filter_preserved():
    seen = []

    class Child:
        def run_as_subagent(self, **kwargs):
            seen.append(kwargs)
            return {
                "success": True,
                "summary": "完成",
                "metadata": {"steps": 1, "duration_seconds": 0},
            }

    tool = TaskTool(lambda kind: Child())
    assert (
        tool.run(
            {"task": "检索", "tool_filter": "readonly", "max_steps": 2}
        ).status.value
        == "success"
    )
    assert isinstance(seen[0]["tool_filter"], ReadOnlyFilter)
    assert seen[0]["max_steps_override"] == 2
    assert tool.run({"task": "检索", "tool_filter": "none"}).status.value == "success"
    assert seen[1]["tool_filter"] is None

