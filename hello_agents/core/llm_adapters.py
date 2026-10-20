"""P01/P04：三种模型协议的统一适配器。

本文件是协议边界：输入使用统一的 OpenAI 风格消息/工具描述，输出转换为
LLMResponse 或 LLMToolResponse。适配器负责一次 SDK 调用，不执行任何工具，
也不管理多轮 Agent 状态。阅读时可先看 BaseLLMAdapter 和末尾的 create_adapter，
再对照各协议的消息转换、请求组装、响应归一化三个步骤。
"""

import asyncio
import queue as thread_queue
import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union
from urllib.parse import urlparse

from .exceptions import HelloAgentsException
from .llm_response import LLMResponse, LLMToolResponse, StreamStats, ToolCall

def _openai_usage(value) -> Dict[str, Any]:
    """保留服务端报告的 Token 明细，不补造缺失的缓存指标。"""
    # 兼容没有 usage、原生字典、Pydantic SDK 对象及仅提供属性的简易对象。
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if callable(getattr(value, "model_dump", None)):
        # SDK 可提供嵌套明细或厂商扩展指标，直接导出以免只保留三个总量字段。
        return value.model_dump(exclude_none=True)
    # 最后才退回已知属性；未报告的字段直接缺省，不把未知缓存命中数补成 0。
    return {name: getattr(value, name) for name in (
        "prompt_tokens", "completion_tokens", "total_tokens",
        "prompt_tokens_details", "completion_tokens_details",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    ) if hasattr(value, name) and getattr(value, name) is not None}

class BaseLLMAdapter(ABC):
    """约定统一调用接口，并管理 SDK 客户端的创建与释放。

    ABC + abstractmethod 要求具体协议实现创建客户端和两种非流式调用。
    子类只转换协议；会话历史、工具执行和重试策略由其他层负责。
    """

    def __init__(self, api_key: str, base_url: Optional[str], timeout: int, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.model = model
        # 构造适配器时仅存配置，首次调用才创建客户端，未使用的协议无需提前加载 SDK。
        self._client = None
        # P01 的异步入口使用同步 SDK + 线程；这里先保留原生异步客户端的生命周期槽位。
        self._async_client = None
        # 只保护懒加载初始化，不会把所有模型请求串行化。
        self._client_lock = threading.Lock()

    def _get_client(self):
        """获取唯一的同步客户端，避免并发首调重复创建连接资源。"""
        with self._client_lock:
            if self._client is None:
                self._client = self.create_client()
            return self._client

    @abstractmethod
    def create_client(self) -> Any:
        """创建客户端实例"""
        pass

    def create_async_client(self) -> Any:
        """创建异步客户端实例（子类可选实现）"""
        # 默认没有原生异步客户端；当前 HelloAgentsLLM.ainvoke 不调用这个工厂。
        return None

    @abstractmethod
    def invoke(self, messages: List[Dict], **kwargs) -> LLMResponse:
        """非流式调用"""
        pass

    @abstractmethod
    def stream_invoke(self, messages: List[Dict], **kwargs) -> Iterator[str]:
        """流式调用抽象接口，子类需返回产出文本片段的同步生成器。"""
        pass


    async def astream_invoke(self, messages: List[Dict], **kwargs):
        """通用异步流式回退实现（生产者-消费者桥接模式）。
        
        对于未提供原生异步流式客户端的适配器，本方法启动一个独立的后台守护线程运行
        同步 stream_invoke，并通过有界队列将数据桥接到异步事件循环中：
        1. items 队列设置容量上限（maxsize=8），提供反压控制（Backpressure），防止消费者较慢时内存溢出；
        2. stopped 信号用于任务取消或上层提前关闭时的协同终止；
        3. put 采用小超时轮询（0.05s），确保取消信号能够迅速响应，避免后台工作线程永久阻塞；
        4. 消费端使用 get_nowait 配合 asyncio.sleep(0.01) 实现对事件循环友好的非阻塞轮询。
        """
        items = thread_queue.Queue(maxsize=8)  # 有界缓冲队列，实现反压
        stopped = threading.Event()           # 协同停止信号
        end = object()                         # 流结束哨兵对象

        def put(value):
            """带停止信号检测的有界队列写入辅助函数。"""
            while not stopped.is_set():
                try:
                    items.put(value, timeout=0.05)
                    return True
                except thread_queue.Full:
                    continue
            return False

        def produce():
            """后台生产者线程：消费同步流式生成器并投递到队列。"""
            source = self.stream_invoke(messages, **kwargs)
            try:
                while not stopped.is_set():
                    try:
                        item = next(source)
                    except StopIteration:
                        break
                    if not put(item):
                        break
            except Exception as exc:
                # 传递异常到消费端以便在事件循环中重新抛出
                put(exc)
            finally:
                close = getattr(source, "close", None)
                if close:
                    close()
                put(end)

        worker = threading.Thread(target=produce, daemon=True, name="helloagents-stream")
        worker.start()
        try:
            while True:
                try:
                    item = items.get_nowait()
                except thread_queue.Empty:
                    # 队列为空时让出事件循环控制权，避免忙等待
                    await asyncio.sleep(0.01)
                    continue
                if item is end:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            # 当外部迭代提前退出（break/aclose）或任务取消时，通知工作线程尽快退出
            stopped.set()


    def close(self):
        """同步释放资源；已有异步客户端时，需要考虑事件循环所在位置。"""
        if self._async_client is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # 当前线程没有运行中的事件循环，可临时创建循环完成异步清理。
                asyncio.run(self.aclose())
                return
            # 已经在循环中不能再嵌套 asyncio.run，交给调用者 await aclose。
            raise RuntimeError("Use await llm.aclose() for async clients")
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self):
        """异步释放两类客户端，随后清空引用。"""
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None
        if self._client is not None:
            # 同步 close 也可能阻塞，因此移到线程执行。
            await asyncio.to_thread(self._client.close)
            self._client = None

    @abstractmethod
    def invoke_with_tools(self, messages: List[Dict], tools: List[Dict], **kwargs) -> LLMToolResponse:
        """工具调用（Function Calling）"""
        pass
    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        """流式工具调用回退实现。
        
        回退适配器只在线程池中发起一次完整请求，不模拟逐 Token 流式输出。
        若模型产生了文本内容，先产出该内容，最后再产出完整的 LLMToolResponse 响应。
        """
        if tools:
            response = await asyncio.to_thread(self.invoke_with_tools, messages, tools, **kwargs)
        else:
            kwargs.pop("tool_choice", None)
            response = await asyncio.to_thread(self.invoke, messages, **kwargs)
        if response.content:
            yield response.content
        yield response


    def _is_thinking_model(self, model_name: str) -> bool:
        """根据模型名称关键字判断是否为具备深度思考链（Reasoning）的模型。"""
        thinking_keywords = ["reasoner", "o1", "o3", "thinking"]
        model_lower = model_name.lower()
        return any(keyword in model_lower for keyword in thinking_keywords)



class OpenAIAdapter(BaseLLMAdapter):
    """使用 OpenAI Chat Completions 协议的默认适配器。

    对兼容服务复用同一套 SDK 和数据结构，具体参数支持情况取决于服务端。
    如果响应提供 reasoning_content，就将其保留下来；本类不主动启用推理模式。
    """

    def create_client(self) -> Any:
        """创建OpenAI客户端"""
        # 局部导入让 SDK 的加载推迟到真正使用该协议时。
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout
        )

    def create_async_client(self) -> Any:
        """创建OpenAI异步客户端"""
        # 这里只提供创建能力，不会自动赋值给 _async_client；P01 请求仍走同步客户端。
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout
        )

    def invoke(self, messages: List[Dict], **kwargs) -> LLMResponse:
        """非流式调用"""
        # 懒加载放在计时和 try 之外：客户端创建错误不会被下面的 API 错误包装捕获。
        self._get_client()
        
        start_time = time.time()
        
        try:
            # OpenAI 风格消息已经是该 SDK 的输入格式，无需另做角色或内容块转换。
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )
            
            latency_ms = int((time.time() - start_time) * 1000)
            
            # 只取第一候选回复；纯工具响应等情况下 content 可能为空，普通响应统一成 ""。
            choice = response.choices[0]
            content = choice.message.content or ""
            reasoning_content = None
            
            # 这是可选扩展字段，没有该属性时返回 None，不推测隐藏的模型推理过程。
            reasoning_content = getattr(choice.message, "reasoning_content", None)

            # 提取usage信息
            usage = {}
            if hasattr(response, 'usage') and response.usage:
                usage = _openai_usage(response.usage)
            
            return LLMResponse(
                finish_reason=getattr(response.choices[0], "finish_reason", None),
                content=content,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content
            )
            
        except Exception as e:
            # 将请求和响应解析阶段的错误包装为框架基础异常；不在这里重试。
            raise HelloAgentsException(f"OpenAI API调用失败: {str(e)}")

    def stream_invoke(self, messages: List[Dict], **kwargs) -> Iterator[str]:
        """OpenAI 协议同步流式调用实现。
        
        通过 stream=True 开启流式响应，逐个 chunk 提取 content 文本进行 yield。
        同时累积思考模型可能返回的 reasoning_content，并在流结束时解析 usage，
        组装为 StreamStats 保存在 self.last_stats 中。
        """
        self._get_client()

        start_time = time.time()

        response = None
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )

            collected_content = []
            reasoning_content = None
            finish_reason = None
            usage = {}

            for chunk in response:
                choices = getattr(chunk, "choices", None)
                if choices:
                    finish_reason = getattr(choices[0], "finish_reason", None) or finish_reason
                    delta = getattr(choices[0], "delta", None)
                    if delta is not None:
                        # 提取可见文本增量并产出
                        content = getattr(delta, "content", None)
                        if content:
                            collected_content.append(content)
                            yield content

                        # 累积 Thinking model 的思维链内容（如 DeepSeek-R1 / o1）
                        reasoning_delta = getattr(delta, "reasoning_content", None)
                        if reasoning_delta:
                            if reasoning_content is None:
                                reasoning_content = ""
                            reasoning_content += reasoning_delta

                # 提取 usage（部分供应商流式末尾 chunk 或单独附加 chunk 中包含）
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage = _openai_usage(chunk.usage)

            latency_ms = int((time.time() - start_time) * 1000)

            # 将统计信息固化到适配器实例，供外部调用层（如 HelloAgentsLLM）回溯获取
            self.last_stats = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content,
                finish_reason=finish_reason
            )

        except Exception as e:
            raise HelloAgentsException(f"OpenAI API流式调用失败: {str(e)}")
        finally:
            # 确保在流消费结束或中途异常时显式关闭 SDK 底层连接
            close = getattr(response, "close", None)
            if close:
                close()


    async def astream_invoke(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """OpenAI 协议原生异步流式调用实现（基于 AsyncOpenAI 客户端）。"""
        if not self._async_client:
            self._async_client = self.create_async_client()

        start_time = time.time()

        response = None
        try:
            response = await self._async_client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )

            collected_content = []
            reasoning_content = None
            finish_reason = None
            usage = {}

            async for chunk in response:
                choices = getattr(chunk, "choices", None)
                if choices:
                    finish_reason = getattr(choices[0], "finish_reason", None) or finish_reason
                    delta = getattr(choices[0], "delta", None)
                    if delta is not None:
                        # 提取文本增量
                        content = getattr(delta, "content", None)
                        if content:
                            collected_content.append(content)
                            yield content

                        # 累加思维链内容
                        reasoning_delta = getattr(delta, "reasoning_content", None)
                        if reasoning_delta:
                            if reasoning_content is None:
                                reasoning_content = ""
                            reasoning_content += reasoning_delta

                # 提取 usage
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage = _openai_usage(chunk.usage)

            latency_ms = int((time.time() - start_time) * 1000)

            # 返回流式统计信息
            self.last_stats = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content,
                finish_reason=finish_reason
            )

        except Exception as e:
            raise HelloAgentsException(f"OpenAI API异步流式调用失败: {str(e)}")
        finally:
            # 异步释放响应流资源
            close = getattr(response, "close", None)
            if close:
                await close()


    def invoke_with_tools(self, messages: List[Dict], tools: List[Dict],
                         tool_choice: Union[str, Dict] = "auto", **kwargs) -> LLMToolResponse:
        """请求模型选择工具；只返回调用意图，不执行 Python 工具。"""
        self._get_client()

        start_time = time.time()
        try:
            # tools 是函数描述/schema；tool_choice 控制模型是否、以及选择哪个工具。
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs
            )

            latency_ms = int((time.time() - start_time) * 1000)
            message = response.choices[0].message

            tool_calls = []
            if message.tool_calls:
                # 同一条回复可以提出多个工具请求，保留顺序、原始 ID 和 JSON 参数文本。
                for tc in message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments
                    ))

            usage = {}
            if response.usage:
                usage = _openai_usage(response.usage)

            # 与普通 invoke 不同，这里保留 nullable content，model 使用响应报告的名称。
            return LLMToolResponse(
                finish_reason=getattr(response.choices[0], "finish_reason", None),
                content=message.content,
                reasoning_content=getattr(message, "reasoning_content", None),
                tool_calls=tool_calls,
                model=response.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"OpenAI Function Calling调用失败: {str(e)}")
    async def astream_invoke_with_tools(self, messages, tools, **kwargs):
        """OpenAI 协议支持工具调用的原生异步流式实现。
        
        执行流程与协议契约：
        1. 配置 stream_options={"include_usage": True}，确保在流结束时服务端返回准确的 Token 消耗；
        2. 在流式传输过程中，若模型输出可见回答（delta.content），实时 yield 文本增量给调用方做前端打字机展示；
        3. 若模型发起工具调用（delta.tool_calls），按照 part.index 逐段累积工具名称（name）和 JSON 参数碎片（arguments）；
        4. 累积 reasoning_content，但仅包含在最终响应中，不混入文本流；
        5. 在 finally 块中严密双重释放底层 stream 和 client 连接，避免连接池泄漏；
        6. 流结束时，将所有累积拼装完整的 ToolCall 对象、usage、finish_reason、耗时封装为
           LLMToolResponse 并作为生成器的最后一项产出。
        """
        started = time.monotonic()
        if tools:
            kwargs["tools"] = tools
        else:
            kwargs.pop("tool_choice", None)
        client = self.create_async_client()
        stream = None
        content, calls, usage, reason = [], {}, {}, None
        reasoning = []
        try:
            stream = await client.chat.completions.create(
                model=self.model, messages=messages, stream=True,
                stream_options={"include_usage": True}, **kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = _openai_usage(chunk.usage)
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                reason = getattr(choice, "finish_reason", None) or reason
                delta = choice.delta
                # 收集推理思维链内容
                if getattr(delta, "reasoning_content", None):
                    reasoning.append(delta.reasoning_content)
                # 产出可见文本增量（实时推送）
                if getattr(delta, "content", None):
                    content.append(delta.content)
                    yield delta.content
                # 拼接流式工具碎片：按工具调用索引合并 id、name 和 arguments
                for part in getattr(delta, "tool_calls", None) or []:
                    item = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    if part.id:
                        item["id"] = part.id
                    if part.function:
                        item["name"] += part.function.name or ""
                        item["arguments"] += part.function.arguments or ""
        finally:
            try:
                if stream is not None:
                    await stream.close()
            finally:
                # 确保每次调用临时创建的异步客户端被完整关闭
                await client.close()
        # 组装完整的工具调用列表并作为终态对象返回
        yield LLMToolResponse(
            content="".join(content), tool_calls=[ToolCall(**calls[i]) for i in sorted(calls)],
            model=self.model, usage=usage, finish_reason=reason, reasoning_content="".join(reasoning) or None,
            latency_ms=int((time.monotonic() - started) * 1000))



class AnthropicAdapter(BaseLLMAdapter):
    """Anthropic Claude适配器

    统一协议的 system 消息变为顶层 system 参数；assistant 工具请求变为
    tool_use 内容块；工具结果变为 user 消息中的 tool_result 内容块。
    返回时再把这些服务商内容块还原为框架的文本与 ToolCall。
    """

    def create_client(self) -> Any:
        """创建Anthropic客户端"""
        try:
            from anthropic import Anthropic
        except ImportError:
            raise HelloAgentsException(
                "缺少 Anthropic 可选依赖。请按安装文档启用 anthropic 组件；"
                "源码目录执行 python -m pip install -r requirements/anthropic.txt"
            )

        return Anthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout
        )

    def _convert_messages(self, messages: List[Dict]) -> tuple[Optional[str], List[Dict]]:
        """返回独立的系统提示词和转换后的历史消息列表。

        按原顺序处理，保留请求 ID 与结果 ID 的对应关系。若存在多条 system，
        当前实现使用最后一条的内容，不会自动拼接它们。
        """
        system_content = None
        converted_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                # Claude 用一个 content 列表同时表达文本和工具请求。
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})

                for tool_call in msg["tool_calls"]:
                    function = tool_call.get("function", {})
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            # 统一协议中的参数是 JSON 文本，Claude input 需要解析后的值。
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            # 保留目标实现的容错行为：非法 JSON 在此退为空对象，并未执行工具。
                            arguments = {}

                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": function.get("name"),
                        "input": arguments,
                    })

                converted_messages.append({
                    "role": "assistant",
                    "content": content_blocks,
                })
            elif msg["role"] == "tool":
                # 工具结果在 Claude 中属于 user 一侧，而不是独立的 tool 角色。
                # tool_use_id 必须对应此前 assistant 请求中的 id。
                converted_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id"),
                        "content": msg.get("content", ""),
                    }],
                })
            else:
                # 普通 user/assistant 消息直接保留；这里并非完整的多模态协议校验器。
                converted_messages.append(msg)

        return system_content, converted_messages

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """将统一的 OpenAI 风格工具 schema 转换为 Anthropic 工具 schema"""
        converted_tools = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                # 去掉外层 function 包装，把 JSON Schema 从 parameters 改名为 input_schema。
                # 仅传描述和参数结构，绝不把本地函数对象发送给模型。
                function = tool["function"]
                converted_tools.append({
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {
                        "type": "object",
                        "properties": {},
                    }),
                })
            else:
                # 非标准包装原样传递，由 SDK/服务端继续解释。
                converted_tools.append(tool)

        return converted_tools

    def _convert_tool_choice(self, tool_choice: Any) -> Optional[Dict]:
        """将统一的 tool_choice 转换为 Anthropic 格式"""
        # auto/none 保留含义；required 对应 any；指定函数名对应 type=tool。
        if tool_choice is None:
            return None
        if tool_choice in ("auto", "none"):
            return {"type": tool_choice}
        if tool_choice == "required":
            return {"type": "any"}
        if isinstance(tool_choice, dict):
            function = tool_choice.get("function")
            if function and function.get("name"):
                return {"type": "tool", "name": function["name"]}
        return tool_choice

    def invoke(self, messages: List[Dict], **kwargs) -> LLMResponse:
        """非流式调用"""
        self._get_client()

        start_time = time.time()
        # 先把 system 从消息数组中拆出来，再交给独立的请求参数。
        system_content, converted_messages = self._convert_messages(messages)

        try:
            # 构建请求参数
            # Claude 需要 max_tokens；仅当键不存在时默认 4096，显式 None 不会被替换。
            # kwargs 是本次请求的独立字典，pop 不会污染 LLM 门面保存的默认参数。
            request_params = {
                "model": self.model,
                "messages": converted_messages,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs
            }
            if system_content:
                request_params["system"] = system_content

            response = self._client.messages.create(**request_params)

            latency_ms = int((time.time() - start_time) * 1000)

            # 一个响应可能含多个内容块，只按顺序连接提供 text 属性的块。
            content = ""
            if response.content:
                for block in response.content:
                    if hasattr(block, 'text'):
                        content += block.text

            # 把服务商的 input/output 命名映射为框架的 prompt/completion 命名。
            # 这里仅归一化三项总量，不会像 OpenAI 路径一样保留全部扩展 usage 字段。
            usage = {}
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                }

            return LLMResponse(
                finish_reason=getattr(response, "stop_reason", None),
                content=content,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Anthropic API调用失败: {str(e)}")

    def stream_invoke(self, messages: List[Dict], **kwargs) -> Iterator[str]:
        """Anthropic 协议同步流式调用实现。
        
        利用 Anthropic SDK 的 messages.stream 上下文管理器建立流式连接，
        逐块从 stream.text_stream 迭代输出增量文本。流退出时调用 get_final_message()
        获取完整的 Message 对象以抽取准确的 input/output tokens 并记录耗时。
        """
        self._get_client()

        start_time = time.time()
        system_content, converted_messages = self._convert_messages(messages)

        try:
            request_params = {
                "model": self.model,
                "messages": converted_messages,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs
            }
            if system_content:
                request_params["system"] = system_content

            usage = {}

            with self._client.messages.stream(**request_params) as stream:
                for text in stream.text_stream:
                    yield text

                # 从流的完整终态消息中提取实际消耗的 Token 数量
                final_message = stream.get_final_message()
                if hasattr(final_message, 'usage') and final_message.usage:
                    usage = {
                        "prompt_tokens": final_message.usage.input_tokens,
                        "completion_tokens": final_message.usage.output_tokens,
                        "total_tokens": final_message.usage.input_tokens + final_message.usage.output_tokens,
                    }

            latency_ms = int((time.time() - start_time) * 1000)

            self.last_stats = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Anthropic API流式调用失败: {str(e)}")


    def invoke_with_tools(self, messages: List[Dict], tools: List[Dict], **kwargs) -> LLMToolResponse:
        """工具调用（Anthropic格式）"""
        self._get_client()

        # 工具调用需要同时转换历史消息、工具定义、工具选择策略，三者缺一不可。
        system_content, converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(tools)
        tool_choice = self._convert_tool_choice(kwargs.pop("tool_choice", None))

        start_time = time.time()
        try:
            request_params = {
                "model": self.model,
                "messages": converted_messages,
                "tools": converted_tools,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs
            }
            if system_content:
                request_params["system"] = system_content
            if tool_choice:
                request_params["tool_choice"] = tool_choice

            response = self._client.messages.create(**request_params)
            latency_ms = int((time.time() - start_time) * 1000)

            content = ""
            tool_calls = []
            for block in response.content:
                # 文本与工具块可以同时出现，因此分别累积，不能遇到工具后就丢掉文本。
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    # Claude input 已是结构化参数；转回 JSON 字符串以对齐 ToolCall 契约。
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=json.dumps(block.input)
                    ))

            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens
            }

            return LLMToolResponse(
                finish_reason=getattr(response, "stop_reason", None),
                content=content if content else None,
                tool_calls=tool_calls,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Anthropic工具调用失败: {str(e)}")

class GeminiAdapter(BaseLLMAdapter):
    """Google Gemini适配器

    使用 google.genai SDK，将统一消息转换成 Content/Part：assistant 改为
    model，系统提示词独立进入生成配置，工具交互使用 function_call/response。
    """

    def create_client(self) -> Any:
        """创建Gemini客户端"""
        try:
            from google import genai
        except ImportError:
            raise HelloAgentsException(
                "缺少 Gemini 可选依赖。请按安装文档启用 gemini 组件；"
                "源码目录执行 python -m pip install -r requirements/gemini.txt"
            )

        # 本框架 timeout 单位为秒，该 SDK 的 http_options.timeout 使用毫秒。
        client = genai.Client(api_key=self.api_key, http_options={"base_url": self.base_url, "timeout": int(self.timeout * 1000)})
        return client

    def _convert_messages(self, messages: List[Dict]) -> tuple[Optional[str], List[Dict]]:
        """拆出 system_instruction，并按历史顺序构造 Gemini 内容块。

        多条 system 仍然使用最后一条；本实现的普通内容路径按文本处理。
        工具结果需要函数名称，因此转换过程中另外维护调用 ID 到函数名的映射。
        """
        from google.genai import types as genai_types

        system_instruction = None
        converted_messages = []
        # 统一协议的工具结果只有 tool_call_id 时，从此前请求中找回函数名称。
        tool_call_names = {}

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                parts = []
                if msg.get("content"):
                    parts.append(genai_types.Part.from_text(text=msg["content"]))

                for tool_call in msg["tool_calls"]:
                    function = tool_call.get("function", {})
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            # from_function_call 需要结构化参数，先解析统一协议的 JSON 文本。
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            # 与 Anthropic 路径保持目标代码现有的非法 JSON 退为空对象行为。
                            arguments = {}

                    tool_name = function.get("name", "")
                    tool_call_id = tool_call.get("id")
                    if tool_call_id and tool_name:
                        # 同名工具可能被调用多次，关联时依靠调用 ID，而不是只靠 name。
                        tool_call_names[tool_call_id] = tool_name

                    parts.append(genai_types.Part.from_function_call(
                        name=tool_name,
                        args=arguments,
                    ))

                converted_messages.append(genai_types.Content(
                    role="model",
                    parts=parts,
                ))
            elif msg["role"] == "tool":
                # 历史中找不到匹配请求时，当前实现以 tool_result 作为函数名回退值。
                tool_name = tool_call_names.get(msg.get("tool_call_id"), "tool_result")
                converted_messages.append(genai_types.Content(
                    role="tool",
                    parts=[genai_types.Part.from_function_response(
                        name=tool_name,
                        response={"result": msg.get("content", "")},
                    )],
                ))
            else:
                # Gemini使用 "user" 和 "model" 作为角色
                role = "model" if msg["role"] == "assistant" else "user"
                converted_messages.append(genai_types.Content(
                    role=role,
                    parts=[genai_types.Part.from_text(text=msg["content"] or "")],
                ))

        return system_instruction, converted_messages

    def _convert_tool_choice(self, tool_choice: Any) -> Optional[Any]:
        """将统一的 tool_choice 转换为 Gemini 工具配置"""
        from google.genai import types as genai_types

        # auto 不另设配置；none 禁用函数调用；required 要求调用任一函数。
        if tool_choice in (None, "auto"):
            return None
        if tool_choice == "none":
            return genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode="NONE")
            )
        if tool_choice == "required":
            return genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode="ANY")
            )
        if isinstance(tool_choice, dict):
            function = tool_choice.get("function")
            if function and function.get("name"):
                # ANY 再限制 allowed_function_names，就表达了“只能选择该函数”。
                return genai_types.ToolConfig(
                    function_calling_config=genai_types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=[function["name"]],
                    )
                )
        return tool_choice

    def invoke(self, messages: List[Dict], **kwargs) -> LLMResponse:
        """非流式调用"""
        self._get_client()

        from google.genai import types as genai_types

        start_time = time.time()
        system_instruction, converted_messages = self._convert_messages(messages)

        try:
            # 创建生成配置
            # 先复制本次额外参数，再把统一的 max_tokens 改为 SDK 的 max_output_tokens。
            config_params = dict(kwargs)
            if "temperature" in kwargs:
                config_params["temperature"] = kwargs.pop("temperature")
            if "max_tokens" in kwargs:
                config_params["max_output_tokens"] = config_params.pop("max_tokens")
            if system_instruction:
                config_params["system_instruction"] = system_instruction

            response = self._client.models.generate_content(
                model=self.model,
                contents=converted_messages,
                config=genai_types.GenerateContentConfig(**config_params) if config_params else None
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # SDK 的 text 访问器汇总文本内容；工具请求另由 invoke_with_tools 解析。
            content = response.text if hasattr(response, 'text') else ""

            # Gemini 的计数位于 usage_metadata；此路径将缺失的计数按当前实现记为 0。
            # 这些映射仅覆盖基本 Token 总量，不代表缓存、推理等扩展指标已被保留。
            usage = {}
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = {
                    "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                    "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                    "total_tokens": response.usage_metadata.total_token_count or 0,
                }

            return LLMResponse(
                finish_reason=getattr(response.candidates[0], "finish_reason", None) if getattr(response, "candidates", None) else None,
                content=content,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Gemini API调用失败: {str(e)}")

    def stream_invoke(self, messages: List[Dict], **kwargs) -> Iterator[str]:
        """Gemini 协议同步流式调用实现。
        
        基于 Google GenAI SDK 的 generate_content_stream 方法，实时产出
        候选块（chunk.text）。在流结束时尝试从 usage_metadata 读取 Token 统计。
        """
        self._get_client()

        from google.genai import types as genai_types

        start_time = time.time()
        system_instruction, converted_messages = self._convert_messages(messages)

        response = None
        try:
            # 创建生成配置
            config_params = dict(kwargs)
            if "temperature" in kwargs:
                config_params["temperature"] = kwargs.pop("temperature")
            if "max_tokens" in kwargs:
                config_params["max_output_tokens"] = config_params.pop("max_tokens")
            if system_instruction:
                config_params["system_instruction"] = system_instruction

            usage = {}

            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=converted_messages,
                config=genai_types.GenerateContentConfig(**config_params) if config_params else None
            )

            for chunk in response:
                if hasattr(chunk, 'text') and chunk.text:
                    yield chunk.text

                # 尝试提取 usage（可能随中间块或末尾块提供）
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    usage = {
                        "prompt_tokens": chunk.usage_metadata.prompt_token_count or 0,
                        "completion_tokens": chunk.usage_metadata.candidates_token_count or 0,
                        "total_tokens": chunk.usage_metadata.total_token_count or 0,
                    }

            latency_ms = int((time.time() - start_time) * 1000)

            self.last_stats = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Gemini API流式调用失败: {str(e)}")
        finally:
            close = getattr(response, "close", None)
            if close:
                close()


    def invoke_with_tools(self, messages: List[Dict], tools: List[Dict], **kwargs) -> LLMToolResponse:
        """工具调用（Gemini格式）"""
        self._get_client()

        from google.genai import types as genai_types

        system_instruction, converted_messages = self._convert_messages(messages)
        tool_choice = self._convert_tool_choice(kwargs.pop("tool_choice", None))

        start_time = time.time()
        try:
            # 把每个 function schema 转成 FunctionDeclaration，再装入一个 Tool 容器。
            # parameters_json_schema 直接承接 JSON Schema，保留 enum、嵌套对象等约束。
            gemini_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool["function"]
                    gemini_tools.append(
                        genai_types.FunctionDeclaration(
                            name=func["name"],
                            description=func.get("description", ""),
                            parameters_json_schema=func.get("parameters", {})
                        )
                    )

            config_params = dict(kwargs)
            if "temperature" in kwargs:
                config_params["temperature"] = kwargs.pop("temperature")
            if "max_tokens" in kwargs:
                config_params["max_output_tokens"] = config_params.pop("max_tokens")
            if gemini_tools:
                # Gemini 将工具与工具选择策略都放在生成配置，而非独立请求形参里。
                config_params["tools"] = [genai_types.Tool(function_declarations=gemini_tools)]
            if system_instruction:
                config_params["system_instruction"] = system_instruction
            if tool_choice:
                config_params["tool_config"] = tool_choice

            response = self._client.models.generate_content(
                model=self.model,
                contents=converted_messages,
                config=genai_types.GenerateContentConfig(**config_params) if config_params else None
            )
            latency_ms = int((time.time() - start_time) * 1000)

            content = response.text if hasattr(response, 'text') else ""
            tool_calls = []

            # 与 OpenAI 路径一致，只读取第一候选中的调用请求，不在适配器内执行。
            # 这里以毫秒时间生成本地关联 ID，参数结构再序列化为统一的 JSON 字符串。
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        tool_calls.append(ToolCall(
                            id=f"call_{int(time.time()*1000)}",  # Gemini 没有显式的 call_id，生成一个
                            name=part.function_call.name,
                            arguments=json.dumps(dict(part.function_call.args))
                        ))

            usage = {}
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = {
                    "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                    "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                    "total_tokens": response.usage_metadata.total_token_count or 0
                }

            return LLMToolResponse(
                finish_reason=getattr(response.candidates[0], "finish_reason", None) if getattr(response, "candidates", None) else None,
                content=content if content else None,
                tool_calls=tool_calls,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms
            )

        except Exception as e:
            raise HelloAgentsException(f"Gemini工具调用失败: {str(e)}")

def create_adapter(api_key, base_url, timeout, model, provider=None) -> BaseLLMAdapter:
    """按协议创建适配器；显式 provider 优先，缺省时才从 URL 主机名推断。

    第三方代理域名通常无法透露真实协议，调用者应显式指定 provider。
    此函数只创建适配器对象，不初始化 SDK 客户端，也不发出网络请求。
    """
    # 厂商名别名只映射协议，例如 deepseek 复用 openai 协议，并不改变 model/base_url。
    adapters = {"openai": OpenAIAdapter, "anthropic": AnthropicAdapter, "gemini": GeminiAdapter}
    aliases = {"deepseek": "openai", "qwen": "openai", "ollama": "openai", "claude": "anthropic", "google": "gemini"}
    if provider is not None:
        # 显式指定但不合法就报错，不能悄悄回退成另一种协议。
        if not isinstance(provider, str):
            raise TypeError("provider must be a protocol name")
        provider = aliases.get(provider.lower(), provider.lower())
        if provider not in adapters:
            raise ValueError("provider must be openai, anthropic or gemini")
    else:
        # 解析 hostname 并按域名边界匹配，避免被 URL 路径或相似字符串误导。
        # anthropic.com 及其子域名 → Anthropic；googleapis.com 及其子域名 → Gemini；
        # 其余默认 OpenAI 兼容协议，而不是根据模型名称猜测。
        host = (urlparse(base_url or "").hostname or "").lower()
        provider = ("anthropic" if host == "anthropic.com" or host.endswith(".anthropic.com")
                    else "gemini" if host == "googleapis.com" or host.endswith(".googleapis.com")
                    else "openai")
    # 返回具有同一调用接口的具体实现，上层门面无需关心其消息格式差异。
    return adapters[provider](api_key, base_url, timeout, model)
