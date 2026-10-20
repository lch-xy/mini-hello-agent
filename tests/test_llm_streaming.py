"""LLM 流式响应兼容性测试套件。

验证 OpenAI 兼容协议适配器在处理边缘流式分块时的健壮性：
1. choices 为空列表或缺少 choices 属性的分块不会导致迭代器崩溃；
2. 仅携带 usage 统计而不包含正文文本的尾部分块能够被正确提取并沉淀到 last_stats；
3. 同步流式 (stream_invoke) 与原生异步流式 (astream_invoke) 具有完全等价的解析和容错行为。
"""

import asyncio
from types import SimpleNamespace

from hello_agents.core.llm_adapters import OpenAIAdapter


def _content_chunk(text):
    """构造携带正文增量文本的模拟流式 Chunk。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
        usage=None,
    )


def _usage_chunk():
    """构造仅携带 Token 消耗统计而无正文选择的模拟流式 Chunk。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
        ),
    )


class FakeCompletions:
    """模拟同步 chat.completions 接口。"""
    def __init__(self, chunks):
        self.chunks = chunks

    def create(self, **kwargs):
        return iter(self.chunks)


class FakeChat:
    """模拟同步 chat 命名空间。"""
    def __init__(self, chunks):
        self.completions = FakeCompletions(chunks)


class FakeClient:
    """模拟同步 OpenAI 客户端。"""
    def __init__(self, chunks):
        self.chat = FakeChat(chunks)


class FakeAsyncStream:
    """模拟异步迭代器流。"""
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        self._iterator = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeAsyncCompletions:
    """模拟异步 chat.completions 接口。"""
    def __init__(self, chunks):
        self.chunks = chunks

    async def create(self, **kwargs):
        return FakeAsyncStream(self.chunks)


class FakeAsyncChat:
    """模拟异步 chat 命名空间。"""
    def __init__(self, chunks):
        self.completions = FakeAsyncCompletions(chunks)


class FakeAsyncClient:
    """模拟异步 AsyncOpenAI 客户端。"""
    def __init__(self, chunks):
        self.chat = FakeAsyncChat(chunks)


def test_openai_stream_skips_empty_or_missing_choices():
    """验证：同步流式解析跳过空 choices 与缺少 choices 的分块，并正确捕获 usage。"""
    adapter = OpenAIAdapter("test-key", "https://api.test/v1", 60, "test-model")
    # 模拟包含空 choices、无 choices、有效 content 和单独 usage 的混合流
    adapter._client = FakeClient(
        [
            SimpleNamespace(choices=[], usage=None),
            SimpleNamespace(usage=None),
            _content_chunk("hello"),
            _usage_chunk(),
        ]
    )

    chunks = list(adapter.stream_invoke([{"role": "user", "content": "hi"}]))

    # 断言：仅有效文本 chunk 被产出，且 usage 统计成功从尾部分块提取
    assert chunks == ["hello"]
    assert adapter.last_stats.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }


def test_openai_astream_skips_empty_or_missing_choices():
    """验证：异步流式 (astream_invoke) 解析跳过空 choices 分块，并正确捕获 usage。"""
    async def collect_chunks(adapter):
        return [
            chunk
            async for chunk in adapter.astream_invoke(
                [{"role": "user", "content": "hi"}]
            )
        ]

    adapter = OpenAIAdapter("test-key", "https://api.test/v1", 60, "test-model")
    adapter._async_client = FakeAsyncClient(
        [
            SimpleNamespace(choices=[], usage=None),
            SimpleNamespace(usage=None),
            _content_chunk("hello"),
            _usage_chunk(),
        ]
    )

    chunks = asyncio.run(collect_chunks(adapter))

    # 断言：异步模式下解析与统计结果完全一致
    assert chunks == ["hello"]
    assert adapter.last_stats.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
