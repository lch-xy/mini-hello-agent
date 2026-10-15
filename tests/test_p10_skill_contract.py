"""P10：当前依赖已就绪的固定目标原定义。"""
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from hello_agents.skills import SkillLoader
from hello_agents.tools.builtin import TodoWriteTool, MultiEditTool


@pytest.mark.parametrize("kind", ["edit", "todo"])
def test_object_array_contract_accepts_real_payload_rejects_strings(tmp_path, kind):
    if kind == "edit":
        tool, field, value = (
            MultiEditTool(),
            "edits",
            {"old_string": "a", "new_string": "b"},
        )
        payload = {"path": "a.txt", field: [value]}
    else:
        tool, field, value = (
            TodoWriteTool(project_root=str(tmp_path)),
            "todos",
            {"content": "工作", "status": "pending"},
        )
        payload = {field: [value]}
    validator = Draft202012Validator(tool.to_openai_schema()["function"]["parameters"])
    assert validator.is_valid(payload)
    assert not validator.is_valid({**payload, field: ["not-an-object"]})
    assert not validator.is_valid({**payload, field: [{}]})



@pytest.mark.parametrize(
    "metadata",
    ["123", "[a,b]", "name: [a]\ndescription: x", "name: valid\ndescription: ''"],
)
def test_invalid_skill_is_isolated_from_valid_skill(tmp_path, metadata):
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    (bad / "SKILL.md").write_text(f"---\n{metadata}\n---\nbody", encoding="utf-8")
    (good / "SKILL.md").write_text(
        "---\nname: good\ndescription: valid\n---\nusable", encoding="utf-8"
    )
    loader = SkillLoader(tmp_path)
    assert loader.list_skills() == ["good"]
    assert loader.get_skill("good").body == "usable"
    assert len(loader.diagnostics) == 1 and "bad" in loader.diagnostics[0]["path"]



def test_lazy_skill_validation_and_reload_use_same_rules(tmp_path):
    directory = tmp_path / "skill"
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text("---\nname: skill\ndescription: valid\n---\nbody", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    path.write_text("---\n123\n---\nbody", encoding="utf-8")
    assert loader.get_skill("skill") is None
    assert loader.diagnostics
    loader.reload()
    assert loader.list_skills() == [] and len(loader.diagnostics) == 1

