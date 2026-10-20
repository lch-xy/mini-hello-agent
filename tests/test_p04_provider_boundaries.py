"""P04 模型供应商边界契约测试。

验证与底层模型供应商交互时的边界契约与资源生命周期：
1. 异步回退流（astream_invoke）在消费端提前关闭（aclose）时，能迅速停止后台生产者线程并释放数据源；
2. 服务商推理思维链（reasoning_content）的隔离性：在多轮对话中原样回传给模型历史，但不泄露给对外公开的文本或流式块；
3. 同步流式生成器在提早 close 时，确保调用底层 SDK 响应对象的 close() 方法以关闭网络连接。
"""
import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hello_agents import SimpleAgent, Config, ToolRegistry, AgentComponents
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.core.runtime import deadline
from hello_agents.core.llm_adapters import BaseLLMAdapter


@pytest.mark.asyncio
async def test_fallback_stream_stops_producer_and_closes_source():
    """验证：异步回退流在外部提前 aclose 时，后台生产者线程协同停止且数据源被关闭。"""
    class Adapter(BaseLLMAdapter):
        def create_client(self): pass
        def invoke(self, *args, **kwargs): pass
        def invoke_with_tools(self, *args, **kwargs): pass
        def stream_invoke(self, *args, **kwargs):
            try:
                for i in range(100):
                    self.produced += 1
                    yield str(i)
                    time.sleep(0.005)
            finally:
                # 标记数据源生成器已完成清理
                self.finished = True
                
    adapter = Adapter('synthetic', None, 1, 'test')
    adapter.produced, adapter.finished = 0, False
    stream = adapter.astream_invoke([])
    
    # 仅读取第一个分块即关闭流
    await anext(stream)
    await stream.aclose()
    await asyncio.sleep(0.15)
    
    # 断言：生产者未把全部 100 个元素产完即响应停止信号并执行了 finally 清理
    assert adapter.finished and adapter.produced < 100



@pytest.mark.parametrize('mode', ['sync', 'async', 'stream'])
def test_provider_reasoning_is_returned_to_model_not_public_chunks(mode):
    """验证：供应商思维链 reasoning_content 仅保留在历史中回传给模型，不混入公开发送给用户的文本。"""
    from copy import deepcopy
    from hello_agents import ToolRegistry
    from hello_agents.tools import Tool, ToolResponse
    from hello_agents.core.llm_response import LLMToolResponse, ToolCall
    
    class Lookup(Tool):
        """测试用无参查询工具。"""
        def __init__(self): super().__init__('lookup', 'lookup a fact')
        def get_parameters(self): return []
        def run(self, parameters): return ToolResponse.success('fact')
        
    class Scripted:
        """脚本化模型：第一轮返回带有 reasoning_content 的工具调用，第二轮返回最终答复。"""
        model = 'scripted'
        def __init__(self): self.inputs = []
        def invoke_with_tools(self, messages, tools, **kw):
            self.inputs.append(deepcopy(messages))
            if len(self.inputs) == 1:
                return LLMToolResponse(None, [ToolCall('c1', 'lookup', '{}')], self.model, reasoning_content='private-field')
            return LLMToolResponse('public answer', [], self.model, reasoning_content='final-private-field')
            
    llm = Scripted()
    registry = ToolRegistry()
    registry.register_tool(Lookup())
    agent = SimpleAgent('test', llm, tool_registry=registry)
    
    # 针对三种入口分别测试
    if mode == 'sync': result = agent.run('task')
    elif mode == 'async': result = asyncio.run(agent.arun('task'))
    else: result = ''.join(agent.stream_run('task'))
    
    # 断言 1：公开输出只包含正文内容，绝不包含私有思维链文本
    assert result == 'public answer' and 'private-field' not in result
    # 断言 2：第二轮发给模型的历史中准确保留了前一轮的 reasoning_content 字段
    assert llm.inputs[1][-2]['reasoning_content'] == 'private-field'



def test_sync_stream_early_close_releases_sdk_response():
    """验证：同步流式生成器提前 close 会正确调用底层 SDK 响应流的 close 方法。"""
    from hello_agents.core.llm_adapters import OpenAIAdapter
    class Stream:
        closed = False
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='x'))], usage=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='y'))], usage=None)
        def close(self): self.closed = True
        
    adapter = OpenAIAdapter('test', 'https://example.invalid', 1, 'test')
    adapter._client = Mock()
    response = Stream()
    adapter._client.chat.completions.create.return_value = response
    
    stream = adapter.stream_invoke([])
    assert next(stream) == 'x'
    # 提早关闭生成器
    stream.close()
    
    # 断言：底层 SDK 响应流的 close() 被触发调用
    assert response.closed

