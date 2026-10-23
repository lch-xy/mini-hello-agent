"""文件操作工具测试套件 - 覆盖标准文件读写、编辑、批量编辑及乐观锁机制验证。

本套件包含以下核心测试类：
- TestReadTool: 验证文件内容读取、行范围切片（offset/limit）、元数据获取与注册表缓存；
- TestWriteTool: 验证新文件写入、覆写自动备份、修改时间（mtime）冲突检测与原子替换；
- TestEditTool: 验证单处唯一字符串精确替换、冲突拦截、非唯一匹配错误及备份生成；
- TestMultiEditTool: 验证多步替换顺序执行、事务原子性保证与批量冲突检测；
- TestOptimisticLocking: 验证端到端乐观并发控制与多 Agent 协同场景下的写冲突防御。
"""

import pytest
import tempfile
import shutil
from pathlib import Path
import time
import os

from hello_agents.tools.builtin.file_tools import ReadTool, WriteTool, EditTool, MultiEditTool
from hello_agents.tools.registry import ToolRegistry
from hello_agents.tools.response import ToolStatus
from hello_agents.tools.errors import ToolErrorCode


@pytest.fixture
def temp_workspace():
    """创建隔离的临时工作空间目录，测试结束后自动清理。"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def registry():
    """创建全新的工具注册表实例。"""
    return ToolRegistry()


class TestReadTool:
    """ReadTool 功能测试集"""
    
    def test_read_file_success(self, temp_workspace, registry):
        """测试成功读取完整文件并返回元数据"""
        # 创建测试文件
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\n", encoding='utf-8')
        
        # 创建工具
        read_tool = ReadTool(project_root=str(temp_workspace), registry=registry)
        
        # 执行读取
        response = read_tool.run({"path": "test.txt"})
        
        # 验证响应状态、内容与行数
        assert response.status == ToolStatus.SUCCESS
        assert response.data["content"] == "Line 1\nLine 2\nLine 3\n"
        assert response.data["lines"] == 3
        # 验证返回了供乐观锁校验的时间戳与文件大小元数据
        assert "file_mtime_ms" in response.data
        assert "file_size_bytes" in response.data
    
    def test_read_file_with_offset_limit(self, temp_workspace, registry):
        """测试带 offset 和 limit 的按行切片读取"""
        # 创建测试文件
        test_file = temp_workspace / "test.txt"
        test_file.write_text("\n".join([f"Line {i}" for i in range(1, 11)]), encoding='utf-8')
        
        read_tool = ReadTool(project_root=str(temp_workspace), registry=registry)
        
        # 读取第 3-5 行
        response = read_tool.run({"path": "test.txt", "offset": 2, "limit": 3})
        
        # 验证读取行数与切片内容正确
        assert response.status == ToolStatus.SUCCESS
        assert response.data["lines"] == 3
        assert "Line 3" in response.data["content"]
        assert "Line 6" not in response.data["content"]
    
    def test_read_file_not_found(self, temp_workspace, registry):
        """测试读取不存在的文件时返回 NOT_FOUND 错误"""
        read_tool = ReadTool(project_root=str(temp_workspace), registry=registry)
        
        response = read_tool.run({"path": "nonexistent.txt"})
        
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.NOT_FOUND
    
    def test_read_metadata_caching(self, temp_workspace, registry):
        """测试文件元数据自动缓存到 ToolRegistry 中"""
        # 创建测试文件
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Test content", encoding='utf-8')
        
        read_tool = ReadTool(project_root=str(temp_workspace), registry=registry)
        
        # 执行读取
        response = read_tool.run({"path": "test.txt"})
        
        # 验证元数据已缓存至关联的注册表中
        cached_metadata = registry.get_read_metadata("test.txt")
        assert cached_metadata is not None
        assert cached_metadata["file_mtime_ms"] == response.data["file_mtime_ms"]
        assert cached_metadata["file_size_bytes"] == response.data["file_size_bytes"]


class TestWriteTool:
    """WriteTool 功能测试集"""
    
    def test_write_new_file(self, temp_workspace, registry):
        """测试创建全新文件与内容落盘"""
        write_tool = WriteTool(project_root=str(temp_workspace), registry=registry)
        
        # 执行写入全新文件
        response = write_tool.run({
            "path": "new_file.txt",
            "content": "Hello, World!"
        })
        
        # 验证返回成功状态且磁盘上文件存在且内容正确
        assert response.status == ToolStatus.SUCCESS
        assert response.data["written"] is True
        assert (temp_workspace / "new_file.txt").exists()
        assert (temp_workspace / "new_file.txt").read_text(encoding='utf-8') == "Hello, World!"
    
    def test_write_overwrite_file(self, temp_workspace, registry):
        """测试覆盖既有文件时自动触发备份机制"""
        # 创建原文件
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Original content", encoding='utf-8')
        
        write_tool = WriteTool(project_root=str(temp_workspace), registry=registry)
        
        # 覆盖写入新内容
        response = write_tool.run({
            "path": "test.txt",
            "content": "New content"
        })
        
        assert response.status == ToolStatus.SUCCESS
        assert test_file.read_text(encoding='utf-8') == "New content"
        
        # 验证备份目录存在，且生成了包含时间戳的历史备份文件
        backup_dir = temp_workspace / ".backups"
        assert backup_dir.exists()
        assert len(list(backup_dir.glob("test.txt.*.bak"))) > 0
    
    def test_write_conflict_detection(self, temp_workspace, registry):
        """测试写入时的乐观锁时间戳（mtime）冲突检测"""
        # 创建测试文件
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Original", encoding='utf-8')

        # 获取初始修改时间戳（毫秒）
        initial_mtime = int(os.path.getmtime(test_file) * 1000)

        # 等待足够时间确保 mtime 发生实际改变（跨平台兼容）
        time.sleep(0.1)
        test_file.write_text("Modified by external", encoding='utf-8')
        
        write_tool = WriteTool(project_root=str(temp_workspace), registry=registry)
        
        # 尝试写入，显式传入已过期的旧 mtime
        response = write_tool.run({
            "path": "test.txt",
            "content": "My changes",
            "file_mtime_ms": initial_mtime
        })
        
        # 验证检测到冲突并正确返回 CONFLICT 错误
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.CONFLICT
        assert "被修改" in response.error_info["message"]
    
    def test_write_atomic_operation(self, temp_workspace, registry):
        """测试原子写入与临时文件正常清理（无孤儿临时文件残留）"""
        write_tool = WriteTool(project_root=str(temp_workspace), registry=registry)
        
        # 写入文件
        response = write_tool.run({
            "path": "atomic.txt",
            "content": "Atomic content"
        })
        
        assert response.status == ToolStatus.SUCCESS
        
        # 验证原子替换后，同目录下无任何 .tmp 临时文件残留
        assert not (temp_workspace / "atomic.txt.tmp").exists()


class TestEditTool:
    """EditTool 功能测试集"""
    
    def test_edit_success(self, temp_workspace, registry):
        """测试单处唯一字符串的成功精确替换与备份"""
        # 创建测试文件
        test_file = temp_workspace / "config.py"
        test_file.write_text('API_KEY = "old_key"\nDEBUG = True\n', encoding='utf-8')
        
        edit_tool = EditTool(project_root=str(temp_workspace), registry=registry)

        # 执行单处精确替换
        response = edit_tool.run({
            "path": "config.py",
            "old_string": 'API_KEY = "old_key"',
            "new_string": 'API_KEY = "new_key"'
        })

        assert response.status == ToolStatus.SUCCESS
        assert response.data["modified"] is True
        assert 'API_KEY = "new_key"' in test_file.read_text(encoding='utf-8')

        # 验证自动创建了备份文件
        backup_dir = temp_workspace / ".backups"
        assert backup_dir.exists()

    def test_edit_conflict_detection(self, temp_workspace, registry):
        """测试编辑过程中的乐观锁冲突拦截"""
        # 创建测试文件
        test_file = temp_workspace / "config.py"
        test_file.write_text('API_KEY = "old_key"\n', encoding='utf-8')

        # 获取初始时间戳
        initial_mtime = int(os.path.getmtime(test_file) * 1000)

        # 模拟外部修改
        time.sleep(0.1)
        test_file.write_text('API_KEY = "external_change"\n', encoding='utf-8')

        edit_tool = EditTool(project_root=str(temp_workspace), registry=registry)

        # 尝试编辑并传入旧时间戳
        response = edit_tool.run({
            "path": "config.py",
            "old_string": 'API_KEY = "old_key"',
            "new_string": 'API_KEY = "my_key"',
            "file_mtime_ms": initial_mtime
        })

        # 验证由于外部改动导致版本不一致，正确拦截并报错
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.CONFLICT

    def test_edit_old_string_not_unique(self, temp_workspace, registry):
        """测试当 old_string 匹配多次时的安全性拦截"""
        # 创建测试文件，包含两个 "foo"
        test_file = temp_workspace / "test.txt"
        test_file.write_text("foo\nfoo\nbar\n", encoding='utf-8')

        edit_tool = EditTool(project_root=str(temp_workspace), registry=registry)

        response = edit_tool.run({
            "path": "test.txt",
            "old_string": "foo",
            "new_string": "baz"
        })

        # 验证拒绝非唯一匹配并返回匹配次数
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.INVALID_PARAM
        assert "2 处匹配" in response.error_info["message"]

    def test_edit_backup_creation(self, temp_workspace, registry):
        """测试编辑前生成的备份文件完整保存了历史原始内容"""
        # 创建测试文件
        test_file = temp_workspace / "important.txt"
        original_content = "Important data"
        test_file.write_text(original_content, encoding='utf-8')

        edit_tool = EditTool(project_root=str(temp_workspace), registry=registry)

        response = edit_tool.run({
            "path": "important.txt",
            "old_string": "Important data",
            "new_string": "Modified data"
        })

        assert response.status == ToolStatus.SUCCESS

        # 验证备份目录下的备份副本精确保存了原始内容
        backup_dir = temp_workspace / ".backups"
        backup_files = list(backup_dir.glob("important.txt.*.bak"))
        assert len(backup_files) > 0
        assert backup_files[0].read_text(encoding='utf-8') == original_content


class TestMultiEditTool:
    """MultiEditTool 功能测试集"""

    def test_multiedit_success(self, temp_workspace, registry):
        """测试多步替换按序批量执行成功并原子落盘"""
        # 创建测试文件
        test_file = temp_workspace / "config.py"
        test_file.write_text(
            'API_KEY = "old_key"\n'
            'DEBUG = False\n'
            'PORT = 8000\n',
            encoding='utf-8'
        )

        multiedit_tool = MultiEditTool(project_root=str(temp_workspace), registry=registry)

        # 执行包含 3 个替换项的批量修改
        response = multiedit_tool.run({
            "path": "config.py",
            "edits": [
                {"old_string": 'API_KEY = "old_key"', "new_string": 'API_KEY = "new_key"'},
                {"old_string": "DEBUG = False", "new_string": "DEBUG = True"},
                {"old_string": "PORT = 8000", "new_string": "PORT = 9000"}
            ]
        })

        assert response.status == ToolStatus.SUCCESS
        assert response.data["num_edits"] == 3

        # 验证所有替换项均成功体现于最终文件中
        content = test_file.read_text(encoding='utf-8')
        assert 'API_KEY = "new_key"' in content
        assert "DEBUG = True" in content
        assert "PORT = 9000" in content

    def test_multiedit_conflict_detection(self, temp_workspace, registry):
        """测试批量编辑在执行前预检乐观锁冲突"""
        # 创建测试文件
        test_file = temp_workspace / "config.py"
        test_file.write_text('KEY = "value"\n', encoding='utf-8')

        # 获取初始时间戳
        initial_mtime = int(os.path.getmtime(test_file) * 1000)

        # 模拟外部并发修改
        time.sleep(0.1)
        test_file.write_text('KEY = "external"\n', encoding='utf-8')

        multiedit_tool = MultiEditTool(project_root=str(temp_workspace), registry=registry)

        # 尝试执行批量替换，传入旧时间戳
        response = multiedit_tool.run({
            "path": "config.py",
            "edits": [
                {"old_string": 'KEY = "value"', "new_string": 'KEY = "new"'}
            ],
            "file_mtime_ms": initial_mtime
        })

        # 验证检测到冲突并明确提示全部替换已取消
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == ToolErrorCode.CONFLICT
        assert "所有替换已取消" in response.error_info["message"]

    def test_multiedit_atomicity(self, temp_workspace, registry):
        """测试批量编辑的事务原子性：中间步骤匹配失败导致整体回滚"""
        # 创建测试文件
        test_file = temp_workspace / "test.txt"
        original_content = "line1\nline2\nline3\n"
        test_file.write_text(original_content, encoding='utf-8')

        multiedit_tool = MultiEditTool(project_root=str(temp_workspace), registry=registry)

        # 尝试批量编辑：第 1 项合法，第 2 项中 "line" 匹配多处（line1/line2/line3）
        response = multiedit_tool.run({
            "path": "test.txt",
            "edits": [
                {"old_string": "line1", "new_string": "LINE1"},
                {"old_string": "line", "new_string": "LINE"}  # 触发多处匹配异常
            ]
        })

        # 验证返回错误状态
        assert response.status == ToolStatus.ERROR

        # 验证文件内容保持完整未修改状态（实现原子回滚）
        assert test_file.read_text(encoding='utf-8') == original_content


class TestOptimisticLocking:
    """乐观并发控制（Optimistic Concurrency Control）集成测试"""

    def test_external_modification_detection(self, temp_workspace, registry):
        """测试从 Read 元数据缓存到外部并发修改再到 Edit 的完整冲突拦截流程"""
        # 创建测试文件
        test_file = temp_workspace / "data.txt"
        test_file.write_text("Original data", encoding='utf-8')

        # 1. Read 文件并自动将 mtime 缓存到 ToolRegistry 中
        read_tool = ReadTool(project_root=str(temp_workspace), registry=registry)
        read_response = read_tool.run({"path": "data.txt"})
        assert read_response.status == ToolStatus.SUCCESS

        # 2. 外部进程并发修改文件
        time.sleep(0.1)
        test_file.write_text("External modification", encoding='utf-8')

        # 3. 尝试 Edit，使用注册表中缓存的时间戳
        edit_tool = EditTool(project_root=str(temp_workspace), registry=registry)
        cached_metadata = registry.get_read_metadata("data.txt")

        edit_response = edit_tool.run({
            "path": "data.txt",
            "old_string": "Original data",
            "new_string": "My changes",
            "file_mtime_ms": cached_metadata["file_mtime_ms"]
        })

        # 验证成功识别版本过期并拦截
        assert edit_response.status == ToolStatus.ERROR
        assert edit_response.error_info["code"] == ToolErrorCode.CONFLICT

    def test_multi_agent_collaboration(self, temp_workspace):
        """测试双 Agent 协同编辑同一文件时的先到先得与后到冲突防御场景"""
        # 创建两个独立的 registry，代表两个独立的 Agent 进程或会话
        registry_a = ToolRegistry()
        registry_b = ToolRegistry()

        # 创建共享测试文件
        test_file = temp_workspace / "shared.txt"
        test_file.write_text("Shared content", encoding='utf-8')

        # Agent A 读取文件并记录自身缓存
        read_tool_a = ReadTool(project_root=str(temp_workspace), registry=registry_a)
        read_a = read_tool_a.run({"path": "shared.txt"})
        assert read_a.status == ToolStatus.SUCCESS

        # Agent B 读取文件并记录自身缓存
        read_tool_b = ReadTool(project_root=str(temp_workspace), registry=registry_b)
        read_b = read_tool_b.run({"path": "shared.txt"})
        assert read_b.status == ToolStatus.SUCCESS

        # Agent B 率先提交编辑，更新成功
        edit_tool_b = EditTool(project_root=str(temp_workspace), registry=registry_b)
        cached_b = registry_b.get_read_metadata("shared.txt")
        edit_b = edit_tool_b.run({
            "path": "shared.txt",
            "old_string": "Shared content",
            "new_string": "Modified by B",
            "file_mtime_ms": cached_b["file_mtime_ms"]
        })
        assert edit_b.status == ToolStatus.SUCCESS

        # 显式前进文件的修改时间戳，确保版本严格递增
        next_mtime = cached_b["file_mtime_ms"] / 1000 + 1
        os.utime(test_file, (next_mtime, next_mtime))

        # Agent A 随后尝试基于其已过期的旧快照提交修改
        edit_tool_a = EditTool(project_root=str(temp_workspace), registry=registry_a)
        cached_a = registry_a.get_read_metadata("shared.txt")
        edit_a = edit_tool_a.run({
            "path": "shared.txt",
            "old_string": "Shared content",
            "new_string": "Modified by A",
            "file_mtime_ms": cached_a["file_mtime_ms"]
        })

        # 核心断言：Agent A 的过期修改被安全拒绝，保护 Agent B 的修改不被静默覆盖
        assert edit_a.status == ToolStatus.ERROR
        assert edit_a.error_info["code"] == ToolErrorCode.CONFLICT

