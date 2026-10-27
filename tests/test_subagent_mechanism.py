"""测试子代理（SubAgent）机制核心功能

测试内容覆盖：
1. Agent.run_as_subagent() 基础执行与响应结构；
2. 父子 Agent 上下文隔离（子代理执行不污染父历史记录）；
3. 只读工具过滤器（ReadOnlyFilter）与完全访问过滤器（FullAccessFilter）的作用域隔离与自动恢复；
4. 摘要返回与完整结果返回模式；
5. 最大执行步数覆盖与恢复；
6. 子代理执行耗时、Token 与工具使用等元数据聚合统计。
"""

import pytest
from hello_agents.core.agent import Agent
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.core.config import Config
from hello_agents.core.message import Message
from hello_agents.tools.registry import ToolRegistry
from hello_agents.tools.tool_filter import ReadOnlyFilter, FullAccessFilter
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode
from typing import Dict, Any, List
from dotenv import load_dotenv
load_dotenv()

# ==================== Mock 测试工具 ====================

class MockReadTool(Tool):
    """模拟只读测试工具"""
    
    def __init__(self):
        super().__init__(name="Read", description="读取文件")
    
    def get_parameters(self) -> List[ToolParameter]:
        return [ToolParameter(name="path", type="string", description="测试参数", required=True)]
    
    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text="文件内容")


class MockWriteTool(Tool):
    """模拟写入测试工具"""
    
    def __init__(self):
        super().__init__(name="Write", description="写入文件")
    
    def get_parameters(self) -> List[ToolParameter]:
        return [ToolParameter(name="path", type="string", description="测试参数", required=True)]
    
    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text="写入成功")


class MockBashTool(Tool):
    """模拟高危命令执行测试工具"""
    
    def __init__(self):
        super().__init__(name="Bash", description="执行命令")
    
    def get_parameters(self) -> List[ToolParameter]:
        return [ToolParameter(name="command", type="string", description="测试参数", required=True)]
    
    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text="命令执行")


# ==================== Mock 测试代理 ====================

class MockSimpleAgent(Agent):
    """简单的 Mock Agent，记录调用次数并在运行期间捕获可用工具列表"""
    
    def __init__(self, name: str, llm: HelloAgentsLLM, config: Config = None, tool_registry: ToolRegistry = None):
        super().__init__(name, llm, config=config, tool_registry=tool_registry)
        self.run_count = 0
    
    def run(self, input_text: str, **kwargs) -> str:
        """执行模拟运行逻辑并记录历史"""
        self.run_count += 1
        self.tools_during_run = self.tool_registry.list_tools() if self.tool_registry else []
        
        # 模拟添加历史消息
        self.add_message(Message(role="user", content=input_text))
        
        # 模拟工具调用记录
        if self.tool_registry and "使用工具" in input_text:
            tools = self.tool_registry.list_tools()
            if tools:
                tool_name = tools[0]
                self.add_message(Message(
                    role="assistant",
                    content=f"Action: {tool_name}[test]"
                ))
        
        result = f"完成任务: {input_text}"
        self.add_message(Message(role="assistant", content=result))
        
        return result


# ==================== 测试用例 ====================

class TestAgentRunAsSubagent:
    """测试 Agent.run_as_subagent() 方法及其边界隔离机制"""
    
    def test_basic_subagent_execution(self):
        """测试基本的子代理执行与标准返回数据结构"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False)  # 禁用自动注册
        agent = MockSimpleAgent("test", llm, config=config)
        
        # 执行子代理任务
        result = agent.run_as_subagent(
            task="测试任务",
            return_summary=True
        )
        
        # 验证返回结构包含 success、summary 与 metadata
        assert "success" in result
        assert "summary" in result
        assert "metadata" in result
        
        assert result["success"] is True
        assert "测试任务" in result["summary"]
        assert result["metadata"]["steps"] > 0
    
    def test_context_isolation(self):
        """测试上下文完全隔离：子代理内部的轮次不会追加到宿主父 Agent 的历史中"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False)
        agent = MockSimpleAgent("test", llm, config=config)
        
        # 主 Agent 添加初始历史消息
        agent.add_message(Message(role="user", content="主任务消息1"))
        agent.add_message(Message(role="assistant", content="主任务回复1"))
        
        original_history_len = len(agent.get_history())
        
        # 执行子代理任务
        result = agent.run_as_subagent(task="子任务")
        
        # 验证主 Agent 历史长度保持不变，未被子代理运行污染
        assert len(agent.get_history()) == original_history_len
        
        # 验证子代理确实独立执行并产生元数据
        assert result["metadata"]["steps"] > 0
    
    def test_tool_filter_readonly(self):
        """测试只读工具过滤：运行期间仅能看到只读工具，运行结束后完整注册表恢复"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        # 仅关注三个测试工具，关闭不相关的内置工具装配
        config = Config(subagent_enabled=False, skills_enabled=False,
                        todowrite_enabled=False, devlog_enabled=False,
                        trace_enabled=False, session_enabled=False)

        # 注册 Read, Write, Bash 三个工具
        registry = ToolRegistry()
        registry.register_tool(MockReadTool())
        registry.register_tool(MockWriteTool())
        registry.register_tool(MockBashTool())

        agent = MockSimpleAgent("test", llm, config=config, tool_registry=registry)

        # 验证初始工具包含全部 3 个
        initial_tools = registry.list_tools()
        assert len(initial_tools) == 3

        # 使用只读过滤器执行子代理
        tool_filter = ReadOnlyFilter()
        result = agent.run_as_subagent(
            task="使用工具完成任务",
            tool_filter=tool_filter
        )

        assert result["success"] is True
        # 运行期间仅有 Read 工具可见
        assert agent.tools_during_run == ["Read"]

        # 验证执行退出后父级工具列表完全恢复
        final_tools = registry.list_tools()
        assert final_tools == initial_tools
        assert "Read" in final_tools
        assert "Write" in final_tools
        assert "Bash" in final_tools
    
    def test_tool_filter_full_access(self):
        """测试完全访问过滤器：仅拦截 Bash 危险工具，运行后恢复"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False, skills_enabled=False,
                        todowrite_enabled=False, devlog_enabled=False,
                        trace_enabled=False, session_enabled=False)

        registry = ToolRegistry()
        registry.register_tool(MockReadTool())
        registry.register_tool(MockWriteTool())
        registry.register_tool(MockBashTool())

        agent = MockSimpleAgent("test", llm, config=config, tool_registry=registry)

        initial_tools = registry.list_tools()
        assert len(initial_tools) == 3

        # 使用完全访问过滤器
        tool_filter = FullAccessFilter()
        result = agent.run_as_subagent(
            task="使用工具完成任务",
            tool_filter=tool_filter
        )

        # 验证执行成功且 Bash 被排除，仅 Read 与 Write 可见
        assert result["success"] is True
        assert agent.tools_during_run == ["Read", "Write"]

        # 验证执行后宿主工具表恢复
        final_tools = registry.list_tools()
        assert final_tools == initial_tools
    
    def test_return_full_result(self):
        """测试 return_summary=False 时返回子任务的完整详细文本"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False)
        agent = MockSimpleAgent("test", llm, config=config)
        
        result = agent.run_as_subagent(
            task="测试任务",
            return_summary=False
        )
        
        # 验证返回完整结果字段而非摘要字段
        assert "result" in result
        assert "summary" not in result
        assert "完成任务" in result["result"]
    
    def test_max_steps_override(self):
        """测试临时覆盖最大步数，并在退出后安全还原原值"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False)
        agent = MockSimpleAgent("test", llm, config=config)
        
        # 设置初始 max_steps
        agent.max_steps = 10
        
        # 使用覆盖值 5 执行子任务
        result = agent.run_as_subagent(
            task="测试任务",
            max_steps_override=5
        )
        
        # 验证执行结束后恢复为原值 10
        assert agent.max_steps == 10
    
    def test_metadata_collection(self):
        """测试子代理执行元数据采集（步数、耗时、Token 及工具调用记录）"""
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        config = Config(subagent_enabled=False)
        agent = MockSimpleAgent("test", llm, config=config)
        
        result = agent.run_as_subagent(task="测试任务")
        
        metadata = result["metadata"]
        
        # 验证元数据各关键指标字段均存在且有效
        assert "steps" in metadata
        assert "tokens" in metadata
        assert "duration_seconds" in metadata
        assert "tools_used" in metadata
        
        assert metadata["steps"] >= 0
        assert metadata["tokens"] >= 0
        assert metadata["duration_seconds"] >= 0
        assert isinstance(metadata["tools_used"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

