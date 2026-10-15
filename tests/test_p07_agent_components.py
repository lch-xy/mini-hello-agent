"""P07：保留当前组件协议范围内的原定义。"""
from pathlib import Path
import pytest
from hello_agents import AgentComponents, Config, DEFAULT_COMPONENT, Message, PlanSolveAgent, ReActAgent, ReflectionAgent, SimpleAgent, ToolRegistry
from hello_agents.context import HistoryManager, ObservationTruncator, TokenCounter
from hello_agents.core.components import build_agent_components
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



class SeparateHistory:
    """通过协议组合，不继承 HistoryManager。"""

    def __init__(self, min_retain_rounds=2):
        self.min_retain_rounds = min_retain_rounds
        self.backend = HistoryManager(min_retain_rounds=min_retain_rounds)

    def append(self, message):
        self.backend.append(message)

    def get_history(self):
        return self.backend.get_history()

    def clear(self):
        self.backend.clear()

    def compress(self, summary):
        self.backend.compress(summary)

    def estimate_rounds(self):
        return self.backend.estimate_rounds()

    def find_round_boundaries(self):
        return self.backend.find_round_boundaries()



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



class CustomTruncator:
    def truncate(self, tool_name, output):
        return {"truncated": False, "preview": f"custom:{output}"}



@pytest.mark.parametrize(
    "agent_class", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
def test_all_agent_constructors_propagate_components(tmp_path, agent_class):
    history = SeparateHistory()
    history.append(Message("预载任务", "user"))
    counter = CountingCounter()
    truncator = CustomTruncator()
    agent = agent_class(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path),
        components=AgentComponents(
            history_manager=history,
            token_counter=counter,
            truncator=truncator,
            session_store=None,
            skill_loader=None,
        ),
    )
    assert agent.history_manager is history
    assert agent.token_counter is counter and agent.truncator is truncator
    assert agent.components.history_manager is history
    assert agent._history_token_count == 13
    agent.add_message(Message("后续任务", "user"))
    assert agent._history_token_count == 26
    assert counter.host_marker == {"preserve": True} and counter.clear_calls == 0
    assert "保留最近 2 轮" in agent._generate_simple_summary(history.get_history())



def test_none_disables_optional_components_despite_enabled_defaults(tmp_path):
    config = local_config(tmp_path, session_enabled=True, skills_enabled=True)
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=config,
        tool_registry=ToolRegistry(),
        components=AgentComponents(session_store=None, skill_loader=None),
    )
    assert agent.session_store is None and agent.skill_loader is None
    assert "Skill" not in agent.tool_registry.list_tools()
    assert (
        not Path(config.session_dir).exists() and not Path(config.skills_dir).exists()
    )
    with pytest.raises(RuntimeError, match="AgentComponents"):
        agent.save_session("disabled")
    assert agent.list_sessions() == []



def test_explicit_loader_can_opt_out_of_tool_registration(tmp_path):
    loader = SkillLoader(tmp_path / "supplied-skills")
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path, skills_auto_register=False),
        tool_registry=ToolRegistry(),
        components=AgentComponents(skill_loader=loader),
    )
    assert agent.skill_loader is loader
    assert "Skill" not in agent.tool_registry.list_tools()



@pytest.mark.parametrize(
    "field",
    ["history_manager", "token_counter", "truncator", "session_store", "skill_loader"],
)
def test_invalid_component_fails_before_creating_default_directories(tmp_path, field):
    config = local_config(tmp_path, session_enabled=True, skills_enabled=True)
    components = AgentComponents(**{field: object()})
    with pytest.raises(TypeError, match=field):
        SimpleAgent("test", CaptureLLM(), config=config, components=components)
    assert (
        not Path(config.session_dir).exists() and not Path(config.skills_dir).exists()
    )



def test_invalid_history_policy_and_bundle_rejected(tmp_path):
    history = SeparateHistory(min_retain_rounds=0)
    with pytest.raises(ValueError, match="min_retain_rounds"):
        build_agent_components(
            local_config(tmp_path), "test", AgentComponents(history_manager=history)
        )
    with pytest.raises(TypeError, match="AgentComponents"):
        build_agent_components(local_config(tmp_path), "test", object())
    assert AgentComponents().session_store is DEFAULT_COMPONENT



def test_injected_history_policy_controls_both_summary_input_and_retention(tmp_path):
    history = SeparateHistory(min_retain_rounds=2)
    for index in range(3):
        history.append(Message(f"user-round-{index}", "user"))
        history.append(Message(f"answer-round-{index}", "assistant"))
    # Config 与注入组件有意不同，组件的实际保留策略优先。
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path, min_retain_rounds=10),
        components=AgentComponents(history_manager=history),
    )
    summarizer = CaptureLLM()
    agent._get_summary_llm = lambda: summarizer
    summary = agent._generate_smart_summary(agent.get_history())
    requested = summarizer.messages[0][-1]["content"]
    assert "user-round-0" in requested and "answer-round-0" in requested
    assert "user-round-1" not in requested and "user-round-2" not in requested
    history.compress(summary)
    assert [m.content for m in history.get_history()[1:]] == [
        "user-round-1",
        "answer-round-1",
        "user-round-2",
        "answer-round-2",
    ]

