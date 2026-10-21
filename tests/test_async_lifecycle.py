"""异步生命周期测试套件。

验证异步生命周期基础设施与核心执行契约：
1. AgentEvent 事件系统的构建、属性提取及字典序列化契约；
2. ExecutionContext 执行上下文的步数递增、Token 累计与元数据（metadata）读写；
3. 工具异步执行接口（Tool.arun）及耗时统计包装（Tool.arun_with_timing）的时延与状态校验。
"""

import pytest
import asyncio
import time
from hello_agents.core.lifecycle import AgentEvent, EventType, ExecutionContext
from hello_agents.core.agent import Agent
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.core.config import Config
from hello_agents.agents.react_agent import ReActAgent
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.registry import ToolRegistry


# ==================== 测试工具 ====================

class MockTool(Tool):
    """测试用模拟工具：模拟指定延迟后返回成功回执与调用计数。"""
    
    def __init__(self, name: str = "MockTool", delay: float = 0.1):
        super().__init__(name, f"模拟工具 {name}")
        self.delay = delay
        self.call_count = 0
    
    def run(self, parameters: dict) -> ToolResponse:
        self.call_count += 1
        time.sleep(self.delay)
        return ToolResponse.success(
            text=f"MockTool executed with {parameters}",
            data={"call_count": self.call_count}
        )
    
    def get_parameters(self):
        return [
            ToolParameter(name="query", type="string", description="查询参数")
        ]


class SlowTool(Tool):
    """测试用慢速工具：执行 1.0 秒耗时操作，用于测试并发与超时行为。"""
    
    def __init__(self):
        super().__init__("SlowTool", "慢速工具")
    
    def run(self, parameters: dict) -> ToolResponse:
        time.sleep(1.0)  # 模拟耗时操作
        return ToolResponse.success(text="SlowTool completed")
    
    def get_parameters(self):
        return [
            ToolParameter(name="data", type="string", description="数据")
        ]


# ==================== 测试类 ====================

class TestAgentEvent:
    """测试 AgentEvent 事件系统的数据封装与序列化能力。"""
    
    def test_event_creation(self):
        """测试事件实例的正确创建及默认时间戳生成。"""
        event = AgentEvent.create(
            EventType.AGENT_START,
            "test_agent",
            input_text="Hello"
        )
        
        # 校验事件类型、Agent 标识、自定义数据与有效时间戳
        assert event.type == EventType.AGENT_START
        assert event.agent_name == "test_agent"
        assert event.data["input_text"] == "Hello"
        assert event.timestamp > 0
    
    def test_event_to_dict(self):
        """测试事件向原生字典格式的序列化输出。"""
        event = AgentEvent.create(
            EventType.TOOL_CALL,
            "test_agent",
            tool_name="search",
            tool_args={"query": "test"}
        )
        
        event_dict = event.to_dict()
        
        # 校验序列化后字典键值与事件枚举值
        assert event_dict["type"] == "tool_call"
        assert event_dict["agent_name"] == "test_agent"
        assert event_dict["data"]["tool_name"] == "search"
        assert event_dict["data"]["tool_args"]["query"] == "test"


class TestExecutionContext:
    """测试 ExecutionContext 执行上下文状态的跟踪与变迁。"""
    
    def test_context_creation(self):
        """测试执行上下文初始状态。"""
        ctx = ExecutionContext(input_text="Hello")
        
        # 初始步数为 0，累计 Token 消耗为 0
        assert ctx.input_text == "Hello"
        assert ctx.current_step == 0
        assert ctx.total_tokens == 0
    
    def test_context_operations(self):
        """测试上下文步数累加、Token 统计与元数据存取操作。"""
        ctx = ExecutionContext(input_text="Hello")
        
        # 步数递增
        ctx.increment_step()
        assert ctx.current_step == 1
        
        # 累加 Token 计数
        ctx.add_tokens(100)
        assert ctx.total_tokens == 100
        
        # 元数据键值读取与缺失默认值回退
        ctx.set_metadata("key", "value")
        assert ctx.get_metadata("key") == "value"
        assert ctx.get_metadata("missing", "default") == "default"


@pytest.mark.asyncio
class TestAsyncLLM:
    """测试 LLM 异步接口（需真实服务商配置时跳过）。"""
    
    async def test_ainvoke_basic(self):
        """测试基础异步调用"""
        pytest.skip("需要真实 LLM 配置")
    
    async def test_astream_invoke(self):
        """测试异步流式调用"""
        pytest.skip("需要真实 LLM 配置")


@pytest.mark.asyncio
class TestAsyncTool:
    """测试工具异步执行协议（Tool.arun 及相关包装器）。"""
    
    async def test_tool_arun(self):
        """验证工具异步执行 Tool.arun 能够正确返回成功回执与符合预期的耗时。"""
        tool = MockTool(delay=0.1)
        
        start_time = time.time()
        response = await tool.arun({"query": "test"})
        elapsed = time.time() - start_time
        
        assert response.status.value == "success"
        assert "MockTool executed" in response.text
        assert elapsed >= 0.1  # 至少等待了 delay 时间
    
    async def test_tool_arun_with_timing(self):
        """验证 Tool.arun_with_timing 能够准确在 stats 中注入耗时统计毫秒数。"""
        tool = MockTool(delay=0.1)
        
        response = await tool.arun_with_timing({"query": "test"})
        
        assert response.stats is not None
        assert "time_ms" in response.stats
        assert response.stats["time_ms"] >= 100  # 至少 100ms
        assert response.context["tool_name"] == "MockTool"


@pytest.mark.asyncio
class TestAsyncAgent:
    """测试 Agent 异步基础功能与生命周期钩子。"""
    
    async def test_lifecycle_hooks(self):
        """测试生命周期钩子（离线测试跳过）"""
        pytest.skip("需要真实 LLM 配置")



