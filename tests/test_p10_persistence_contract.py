"""P10 持久化协议与工具 Schema 契约验证测试

包含：
1. TodoWriteTool 的清空操作（clear）在磁盘持久化与新实例恢复上的一致性
2. DevLogTool 的 action 枚举模式校验及非法 action 输入拦截
"""
from pathlib import Path
import json
from hello_agents.tools.errors import ToolErrorCode


def test_todo_clear_persists(tmp_path):
    """验证 TodoWriteTool 清空操作的持久化契约

    当执行 clear 动作后，内存状态被清空且写入磁盘，
    重新初始化一个读取同目录的 TodoWriteTool 实例时，其任务列表应为空。
    """
    from hello_agents.tools.builtin import TodoWriteTool

    # 初始化工具并写入初始待办事项
    tool = TodoWriteTool(persistence_dir=str(tmp_path))
    tool.run({"todos": [{"content": "旅行预算", "status": "pending"}]})
    assert tool.current_todos.todos

    # 执行清空动作
    tool.run({"action": "clear"})

    # 重新加载同持久化目录的新实例，验证持久化文件已正确反映清空状态
    loaded = TodoWriteTool(persistence_dir=str(tmp_path))
    assert loaded.current_todos.todos == []



def test_devlog_schema_and_invalid_action(tmp_path):
    """验证 DevLogTool 导出的参数 Schema 与非法动作校验

    DevLogTool 的 action 参数在导出的 OpenAI Schema 中必须明确约束为
    ['append', 'read', 'summary', 'clear'] 枚举，且当传入未知 action 时应
    返回 INVALID_PARAM 错误码。
    """
    from hello_agents.tools.builtin import DevLogTool
    from hello_agents.tools.errors import ToolErrorCode

    tool = DevLogTool(
        session_id="test", agent_name="test", persistence_dir=str(tmp_path)
    )
    # 验证导出的参数模式包含 action 枚举列表
    schema = tool.to_openai_schema()["function"]["parameters"]
    assert schema["properties"]["action"]["enum"] == [
        "append",
        "read",
        "summary",
        "clear",
    ]
    # 传入非法 action，验证返回标准化 INVALID_PARAM 错误
    result = tool.run({"action": "not-an-action"})
    assert result.error_info["code"] == ToolErrorCode.INVALID_PARAM

