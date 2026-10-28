"""可选的官方 MCP SDK 桥接层，连接生命周期由调用方的异步作用域管理。"""

import asyncio
from copy import deepcopy
import re
from typing import Optional, Sequence

from .base import Tool
from .response import ToolResponse
from .errors import ToolErrorCode


class MCPTool(Tool):
    """MCP 远端工具适配器

    将 Model Context Protocol (MCP) 服务端暴露的工具包装为 HelloAgents 的标准 Tool 实例。
    特性：
    - 保留原生的 Draft 2020-12 嵌套 JSON Schema 契约（不强制扁平化为 ToolParameter）；
    - 绑定底层连接的 generation 代际标识，防止跨断开重连复用旧工具产生悬挂调用；
    - 仅支持异步执行（arun），同步调用 run 将抛出运行时错误提示。
    """

    def __init__(self, connection, definition, name):
        """初始化 MCPTool 适配器

        Args:
            connection: 所属的 MCPConnection 实例
            definition: 远端 MCP 服务返回的工具定义字典（含 name, description, inputSchema）
            name: 注册到本地 ToolRegistry 的工具名称（包含 prefix 前缀）
        """
        self.connection = connection
        # 记录创建该工具时的连接代际标识
        self.generation = connection._generation
        self.remote_name = definition["name"]
        self.input_schema = deepcopy(definition["inputSchema"])
        from jsonschema import Draft202012Validator

        # 校验模式引用：防止远程或外部 URL 引用注入，严格要求本地局部引用（以 '#' 开头）
        def check_refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef"} and not str(item).startswith("#"):
                        raise ValueError("MCP tool schemas must use local references")
                    check_refs(item)
            elif isinstance(value, list):
                for item in value:
                    check_refs(item)

        check_refs(self.input_schema)
        # 验证 inputSchema 符合 Draft 2020-12 模式标准规范
        Draft202012Validator.check_schema(self.input_schema)
        self.validator = Draft202012Validator(self.input_schema)
        super().__init__(name, definition.get("description") or self.remote_name)

    def get_parameters(self):
        """保留协商得到的嵌套 JSON Schema，不将其展开为扁平结构"""
        return []

    def to_openai_schema(self):
        """导出符合 OpenAI Function Call 规范的完整参数模式结构"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.input_schema),
            },
        }

    def run(self, parameters):
        """同步运行入口（MCP 工具严格要求异步执行，同步调用直接拦截）"""
        raise RuntimeError(
            "MCP tools require await agent.arun / registry.aexecute_tool within async with"
        )

    async def arun(self, parameters):
        """异步执行 MCP 远端工具调用

        1. 检查连接状态与事件循环所有权；
        2. 校验工具代际标识（防止跨连接复用已关闭的工具）；
        3. 本地基于 inputSchema 进行强参数校验；
        4. 远端 RPC 调用（不自动重试，确保非幂等写入操作安全）；
        5. 提取文本内容与多模态/结构化原始数据，包装为 ToolResponse。
        """
        self.connection._check_open()
        if self.generation is not self.connection._generation:
            raise RuntimeError("MCP tool belongs to a closed connection generation")
        # 参数结构与 Schema 校验
        if not isinstance(parameters, dict) or not self.validator.is_valid(parameters):
            return ToolResponse.error(
                ToolErrorCode.INVALID_PARAM,
                "Arguments do not match the MCP tool schema",
            )
        # 不自动重试远端写入操作：操作可能已在远端提交，重试可能导致重复写入
        result = await self.connection.client.call_tool(self.remote_name, parameters)
        raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        # 提取 content 列表中的所有文本块
        text = "\n".join(
            block["text"]
            for block in raw.get("content", [])
            if block.get("type") == "text"
        )
        # 处理远端业务报错标识
        if raw.get("isError"):
            response = ToolResponse.error(
                ToolErrorCode.EXECUTION_ERROR, text or "MCP tool failed"
            )
            response.data = {"mcp": raw}
            return response
        return ToolResponse.success(
            text or "MCP tool returned content", data={"mcp": raw}
        )


class MCPConnection:
    """MCP 客户端连接管理器与生命周期控制器

    在与 Agent 相同的事件循环中使用 ``async with`` 上下文管理。

    特性：
    - ``server`` 参数支持官方 SDK 传输对象、StdioServerParameters 进程参数或 Streamable HTTP URL；
    - 支持可选工具名称前缀（prefix）防止命名冲突；
    - 支持分页获取远端工具目录并防止分页死循环；
    - 支持动态选择性白名单导入（tools 过滤）；
    - 支持连接退出时自动安全清理（unregister），且不影响宿主后续替换的同名工具。
    """

    def __init__(
        self,
        server,
        *,
        prefix: str = "",
        timeout: float = 30,
        tools: Optional[Sequence[str]] = None,
    ):
        """初始化 MCPConnection

        Args:
            server: 传输连接对象或 URL（支持 StdioServerParameters 或 HTTP URL）
            prefix: 本地导出的工具名称前缀（必须符合 [A-Za-z0-9_-]+ 正则）
            timeout: 请求超时时间（秒，必须为有限正数）
            tools: 可选的远端工具名称白名单序列（None 表示全量导入）
        """
        # 校验工具名称前缀格式
        if not isinstance(prefix, str) or (
            prefix and not re.fullmatch(r"[A-Za-z0-9_-]+", prefix)
        ):
            raise ValueError(
                "prefix must contain letters, digits, underscores or hyphens"
            )
        # 校验超时时间参数
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout < float("inf")
        ):
            raise ValueError("timeout must be positive and finite")
        # 校验工具过滤白名单参数
        if tools is not None and (
            isinstance(tools, str)
            or any(not isinstance(t, str) or not t for t in tools)
        ):
            raise ValueError("tools must be a sequence of remote tool names")
        self.server, self.prefix, self.timeout = server, prefix, timeout
        self.allowed = frozenset(tools) if tools is not None else None
        self.client = None
        self._generation = None
        self._loop = None
        self._catalog = []
        self._registrations = []

    @classmethod
    def stdio(
        cls, command: str, args: Sequence[str] = (), *, env=None, cwd=None, **kwargs
    ):
        """便捷构造工厂：基于子进程标准输入输出（stdio）启动本地 MCP 进程

        Args:
            command: 待执行的命令路径（如 python 解释器路径）
            args: 命令行参数列表
            env: 环境变量字典
            cwd: 进程工作目录
            **kwargs: 透传给 __init__ 的其他参数（如 prefix、timeout、tools）
        """
        try:
            from mcp import StdioServerParameters
        except ImportError as exc:
            raise ImportError("Install hello-agents[mcp] to use MCP") from exc
        return cls(
            StdioServerParameters(command=command, args=list(args), env=env, cwd=cwd),
            **kwargs,
        )

    def _check_open(self):
        """检查连接是否处于打开状态且运行在同一事件循环中"""
        if self.client is None or self._loop is not asyncio.get_running_loop():
            raise RuntimeError(
                "MCP connection is closed or belongs to another event loop"
            )

    async def __aenter__(self):
        """进入异步上下文：启动连接、分页协商工具目录并实例化 MCPTool 列表"""
        if self.client is not None:
            raise RuntimeError("MCP connection is already open")
        try:
            from mcp import Client
            import jsonschema  # 在建立远端连接前完成依赖校验，缺失时立即报错
        except ImportError as exc:
            raise ImportError("Install hello-agents[mcp] to use MCP") from exc
        client = Client(self.server, read_timeout_seconds=self.timeout)
        await client.__aenter__()
        self.client, self._loop = client, asyncio.get_running_loop()
        # 创建单调代际对象标识
        self._generation = object()
        try:
            cursor, seen = None, set()
            definitions = []
            # 循环分页拉取服务端所有声明的工具定义
            while True:
                result = await client.list_tools(cursor=cursor)
                page = result.model_dump(mode="json", by_alias=True)
                definitions.extend(page["tools"])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                # 防止服务端返回重复游标陷入无限死循环
                if cursor in seen:
                    raise ValueError("MCP server repeated its pagination cursor")
                seen.add(cursor)
            # 校验远端工具名称唯一性
            names = [d["name"] for d in definitions]
            if len(set(names)) != len(names):
                raise ValueError("MCP server advertised duplicate tools")
            # 校验请求指定的工具是否均存在于远端服务目录中
            if self.allowed is not None and not self.allowed.issubset(names):
                raise ValueError(
                    "Requested MCP tools are missing from the server catalog"
                )
            self._catalog = []
            # 遍历定义并构建本地适配的 MCPTool 列表
            for definition in definitions:
                if self.allowed is not None and definition["name"] not in self.allowed:
                    continue
                name = self.prefix + definition["name"]
                # 校验拼装后的工具名称符合标准正则且长度不超过 64 字符
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                    raise ValueError(
                        "Exported MCP tool name must match [A-Za-z0-9_-]{1,64}"
                    )
                self._catalog.append(MCPTool(self, definition, name))
            return self
        except BaseException:
            # 初始化中途发生任何异常，确保及时调用 __aexit__ 清理底层资源
            await self.__aexit__(None, None, None)
            raise

    def register_tools(self, registry):
        """将协商就绪的所有 MCP 工具批量注册到本地工具注册表

        Args:
            registry: 目标 ToolRegistry 实例

        Returns:
            tuple[str]: 成功注册的所有本地工具名称元组
        """
        self._check_open()
        # 冲突检测：若注册表中已存在相同名称的工具，抛出 ValueError
        if set(registry.list_tools()) & {t.name for t in self._catalog}:
            raise ValueError("MCP tool name collision; use a distinct prefix")
        for tool in self._catalog:
            registry.register_tool(tool)
            self._registrations.append((registry, tool))
        return tuple(t.name for t in self._catalog)

    async def __aexit__(self, exc_type, exc, tb):
        """退出异步上下文：安全注销工具并关闭底层客户端连接"""
        self._check_open()
        for registry, tool in self._registrations:
            # 仅当注册表中对应名称的工具仍为该 MCPTool 实例时才注销；
            # 连接清理时，必须保留宿主后来替换注册的同名工具。
            if registry.get_tool(tool.name) is tool:
                registry.unregister(tool.name)
        self._registrations.clear()
        client, self.client = self.client, None
        self._loop, self._catalog = None, []
        self._generation = None
        return await client.__aexit__(exc_type, exc, tb)
