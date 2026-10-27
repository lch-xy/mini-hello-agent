"""TodoWrite 进度管理工具测试"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from hello_agents.tools.builtin.todowrite_tool import TodoWriteTool, TodoItem, TodoList
from hello_agents.tools.response import ToolResponse, ToolStatus
from hello_agents.tools.errors import ToolErrorCode
from hello_agents import ToolRegistry, ReActAgent, HelloAgentsLLM, Config
from dotenv import load_dotenv
load_dotenv()

class TestTodoDataModel:
    """测试 TodoItem 与 TodoList 数据模型

    验证数据项构建、默认时间戳赋值、状态统计与子列表筛选方法。
    """

    def test_todo_item_creation(self):
        """测试 TodoItem 创建及 updated_at 自动填充为 created_at"""
        now = datetime.now().isoformat()
        item = TodoItem(
            content="实现用户认证",
            status="pending",
            created_at=now
        )

        assert item.content == "实现用户认证"
        assert item.status == "pending"
        assert item.created_at == now
        assert item.updated_at == now  # 自动填充

    def test_todo_item_with_updated_at(self):
        """测试显式传入 updated_at 时保留指定更新时间"""
        now = datetime.now().isoformat()
        later = datetime.now().isoformat()
        item = TodoItem(
            content="实现订单处理",
            status="in_progress",
            created_at=now,
            updated_at=later
        )

        assert item.updated_at == later

    def test_todo_list_stats(self):
        """测试 TodoList 统计指标计算（总数、已完成、进行中、待处理）"""
        now = datetime.now().isoformat()
        todos = TodoList(
            summary="实现电商系统",
            todos=[
                TodoItem("任务1", "completed", now),
                TodoItem("任务2", "completed", now),
                TodoItem("任务3", "in_progress", now),
                TodoItem("任务4", "pending", now),
                TodoItem("任务5", "pending", now),
            ]
        )

        stats = todos.get_stats()
        assert stats["total"] == 5
        assert stats["completed"] == 2
        assert stats["in_progress"] == 1
        assert stats["pending"] == 2

    def test_todo_list_get_in_progress(self):
        """测试获取当前进行中任务（单任务专注）"""
        now = datetime.now().isoformat()
        todos = TodoList(
            summary="测试",
            todos=[
                TodoItem("任务1", "completed", now),
                TodoItem("任务2", "in_progress", now),
                TodoItem("任务3", "pending", now),
            ]
        )

        in_progress = todos.get_in_progress()
        assert in_progress is not None
        assert in_progress.content == "任务2"
        assert in_progress.status == "in_progress"

    def test_todo_list_get_pending(self):
        """测试获取待处理任务列表并验证 limit 截断"""
        now = datetime.now().isoformat()
        todos = TodoList(
            summary="测试",
            todos=[
                TodoItem("任务1", "pending", now),
                TodoItem("任务2", "pending", now),
                TodoItem("任务3", "pending", now),
                TodoItem("任务4", "in_progress", now),
            ]
        )

        pending = todos.get_pending(limit=2)
        assert len(pending) == 2
        assert all(t.status == "pending" for t in pending)

    def test_todo_list_get_completed(self):
        """测试获取所有已完成任务列表"""
        now = datetime.now().isoformat()
        todos = TodoList(
            summary="测试",
            todos=[
                TodoItem("任务1", "completed", now),
                TodoItem("任务2", "completed", now),
                TodoItem("任务3", "pending", now),
            ]
        )

        completed = todos.get_completed()
        assert len(completed) == 2
        assert all(t.status == "completed" for t in completed)


class TestTodoWriteTool:
    """测试 TodoWriteTool 工具的核心功能与业务约束

    覆盖声明式列表创建、最多单任务进行中约束、字段完整性验证、
    列表清空、Recap 生成格式、原子文件持久化及从文件反序列化恢复。
    """

    @pytest.fixture
    def temp_dir(self):
        """创建测试隔离的临时工作目录"""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp)

    @pytest.fixture
    def tool(self, temp_dir):
        """构建绑定临时持久化路径的 TodoWriteTool 测试实例"""
        return TodoWriteTool(
            project_root=temp_dir,
            persistence_dir="todos"
        )

    def test_tool_initialization(self, tool, temp_dir):
        """测试工具基础属性初始化及持久化目录自动创建"""
        assert tool.name == "TodoWrite"
        assert tool.project_root == Path(temp_dir)
        assert tool.persistence_dir.exists()
        assert tool.current_todos.summary == ""
        assert len(tool.current_todos.todos) == 0

    def test_create_todo_list(self, tool):
        """测试声明式创建任务列表并生成标准格式 Recap"""
        response = tool.run({
            "summary": "实现用户系统",
            "todos": [
                {"content": "实现用户注册", "status": "completed"},
                {"content": "实现用户登录", "status": "in_progress"},
                {"content": "实现权限管理", "status": "pending"},
            ],
            "action": "create"
        })

        assert isinstance(response, ToolResponse)
        assert response.status == ToolStatus.SUCCESS
        assert "实现用户系统" in tool.current_todos.summary
        assert len(tool.current_todos.todos) == 3

        # 验证 Recap 摘要中包含进度比例与进行中任务
        assert "[1/3]" in response.text
        assert "进行中: 实现用户登录" in response.text

    def test_validate_single_in_progress_constraint(self, tool):
        """测试单线程专注约束：当出现多个 in_progress 任务时必须拒绝"""
        # 尝试创建多个 in_progress 任务
        response = tool.run({
            "todos": [
                {"content": "任务1", "status": "in_progress"},
                {"content": "任务2", "status": "in_progress"},
            ]
        })

        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.INVALID_PARAM
        assert "最多只能有 1 个 in_progress" in response.error_info["message"]

    def test_validate_empty_content(self, tool):
        """测试参数校验：任务 content 不能为空白字符串"""
        response = tool.run({
            "todos": [
                {"content": "", "status": "pending"},
            ]
        })

        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.INVALID_PARAM
        assert "content 不能为空" in response.error_info["message"]

    def test_validate_invalid_status(self, tool):
        """测试参数校验：任务 status 必须为有效枚举值"""
        response = tool.run({
            "todos": [
                {"content": "任务1", "status": "invalid_status"},
            ]
        })

        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.INVALID_PARAM
        assert "status 必须是" in response.error_info["message"]

    def test_clear_action(self, tool):
        """测试 action=clear 清空任务列表操作"""
        # 先创建任务列表
        tool.run({
            "todos": [
                {"content": "任务1", "status": "pending"},
            ]
        })

        # 执行清空
        response = tool.run({"action": "clear"})

        assert response.status == ToolStatus.SUCCESS
        assert "已清空" in response.text
        assert len(tool.current_todos.todos) == 0

    def test_recap_generation_all_completed(self, tool):
        """测试 Recap 格式生成：所有任务均已完成时的完成提示"""
        response = tool.run({
            "todos": [
                {"content": "任务1", "status": "completed"},
                {"content": "任务2", "status": "completed"},
            ]
        })

        assert "✅" in response.text
        assert "[2/2]" in response.text
        assert "所有任务已完成" in response.text

    def test_recap_generation_with_pending(self, tool):
        """测试 Recap 格式生成：包含待处理与进行中任务时的精炼汇总"""
        response = tool.run({
            "todos": [
                {"content": "任务1", "status": "completed"},
                {"content": "任务2", "status": "in_progress"},
                {"content": "任务3", "status": "pending"},
                {"content": "任务4", "status": "pending"},
            ]
        })

        assert "[1/4]" in response.text
        assert "进行中: 任务2" in response.text
        assert "待处理: 任务3; 任务4" in response.text

    def test_recap_generation_many_pending(self, tool):
        """测试 Recap 格式生成：待处理任务超出 3 个时的折叠截断提示"""
        todos = [{"content": f"任务{i}", "status": "pending"} for i in range(10)]
        todos[0]["status"] = "in_progress"

        response = tool.run({"todos": todos})

        assert "还有" in response.text  # 验证呈现 "还有 N 个..."

    def test_persistence(self, tool):
        """测试任务列表文件原子持久化落盘"""
        tool.run({
            "summary": "测试持久化",
            "todos": [
                {"content": "任务1", "status": "pending"},
            ]
        })

        # 检查持久化 JSON 文件是否按时间戳命名创建
        files = list(tool.persistence_dir.glob("todoList-*.json"))
        assert len(files) > 0

        # 验证持久化 JSON 内容完整性
        with open(files[0], 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["summary"] == "测试持久化"
        assert len(data["todos"]) == 1
        assert data["todos"][0]["content"] == "任务1"
        assert "stats" in data

    def test_load_todos(self, tool, temp_dir):
        """测试从已有的持久化 JSON 文件中恢复任务状态"""
        # 创建待加载的测试数据文件
        test_data = {
            "summary": "加载测试",
            "todos": [
                {
                    "content": "任务1",
                    "status": "completed",
                    "created_at": "2025-01-01T00:00:00",
                    "updated_at": "2025-01-01T00:00:00"
                }
            ],
            "created_at": "2025-01-01T00:00:00",
            "stats": {"total": 1, "completed": 1, "in_progress": 0, "pending": 0}
        }

        test_file = Path(temp_dir) / "test_load.json"
        with open(test_file, 'w', encoding='utf-8') as f:
            json.dump(test_data, f)

        # 执行从文件反序列化加载
        tool.load_todos(str(test_file))

        assert tool.current_todos.summary == "加载测试"
        assert len(tool.current_todos.todos) == 1
        assert tool.current_todos.todos[0].content == "任务1"

    def test_json_string_parameter(self, tool):
        """测试模型传入 JSON 字符串形式的 todos 时的自动反序列化解析兼容"""
        # 传入序列化为字符串的 JSON 数组
        response = tool.run({
            "todos": json.dumps([
                {"content": "任务1", "status": "pending"}
            ])
        })

        assert response.status == ToolStatus.SUCCESS
        assert len(tool.current_todos.todos) == 1


class TestAgentIntegration:
    """测试 Agent 初始化与 TodoWriteTool 自动装配集成"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时工作目录"""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp)

    def test_auto_register_todowrite_tool(self, temp_dir):
        """测试当配置启用 todowrite_enabled 时 Agent 自动注册 TodoWriteTool 到 tool_registry"""
        config = Config(
            todowrite_enabled=True,
            todowrite_persistence_dir="todos"
        )

        registry = ToolRegistry()
        llm = HelloAgentsLLM()

        # 创建 Agent（预期自动将 TodoWriteTool 注册到注册表）
        agent = ReActAgent(
            name="test_agent",
            llm=llm,
            tool_registry=registry,
            config=config
        )

        # 验证工具已注册
        tool = registry.get_tool("TodoWrite")
        assert tool is not None
        assert isinstance(tool, TodoWriteTool)

    def test_todowrite_disabled(self):
        """测试当配置禁用 todowrite_enabled 时 Agent 不注册 TodoWriteTool"""
        config = Config(todowrite_enabled=False)

        registry = ToolRegistry()
        llm = HelloAgentsLLM()

        agent = ReActAgent(
            name="test_agent",
            llm=llm,
            tool_registry=registry,
            config=config
        )

        # 验证工具未被注册
        tool = registry.get_tool("TodoWrite")
        assert tool is None

