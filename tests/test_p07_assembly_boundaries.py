"""显式组件覆盖的三态、对象身份及宿主生命周期边界测试。

本模块系统性验证 P07 组件依赖注入架构的核心装配边界：
1. 三态哨兵机制（DEFAULT_COMPONENT 自动装配、None 显式关闭、实例注入显式接管）；
2. 非真值对象身份安全性（基于 `is` 判等，杜绝因 `__bool__` 返回 False 导致的假值陷阱）；
3. 默认组件隔离与宿主生命周期边界（组件全新实例化，借用外部 LLM 不越权触发 close/aclose）；
4. 工具注册表冲突保留（外部注入的同名工具不被内置特性静默覆盖）；
5. 凭据隔离安全防御（异构模型名称无法静默借用主模型凭据，强制要求 AgentComponents 显式注入）。
"""

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
    """验证可选组件（SessionStore/SkillLoader）的三态解析矩阵与非真值对象身份判定。

    覆盖 12 种装配组合（2 字段 × 2 开关状态 × 3 三态取值）：
    - choice == "default": 由配置项（enabled）决定是否自动构造默认实例；
    - choice == "none": 显式禁用组件，即使配置开启也不构造实例；
    - choice == "instance": 显式注入宿主构造的实例，且即使其 `__bool__` 返回 False，
      装配逻辑依然使用 `is` 比较识别为有效实例，绝不按布尔假值当作 None 处理，
      同时不触发默认目录的创建。
    """
    # 定义 __bool__ 返回 False 的测试桩类，模拟特殊容器或未就绪对象等非真值场景
    class FalseySession(SessionStore):
        def __bool__(self):
            return False

    class FalseyLoader(SkillLoader):
        def __bool__(self):
            return False

    directory = tmp_path / "config-default"
    host_directory = tmp_path / "host-owned"
    # 根据当前被测字段构造配置项
    config = Config(session_enabled=enabled if field == "session_store" else False,
                    skills_enabled=enabled if field == "skill_loader" else False,
                    session_dir=str(directory), skills_dir=str(directory))
    supplied = None
    value = DEFAULT_COMPONENT
    if choice == "none":
        # 显式关闭
        value = None
    elif choice == "instance":
        # 显式注入非真值实例
        supplied = FalseySession(str(host_directory)) if field == "session_store" else FalseyLoader(host_directory)
        value = supplied

    # 执行核心装配解析
    resolved = build_agent_components(config, "offline", AgentComponents(**{field: value}))
    actual = getattr(resolved, field)

    if choice == "instance":
        # 显式注入场景：必须保持对象身份同一性，且不应创建默认目录
        assert actual is supplied and not directory.exists()
    elif choice == "none" or not enabled:
        # 显式关闭或配置未开启：解析结果为 None，且不创建默认目录
        assert actual is None and not directory.exists()
    else:
        # 默认且开启场景：由装配层自动实例化默认组件并创建相应目录
        assert isinstance(actual, SessionStore if field == "session_store" else SkillLoader)
        assert directory.exists()


def test_default_components_are_fresh_and_borrowed_models_are_not_closed(tmp_path, monkeypatch):
    """验证默认组件的全新独立性（实例隔离）与外部借用模型的生命周期所有权边界。

    1. 多 Agent 实例间默认组件互不共享，状态严格隔离；
    2. Agent 借用外部传入的模型实例，运行结束后绝不越权调用模型的 close() 或 aclose()；
    3. 运行过程无意外副作用，不隐式创建磁盘残留文件。
    """
    monkeypatch.chdir(tmp_path)
    model = Mock(model="offline")
    model.invoke.return_value = LLMResponse("done", "offline")

    # 创建两个使用相同外部模型的独立智能体
    first, second = SimpleAgent("first", model), SimpleAgent("second", model)

    # 1. 验证默认内部组件实例相互独立
    assert first.history_manager is not second.history_manager
    assert first.token_counter is not second.token_counter
    assert first.truncator is not second.truncator
    # 默认配置下，未开启的可选组件均为 None
    assert first.session_store is None and first.skill_loader is None

    # 2. 执行任务并验证模型生命周期借用语义
    assert first.run("task") == "done"
    # Agent 仅是模型的借用者而非所有者，禁止关闭外部模型
    model.close.assert_not_called()
    model.aclose.assert_not_called()

    # 3. 验证无多余隐式文件副作用产生
    assert list(tmp_path.iterdir()) == []


def test_explicit_feature_flags_do_not_replace_existing_named_functions(tmp_path):
    """验证智能体特性开关不会静默覆盖工具注册表中已存在的同名自定义工具。

    宿主可能在传入注册表前已注册了同名的高优先级定制工具（如 Skill、Task 等），
    智能体装配时需遵循优先保留原则，不得强制覆盖。
    """
    registry = ToolRegistry()
    names = ["Skill", "Task", "TodoWrite", "DevLog"]
    for name in names:
        # 预先在注册表中注册宿主自定义函数
        registry.register_function(lambda value, name=name: name, name=name)

    # 即使在 Config 中开启所有对应特性的开关
    config = Config(skills_enabled=True, subagent_enabled=True,
                    todowrite_enabled=True, devlog_enabled=True,
                    skills_dir=str(tmp_path / "skills"))
    agent = SimpleAgent("preserve", Mock(model="offline"), config=config, tool_registry=registry)

    # 验证原有已注册的工具名称与执行逻辑完整保留，未被内置工具替换
    assert agent.tool_registry.list_tools() == names
    assert [registry.execute_tool(name, "input").data["output"] for name in names] == names


@pytest.mark.parametrize("field", ["summary_llm_model", "subagent_light_llm_model"])
def test_model_name_only_does_not_silently_reuse_other_service_credentials(field):
    """验证凭据隔离安全防御：异构模型名称禁止跨服务静默复用主模型凭据。

    当在配置中指定了不同的摘要或轻量级子模型名称（如 summary_llm_model）时，
    系统不能假设其可直接复用主模型的 API 密钥或凭据配置，
    必须显式抛出 ValueError，指引开发者通过 AgentComponents 注入完整的模型实例。
    """
    model = Mock(model="main")
    with pytest.raises(ValueError, match="AgentComponents"):
        SimpleAgent("config", model, config=Config(**{field: "other-service-model"}))

