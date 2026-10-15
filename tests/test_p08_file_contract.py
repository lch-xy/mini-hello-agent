"""P08：保留原始文件副作用回归定义。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import pytest
from hello_agents.tools.builtin import WriteTool, EditTool, MultiEditTool


@pytest.mark.parametrize("kind", ["write", "edit", "multi"])
def test_absolute_external_path_is_successful_after_allowed_mutation(tmp_path, kind):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("alpha", encoding="utf-8")
    tool = {"write": WriteTool, "edit": EditTool, "multi": MultiEditTool}[kind](
        project_root=str(root)
    )
    parameters = {"path": str(target)}
    if kind == "write":
        parameters["content"] = "beta"
    elif kind == "edit":
        parameters.update(old_string="alpha", new_string="beta")
    else:
        parameters["edits"] = [{"old_string": "alpha", "new_string": "beta"}]
    response = tool.run(parameters)
    assert response.status.value == "success"
    assert target.read_text(encoding="utf-8") == "beta"
    assert Path(response.data["backup_path"]).is_absolute()
    assert Path(response.data["backup_path"]).read_text(encoding="utf-8") == "alpha"



def test_fixed_temp_is_untouched_and_rapid_backups_are_distinct(tmp_path):
    existing = tmp_path / "doc.txt.tmp"
    existing.write_text("unrelated", encoding="utf-8")
    target = tmp_path / "doc.txt"
    target.write_text("one", encoding="utf-8")
    tool = WriteTool(project_root=str(tmp_path))
    first = tool.run({"path": "doc.txt", "content": "two"})
    second = tool.run({"path": "doc.txt", "content": "three"})
    assert existing.read_text(encoding="utf-8") == "unrelated"
    assert first.data["backup_path"] != second.data["backup_path"]
    assert (tmp_path / first.data["backup_path"]).read_text(encoding="utf-8") == "one"
    assert (tmp_path / second.data["backup_path"]).read_text(encoding="utf-8") == "two"



@pytest.mark.parametrize("kind", ["write", "edit", "multi"])
def test_replace_failure_leaves_original_and_cleans_only_owned_temp(
    tmp_path, monkeypatch, kind
):
    import hello_agents.tools.builtin.file_tools as module

    target = tmp_path / "doc.txt"
    target.write_text("alpha", encoding="utf-8")
    unrelated = tmp_path / "doc.txt.tmp"
    unrelated.write_text("unrelated", encoding="utf-8")

    def fail(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    tool = {"write": WriteTool, "edit": EditTool, "multi": MultiEditTool}[kind](
        project_root=str(tmp_path)
    )
    args = {
        "path": "doc.txt",
        "content": "beta",
        "old_string": "alpha",
        "new_string": "beta",
        "edits": [{"old_string": "alpha", "new_string": "beta"}],
    }
    assert tool.run(args).status.value == "error"
    assert target.read_text(encoding="utf-8") == "alpha"
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    assert list(tmp_path.glob(".doc.txt.*.tmp")) == []



def test_concurrent_writes_do_not_share_temporary_file(tmp_path, monkeypatch):
    import hello_agents.tools.builtin.file_tools as module

    original_replace = module.os.replace
    barrier = threading.Barrier(2)
    temporary_names = []

    def coordinated_replace(source, destination):
        temporary_names.append(str(source))
        barrier.wait(timeout=3)
        return original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", coordinated_replace)
    tool = WriteTool(project_root=str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda value: tool.run({"path": "doc.txt", "content": value}),
                ["a" * 1000, "b" * 1000],
            )
        )
    # Windows 上同时 ReplaceFile 可能返回共享冲突；原子替换不承诺写者串行化。
    assert any(result.status.value == "success" for result in results)
    assert all(
        result.status.value == "success"
        or result.error_info["code"] == "PERMISSION_DENIED"
        for result in results
    ), [result.to_dict() for result in results]
    assert len(set(temporary_names)) == 2
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") in {
        "a" * 1000,
        "b" * 1000,
    }



def test_multiedit_validates_sequential_state_before_writing(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("alpha beta", encoding="utf-8")
    tool = MultiEditTool(project_root=str(tmp_path))
    invalid = tool.run(
        {
            "path": "doc.txt",
            "edits": [
                {"old_string": "alpha", "new_string": "beta"},
                {"old_string": "beta", "new_string": "gamma"},
            ],
        }
    )
    assert invalid.error_info["code"] == "INVALID_PARAM"
    assert invalid.context["edit_index"] == 1
    assert target.read_text(encoding="utf-8") == "alpha beta"
    assert not (tmp_path / ".backups").exists()
    valid = tool.run(
        {
            "path": "doc.txt",
            "edits": [
                {"old_string": "alpha", "new_string": "delta"},
                {"old_string": "delta", "new_string": "gamma"},
            ],
        }
    )
    assert valid.status.value == "success"
    assert target.read_text(encoding="utf-8") == "gamma beta"

