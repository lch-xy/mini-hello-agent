from pathlib import Path
from hello_agents import SimpleAgent, AgentComponents, Message, ToolRegistry, Config
from hello_agents.core.session_store import SessionStore
from hello_agents.core.llm_response import LLMResponse
from hello_agents.skills import SkillLoader

class CaptureLLM:
    model = "test-component-capture"
    provider = "test"

    def __init__(self):
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.messages.append(messages)
        return LLMResponse("本地替身回复", self.model)



def local_config(tmp_path, **overrides):
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
    class FalseySessionStore(SessionStore):
        def __bool__(self):
            return False

    class LoaderProxy:
        def __init__(self, loader):
            self.loader = loader

        def get_descriptions(self):
            return self.loader.get_descriptions()

        def list_skills(self):
            return self.loader.list_skills()

        def get_skill(self, name):
            return self.loader.get_skill(name)

    skill_dir = tmp_path / "custom-skills" / "travel"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: travel\ndescription: 旅行规划规则\n---\n检查步行限制。",
        encoding="utf-8",
    )
    loader = LoaderProxy(SkillLoader(skill_dir.parent))
    store = FalseySessionStore(str(tmp_path / "custom-sessions"))
    counter = CountingCounter()
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path),
        tool_registry=ToolRegistry(),
        components=AgentComponents(
            session_store=store, skill_loader=loader, token_counter=counter
        ),
    )
    assert agent.skill_loader is loader and agent.session_store is store
    skill_result = agent.tool_registry.execute_tool("Skill", {"skill": "travel"})
    assert "检查步行限制" in skill_result.text
    agent.add_message(Message("原始记录", "user"))
    saved_path = agent.save_session("injected")
    assert Path(saved_path).exists() and agent.list_sessions()
    agent.add_message(Message("不应留在恢复状态", "assistant"))
    agent.load_session(saved_path)
    assert [m.content for m in agent.get_history()] == ["原始记录"]
    assert agent._history_token_count == 13 and counter.clear_calls == 0

