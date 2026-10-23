"""可观测性模块核心单元测试。

验证 TraceLogger 类的关键功能：
1. 会话标识生成：检验 session_id 规范格式（s-YYYYMMDD-HHMMSS-xxxx）；
2. 结构化日志写入：检验每条事件在 JSONL 文件中的完整记录与字段映射；
3. HTML 报表渲染：检验可视化 HTML 报表生成、样式结构及关键事件渲染；
4. 凭据敏感信息脱敏：检验自动打码（API Key、Bearer Token 等）；
5. 运行指标统计聚合：检验 token 消耗、步骤数、工具调用频次及错误列表计算；
6. 上下文管理器行为：检验 with 语句自动 finalize 以及异常捕获记录机制。
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from hello_agents.observability import TraceLogger


class TestTraceLogger:
    """TraceLogger 核心功能测试集"""
    
    def setup_method(self):
        """每个测试前创建独立的临时输出目录"""
        self.temp_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """每个测试后递归清理临时目录"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_session_id_generation(self):
        """测试会话 ID 的格式规范性（s-YYYYMMDD-HHMMSS-xxxx）"""
        logger = TraceLogger(output_dir=self.temp_dir)
        
        # 验证格式: s-YYYYMMDD-HHMMSS-xxxx
        assert logger.session_id.startswith("s-")
        parts = logger.session_id.split("-")
        assert len(parts) == 4
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS
        assert len(parts[3]) == 4  # 4 位随机后缀
        
        logger.finalize()
    
    def test_log_event_creates_jsonl(self):
        """测试事件正确序列化写入到 JSONL 日志文件中"""
        logger = TraceLogger(output_dir=self.temp_dir)
        
        # 记录会话开始与工具调用事件
        logger.log_event(
            "session_start",
            {"agent_name": "TestAgent", "config": {}}
        )
        logger.log_event(
            "tool_call",
            {"tool_name": "Calculator", "args": {"input": "2+2"}},
            step=1
        )
        
        logger.finalize()
        
        # 验证 JSONL 文件在目标目录生成
        jsonl_path = Path(self.temp_dir) / f"trace-{logger.session_id}.jsonl"
        assert jsonl_path.exists()
        
        # 验证事件行内容解析正确
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            assert len(lines) == 2
            
            # 解析第 1 个事件（session_start）
            event1 = json.loads(lines[0])
            assert event1["event"] == "session_start"
            assert event1["session_id"] == logger.session_id
            assert event1["payload"]["agent_name"] == "TestAgent"
            
            # 解析第 2 个事件（tool_call）
            event2 = json.loads(lines[1])
            assert event2["event"] == "tool_call"
            assert event2["step"] == 1
            assert event2["payload"]["tool_name"] == "Calculator"
    
    def test_html_generation(self):
        """测试 HTML 可视化报告的完整渲染与关键节点包含"""
        logger = TraceLogger(output_dir=self.temp_dir)
        
        # 模拟记录完整步骤事件链
        logger.log_event("session_start", {"agent_name": "TestAgent"})
        logger.log_event("tool_call", {"tool_name": "Calculator"}, step=1)
        logger.log_event("tool_result", {"result": "4"}, step=1)
        logger.log_event("model_output", {"usage": {"total_tokens": 100}}, step=1)
        
        logger.finalize()
        
        # 验证 HTML 文件已正确输出
        html_path = Path(self.temp_dir) / f"trace-{logger.session_id}.html"
        assert html_path.exists()
        
        # 验证 HTML 包含关键元素
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
            assert "<!DOCTYPE html>" in html_content
            assert logger.session_id in html_content
            assert "session_start" in html_content
            assert "tool_call" in html_content
            assert "Calculator" in html_content
            assert "统计" in html_content  # 统计面板
    
    def test_sanitize_api_key(self):
        """测试敏感凭据（API Key / Bearer Token）的自动脱敏打码保护"""
        logger = TraceLogger(output_dir=self.temp_dir, sanitize=True)
        
        # 记录包含明文 API Key 与 Bearer Token 的测试事件
        logger.log_event(
            "model_output",
            {
                "api_key": "sk-1234567890abcdef",
                "auth": "Bearer token_secret_123"
            }
        )
        
        logger.finalize()
        
        # 验证落盘的日志中敏感明文已被完全遮蔽替换
        jsonl_path = Path(self.temp_dir) / f"trace-{logger.session_id}.jsonl"
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            event = json.loads(f.read())
            payload_str = json.dumps(event["payload"])
            assert event["payload"]["api_key"] == "[REDACTED]"
            assert "Bearer ***" in payload_str
            assert "1234567890abcdef" not in payload_str
            assert "token_secret_123" not in payload_str
    
    def test_stats_computation(self):
        """测试运行指标统计逻辑（总步数、Tokens、耗费、调用分布与错误统计）"""
        logger = TraceLogger(output_dir=self.temp_dir)
        
        # 记录会话开始
        logger.log_event("session_start", {"agent_name": "TestAgent"})
        
        # 模拟记录 3 个步骤的调用与 Token 消耗
        for step in range(1, 4):
            logger.log_event("tool_call", {"tool_name": "Calculator"}, step=step)
            logger.log_event("tool_result", {"result": str(step * 2)}, step=step)
            logger.log_event(
                "model_output",
                {"usage": {"total_tokens": 100, "cost": 0.001}},
                step=step
            )
        
        # 模拟记录 1 处错误事件
        logger.log_event(
            "error",
            {"error_type": "TIMEOUT", "message": "Tool timeout"},
            step=2
        )
        
        # 记录会话结束
        logger.log_event("session_end", {"duration": 10.5})
        
        # 计算统计数据并验证指标
        stats = logger._compute_stats()
        
        assert stats["total_steps"] == 3
        assert stats["total_tokens"] == 300  # 3 * 100
        assert stats["total_cost"] == 0.003  # 3 * 0.001
        assert stats["model_calls"] == 3
        assert stats["tool_calls"]["Calculator"] == 3
        assert len(stats["errors"]) == 1
        assert stats["errors"][0]["type"] == "TIMEOUT"
        
        logger.finalize()
    
    def test_context_manager(self):
        """测试 TraceLogger 的上下文管理器支持（退出时自动 finalize）"""
        with TraceLogger(output_dir=self.temp_dir) as logger:
            logger.log_event("session_start", {"agent_name": "TestAgent"})
            logger.log_event("tool_call", {"tool_name": "Calculator"}, step=1)
        
        # 验证退出 with 块后，文件已被自动写入落盘
        jsonl_path = Path(self.temp_dir) / f"trace-{logger.session_id}.jsonl"
        html_path = Path(self.temp_dir) / f"trace-{logger.session_id}.html"
        assert jsonl_path.exists()
        assert html_path.exists()
    
    def test_context_manager_with_exception(self):
        """测试上下文管理器在作用域内抛出未捕获异常时自动记录错误事件"""
        try:
            with TraceLogger(output_dir=self.temp_dir) as logger:
                logger.log_event("session_start", {"agent_name": "TestAgent"})
                raise ValueError("Test exception")
        except ValueError:
            pass
        
        # 验证异常信息已被自动捕获并记入日志末尾
        jsonl_path = Path(self.temp_dir) / f"trace-{logger.session_id}.jsonl"
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # 最后一个事件应该是自动记录的 error
            last_event = json.loads(lines[-1])
            assert last_event["event"] == "error"
            assert last_event["payload"]["error_type"] == "ValueError"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


