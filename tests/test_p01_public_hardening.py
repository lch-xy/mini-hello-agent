"""提取参考 test_public_hardening.py 中 P01 的原始断言。"""

import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hello_agents import HelloAgentsLLM, Config
from hello_agents.core.llm_adapters import AnthropicAdapter

@pytest.mark.parametrize('method', ['invoke', 'invoke_with_tools', 'stream_invoke', 'ainvoke', 'ainvoke_with_tools', 'astream_invoke', 'astream_invoke_with_tools'])
def test_instance_options_are_overridden_at_every_entry(method):
    llm = HelloAgentsLLM(top_p=0.2, extra_body={'nested': 1})
    adapter = Mock()
    captured = []
    def call(*args, **kw):
        captured.append(kw)
        return SimpleNamespace(content='ok', model='test', usage={}, latency_ms=0, finish_reason='stop')
    def stream(*args, **kw):
        call(*args, **kw)
        yield 'ok'
    async def astream(*args, **kw):
        call(*args, **kw)
        yield 'ok'
    adapter.invoke = adapter.invoke_with_tools = call
    adapter.stream_invoke = stream
    adapter.astream_invoke = adapter.astream_invoke_with_tools = astream
    llm._adapter = adapter
    args = ([{'role':'user','content':'hello'}],)
    if method.endswith('with_tools'):
        args += ([],)
    result = getattr(llm, method)(*args, top_p=0.7)
    async def consume():
        if method.startswith('astream'):
            return [x async for x in result]
        return await result
    if method.startswith('a'):
        asyncio.run(consume())
    elif method.startswith('stream'):
        list(result)
    assert captured[0]['top_p'] == 0.7
    captured[0]['extra_body']['nested'] = 99
    assert llm.kwargs['extra_body']['nested'] == 1

def test_provider_proxy_selection_and_host_detection():
    with HelloAgentsLLM(provider='anthropic', base_url='https://proxy.invalid') as llm:
        assert llm.provider == 'anthropic'
    with HelloAgentsLLM(base_url='https://anthropic.com.evil.invalid') as llm:
        assert llm.provider == 'openai'
    with pytest.raises(ValueError):
        HelloAgentsLLM(provider='typo')

@pytest.mark.parametrize('choice,expected', [('none', {'type':'none'}), ('auto', {'type':'auto'}), ('required', {'type':'any'}), ({'type':'function','function':{'name':'read'}}, {'type':'tool','name':'read'})])
def test_anthropic_tool_choice_in_actual_request(choice, expected):
    adapter = AnthropicAdapter('synthetic', 'https://example.invalid', 1, 'model')
    adapter._client = Mock()
    adapter._client.messages.create.return_value = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason='end_turn')
    adapter.invoke_with_tools([{'role':'user','content':'hi'}], [{'type':'function','function':{'name':'read','parameters':{'type':'object'}}}], tool_choice=choice)
    assert adapter._client.messages.create.call_args.kwargs['tool_choice'] == expected

@pytest.mark.parametrize('value', [float('inf'), float('nan'), -1, 0])
def test_timeout_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        HelloAgentsLLM(timeout=value)
    with pytest.raises(ValueError):
        Config(llm_async_timeout=value)

def test_import_does_not_change_host_logging():
    result = subprocess.run([sys.executable, '-c', 'import logging; logging.getLogger("httpx").setLevel(10); import hello_agents; assert logging.getLogger("httpx").level == 10'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

@pytest.mark.asyncio
async def test_client_closure_idempotent_and_reuse_rejected():
    from unittest.mock import AsyncMock
    llm = HelloAgentsLLM()
    sync, async_client = Mock(), Mock(close=AsyncMock())
    llm._adapter._client, llm._adapter._async_client = sync, async_client
    with pytest.raises(RuntimeError, match='aclose'):
        llm.close()
    await llm.aclose()
    await llm.aclose()
    sync.close.assert_called_once()
    async_client.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match='closed'):
        llm.invoke([{'role':'user','content':'x'}])

def test_gemini_passes_options_and_rejects_unknown_fields():
    pytest.importorskip('google.genai')
    from hello_agents.core.llm_adapters import GeminiAdapter
    adapter=GeminiAdapter('synthetic','https://example.invalid',2,'model')
    adapter._client=Mock()
    adapter._client.models.generate_content.return_value=SimpleNamespace(text='ok',usage_metadata=None,candidates=[])
    adapter.invoke([{'role':'user','content':'hello'}],top_p=0.2,max_tokens=64)
    config=adapter._client.models.generate_content.call_args.kwargs['config']
    assert config.top_p==0.2 and config.max_output_tokens==64
    with pytest.raises(Exception,match='unsupported_audit_option'):
        adapter.invoke([{'role':'user','content':'hello'}],unsupported_audit_option=True)

def test_first_concurrent_requests_create_one_sdk_client():
    from concurrent.futures import ThreadPoolExecutor
    from hello_agents.core.llm_adapters import OpenAIAdapter
    adapter=OpenAIAdapter('test','https://example.invalid',1,'test')
    client=object()
    def create():
        time.sleep(0.01)
        return client
    adapter.create_client=Mock(side_effect=create)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(item is client for item in pool.map(lambda _:adapter._get_client(),range(16)))
    adapter.create_client.assert_called_once()
