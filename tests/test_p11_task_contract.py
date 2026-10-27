"""P11 Task 工具参数校验与工具过滤契约测试

测试重点：
1. Task 参数前置校验契约：在触发 agent_factory 前拦截非法参数并返回 INVALID_PARAM；
2. ReadOnlyFilter 权限边界：允许知识检索与只读查询，严格排除内存与文件写操作；
3. TaskTool 策略透传契约：验证 tool_filter 与 max_steps_override 精确传递至子代理。
"""
import pytest
from hello_agents.tools.builtin import TaskTool
from hello_agents.tools.tool_filter import ReadOnlyFilter

@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_filter": "readonyl"},  # 拼写错误的策略名
        {"tool_filter": None},        # 非字符串类型的策略
        {"agent_type": []},           # 非字符串类型的 agent_type
        {"agent_type": "unknown"},    # 不受支持的 agent_type 枚举
        {"max_steps": 0},             # 步数非正整数（必须 >= 1）
        {"max_steps": True},          # 布尔值类型（虽然是 int 子类但在强校验下被拦截）
        {"task": "  "},               # 空白任务字符串
        {"task": 12},                 # 非字符串任务
    ],
)
def test_task_invalid_parameters_rejected_before_factory(overrides):
    """测试 TaskTool 在调用子代理工厂前进行前置校验拦截

    非法参数必须返回 INVALID_PARAM 错误码，且严禁触发 agent_factory 实例化（零副作用保证）。
    """
    calls = []
    task = TaskTool(lambda kind: calls.append(kind))
    result = task.run({"task": "搜索资料", **overrides})
    assert result.error_info["code"] == "INVALID_PARAM"
    assert calls == []  # 验证工厂未被调用



def test_readonly_filter_includes_evidence_without_memory_mutations():
    """测试只读过滤器：包含证据链检索工具，排除任何可修改内存或文件的写入工具"""
    reads = [
        "rag",
        "rag_search",
        "rag_read",
        "rag_read_document",
        "memory_search",
        "relation_lookup",
    ]
    writes = [
        "memory",
        "memory_add",
        "memory_revise",
        "memory_retract",
        "Write",
        "Bash",
    ]
    # 过滤混合工具列表，断言仅保留只读集合
    assert ReadOnlyFilter().filter(reads + writes) == reads



def test_task_explicit_filter_preserved():
    """测试 TaskTool 执行时正确将策略实例及步数覆盖值传递给子代理 run_as_subagent"""
    seen = []

    class Child:
        def run_as_subagent(self, **kwargs):
            seen.append(kwargs)
            return {
                "success": True,
                "summary": "完成",
                "metadata": {"steps": 1, "duration_seconds": 0},
            }

    tool = TaskTool(lambda kind: Child())
    # 1. 传入 readonly 策略与 max_steps=2
    assert (
        tool.run(
            {"task": "检索", "tool_filter": "readonly", "max_steps": 2}
        ).status.value
        == "success"
    )
    assert isinstance(seen[0]["tool_filter"], ReadOnlyFilter)
    assert seen[0]["max_steps_override"] == 2

    # 2. 传入 none 策略，验证传递给子代理为 None（无限制）
    assert tool.run({"task": "检索", "tool_filter": "none"}).status.value == "success"
    assert seen[1]["tool_filter"] is None

