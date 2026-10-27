"""P10 运行时知识工具端到端全链路集成测试

测试验证：
1. Agent 核心 system_prompt 不被技能正文覆盖污染（知识作为 tool 回执流转）；
2. 技能附属资源文件仅在提示中呈现，严格不被自动执行；
3. SkillTool、TodoWriteTool、DevLogTool 多工具组合调用及持久化正常运作。
"""

import json
from pathlib import Path

from hello_agents import AgentComponents, Config, SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.skills import SkillLoader


def test_skill_todo_devlog_roundtrip_keeps_host_prompt_and_does_not_run_resources(tmp_path):
    """测试多知识工具协同运行：系统提示词保持不变、资源文件绝不自动执行

    流程：
    1. 准备技能目录与带副作用的脚本文件（若执行则生成 marker 文件）；
    2. 模拟 LLM 第一次调用发出 3 个工具调用：Skill、TodoWrite、DevLog；
    3. 校验传入的 system_prompt 始终保持原始值（host rules）；
    4. 校验工具回执：Skill 正文完成参数替换、脚本名出现在可用资源中；
    5. 校验 marker 文件不存在（确认未执行外部脚本）；
    6. 校验 todos 与 devlogs 持久化文件落盘。
    """
    directory = tmp_path / "skills" / "design"
    scripts = directory / "scripts"
    scripts.mkdir(parents=True)
    # 若 resource.py 被错误执行，该标记文件会被创建
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
        """测试用离线模型替身，验证工具调用协议与回执结构"""
        model = "offline"

        def __init__(self):
            self.calls = 0

        def invoke_with_tools(self, messages, tools, **kwargs):
            self.calls += 1
            # 关键断言：无论多少轮对话，宿主 system_prompt 始终保持不变
            assert messages[0] == {"role": "system", "content": "host rules"}
            if self.calls == 1:
                # 检查动态注入的 Skill 工具描述中包含了可用技能
                schema = next(t for t in tools if t["function"]["name"] == "Skill")
                assert "design: 设计规则" in schema["function"]["description"]
                # 第一次返回 3 个并发工具调用
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
            # 第二次调用时检查工具返回的回执内容
            receipts = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
            # $ARGUMENTS 占位符替换断言
            assert "先明确 Runtime 的职责" in receipts["skill"]["text"]
            # 资源扫描提示断言
            assert "resource.py" in receipts["skill"]["text"]
            # TodoWrite 统计数据断言
            assert receipts["todo"]["data"]["stats"]["in_progress"] == 1
            # DevLog 执行状态断言
            assert receipts["log"]["status"] == "success"
            return LLMToolResponse("已加载并记录", [], self.model)

    # 启用相关配置并指定持久化路径
    config = Config(todowrite_enabled=True, devlog_enabled=True,
                    todowrite_persistence_dir=str(tmp_path / "todos"),
                    devlog_persistence_dir=str(tmp_path / "devlogs"))
    agent = SimpleAgent("knowledge", Model(), system_prompt="host rules", config=config,
                        tool_registry=registry, components=AgentComponents(skill_loader=loader))

    # 执行完整的 Agent 循环
    assert agent.run("设计 Runtime") == "已加载并记录"

    # 断言系统提示词未被修改，且脚本资源文件未被执行
    assert agent.system_prompt == "host rules" and not marker.exists()
    # 断言技能对象已按需缓存
    assert loader.get_skill("design") is loader.skills_cache["design"]
    # 断言模型与工具调用统计
    assert agent.last_run.model_calls == 2 and agent.last_run.tool_calls == 3
    # 断言落盘文件存在
    assert list((tmp_path / "todos").glob("*.json"))
    assert list((tmp_path / "devlogs").glob("*.json"))
