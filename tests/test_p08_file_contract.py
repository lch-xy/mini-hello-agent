"""P08：文件操作底层副作用边界与并发/回滚契约测试。

系统验证文件写入、编辑和批量编辑在底层操作系统交互中的严苛边界：
1. 项目根目录外部的绝对路径访问与安全变更，验证备份路径正确解析为绝对路径；
2. 独占临时文件与备份文件命名隔离：绝不误伤或删除同目录下用户既有的 `.tmp` 临时文件，快速备份时通过唯一随机后缀避免冲突；
3. 原子写入失败时的回滚与清理契约：当原子重命名（os.replace）失败时，原文件完整保留，且严格只清理属于本次调用的临时文件；
4. 并发写入线程安全：多线程并发写入同一文件时，各自独占创建不同名字的临时文件，杜绝缓冲区竞争与写污染；
5. MultiEdit 顺序内存状态预校验：在执行任何磁盘写入或创建备份之前，按序在内存中完整校验所有替换步骤，任何一步不唯一匹配立即安全中止。
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import pytest
from hello_agents.tools.builtin import WriteTool, EditTool, MultiEditTool


@pytest.mark.parametrize("kind", ["write", "edit", "multi"])
def test_absolute_external_path_is_successful_after_allowed_mutation(tmp_path, kind):
    """验证工具处理项目根目录之外的绝对路径时，能正确执行变更并返回绝对路径格式的备份路径。"""
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("alpha", encoding="utf-8")
    # 构造 project_root 指向 root 子目录的工具实例
    tool = {"write": WriteTool, "edit": EditTool, "multi": MultiEditTool}[kind](
        project_root=str(root)
    )
    # 传入项目根目录之外的目标文件绝对路径
    parameters = {"path": str(target)}
    if kind == "write":
        parameters["content"] = "beta"
    elif kind == "edit":
        parameters.update(old_string="alpha", new_string="beta")
    else:
        parameters["edits"] = [{"old_string": "alpha", "new_string": "beta"}]
    response = tool.run(parameters)
    # 验证操作成功并写入了新内容
    assert response.status.value == "success"
    assert target.read_text(encoding="utf-8") == "beta"
    # 验证当文件在 project_root 之外时，备份路径安全返回为绝对路径，且内容为原内容
    assert Path(response.data["backup_path"]).is_absolute()
    assert Path(response.data["backup_path"]).read_text(encoding="utf-8") == "alpha"



def test_fixed_temp_is_untouched_and_rapid_backups_are_distinct(tmp_path):
    """验证独占临时文件绝不触碰既有同名 .tmp 文件，且微秒内连续快速触发备份时名称唯一互不覆盖。"""
    # 预先创建一个名为 doc.txt.tmp 的无关文件，模拟外部固定临时文件
    existing = tmp_path / "doc.txt.tmp"
    existing.write_text("unrelated", encoding="utf-8")
    target = tmp_path / "doc.txt"
    target.write_text("one", encoding="utf-8")
    tool = WriteTool(project_root=str(tmp_path))
    # 连续执行两次快速写入
    first = tool.run({"path": "doc.txt", "content": "two"})
    second = tool.run({"path": "doc.txt", "content": "three"})
    # 1. 验证用户既有的无关 doc.txt.tmp 文件完好无损，未被原子写入逻辑当作自身临时文件清理
    assert existing.read_text(encoding="utf-8") == "unrelated"
    # 2. 验证两次快速备份生成了互不相同的独立备份文件
    assert first.data["backup_path"] != second.data["backup_path"]
    # 3. 验证两次备份分别完整保留了修改前的历史版本内容（第一次备份为 "one"，第二次备份为 "two"）
    assert (tmp_path / first.data["backup_path"]).read_text(encoding="utf-8") == "one"
    assert (tmp_path / second.data["backup_path"]).read_text(encoding="utf-8") == "two"



@pytest.mark.parametrize("kind", ["write", "edit", "multi"])
def test_replace_failure_leaves_original_and_cleans_only_owned_temp(
    tmp_path, monkeypatch, kind
):
    """验证原子替换失败时的安全契约：原文件不被破坏，且只清理属于本调用的独占临时文件。"""
    import hello_agents.tools.builtin.file_tools as module

    target = tmp_path / "doc.txt"
    target.write_text("alpha", encoding="utf-8")
    unrelated = tmp_path / "doc.txt.tmp"
    unrelated.write_text("unrelated", encoding="utf-8")

    # 模拟 os.replace 发生系统级 IO 异常
    def fail(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    tool = {"write": WriteTool, "edit": EditTool, "multi": MultiEditTool}[kind](
        project_root=str(tmp_path)
    )
    args = {
        "path": "doc.txt",
        "content": "beta",
        "old_string": "alpha",
        "new_string": "beta",
        "edits": [{"old_string": "alpha", "new_string": "beta"}],
    }
    # 验证工具返回错误状态
    assert tool.run(args).status.value == "error"
    # 验证原文件内容未受任何影响
    assert target.read_text(encoding="utf-8") == "alpha"
    # 验证既有的无关临时文件保持原样
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    # 验证本次操作通过 mkstemp 创建的同目录临时文件已被 finally 逻辑安全清理
    assert list(tmp_path.glob(".doc.txt.*.tmp")) == []



def test_concurrent_writes_do_not_share_temporary_file(tmp_path, monkeypatch):
    """验证多线程并发写入同一文件时，各自使用独立的临时文件名，避免共享冲突。"""
    import hello_agents.tools.builtin.file_tools as module

    original_replace = module.os.replace
    barrier = threading.Barrier(2)
    temporary_names = []

    # 拦截 replace 操作以记录各线程使用的临时文件名，并在替换前通过栅栏同步两线程
    def coordinated_replace(source, destination):
        temporary_names.append(str(source))
        barrier.wait(timeout=3)
        return original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", coordinated_replace)
    tool = WriteTool(project_root=str(tmp_path))
    # 使用线程池并发执行写入
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda value: tool.run({"path": "doc.txt", "content": value}),
                ["a" * 1000, "b" * 1000],
            )
        )
    # Windows 上同时 ReplaceFile 可能返回共享冲突；原子替换不承诺写者串行化。
    assert any(result.status.value == "success" for result in results)
    assert all(
        result.status.value == "success"
        or result.error_info["code"] == "PERMISSION_DENIED"
        for result in results
    ), [result.to_dict() for result in results]
    # 核心断言：两个并发线程生成的临时文件路径互不相同，避免了共享临时文件写损坏
    assert len(set(temporary_names)) == 2
    # 最终目标文件内容必须是完整的 "a"*1000 或 "b"*1000 之一，杜绝半写入乱码
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") in {
        "a" * 1000,
        "b" * 1000,
    }



def test_multiedit_validates_sequential_state_before_writing(tmp_path):
    """验证 MultiEdit 的顺序内存状态预校验机制与原子性保障。

    在对磁盘文件进行任何写入或产生备份前，必须在内存副本中按序演进校验所有 edits 项：
    如果后续替换项由于前序替换而导致匹配次数不为 1，则立刻中止，绝不触碰磁盘和备份目录。
    """
    target = tmp_path / "doc.txt"
    target.write_text("alpha beta", encoding="utf-8")
    tool = MultiEditTool(project_root=str(tmp_path))
    # 1. 构造非法序列：
    # 第一步将 "alpha" -> "beta"，此时内存中变为 "beta beta"；
    # 第二步再替换 "beta" 时，发现已存在 2 处匹配，不满足唯一匹配约束
    invalid = tool.run(
        {
            "path": "doc.txt",
            "edits": [
                {"old_string": "alpha", "new_string": "beta"},
                {"old_string": "beta", "new_string": "gamma"},
            ],
        }
    )
    # 验证返回 INVALID_PARAM，指出第 1 项（即第二步）校验失败
    assert invalid.error_info["code"] == "INVALID_PARAM"
    assert invalid.context["edit_index"] == 1
    # 验证磁盘原文件未被修改
    assert target.read_text(encoding="utf-8") == "alpha beta"
    # 验证中止前没有产生任何多余的 .backups 备份目录
    assert not (tmp_path / ".backups").exists()

    # 2. 构造合法顺序序列：
    # 第一步将 "alpha" -> "delta"，内存变为 "delta beta"；
    # 第二步将 "delta" -> "gamma"，内存变为 "gamma beta"
    valid = tool.run(
        {
            "path": "doc.txt",
            "edits": [
                {"old_string": "alpha", "new_string": "delta"},
                {"old_string": "delta", "new_string": "gamma"},
            ],
        }
    )
    # 验证执行成功并原子落盘
    assert valid.status.value == "success"
    assert target.read_text(encoding="utf-8") == "gamma beta"


