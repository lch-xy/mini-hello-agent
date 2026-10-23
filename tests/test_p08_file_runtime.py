"""真实文件副作用与 Agent/Policy 运行时集成测试。

本模块在受控临时目录中验证 P08 文件工具在智能体运行时的核心行为：
1. Agent 驱动的 Read -> Edit 端到端工具调用链、UTF-8 字符修改字节差值与备份回执；
2. 乐观锁按需生效机制：元数据缓存不会静默强加写锁，仅在显式传入 file_mtime_ms 时拦截冲突；
3. 工具安全策略（AllowlistPolicy）在文件系统发生任何变更前执行鉴权与阻断；
4. 原子覆盖写入过程中原有文件权限（file mode / chmod）的继承与保留。
"""

import json
import os
from pathlib import Path

from hello_agents import SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.tools import AllowlistPolicy
from hello_agents.tools.builtin import EditTool, ReadTool, WriteTool


def test_runtime_reads_then_edits_with_explicit_mtime_and_utf8_receipt(tmp_path):
    """验证智能体运行时 Read -> Edit 完整调用链、显式乐观锁传递与 UTF-8 变动回执。"""
    path = tmp_path / "note.txt"
    path.write_text("old", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_tool(ReadTool(str(tmp_path), registry=registry))
    registry.register_tool(EditTool(str(tmp_path), registry=registry))

    class Model:
        model = "offline"

        def __init__(self):
            self.calls = 0

        def invoke_with_tools(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                # 第 1 步：模型决定先读取文件内容
                call = ToolCall("read", "Read", '{"path":"note.txt"}')
            elif self.calls == 2:
                # 第 2 步：验证读取结果回执，并提取缓存的 mtime 构造带乐观锁的 Edit 调用
                receipt = json.loads(messages[-1]["content"])["data"]
                assert receipt["content"] == "old" and receipt["file_size_bytes"] == 3
                assert registry.get_read_metadata("note.txt")["file_mtime_ms"] == receipt["file_mtime_ms"]
                call = ToolCall("edit", "Edit", json.dumps({
                    "path": "note.txt", "old_string": "old", "new_string": "世界",
                    "file_mtime_ms": receipt["file_mtime_ms"],
                }))
            else:
                # 第 3 步：验证编辑完成回执（UTF-8 "世界" 占 6 字节，原 "old" 占 3 字节，净增 3 字节）及备份文件
                receipt = json.loads(messages[-1]["content"])["data"]
                assert receipt["changed_bytes"] == len("世界".encode("utf-8")) - 3
                assert (tmp_path / receipt["backup_path"]).read_text(encoding="utf-8") == "old"
                return LLMToolResponse("已编辑", [], self.model)
            return LLMToolResponse(None, [call], self.model)

    agent = SimpleAgent("files", Model(), tool_registry=registry)
    # 验证智能体完整任务运行输出
    assert agent.run("read then edit") == "已编辑"
    # 验证目标文件已被成功替换为新内容
    assert path.read_text(encoding="utf-8") == "世界"
    # 验证模型交互轮次（3 次）与工具执行次数（2 次）
    assert agent.last_run.model_calls == 3 and agent.last_run.tool_calls == 2
    # 验证历史消息中工具调用的 ID 正确映射
    assert [m.metadata["model_message"]["tool_call_id"] for m in agent.get_history() if m.role == "tool"] == ["read", "edit"]


def test_read_cache_does_not_silently_enforce_write_lock_without_parameter(tmp_path):
    """验证乐观锁为显式按需生效：未显式传入 file_mtime_ms 时不强制阻断写入。

    1. 读取时会将元数据记录进注册表缓存；
    2. 外部发生修改后，若 Write 传入了旧的 file_mtime_ms，则严格校验并拒绝（抛出 CONFLICT）；
    3. 若 Write 未显式传入 file_mtime_ms 参数，则默认作为无锁覆盖操作顺利执行。
    """
    path = tmp_path / "note.txt"
    path.write_text("original", encoding="utf-8")
    registry = ToolRegistry()
    read, write = ReadTool(str(tmp_path), registry=registry), WriteTool(str(tmp_path), registry=registry)
    # 1. 读文件获取并记录旧时间戳
    old_mtime = read.run({"path": "note.txt"}).data["file_mtime_ms"]
    # 2. 模拟外部进程修改了文件内容和修改时间
    path.write_text("external", encoding="utf-8")
    changed_seconds = (old_mtime + 2000) / 1000
    os.utime(path, (changed_seconds, changed_seconds))
    # 3. 显式传入旧 mtime 时，拒绝写入并触发冲突错误
    rejected = write.run({"path": "note.txt", "content": "protected", "file_mtime_ms": old_mtime})
    assert rejected.error_info["code"] == "CONFLICT"
    assert path.read_text(encoding="utf-8") == "external"
    # 4. 未传入 file_mtime_ms 时，视为强制覆写，顺利执行
    allowed = write.run({"path": "note.txt", "content": "unprotected"})
    assert allowed.status.value == "success"
    assert path.read_text(encoding="utf-8") == "unprotected"


def test_registry_policy_denies_write_before_any_filesystem_mutation(tmp_path):
    """验证安全策略在触碰文件系统前即时生效（无副作用防御）。

    当安全策略仅允许执行 Read 时，调用 Write 会直接在权限校验层被拒绝，
    绝不会提前在磁盘上创建多级父目录或临时文件。
    """
    registry = ToolRegistry(policy=AllowlistPolicy({"Read"}))
    registry.register_tool(WriteTool(str(tmp_path)))
    # 尝试写入深层嵌套路径
    result = registry.execute_tool("Write", {"path": "nested/new.txt", "content": "data"})
    # 验证权限拦截
    assert result.error_info["code"] == "PERMISSION_DENIED"
    # 验证临时工作空间完全保持干净，未创建 nested/ 目录
    assert list(tmp_path.iterdir()) == []


def test_atomic_replacement_preserves_existing_file_mode(tmp_path):
    """验证原子覆盖替换能够完整保留目标文件既有的权限位（如 0o750 可执行权限）。"""
    path = tmp_path / "run.sh"
    path.write_text("old", encoding="utf-8")
    # 设定自定义权限（读、写、执行权限）
    path.chmod(0o750)
    # 执行原子覆写
    response = WriteTool(str(tmp_path)).run({"path": "run.sh", "content": "new"})
    assert response.status.value == "success"
    # 验证覆写后文件权限保持 0o750，未被临时文件的默认 umask 覆盖
    assert Path(path).stat().st_mode & 0o777 == 0o750

