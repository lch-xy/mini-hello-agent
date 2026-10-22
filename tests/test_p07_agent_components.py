"""P07 组件依赖注入（AgentComponents）综合测试套件。

系统验证组件依赖注入框架在各类核心智能体中的装配行为与协议约定：
1. 鸭子类型（Duck Typing）协议支持：组件仅需实现相应协议方法，无需继承特定框架基类；
2. 多智能体构造器透传：所有智能体子类（SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent）均无缝支持组件注入；
3. 显式禁用可选组件：传入 None 可精确关闭 SessionStore 与 SkillLoader，即使 Config 开启默认特性也不生效；
4. 工具自动注册选择性关闭：注入 SkillLoader 时支持通过 `skills_auto_register=False` 选择不注册 Skill 工具；
5. 防御式协议校验：非法组件在实例化初期即被拒绝，并在创建任何磁盘目录前触发异常，杜绝环境副作用；
6. 注入策略优先权：外部注入的历史管理器的保留策略（min_retain_rounds）绝对优先于 Config 内部配置。
"""
from pathlib import Path
import pytest
from hello_agents import AgentComponents, Config, DEFAULT_COMPONENT, Message, PlanSolveAgent, ReActAgent, ReflectionAgent, SimpleAgent, ToolRegistry
from hello_agents.context import HistoryManager, ObservationTruncator, TokenCounter
from hello_agents.core.components import build_agent_components
from hello_agents.core.llm_response import LLMResponse
from hello_agents.skills import SkillLoader


class CaptureLLM:
    """测试用 LLM 替身桩。

    用于捕获智能体发送的所有输入提示词消息（messages），并返回固定的测试响应，
    以便断言智能体在对话摘要或规划过程中的 LLM 调用上下文。
    """
    model = "test-component-capture"
    provider = "test"

    def __init__(self):
        self.messages = []

    def invoke(self, messages, **kwargs):
        self.messages.append(messages)
        return LLMResponse("本地替身回复", self.model)



def local_config(tmp_path, **overrides):
    """构造用于单元测试的轻量级纯内存/临时目录 Config 对象。

    默认禁用所有具备外部副作用或自动加载行为的开关（如链路追踪、持久化等），
    并将所有目录指向 tmp_path 隔离目录，防止测试污染宿主环境。
    """
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
    """通过协议组合实现的历史管理器，验证不继承 HistoryManager 基类的鸭子类型兼容性。"""

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
    """自定义 Token 计数器实现，验证无需继承 TokenCounter 的协议适配。

    包含自定义宿主标记 host_marker 与计数状态追踪 clear_calls。
    """
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
    """自定义观察结果截断器实现，验证无需继承 ObservationTruncator 的协议适配。"""
    def truncate(self, tool_name, output):
        return {"truncated": False, "preview": f"custom:{output}"}



@pytest.mark.parametrize(
    "agent_class", [SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent]
)
def test_all_agent_constructors_propagate_components(tmp_path, agent_class):
    """验证所有智能体类型的构造函数均正确透传并支持非继承型的自定义组件实现。"""
    # 构造非继承协议组件实例
    history = SeparateHistory()
    history.append(Message("预载任务", "user"))
    counter = CountingCounter()
    truncator = CustomTruncator()
    # 验证智能体接受组件包并成功初始化
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
    # 验证组件直接绑定且对象身份完全一致
    assert agent.history_manager is history
    assert agent.token_counter is counter and agent.truncator is truncator
    assert agent.components.history_manager is history
    # 验证计数器根据自定义实现计算初始历史 token 数（1 条消息 * 13）
    assert agent._history_token_count == 13
    # 追加一条消息后 token 数按自定义逻辑递增（2 条消息 * 13 = 26）
    agent.add_message(Message("后续任务", "user"))
    assert agent._history_token_count == 26
    # 验证自定义计数器的宿主标记完好无损，且未被意外清除缓存
    assert counter.host_marker == {"preserve": True} and counter.clear_calls == 0
    # 验证简易摘要中保留了正确的保留轮次信息（min_retain_rounds=2）
    assert "保留最近 2 轮" in agent._generate_simple_summary(history.get_history())



def test_none_disables_optional_components_despite_enabled_defaults(tmp_path):
    """验证传入 None 能强制禁用可选组件，即便全局配置显式开启了这些特性。"""
    # 配置中开启会话持久化与技能库特性
    config = local_config(tmp_path, session_enabled=True, skills_enabled=True)
    # 但在组件容器中显式指定 session_store=None, skill_loader=None
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=config,
        tool_registry=ToolRegistry(),
        components=AgentComponents(session_store=None, skill_loader=None),
    )
    # 验证组件实例被精确禁用
    assert agent.session_store is None and agent.skill_loader is None
    # 验证 Skill 工具未被注册进工具注册表
    assert "Skill" not in agent.tool_registry.list_tools()
    # 验证未在磁盘上创建相应的默认持久化目录
    assert (
        not Path(config.session_dir).exists() and not Path(config.skills_dir).exists()
    )
    # 验证调用被禁用的会话保存方法时明确报错抛出 RuntimeError
    with pytest.raises(RuntimeError, match="AgentComponents"):
        agent.save_session("disabled")
    # 验证列表查询安全降级返回空列表
    assert agent.list_sessions() == []



def test_explicit_loader_can_opt_out_of_tool_registration(tmp_path):
    """验证显式注入 SkillLoader 时，可通过配置关闭向 ToolRegistry 自动注册 Skill 工具。"""
    loader = SkillLoader(tmp_path / "supplied-skills")
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path, skills_auto_register=False),
        tool_registry=ToolRegistry(),
        components=AgentComponents(skill_loader=loader),
    )
    # 验证 SkillLoader 成功注入
    assert agent.skill_loader is loader
    # 验证因 skills_auto_register=False，工具注册表中不包含内置 Skill 工具
    assert "Skill" not in agent.tool_registry.list_tools()



@pytest.mark.parametrize(
    "field",
    ["history_manager", "token_counter", "truncator", "session_store", "skill_loader"],
)
def test_invalid_component_fails_before_creating_default_directories(tmp_path, field):
    """验证类型/协议校验发生在创建任何默认文件系统目录之前（无副作用防护）。"""
    config = local_config(tmp_path, session_enabled=True, skills_enabled=True)
    # 传入未实现对应协议的非法对象（object()）
    components = AgentComponents(**{field: object()})
    # 预期立即抛出 TypeError
    with pytest.raises(TypeError, match=field):
        SimpleAgent("test", CaptureLLM(), config=config, components=components)
    # 验证在此失败场景下，未发生任何磁盘目录创建的副作用
    assert (
        not Path(config.session_dir).exists() and not Path(config.skills_dir).exists()
    )



def test_invalid_history_policy_and_bundle_rejected(tmp_path):
    """验证非法历史保留参数校验与组件容器参数类型防护。"""
    # 1. 验证 min_retain_rounds <= 0 时抛出 ValueError
    history = SeparateHistory(min_retain_rounds=0)
    with pytest.raises(ValueError, match="min_retain_rounds"):
        build_agent_components(
            local_config(tmp_path), "test", AgentComponents(history_manager=history)
        )
    # 2. 验证 components 参数非 AgentComponents 类型时抛出 TypeError
    with pytest.raises(TypeError, match="AgentComponents"):
        build_agent_components(local_config(tmp_path), "test", object())
    # 3. 验证默认初始化时可选组件保持 DEFAULT_COMPONENT 哨兵
    assert AgentComponents().session_store is DEFAULT_COMPONENT



def test_injected_history_policy_controls_both_summary_input_and_retention(tmp_path):
    """验证注入组件自带的保留策略（min_retain_rounds）绝对优先于 Config 配置。"""
    # 注入的管理器配置为保留最近 2 轮
    history = SeparateHistory(min_retain_rounds=2)
    # 构造 3 轮完整对话（共 6 条消息）
    for index in range(3):
        history.append(Message(f"user-round-{index}", "user"))
        history.append(Message(f"answer-round-{index}", "assistant"))
    # Config 与注入组件有意不同，配置中设定为保留 10 轮，组件的实际保留策略（2 轮）应优先生效
    agent = SimpleAgent(
        "test",
        CaptureLLM(),
        config=local_config(tmp_path, min_retain_rounds=10),
        components=AgentComponents(history_manager=history),
    )
    # 使用替身 LLM 捕获摘要请求
    summarizer = CaptureLLM()
    agent._get_summary_llm = lambda: summarizer
    # 生成智能摘要
    summary = agent._generate_smart_summary(agent.get_history())
    requested = summarizer.messages[0][-1]["content"]
    # 验证发送给摘要模型的仅为第 0 轮内容（因为第 1、2 轮作为最近 2 轮被保留，不参与摘要压缩）
    assert "user-round-0" in requested and "answer-round-0" in requested
    assert "user-round-1" not in requested and "user-round-2" not in requested
    # 执行压缩后，验证保留的消息正是最近 2 轮（round 1 与 round 2，加上第 0 项 summary）
    history.compress(summary)
    assert [m.content for m in history.get_history()[1:]] == [
        "user-round-1",
        "answer-round-1",
        "user-round-2",
        "answer-round-2",
    ]


