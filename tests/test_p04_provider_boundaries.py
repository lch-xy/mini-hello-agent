"""P04：从固定快照提取当前阶段原定义，断言保持原样。"""
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
                self.finished = True
    adapter = Adapter('synthetic', None, 1, 'test')
    adapter.produced, adapter.finished = 0, False
    stream = adapter.astream_invoke([])
    await anext(stream)
    await stream.aclose()
    await asyncio.sleep(0.15)
    assert adapter.finished and adapter.produced < 100



@pytest.mark.parametrize('mode',['sync','async','stream'])
def test_provider_reasoning_is_returned_to_model_not_public_chunks(mode):
    from copy import deepcopy
    from hello_agents import ToolRegistry
    from hello_agents.tools import Tool, ToolResponse
    from hello_agents.core.llm_response import LLMToolResponse, ToolCall
    class Lookup(Tool):
        def __init__(self): super().__init__('lookup','lookup a fact')
        def get_parameters(self): return []
        def run(self,parameters): return ToolResponse.success('fact')
    class Scripted:
        model='scripted'
        def __init__(self): self.inputs=[]
        def invoke_with_tools(self,messages,tools,**kw):
            self.inputs.append(deepcopy(messages))
            if len(self.inputs)==1:
                return LLMToolResponse(None,[ToolCall('c1','lookup','{}')],self.model,reasoning_content='private-field')
            return LLMToolResponse('public answer',[],self.model,reasoning_content='final-private-field')
    llm=Scripted()
    registry=ToolRegistry();registry.register_tool(Lookup())
    agent=SimpleAgent('test',llm,tool_registry=registry)
    if mode=='sync': result=agent.run('task')
    elif mode=='async': result=asyncio.run(agent.arun('task'))
    else: result=''.join(agent.stream_run('task'))
    assert result=='public answer' and 'private-field' not in result
    assert llm.inputs[1][-2]['reasoning_content']=='private-field'



def test_sync_stream_early_close_releases_sdk_response():
    from hello_agents.core.llm_adapters import OpenAIAdapter
    class Stream:
        closed=False
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='x'))],usage=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='y'))],usage=None)
        def close(self): self.closed=True
    adapter=OpenAIAdapter('test','https://example.invalid',1,'test')
    adapter._client=Mock()
    response=Stream()
    adapter._client.chat.completions.create.return_value=response
    stream=adapter.stream_invoke([])
    assert next(stream)=='x'
    stream.close()
    assert response.closed

