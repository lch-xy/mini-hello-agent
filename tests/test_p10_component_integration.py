"""P10 Agent 组件集成契约测试

测试重点：
1. 通过 AgentComponents 显式传入的可选组件实例（如 skill_loader, session_store, token_counter）
   应能无条件覆盖配置对象中全部禁用的默认选项（即使组件布尔值为 Falsey）；
2. 验证注入组件在执行工具、保存会话、重载会话及 Token 计数时的真实协同有效性。
"""
from pathlib import Path
from hello_agents import SimpleAgent, AgentComponents, Message, ToolRegistry, Config
from hello_agents.core.session_store import SessionStore
from hello_agents.core.llm_response import LLMResponse
from hello_agents.skills import SkillLoader

class CaptureLLM:
    """测试用 LLM 替身，捕获收到的消息历史并返回固定文本响应"""
    model = "test-component-capture"
    provider = "test"

    def __init__(self):
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.messages.append(messages)
        return LLMResponse("本地替身回复", self.model)



def local_config(tmp_path, **overrides):
    """生成所有内置特性均显式关闭（disabled）的基础配置，用于验证外部注入组件的覆盖优先级"""
    options = dict(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
        skills_dir=str(tmp_path / "default-skills"),
        session_dir=str(tmp_path / "default-sessions"),
        tool_output_dir=str(tmp_path / "tool-output"),
        todowrite_persistence_dir=str(tmp_path / "todos"),
        devlog_persistence_dir=str(tmp_path / "devlogs"),
    )
    options.update(overrides)
    return Config(**options)



class CountingCounter:
    """测试用 Token 计数器替身，每条消息固定返回 13 tokens 并记录缓存清理调用次数"""
    def __init__(self):
        self.host_marker = {"preserve": True}
        self.clear_calls = 0

    def count_message(self, message):
        return 13

    def count_messages(self, messages):
        return len(messages) * 13

    def clear_cache(self):
        self.clear_calls += 1



def test_optional_instances_override_disabled_config_and_really_work(tmp_path):
    """验证显式注入的组件实例覆盖禁用的配置项，且在运行时各司其职

    1. FalseySessionStore：__bool__ 返回 False，验证框架使用 `is not None` 而非 `if store:`；
    2. LoaderProxy：代理 SkillLoader 接口，验证多态注入下的技能加载能力；
    3. CountingCounter：自定义 Token 计数组件；
    4. 验证会话持久化与恢复：恢复后历史记录精确还原，Token 计数准确。
    """
    class FalseySessionStore(SessionStore):
        """布尔值为 False 的 SessionStore，检验框架是否遵循 `is not None` 判定规范"""
        def __bool__(self):
            return False

    class LoaderProxy:
        """代理包装类，模拟自定义或远程代理的技能加载器"""
        def __init__(self, loader):
            self.loader = loader

        def get_descriptions(self):
            return self.loader.get_descriptions()

        def list_skills(self):
            return self.loader.list_skills()

        def get_skill(self, name):
            return self.loader.get_skill(name)

    # 准备测试技能目录与技能描述文件
    skill_dir = tmp_path / "custom-skills" / "travel"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: travel\ndescription: 旅行规划规则\n---\n检查步行限制。",
        encoding="utf-8",
    )
    loader = LoaderProxy(SkillLoader(skill_dir.parent))
    store = FalseySessionStore(str(tmp_path / "custom-sessions"))
    counter = CountingCounter()

    # 初始化 Agent，传入全部禁用的 local_config 但显式注入 AgentComponents
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path),
        tool_registry=ToolRegistry(),
        components=AgentComponents(
            session_store=store, skill_loader=loader, token_counter=counter
        ),
    )

    # 校验显式组件已挂载到 Agent
    assert agent.skill_loader is loader and agent.session_store is store

    # 校验 Skill 工具已自动注册并通过代理加载器成功加载技能
    skill_result = agent.tool_registry.execute_tool("Skill", {"skill": "travel"})
    assert "检查步行限制" in skill_result.text

    # 测试会话保存与列表检索
    agent.add_message(Message("原始记录", "user"))
    saved_path = agent.save_session("injected")
    assert Path(saved_path).exists() and agent.list_sessions()

    # 追加新消息后重新加载已保存会话，验证会话完整恢复至历史快照
    agent.add_message(Message("不应留在恢复状态", "assistant"))
    agent.load_session(saved_path)
    assert [m.content for m in agent.get_history()] == ["原始记录"]
    # 验证 Token 计数准确且未触发多余的 clear 缓存调用
    assert agent._history_token_count == 13 and counter.clear_calls == 0

