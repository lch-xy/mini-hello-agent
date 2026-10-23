"""端到端集成测试 - 验证 TraceLogger 与 Agent 的协同装配。

覆盖以下关键链路：
1. 配置透传：Config 中的 trace_enabled、trace_dir、trace_sanitize 正确作用于 TraceLogger；
2. 禁用行为：trace_enabled=False 时 agent.trace_logger 保持为 None，无多余性能开销；
3. 文件产出：验证运行结束后 trace-*.jsonl 与 trace-*.html 正常生成，且 HTML 包含统计面板与工具调用信息。
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from hello_agents.core.config import Config
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.agents.react_agent import ReActAgent
from hello_agents.tools.registry import ToolRegistry
from hello_agents.tools.builtin.calculator import CalculatorTool


class TestTraceIntegration:
    """TraceLogger 与 Agent 集成测试集"""
    
    def setup_method(self):
        """每个测试前创建临时目录，隔离日志输出。"""
        self.temp_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """每个测试后清理临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_react_agent_with_trace(self):
        """测试 ReActAgent 在启用 Trace 时正确初始化 TraceLogger 并透传配置"""
        # 配置开启追踪与脱敏，指定临时输出目录
        config = Config(
            trace_enabled=True,
            trace_dir=self.temp_dir,
            trace_sanitize=True
        )
        
        # 创建 LLM（使用 mock）
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        
        # 创建工具注册表并注册计算器工具
        tool_registry = ToolRegistry()
        tool_registry.register_tool(CalculatorTool())
        
        # 创建 Agent
        agent = ReActAgent(
            name="TestAgent",
            llm=llm,
            tool_registry=tool_registry,
            config=config
        )
        
        # 验证 TraceLogger 已成功创建且 session_id 格式符合规范
        assert agent.trace_logger is not None
        assert agent.trace_logger.session_id.startswith("s-")
        
        # 验证配置正确透传至 TraceLogger
        assert agent.trace_logger.sanitize == True
        assert agent.trace_logger.output_dir == Path(self.temp_dir)
    
    def test_trace_disabled(self):
        """测试禁用 Trace 时 Agent 不创建 TraceLogger 实例"""
        config = Config(trace_enabled=False)
        
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        agent = ReActAgent(
            name="TestAgent",
            llm=llm,
            config=config
        )
        
        # 验证 TraceLogger 未创建（保持为 None）
        assert agent.trace_logger is None
    
    def test_trace_files_generated(self):
        """测试 Trace 正常生成 JSONL 原始事件流与可视化 HTML 统计报告"""
        config = Config(
            trace_enabled=True,
            trace_dir=self.temp_dir
        )
        
        llm = HelloAgentsLLM(provider="openai", model="gpt-3.5-turbo")
        agent = ReActAgent(
            name="TestAgent",
            llm=llm,
            config=config
        )
        
        # 模拟记录事件并调用 finalize 完成报表生成
        if agent.trace_logger:
            agent.trace_logger.log_event("session_start", {"agent_name": "TestAgent"})
            agent.trace_logger.log_event("tool_call", {"tool_name": "Calculator"}, step=1)
            agent.trace_logger.finalize()
            
            # 验证对应 session_id 的 JSONL 与 HTML 文件均已落盘
            session_id = agent.trace_logger.session_id
            jsonl_path = Path(self.temp_dir) / f"trace-{session_id}.jsonl"
            html_path = Path(self.temp_dir) / f"trace-{session_id}.html"
            
            assert jsonl_path.exists()
            assert html_path.exists()
            
            # 验证 HTML 包含统计面板与调用的工具名
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
                assert "统计" in html_content
                assert "Calculator" in html_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


