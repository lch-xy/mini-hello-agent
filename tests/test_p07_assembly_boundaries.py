"""显式组件覆盖的三态、对象身份及宿主生命周期。"""

from unittest.mock import Mock

import pytest

from hello_agents import AgentComponents, Config, DEFAULT_COMPONENT, SimpleAgent, ToolRegistry
from hello_agents.core.components import build_agent_components
from hello_agents.core.llm_response import LLMResponse
from hello_agents.core.session_store import SessionStore
from hello_agents.skills import SkillLoader


@pytest.mark.parametrize("field", ["session_store", "skill_loader"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("choice", ["default", "none", "instance"])
def test_optional_three_states_override_config_without_truthiness(tmp_path, field, enabled, choice):
    class FalseySession(SessionStore):
        def __bool__(self):
            return False

    class FalseyLoader(SkillLoader):
        def __bool__(self):
            return False

    directory = tmp_path / "config-default"
    host_directory = tmp_path / "host-owned"
    config = Config(session_enabled=enabled if field == "session_store" else False,
                    skills_enabled=enabled if field == "skill_loader" else False,
                    session_dir=str(directory), skills_dir=str(directory))
    supplied = None
    value = DEFAULT_COMPONENT
    if choice == "none":
        value = None
    elif choice == "instance":
        supplied = FalseySession(str(host_directory)) if field == "session_store" else FalseyLoader(host_directory)
        value = supplied
    resolved = build_agent_components(config, "offline", AgentComponents(**{field: value}))
    actual = getattr(resolved, field)
    if choice == "instance":
        assert actual is supplied and not directory.exists()
    elif choice == "none" or not enabled:
        assert actual is None and not directory.exists()
    else:
        assert isinstance(actual, SessionStore if field == "session_store" else SkillLoader)
        assert directory.exists()


def test_default_components_are_fresh_and_borrowed_models_are_not_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = Mock(model="offline")
    model.invoke.return_value = LLMResponse("done", "offline")
    first, second = SimpleAgent("first", model), SimpleAgent("second", model)
    assert first.history_manager is not second.history_manager
    assert first.token_counter is not second.token_counter
    assert first.truncator is not second.truncator
    assert first.session_store is None and first.skill_loader is None
    assert first.run("task") == "done"
    model.close.assert_not_called()
    model.aclose.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_explicit_feature_flags_do_not_replace_existing_named_functions(tmp_path):
    registry = ToolRegistry()
    names = ["Skill", "Task", "TodoWrite", "DevLog"]
    for name in names:
        registry.register_function(lambda value, name=name: name, name=name)
    config = Config(skills_enabled=True, subagent_enabled=True,
                    todowrite_enabled=True, devlog_enabled=True,
                    skills_dir=str(tmp_path / "skills"))
    agent = SimpleAgent("preserve", Mock(model="offline"), config=config, tool_registry=registry)
    assert agent.tool_registry.list_tools() == names
    assert [registry.execute_tool(name, "input").data["output"] for name in names] == names


@pytest.mark.parametrize("field", ["summary_llm_model", "subagent_light_llm_model"])
def test_model_name_only_does_not_silently_reuse_other_service_credentials(field):
    model = Mock(model="main")
    with pytest.raises(ValueError, match="AgentComponents"):
        SimpleAgent("config", model, config=Config(**{field: "other-service-model"}))
