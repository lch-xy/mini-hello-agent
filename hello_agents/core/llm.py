"""P01/P04：模型配置、消息边界和统一调用。

阅读主线：构造函数选择适配器 → invoke 校验消息 → 合并请求参数 →
适配器调用 SDK 并转换响应。本层只负责一次模型请求，不负责 Agent 的工具循环。
"""

import asyncio
from contextlib import aclosing
import math
import os
from copy import deepcopy
from typing import Dict, List, Optional

from .exceptions import HelloAgentsException
from .llm_adapters import BaseLLMAdapter, create_adapter
from .llm_response import LLMResponse, LLMToolResponse, StreamStats

class HelloAgentsLLM:
    """
    HelloAgents统一LLM客户端

    对上层暴露统一的 invoke / invoke_with_tools，协议差异交给适配器处理。
    provider 显式指定时优先使用；省略时才根据 base_url 主机名推断协议。
    使用 OpenAI 兼容服务时仍需确认服务端支持传入的参数和响应字段。

    本阶段会保留部分服务商返回的 reasoning_content，但不会自动开启推理模式；
    P04 接通流式执行，预算控制留到 P18。创建本对象不会立刻发送模型请求。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        provider: Optional[str] = None,
        **kwargs
    ):
        """
        初始化LLM客户端

        model/api_key/base_url 使用非空传入值，否则回退到环境变量；
        timeout 仅在传入 None 时读取环境变量。temperature 等参数使用显式默认值。

        Args:
            model: 模型名称，默认从 LLM_MODEL_ID 读取
            api_key: API密钥，默认从 LLM_API_KEY 读取
            base_url: 服务地址，默认从 LLM_BASE_URL 读取
            temperature: 温度参数，默认0.7
            max_tokens: 最大token数
            timeout: 超时时间（秒），默认从 LLM_TIMEOUT 读取，默认60秒
            provider: 协议选择，如 openai、anthropic、gemini；不等于模型名称
            **kwargs: 可重复用于各次请求的 SDK 参数，每次调用还可以覆盖
        """
        # 1. 解析连接配置。此处读取环境变量，不会自动寻找或加载 .env 文件。
        self.model = model or os.getenv("LLM_MODEL_ID")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.timeout = timeout if timeout is not None else float(os.getenv("LLM_TIMEOUT", "60"))
        # bool 在 Python 中也是 int 的子类，因此要先单独排除；NaN/无穷也不能作超时。
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._closed = False

        self.temperature = temperature
        self.max_tokens = max_tokens
        # 这些参数属于单次调用或专用入口，禁止混入全局 kwargs 导致重复传参或路径混用。
        if {"messages", "tools", "stream", "model", "tool_choice"} & kwargs.keys():
            raise ValueError("messages/tools/stream/model/tool_choice belong to call methods")
        # 连嵌套字典/列表也复制，避免调用者修改原始 options 时污染后续请求。
        self.kwargs = deepcopy(kwargs)

        # 验证必要参数
        if not self.model:
            raise HelloAgentsException("必须提供模型名称（model参数或LLM_MODEL_ID环境变量）")
        if not self.api_key:
            raise HelloAgentsException("必须提供API密钥（api_key参数或LLM_API_KEY环境变量）")
        if not self.base_url:
            raise HelloAgentsException("必须提供服务地址（base_url参数或LLM_BASE_URL环境变量）")

        # 2. 选择协议适配器；实际 SDK 客户端会在首次调用时才懒加载。
        self._adapter: BaseLLMAdapter = create_adapter(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            model=self.model,
            provider=provider
        )

        # 记录最终选中的协议，方便观察；它不一定与模型厂商名称相同。
        self.provider = {"OpenAIAdapter": "openai", "AnthropicAdapter": "anthropic", "GeminiAdapter": "gemini"}.get(type(self._adapter).__name__, "custom")

        # 普通调用的统计在返回对象中，完成的文本流在这里保存统计。
        self.last_call_stats: Optional[StreamStats] = None

    @staticmethod
    def _validate_messages(messages: List[Dict]) -> None:
        """检查公共消息边界；具体内容块和工具字段仍由适配器处理。"""
        # 只接受 list[dict]，不会自动把 Message、HumanMessage 等对象转为 API 消息。
        if not isinstance(messages, list):
            raise TypeError('messages 必须是字典列表，例如 [{"role": "user", "content": "你好"}]')
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise TypeError(
                    f"messages[{index}] 必须是字典，收到 {type(message).__name__}。"
                    '请使用 {"role": "user", "content": prompt}；'
                    "LangChain HumanMessage 等消息对象需要显式转换。"
                )
            # 这里只要求非空角色名，不设白名单，也不全面校验 content、tool_calls。
            # 协议适配与 SDK/服务端仍可能对这些字段继续校验。
            if not isinstance(message.get("role"), str) or not message["role"].strip():
                raise ValueError(f"messages[{index}].role 必须是非空字符串")

    def _call_kwargs(self, overrides):
        """为本次请求创建独立参数字典，并阻止关闭后的客户端继续使用。"""
        if self._closed:
            raise RuntimeError("HelloAgentsLLM is closed")
        # 合并优先级：全局额外参数 → 构造参数 temperature/max_tokens → 本次覆盖值。
        # update 按顶层键覆盖，不递归合并嵌套字典；深拷贝负责避免共享可变数据。
        result = deepcopy(self.kwargs)
        result["temperature"] = self.temperature
        if self.max_tokens is not None:
            result["max_tokens"] = self.max_tokens
        result.update(deepcopy(overrides))
        return result

    def think(self, messages, temperature=None):
        """文本流式接口的别名；展示方式由调用方决定。"""
        kwargs = {} if temperature is None else {"temperature": temperature}
        yield from self.stream_invoke(messages, **kwargs)


    def invoke(self, messages, **kwargs) -> LLMResponse:
        """发送一次普通非流式请求，返回统一响应而非 SDK 原始对象。"""
        self._validate_messages(messages)
        return self._adapter.invoke(messages, **self._call_kwargs(kwargs))

    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs) -> LLMToolResponse:
        """附带工具描述，让模型决定回答文本或提出工具请求。

        tools 是工具 schema 列表，不是 Python 函数列表；tool_choice 默认 auto。
        返回后仍需由后续 Agent 执行工具、追加工具结果，再决定是否调用下一轮。
        """
        self._validate_messages(messages)
        return self._adapter.invoke_with_tools(messages, tools, **self._call_kwargs(dict(kwargs, tool_choice=tool_choice)))

    def stream_invoke(self, messages, **kwargs):
        self._validate_messages(messages)
        source = self._adapter.stream_invoke(messages, **self._call_kwargs(kwargs))
        try:
            yield from source
            self.last_call_stats = getattr(self._adapter, "last_stats", None)
        finally:
            close = getattr(source, "close", None)
            if close:
                close()


    async def ainvoke(self, messages, **kwargs):
        """在线程中运行同步 invoke，避免等待 SDK 时阻塞当前事件循环。"""
        # 这是异步包装，不是原生异步 HTTP；取消等待不保证后台请求立即终止。
        return await asyncio.to_thread(self.invoke, messages, **kwargs)

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        """与 ainvoke 使用相同线程策略，输出仍是 LLMToolResponse。"""
        return await asyncio.to_thread(self.invoke_with_tools, messages, tools, tool_choice, **kwargs)

    async def astream_invoke(self, messages, **kwargs):
        self._validate_messages(messages)
        async with aclosing(self._adapter.astream_invoke(messages, **self._call_kwargs(kwargs))) as source:
            async for chunk in source:
                yield chunk
        self.last_call_stats = getattr(self._adapter, "last_stats", None)


    async def astream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self._validate_messages(messages)
        call_kwargs = self._call_kwargs(dict(kwargs, tool_choice=tool_choice))
        async with aclosing(self._adapter.astream_invoke_with_tools(messages, tools, **call_kwargs)) as source:
            async for item in source:
                if not isinstance(item, str):
                    self.last_call_stats = StreamStats(model=item.model, usage=item.usage,
                        latency_ms=item.latency_ms, finish_reason=item.finish_reason)
                yield item


    def close(self):
        """释放自身持有的客户端；异步应用中应使用 aclose。"""
        # 成功关闭后才设置状态；重复 close 不会再次关闭资源。
        if not self._closed:
            self._adapter.close()
            self._closed = True

    async def aclose(self):
        """按异步生命周期释放资源；关闭状态与同步 close 共用。"""
        if not self._closed:
            await self._adapter.aclose()
            self._closed = True

    def __enter__(self):
        # 支持 with HelloAgentsLLM(...) as llm；进入时只检查状态，不创建 SDK 客户端。
        self._call_kwargs({})
        return self

    def __exit__(self, *_):
        # with 代码块即使抛出异常，也会走到退出清理；返回 None 不会吞掉该异常。
        self.close()

    async def __aenter__(self):
        # async with 与普通 with 使用相同的进入检查。
        return self.__enter__()

    async def __aexit__(self, *_):
        # 异步代码块退出时等待清理完成。
        await self.aclose()
