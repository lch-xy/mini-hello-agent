"""Agent 状态边界、异常恢复与 Schema 哈希契约测试

测试重点：
1. 子代理后处理异常、键盘中断（KeyboardInterrupt）与预算参数错误时的父 Agent 状态严格恢复；
2. 子代理运行期间自动保存禁用与工具读取元数据缓存（read_metadata_cache）的写保护隔离；
3. 会话工具哈希（_compute_tool_schema_hash）对完整工具契约、类型、必填性、描述与函数的敏感性，且与注册顺序无关；
4. 预算超限异常（BudgetExceeded）不被吞噬或降级为统计摘要。
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hello_agents import Config
from hello_agents.core.agent import Agent
from hello_agents.core.budget import BudgetExceeded
from hello_agents.core.message import Message
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.registry import ToolRegistry


class ProbeAgent(Agent):
    """测试用探针 Agent，在运行时产生状态修改和自动保存尝试"""
    def run(self, input_text, **kwargs):
        self.add_message(Message(input_text, "user"))
        self._session_metadata["total_steps"] = 99
        self.last_run = SimpleNamespace(status="completed")
        self._messages_since_save = 19
        self._auto_save()
        return "child answer"


def agent():
    """工厂函数：构造具有初始状态与配置的父 ProbeAgent 实例"""
    value = ProbeAgent("test", SimpleNamespace(model="test"), config=Config(
        trace_enabled=False, subagent_enabled=False, session_enabled=False,
        skills_enabled=False,
    ), tool_registry=ToolRegistry())
    value.add_message(Message("parent", "user"))
    value.max_steps = 8
    value.max_tool_iterations = 7
    value.last_run = SimpleNamespace(status="completed", answer="parent answer")
    value._messages_since_save = 3
    return value


@pytest.mark.parametrize("phase", ["summary", "metadata"])
def test_subagent_restores_parent_if_postprocessing_raises(phase):
    """测试当子代理后处理（生成摘要或提取元数据）发生异常时，宿主父 Agent 的所有状态均能完整恢复"""
    value = agent()
    original = (value.tool_registry, value.last_run, value._session_metadata)
    def fail(*args):
        raise RuntimeError("postprocessing failed")
    setattr(value, "_generate_subagent_summary" if phase == "summary" else "_get_subagent_metadata", fail)
    with pytest.raises(RuntimeError, match="postprocessing failed"):
        value.run_as_subagent("child", max_steps_override=2)
    # 验证历史、注册表、运行状态与步数限制均还原
    assert [m.content for m in value.get_history()] == ["parent"]
    assert value.tool_registry is original[0] and value.last_run is original[1]
    assert value._session_metadata is original[2]
    assert value._session_metadata["total_steps"] == 0
    assert value._messages_since_save == 3
    assert (value.max_steps, value.max_tool_iterations) == (8, 7)


def test_subagent_does_not_overwrite_parent_autosave_or_tool_cache():
    """测试子代理运行期间不会触发父级的自动保存，且不会篡改父级的读元数据缓存"""
    value = agent()
    saved = []
    store = SimpleNamespace(save=lambda **kwargs: saved.append(deepcopy(kwargs)))
    value.session_store = store
    value.tool_registry.read_metadata_cache = {"parent": {"version": 1}}
    original_registry = value.tool_registry
    original_run = value.last_run
    def summary(*args):
        # 尝试在子代理内部清空工具缓存
        value.tool_registry.read_metadata_cache.clear()
        return "done"
    value._generate_subagent_summary = summary
    assert value.run_as_subagent("child")["success"]
    # 验证子代理内禁用自动保存（saved 为空）
    assert saved == []
    assert value.session_store is store and value.last_run is original_run
    assert value.tool_registry is original_registry
    # 验证父级缓存不受子代理清空影响
    assert value.tool_registry.read_metadata_cache == {"parent": {"version": 1}}


def test_subagent_restores_parent_on_keyboard_interrupt():
    """测试当子代理收到 KeyboardInterrupt 中断时，父 Agent 状态依然能安全恢复"""
    value = agent()
    registry, last_run = value.tool_registry, value.last_run

    def cancel(*args, **kwargs):
        value.add_message(Message("unfinished child", "user"))
        raise KeyboardInterrupt()

    value.run = cancel
    with pytest.raises(KeyboardInterrupt):
        value.run_as_subagent("child", max_steps_override=1)
    assert [m.content for m in value.get_history()] == ["parent"]
    assert value.tool_registry is registry and value.last_run is last_run
    assert (value.max_steps, value.max_tool_iterations) == (8, 7)


@pytest.mark.parametrize("limit", [0, -1, True, "2"])
def test_subagent_rejects_invalid_budget_before_changing_state(limit):
    """测试非法 max_steps_override 预算值在修改任何状态前被拦截"""
    value = agent()
    with pytest.raises(ValueError, match="max_steps_override"):
        value.run_as_subagent("child", max_steps_override=limit)
    assert [m.content for m in value.get_history()] == ["parent"]


class ParameterTool(Tool):
    """测试用可动态修改参数签名的工具"""
    def __init__(self, parameter_type="string", required=True):
        super().__init__("query", "description " * 20)
        self.parameter_type = parameter_type
        self.required = required

    def get_parameters(self):
        return [ToolParameter(name="value", type=self.parameter_type,
                              description="query value", required=self.required)]

    def run(self, parameters):
        return parameters


def test_session_hash_covers_complete_tool_contract_and_functions():
    """测试会话 Schema 哈希对参数类型、必填性、描述长度与注册函数的变动敏感性"""
    value = agent()
    tool = ParameterTool()
    value.tool_registry.register_tool(tool)
    baseline = value._compute_tool_schema_hash()
    # 改变参数类型
    tool.parameter_type = "integer"
    assert value._compute_tool_schema_hash() != baseline
    # 改变参数必填性
    tool.parameter_type = "string"
    tool.required = False
    assert value._compute_tool_schema_hash() != baseline
    # 改变描述文本
    tool.required = True
    tool.description += " changed beyond character 100"
    assert value._compute_tool_schema_hash() != baseline
    # 注册额外函数
    before_function = value._compute_tool_schema_hash()
    value.tool_registry.register_function(lambda x: x, name="echo")
    assert value._compute_tool_schema_hash() != before_function


def test_session_hash_ignores_tool_registration_order():
    """测试工具注册顺序不影响计算出的工具模式哈希值（哈希与注册顺序无关）"""
    first, second = agent(), agent()
    for value, names in ((first, ("alpha", "beta")), (second, ("beta", "alpha"))):
        for name in names:
            tool = ParameterTool()
            tool.name = name
            value.tool_registry.register_tool(tool)
    assert first._compute_tool_schema_hash() == second._compute_tool_schema_hash()


def test_budget_exhaustion_is_not_downgraded_to_statistical_summary():
    """测试摘要生成过程中抛出的 BudgetExceeded 异常被向上透传，绝不降级吞噬"""
    value = agent()
    for _ in range(10):
        value.add_message(Message("next", "user"))
    def exhausted(*args, **kwargs):
        raise BudgetExceeded("task exhausted")
    value._get_summary_llm = lambda: SimpleNamespace(invoke=exhausted)
    with pytest.raises(BudgetExceeded):
        value._generate_smart_summary(value.get_history())
