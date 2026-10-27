"""P10 技能契约与对象数组协议验证测试

包含：
1. 工具参数对象数组（JSON Schema Draft 2020-12）规范校验
2. 非法技能元数据的异常隔离能力验证
3. 懒加载校验与热重载规则一致性验证
"""
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from hello_agents.skills import SkillLoader
from hello_agents.tools.builtin import TodoWriteTool, MultiEditTool


@pytest.mark.parametrize("kind", ["edit", "todo"])
def test_object_array_contract_accepts_real_payload_rejects_strings(tmp_path, kind):
    """验证对象数组参数协议（MultiEditTool 的 edits 与 TodoWriteTool 的 todos）

    根据 Draft 2020-12 校验器：
    - 接受符合 schema 结构的对象数组负载；
    - 拒绝字符串标量等非对象数组元素；
    - 拒绝缺少必填字段的空对象元素。
    """
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
    # 获取工具导出的 OpenAI 兼容 JSON Schema 并创建验证器
    validator = Draft202012Validator(tool.to_openai_schema()["function"]["parameters"])
    # 合法载荷应通过校验
    assert validator.is_valid(payload)
    # 数组元素为标量字符串时应拒绝
    assert not validator.is_valid({**payload, field: ["not-an-object"]})
    # 数组元素缺少必填字段时应拒绝
    assert not validator.is_valid({**payload, field: [{}]})



@pytest.mark.parametrize(
    "metadata",
    ["123", "[a,b]", "name: [a]\ndescription: x", "name: valid\ndescription: ''"],
)
def test_invalid_skill_is_isolated_from_valid_skill(tmp_path, metadata):
    """验证损坏技能文件的异常隔离机制

    当部分技能的 SKILL.md frontmatter 格式不合法或必填字段缺失时，
    SkillLoader 应记录诊断信息（diagnostics）并跳过损坏技能，
    不影响正常技能的发现、列表列举与正文加载。
    """
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    (bad / "SKILL.md").write_text(f"---\n{metadata}\n---\nbody", encoding="utf-8")
    (good / "SKILL.md").write_text(
        "---\nname: good\ndescription: valid\n---\nusable", encoding="utf-8"
    )
    loader = SkillLoader(tmp_path)
    # 损坏技能被排除，正常技能可用
    assert loader.list_skills() == ["good"]
    assert loader.get_skill("good").body == "usable"
    # 损坏技能产生且仅产生一条诊断日志
    assert len(loader.diagnostics) == 1 and "bad" in loader.diagnostics[0]["path"]



def test_lazy_skill_validation_and_reload_use_same_rules(tmp_path):
    """验证技能懒加载按需校验与全量 reload 采用同一套规则

    当技能文件被篡改损坏时，按需获取时应捕获校验失败并返回 None；
    调用 reload() 刷新后，损坏技能应从可用列表剔除。
    """
    directory = tmp_path / "skill"
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text("---\nname: skill\ndescription: valid\n---\nbody", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    # 动态篡改为非法内容
    path.write_text("---\n123\n---\nbody", encoding="utf-8")
    # 懒加载时发现损坏应返回 None 并记录诊断信息
    assert loader.get_skill("skill") is None
    assert loader.diagnostics
    # 全量重载后应保持一致的过滤与诊断结果
    loader.reload()
    assert loader.list_skills() == [] and len(loader.diagnostics) == 1

