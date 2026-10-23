"""文件操作工具集 - 支持乐观并发控制（OCC）与原子写入保证

提供标准的企业级文件读写编辑能力：
- ReadTool: 读取文件内容（支持按行切片）、列出目录内容、自动获取并缓存元数据（mtime、size）
- WriteTool: 创建或覆盖文件、乐观锁冲突检测、自动备份、基于同目录临时文件的原子替换
- EditTool: 单处唯一字符串精确替换、乐观锁检测、原文件自动备份、原子覆盖
- MultiEditTool: 批量字符串顺序替换、全事务原子性保证（任意项失败即整体回滚）、乐观锁检测

核心设计与安全原则：
1. 乐观并发控制（OCC）：读操作可向 ToolRegistry 缓存时间戳（file_mtime_ms），写操作可按需校验，防止多 Agent 或外部并发写冲突；
2. 强一致原子写入：利用 tempfile.mkstemp 在目标文件同级目录下创建独占临时文件，写入并执行 os.fsync 强制落盘，继承原文件 POSIX 权限（shutil.copymode），最终通过系统级原子操作 os.replace 替换，绝不留下损坏的半写文件；
3. 临时文件生命周期隔离：仅清理本次操作独占创建的临时文件，绝不误删同目录下已存在的其他 .tmp 文件；
4. 历史版本自动备份：在目标所在目录的 .backups/ 子目录下为覆写或编辑操作创建带时间戳的备份副本；
5. 唯一匹配约束：Edit 与 MultiEdit 严格要求被替换文本在当前作用域内仅出现一次，杜绝歧义替换。

使用示例：
```python
from hello_agents import ToolRegistry
from hello_agents.tools.builtin import ReadTool, WriteTool, EditTool, MultiEditTool

registry = ToolRegistry()
registry.register_tool(ReadTool(project_root="./", registry=registry))
registry.register_tool(WriteTool(project_root="./", registry=registry))
registry.register_tool(EditTool(project_root="./", registry=registry))
registry.register_tool(MultiEditTool(project_root="./", registry=registry))
```
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING
from pathlib import Path
import os
import shutil
import tempfile
from datetime import datetime

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode

if TYPE_CHECKING:
    from ..registry import ToolRegistry


def _atomic_write(full_path: Path, content: str) -> None:
    """原子化写入文件：独占创建同目录临时文件，成功后通过系统级原子替换目标文件。

    实现细节：
    1. 在 full_path.parent 目录下使用 tempfile.mkstemp 创建独占临时文件，确保临时文件与目标文件在同一文件系统，
       使得底层 os.replace 可以作为原子重命名执行；
    2. 写入数据并通过 flush() 与 os.fsync() 强制刷盘，确保意外断电或系统故障时数据完整性；
    3. 若原目标文件已存在，调用 shutil.copymode 将原文件的文件模式（chmod 权限位）拷贝至临时文件；
    4. 调用 os.replace 将临时文件原子覆盖替换目标文件；
    5. finally 块中仅清理本次由 mkstemp 创建的具体临时文件（temporary.unlink），绝不触碰同目录下其他无关临时文件。
    """
    fd, name = tempfile.mkstemp(
        prefix=f".{full_path.name}.", suffix=".tmp", dir=full_path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if full_path.exists():
            shutil.copymode(full_path, temporary)
        os.replace(temporary, full_path)
    finally:
        # name 来自 mkstemp 的独占创建，不触碰目标名+.tmp 等其他文件。
        temporary.unlink(missing_ok=True)


def _backup_file(full_path: Path) -> Path:
    """在文件所在目录的 .backups 隐藏子目录下创建带时间戳与独占随机后缀的备份副本。

    使用 shutil.copy2 完整保留文件的元数据属性（如访问时间与修改时间）。
    """
    backup_dir = full_path.parent / ".backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fd, name = tempfile.mkstemp(
        prefix=f"{full_path.name}.{timestamp}.", suffix=".bak", dir=backup_dir
    )
    os.close(fd)
    backup = Path(name)
    shutil.copy2(full_path, backup)
    return backup


def _display_backup(backup: Optional[Path], working_dir: Path) -> Optional[str]:
    """格式化备份文件的输出路径。

    若备份文件位于当前工作目录内，返回相对路径；若位于工作目录外，则安全回退返回绝对路径。
    """
    if backup is None:
        return None
    try:
        return str(backup.relative_to(working_dir))
    except ValueError:
        return str(backup.resolve())


class ReadTool(Tool):
    """文件与目录读取工具。

    功能：
    - 读取文件内容（支持 offset 起始行与 limit 最大行数分页）
    - 列出目录内容（当 path 为目录时，显示包含大小、时间戳的结构化列表）
    - 自动获取文件元数据（mtime 修改时间戳毫秒数, size 字节数）
    - 自动将元数据缓存至 ToolRegistry（供后续写操作进行乐观锁版本校验）
    - 跨平台兼容（规范化 Windows 与 POSIX 的路径分隔符）

    参数契约：
    - path: 文件或目录路径（相对 working_dir 或绝对路径）
    - offset: 起始行号（可选，从 0 开始，仅读取文件时生效）
    - limit: 最大读取行数（可选，默认 2000 行，仅读取文件时生效）
    """

    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional["ToolRegistry"] = None,
    ):
        super().__init__(
            name="Read",
            description="读取文件内容或列出目录内容，支持行号范围和元数据缓存",
            expandable=False,
        )
        self.project_root = Path(project_root).resolve()
        self.working_dir = (
            Path(working_dir).resolve() if working_dir else self.project_root
        )
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        """获取工具参数规范列表"""
        return [
            ToolParameter(
                name="path",
                type="string",
                description="要读取的文件路径或目录路径（相对项目根目录）。如果是目录，将列出目录内容",
                required=True,
            ),
            ToolParameter(
                name="offset",
                type="integer",
                description="起始行号（从 0 开始，仅读取文件时有效）",
                required=False,
                default=0,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="最大行数（仅读取文件时有效）",
                required=False,
                default=2000,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行文件读取或目录内容展示。

        执行步骤：
        1. 校验必填参数 path；
        2. 解析目标绝对路径并检查存在性；
        3. 若目标为目录，转发至 _list_directory 列出结构化条目；
        4. 若目标为文件，以 UTF-8 编码读取全部行，并应用 offset/limit 进行行切片；
        5. 读取文件 mtime 与 size 元数据，自动向绑定的 ToolRegistry 缓存，供乐观锁使用；
        6. 返回包含内容、切片行数、总行数与元数据的 ToolResponse。
        """
        path = parameters.get("path")
        offset = parameters.get("offset", 0)
        limit = parameters.get("limit", 2000)

        if not path:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="缺少必需参数: path"
            )

        try:
            # 解析目标路径（支持相对 working_dir 或绝对路径）
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return ToolResponse.error(
                    code=ToolErrorCode.NOT_FOUND, message=f"路径 '{path}' 不存在"
                )

            # 如果是目录，转入目录遍历逻辑
            if full_path.is_dir():
                return self._list_directory(path, full_path)

            # 读取普通文本文件内容
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 应用 offset（起始行偏移）和 limit（最大读取行数）
            total_lines = len(lines)
            if offset > 0:
                lines = lines[offset:]
            if limit > 0:
                lines = lines[:limit]

            content = "".join(lines)

            # 获取文件底层元数据（毫秒修改时间戳和字节大小，用于乐观锁校验）
            mtime = os.path.getmtime(full_path)
            size = os.path.getsize(full_path)
            file_mtime_ms = int(mtime * 1000)
            file_size_bytes = size

            # 若注册了 ToolRegistry，将读取时元数据自动缓存，供后续写操作按需校验
            if self.registry:
                self.registry.cache_read_metadata(
                    path,
                    {
                        "file_mtime_ms": file_mtime_ms,
                        "file_size_bytes": file_size_bytes,
                    },
                )

            return ToolResponse.success(
                text=f"读取 {len(lines)} 行（共 {total_lines} 行，{file_size_bytes} 字节）",
                data={
                    "content": content,
                    "lines": len(lines),
                    "total_lines": total_lines,
                    "file_mtime_ms": file_mtime_ms,
                    "file_size_bytes": file_size_bytes,
                    "offset": offset,
                    "limit": limit,
                },
            )

        except PermissionError:
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED, message=f"无权限读取 '{path}'"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR, message=f"读取文件失败：{str(e)}"
            )

    def _list_directory(self, path: str, full_path: Path) -> ToolResponse:
        """列出目录内容并格式化为友好的展示视图（跨平台兼容）。"""
        try:
            entries = []
            total_files = 0
            total_dirs = 0

            # 遍历目录下所有直接子项，排序策略：目录优先，同类按名称不区分大小写排序
            for entry in sorted(
                full_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
            ):
                try:
                    # 获取条目类型与名称
                    is_dir = entry.is_dir()
                    name = entry.name

                    # 格式化文件大小与统计
                    if is_dir:
                        size_str = "<DIR>"
                        total_dirs += 1
                    else:
                        try:
                            size = entry.stat().st_size
                            size_str = self._format_size(size)
                            total_files += 1
                        except:
                            size_str = "?"

                    # 格式化修改时间
                    try:
                        mtime = entry.stat().st_mtime
                        mtime_str = self._format_time(mtime)
                    except:
                        mtime_str = "?"

                    # 统一使用正斜杠路径分隔符，保障跨平台统一性
                    relative_path = str(entry.relative_to(self.project_root)).replace(
                        os.sep, "/"
                    )

                    entries.append(
                        {
                            "name": name,
                            "type": "directory" if is_dir else "file",
                            "size": size_str,
                            "mtime": mtime_str,
                            "path": relative_path,
                        }
                    )
                except Exception as e:
                    # 跳过权限受限或无法访问的孤立条目
                    continue

            # 构建纯文本摘要
            if not entries:
                text = f"目录 '{path}' 为空"
            else:
                lines = [
                    f"目录 '{path}' 包含 {total_files} 个文件，{total_dirs} 个目录：\n"
                ]
                for entry in entries:
                    type_icon = "📁" if entry["type"] == "directory" else "📄"
                    lines.append(
                        f"{type_icon} {entry['name']:<40} {entry['size']:>10} {entry['mtime']}"
                    )
                text = "\n".join(lines)

            return ToolResponse.success(
                text=text,
                data={
                    "path": path,
                    "entries": entries,
                    "total_files": total_files,
                    "total_dirs": total_dirs,
                    "is_directory": True,
                },
            )
        except PermissionError:
            return ToolResponse.error(
                code=ToolErrorCode.ACCESS_DENIED, message=f"无权访问目录 '{path}'"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR, message=f"列出目录失败：{str(e)}"
            )

    def _format_size(self, size: int) -> str:
        """将字节数格式化为人类可读的字符串（B/KB/MB/GB/TB）。"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"

    def _format_time(self, timestamp: float) -> str:
        """将秒级浮点时间戳格式化为本地标准日期时间字符串。"""
        from datetime import datetime

        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _resolve_path(self, path: str) -> Path:
        """解析路径字符串为 Path 对象（兼容 Windows/POSIX，绝对路径直用，相对路径基于 working_dir）。"""
        # 统一路径分隔符：将反斜杠转换为正斜杠
        path = path.replace("\\", "/")

        # 如果是绝对路径，直接使用
        if os.path.isabs(path):
            return Path(path)

        # 否则相对于 working_dir
        return self.working_dir / path


class WriteTool(Tool):
    """文件写入工具。

    功能：
    - 创建新文件或覆盖既有文件
    - 乐观锁冲突检测（当文件已存在且传入 file_mtime_ms 时）
    - 覆盖前自动备份原文件至 .backups/ 目录
    - 强一致原子写入（同目录独占临时文件 + fsync + os.replace），保留既有文件权限

    参数契约：
    - path: 目标文件路径（相对 working_dir 或绝对路径）
    - content: 要写入的文本内容（必须为字符串）
    - file_mtime_ms: 上次读取时缓存的文件毫秒修改时间（可选，用于并发冲突检测）
    """

    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional["ToolRegistry"] = None,
    ):
        super().__init__(
            name="Write",
            description="创建或覆盖文件，支持冲突检测和原子写入",
            expandable=False,
        )
        self.project_root = Path(project_root).resolve()
        self.working_dir = (
            Path(working_dir).resolve() if working_dir else self.project_root
        )
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        """获取写入工具参数规范"""
        return [
            ToolParameter(
                name="path",
                type="string",
                description="文件路径（相对项目根目录）",
                required=True,
            ),
            ToolParameter(
                name="content", type="string", description="文件内容", required=True
            ),
            ToolParameter(
                name="file_mtime_ms",
                type="integer",
                description="缓存的文件修改时间（用于冲突检测）",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行文件写入与原子落盘。

        执行步骤：
        1. 参数完整性与类型校验（path 必须存在且 content 必须为字符串）；
        2. 解析目标绝对路径；
        3. 若文件已存在：
           a. 乐观锁检测：若调用方显式提供了 cached_mtime，比对当前文件的 mtime_ms，不一致则返回 CONFLICT 错误；
           b. 创建原文件备份至 .backups/ 目录；
        4. 若文件不存在：
           递归创建父目录（parents=True, exist_ok=True）；
        5. 调用 _atomic_write 进行同目录临时文件落盘与原子重命名替换；
        6. 计算写入文本的 UTF-8 编码字节数，返回成功回执与备份路径。
        """
        path = parameters.get("path")
        content = parameters.get("content")
        cached_mtime = parameters.get("file_mtime_ms")

        if not path:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="缺少必需参数: path"
            )

        if not isinstance(content, str):
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="content 必须是字符串"
            )

        try:
            # 解析路径
            full_path = self._resolve_path(path)
            backup_path = None

            # 检查文件是否已存在
            if full_path.exists():
                # 获取磁盘上当前文件真实的修改时间戳（毫秒）
                current_mtime = os.path.getmtime(full_path)
                current_mtime_ms = int(current_mtime * 1000)

                # 乐观锁校验：调用方显式传入了时间戳时，才比对是否发生版本漂移
                if cached_mtime is not None:
                    if current_mtime_ms != cached_mtime:
                        return ToolResponse.error(
                            code=ToolErrorCode.CONFLICT,
                            message=f"文件自上次读取后被修改。当前 mtime={current_mtime_ms}, 缓存 mtime={cached_mtime}",
                            context={
                                "current_mtime_ms": current_mtime_ms,
                                "cached_mtime_ms": cached_mtime,
                            },
                        )

                # 覆写前自动备份原文件
                backup_path = self._backup_file(full_path)
            else:
                # 新建文件场景：确保其多级父目录均已递归创建
                full_path.parent.mkdir(parents=True, exist_ok=True)

            # 原子写入（同目录 mkstemp 临时文件 -> fsync 刷盘 -> 权限同步 -> rename 原子覆盖）
            _atomic_write(full_path, content)

            size_bytes = len(content.encode("utf-8"))

            return ToolResponse.success(
                text=f"成功写入 {path} ({size_bytes} 字节)",
                data={
                    "written": True,
                    "size_bytes": size_bytes,
                    "backup_path": _display_backup(backup_path, self.working_dir),
                },
            )

        except PermissionError:
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED, message=f"无权限写入 '{path}'"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR, message=f"写入文件失败：{str(e)}"
            )

    def _backup_file(self, full_path: Path) -> Path:
        """调用全局备份函数，在 full_path 所在目录下创建独立历史备份。"""
        return _backup_file(full_path)

    def _resolve_path(self, path: str) -> Path:
        """解析相对路径（若为绝对路径则直接使用，否则拼接 working_dir）。"""
        if os.path.isabs(path):
            return Path(path)
        return self.working_dir / path


class EditTool(Tool):
    """文件编辑工具（单处精确替换）。

    功能：
    - 精确单处字符串替换（old_string 必须在当前文件中严格唯一匹配）
    - 乐观锁冲突检测（可选参数 file_mtime_ms）
    - 修改前自动备份原文件至 .backups/ 目录
    - 原子覆盖写入新文件内容并保持文件既有权限

    参数契约：
    - path: 目标文件路径（相对 working_dir 或绝对路径）
    - old_string: 待替换的原始字符串（必须唯一匹配）
    - new_string: 替换后的目标字符串
    - file_mtime_ms: 缓存的毫秒修改时间戳（可选，用于冲突检测）
    """

    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional["ToolRegistry"] = None,
    ):
        super().__init__(
            name="Edit",
            description="精确替换文件内容，支持冲突检测和自动备份",
            expandable=False,
        )
        self.project_root = Path(project_root).resolve()
        self.working_dir = (
            Path(working_dir).resolve() if working_dir else self.project_root
        )
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        """获取编辑工具参数规范"""
        return [
            ToolParameter(
                name="path",
                type="string",
                description="要编辑的文件路径（相对项目根目录）",
                required=True,
            ),
            ToolParameter(
                name="old_string",
                type="string",
                description="要替换的内容（必须唯一匹配）",
                required=True,
            ),
            ToolParameter(
                name="new_string",
                type="string",
                description="替换后的内容",
                required=True,
            ),
            ToolParameter(
                name="file_mtime_ms",
                type="integer",
                description="缓存的文件修改时间（用于冲突检测）",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行单处精确字符串编辑与原子落盘。

        执行步骤：
        1. 基础参数非空与字符串类型校验；
        2. 目标文件存在性检查（文件不存在直接返回 NOT_FOUND）；
        3. 乐观锁检测：若调用方提供了 cached_mtime，检测文件当前 mtime 是否一致，不一致则返回 CONFLICT 错误；
        4. 读取文件全文内容；
        5. 唯一性匹配校验：使用 content.count(old_string) 统计匹配次数，若不等于 1 则拒绝修改，杜绝歧义替换；
        6. 在内存中完成字符串替换；
        7. 自动备份原文件至 .backups/ 目录；
        8. 调用 _atomic_write 进行原子落盘；
        9. 计算 UTF-8 变动字节差（changed_bytes），返回成功回执与备份路径。
        """
        path = parameters.get("path")
        old_string = parameters.get("old_string")
        new_string = parameters.get("new_string")
        cached_mtime = parameters.get("file_mtime_ms")

        if not path:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="缺少必需参数: path"
            )

        if not isinstance(old_string, str):
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="old_string 必须是字符串"
            )

        if not isinstance(new_string, str):
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="new_string 必须是字符串"
            )

        try:
            # 解析路径
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return ToolResponse.error(
                    code=ToolErrorCode.NOT_FOUND, message=f"文件 '{path}' 不存在"
                )

            # 获取当前文件元数据
            current_mtime = os.path.getmtime(full_path)
            current_mtime_ms = int(current_mtime * 1000)

            # 检查乐观锁冲突（仅当调用方显式传入 cached_mtime 时）
            if cached_mtime is not None and current_mtime_ms != cached_mtime:
                return ToolResponse.error(
                    code=ToolErrorCode.CONFLICT,
                    message=f"文件自上次读取后被修改。当前 mtime={current_mtime_ms}, 缓存 mtime={cached_mtime}",
                    context={
                        "current_mtime_ms": current_mtime_ms,
                        "cached_mtime_ms": cached_mtime,
                    },
                )

            # 读取文件内容
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 核心约束：old_string 必须唯一匹配文件内容，不能匹配 0 次或多次
            matches = content.count(old_string)
            if matches != 1:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"old_string 必须唯一匹配文件内容。找到 {matches} 处匹配。",
                    context={"matches": matches},
                )

            # 执行内存替换
            new_content = content.replace(old_string, new_string)

            # 备份原文件
            backup_path = self._backup_file(full_path)

            # 原子写入新内容
            _atomic_write(full_path, new_content)

            # 计算 UTF-8 编码字节变化量
            changed_bytes = len(new_string.encode("utf-8")) - len(
                old_string.encode("utf-8")
            )

            return ToolResponse.success(
                text=f"成功编辑 {path} (变化 {changed_bytes:+d} 字节)",
                data={
                    "modified": True,
                    "changed_bytes": changed_bytes,
                    "backup_path": _display_backup(backup_path, self.working_dir),
                },
            )

        except PermissionError:
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED, message=f"无权限编辑 '{path}'"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR, message=f"编辑文件失败：{str(e)}"
            )

    def _backup_file(self, full_path: Path) -> Path:
        """调用全局备份函数在目标目录下生成带时间戳的历史备份副本。"""
        return _backup_file(full_path)

    def _resolve_path(self, path: str) -> Path:
        """解析相对路径（若为绝对路径则直接使用，否则拼接 working_dir）。"""
        if os.path.isabs(path):
            return Path(path)
        return self.working_dir / path


class MultiEditTool(Tool):
    """批量文件编辑工具（支持多步顺序替换与事务原子性）。

    功能：
    - 在单次调用中批量执行多个连续替换操作
    - 事务原子性保证（要么全部替换成功并落盘，中间任何一步失败则整体回滚，绝不触碰磁盘和备份）
    - 统一乐观锁预检（在执行所有替换前统一校验一次 mtime）
    - 顺序语义（后续替换项基于前序替换已完成的内存状态进行匹配与替换）

    参数契约：
    - path: 目标文件路径
    - edits: 替换操作列表 [{"old_string": "...", "new_string": "..."}, ...]
    - file_mtime_ms: 缓存的毫秒修改时间戳（可选，用于冲突检测）
    """

    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional["ToolRegistry"] = None,
    ):
        super().__init__(
            name="MultiEdit",
            description="批量替换文件内容，支持原子性和冲突检测",
            expandable=False,
        )
        self.project_root = Path(project_root).resolve()
        self.working_dir = (
            Path(working_dir).resolve() if working_dir else self.project_root
        )
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        """获取批量编辑参数规范（包含 edits 数组的 JSON Schema 严格校验）"""
        return [
            ToolParameter(
                name="path",
                type="string",
                description="要编辑的文件路径（相对项目根目录）",
                required=True,
            ),
            ToolParameter(
                name="edits",
                type="array",
                description="替换列表，每项包含 old_string 和 new_string",
                required=True,
                json_schema={
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                        "required": ["old_string", "new_string"],
                        "additionalProperties": False,
                    },
                },
            ),
            ToolParameter(
                name="file_mtime_ms",
                type="integer",
                description="缓存的文件修改时间（用于冲突检测）",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行批量编辑事务。

        执行步骤：
        1. 校验 path 和 edits 参数非空与数据结构合法性；
        2. 检查目标文件存在性；
        3. 在执行任何修改前进行一次性乐观锁预检；
        4. 读取文件全文至内存，并保留 original_content 用于字节统计；
        5. 在纯内存副本中按序演进校验并执行各个替换项：
           a. 校验编辑项为字典且包含字符串类型的 old_string/new_string；
           b. 针对当前演进中的内存文本，校验 old_string 匹配次数严格为 1；若不为 1 则立即返回错误；
           c. 顺序应用单次替换（content.replace(old_string, new_string, 1)）；
        6. 当且仅当所有编辑项在内存中均顺利校验替换完成后：
           a. 备份原文件；
           b. 原子落盘写入最终内容；
        7. 计算净变动 UTF-8 字节数，返回成功回执与备份路径。
        """
        path = parameters.get("path")
        edits = parameters.get("edits")
        cached_mtime = parameters.get("file_mtime_ms")

        if not path:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="缺少必需参数: path"
            )

        if not edits or not isinstance(edits, list):
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="缺少必需参数: edits（必须是列表）",
            )

        try:
            # 解析路径
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return ToolResponse.error(
                    code=ToolErrorCode.NOT_FOUND, message=f"文件 '{path}' 不存在"
                )

            # 获取当前文件元数据
            current_mtime = os.path.getmtime(full_path)
            current_mtime_ms = int(current_mtime * 1000)

            # 检查乐观锁冲突（所有替换前统一检查一次，避免半路拦截产生脏数据）
            if cached_mtime is not None and current_mtime_ms != cached_mtime:
                return ToolResponse.error(
                    code=ToolErrorCode.CONFLICT,
                    message=f"文件自上次读取后被修改。所有替换已取消。当前 mtime={current_mtime_ms}, 缓存 mtime={cached_mtime}",
                    context={
                        "current_mtime_ms": current_mtime_ms,
                        "cached_mtime_ms": cached_mtime,
                    },
                )

            # 读取文件内容
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            original_content = content

            # 在纯内存副本中，顺序验证与演进所有替换操作
            for i, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    return ToolResponse.error(
                        ToolErrorCode.INVALID_PARAM, f"编辑项 {i} 必须是对象"
                    )
                old_string = edit.get("old_string")
                new_string = edit.get("new_string")

                if not isinstance(old_string, str) or not isinstance(new_string, str):
                    return ToolResponse.error(
                        code=ToolErrorCode.INVALID_PARAM,
                        message=f"编辑项 {i} 的 old_string 和 new_string 必须是字符串",
                    )

                # 检查当前内存版本中是否唯一匹配
                matches = content.count(old_string)
                if matches != 1:
                    return ToolResponse.error(
                        code=ToolErrorCode.INVALID_PARAM,
                        message=f"编辑项 {i}: old_string 必须唯一匹配。找到 {matches} 处匹配。",
                        context={"edit_index": i, "matches": matches},
                    )

                # 顺序语义：下一项针对已经替换的内存副本验证；尚不写文件。
                content = content.replace(old_string, new_string, 1)

            # 所有项均成功后，才对原文件进行备份
            backup_path = self._backup_file(full_path)

            # 最终原子覆盖落盘
            _atomic_write(full_path, content)

            # 计算整体变动字节差值
            changed_bytes = len(content.encode("utf-8")) - len(
                original_content.encode("utf-8")
            )

            return ToolResponse.success(
                text=f"成功执行 {len(edits)} 个替换操作 (变化 {changed_bytes:+d} 字节)",
                data={
                    "modified": True,
                    "num_edits": len(edits),
                    "changed_bytes": changed_bytes,
                    "backup_path": _display_backup(backup_path, self.working_dir),
                },
            )

        except PermissionError:
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED, message=f"无权限编辑 '{path}'"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR, message=f"批量编辑失败：{str(e)}"
            )

    def _backup_file(self, full_path: Path) -> Path:
        """调用全局备份函数在目标目录下生成带时间戳的历史备份副本。"""
        return _backup_file(full_path)

    def _resolve_path(self, path: str) -> Path:
        """解析相对路径（若为绝对路径则直接使用，否则拼接 working_dir）。"""
        if os.path.isabs(path):
            return Path(path)
        return self.working_dir / path
