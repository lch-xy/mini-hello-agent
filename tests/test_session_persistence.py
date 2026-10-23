"""会话持久化（SessionStore）与 Agent 状态恢复测试套件。

系统验证两大维度的持久化能力：
1. SessionStore 底层持久化功能：
   - 会话状态保存与反序列化加载（save/load）；
   - 多会话元数据列表查询与过滤（list_sessions）；
   - 指定会话的幂等删除（delete）；
   - 配置与工具 Schema 一致性对比检测（check_config_consistency, check_tool_schema_consistency）。
2. Agent 会话持久化集成机制：
   - Agent 实例级主动保存与恢复（save_session, load_session）；
   - 禁用会话时的安全异常拦截；
   - 基于消息轮次的自动保存（auto_save）；
   - 工具集 Schema 16 位哈希指纹计算；
   - 会话时长、步骤数与 Token 消耗等元数据动态追踪。
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from hello_agents.core.session_store import SessionStore
from hello_agents.core.message import Message
from hello_agents.core.agent import Agent
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.core.config import Config
from hello_agents.agents.simple_agent import SimpleAgent
# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

class TestSessionStore:
    """SessionStore 基础持久化功能测试集"""
    
    def setup_method(self):
        """每个测试前创建临时目录并初始化 SessionStore"""
        self.temp_dir = tempfile.mkdtemp()
        self.store = SessionStore(session_dir=self.temp_dir)
    
    def teardown_method(self):
        """每个测试后清理临时目录"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_save_and_load_session(self):
        """测试保存与加载会话快照的完整生命周期"""
        # 准备测试数据
        agent_config = {
            "name": "test_agent",
            "llm_model": "gpt-4",
            "llm_provider": "openai"
        }
        
        history = [
            Message("Hello", "user"),
            Message("Hi there!", "assistant")
        ]
        
        metadata = {
            "created_at": datetime.now().isoformat(),
            "total_tokens": 100,
            "total_steps": 2
        }
        
        # 保存会话快照
        filepath = self.store.save(
            agent_config=agent_config,
            history=history,
            tool_schema_hash="abc123",
            read_cache={},
            metadata=metadata,
            session_name="test-session"
        )
        
        # 验证文件落盘存在
        assert Path(filepath).exists()
        
        # 加载会话快照
        loaded_data = self.store.load(filepath)
        
        # 验证反序列化数据各字段正确无误
        assert loaded_data["agent_config"]["name"] == "test_agent"
        assert len(loaded_data["history"]) == 2
        assert loaded_data["history"][0]["content"] == "Hello"
        assert loaded_data["tool_schema_hash"] == "abc123"
        assert loaded_data["metadata"]["total_tokens"] == 100
    
    def test_list_sessions(self):
        """测试列出所有已保存会话的元数据摘要"""
        # 保存多个会话文件
        for i in range(3):
            self.store.save(
                agent_config={"name": f"agent_{i}"},
                history=[],
                tool_schema_hash="hash",
                read_cache={},
                metadata={"total_tokens": i * 100},
                session_name=f"session-{i}"
            )
        
        # 获取会话列表
        sessions = self.store.list_sessions()
        
        # 验证列表数量与字段完整性
        assert len(sessions) == 3
        assert all("filename" in s for s in sessions)
        assert all("session_id" in s for s in sessions)
    
    def test_delete_session(self):
        """测试删除会话文件的有效性与二次删除的幂等返回"""
        # 保存测试会话
        self.store.save(
            agent_config={"name": "test"},
            history=[],
            tool_schema_hash="hash",
            read_cache={},
            metadata={},
            session_name="to-delete"
        )
        
        # 首次删除应返回 True 并删除文件
        result = self.store.delete("to-delete")
        assert result is True
        
        filepath = Path(self.temp_dir) / "to-delete.json"
        assert not filepath.exists()
        
        # 再次删除不存在的文件应安全返回 False
        result = self.store.delete("to-delete")
        assert result is False
    
    def test_check_config_consistency(self):
        """测试配置一致性检查（检测模型变更等差异）"""
        saved_config = {
            "llm_provider": "openai",
            "llm_model": "gpt-4",
            "max_steps": 10
        }
        
        # 相同配置时验证一致
        current_config = saved_config.copy()
        result = self.store.check_config_consistency(saved_config, current_config)
        assert result["consistent"] is True
        assert len(result["warnings"]) == 0
        
        # 模型变更时识别为不一致并输出警告提示
        current_config["llm_model"] = "gpt-3.5-turbo"
        result = self.store.check_config_consistency(saved_config, current_config)
        assert result["consistent"] is False
        assert len(result["warnings"]) > 0
    
    def test_check_tool_schema_consistency(self):
        """测试工具签名指纹变化检测"""
        saved_hash = "abc123"
        
        # 相同哈希：工具集一致
        result = self.store.check_tool_schema_consistency(saved_hash, "abc123")
        assert result["changed"] is False
        
        # 不同哈希：工具集发生变更并输出修复建议
        result = self.store.check_tool_schema_consistency(saved_hash, "def456")
        assert result["changed"] is True
        assert "建议" in result["recommendation"]


class TestAgentSessionPersistence:
    """Agent 会话持久化端到端集成测试集"""

    def setup_method(self):
        """测试前创建隔离的会话存储目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.config = Config(
            session_enabled=True,
            session_dir=self.temp_dir,
            trace_enabled=False  # 禁用 trace 避免干扰
        )

    def teardown_method(self):
        """测试后清理目录"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_agent_save_session(self):
        """测试 Agent 主动保存当前会话到文件"""
        # 创建 Agent
        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=self.config
        )

        # 添加多轮交互历史
        agent.add_message(Message("Hello", "user"))
        agent.add_message(Message("Hi!", "assistant"))

        # 保存会话
        filepath = agent.save_session("test-save")

        # 验证文件存在且内容正确
        assert Path(filepath).exists()

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["agent_config"]["name"] == "test_agent"
        assert len(data["history"]) == 2

    def test_agent_load_session(self):
        """测试新 Agent 实例加载已保存会话并恢复对话历史"""
        # 创建并保存前驱智能体状态
        llm = HelloAgentsLLM()
        agent1 = SimpleAgent(
            name="agent1",
            llm=llm,
            config=self.config
        )

        agent1.add_message(Message("Question 1", "user"))
        agent1.add_message(Message("Answer 1", "assistant"))
        filepath = agent1.save_session("test-load")

        # 创建全新 Agent 实例并恢复会话
        agent2 = SimpleAgent(
            name="agent2",
            llm=llm,
            config=self.config
        )

        agent2.load_session(filepath, check_consistency=False)

        # 验证新智能体的历史消息已完整恢复
        history = agent2.get_history()
        assert len(history) == 2
        assert history[0].content == "Question 1"
        assert history[1].content == "Answer 1"

    def test_agent_list_sessions(self):
        """测试 Agent 查询会话列表"""
        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=self.config
        )

        # 连续保存 3 个会话
        for i in range(3):
            agent.add_message(Message(f"Message {i}", "user"))
            agent.save_session(f"session-{i}")

        # 列出会话并验证数量
        sessions = agent.list_sessions()
        assert len(sessions) >= 3

    def test_session_disabled(self):
        """测试配置禁用会话时调用 save_session 明确抛出异常"""
        config = Config(session_enabled=False)
        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=config
        )

        # 验证抛出明确的未启用异常
        with pytest.raises(RuntimeError, match="会话持久化未启用"):
            agent.save_session("test")

    def test_auto_save(self):
        """测试消息轮次阈值触发自动保存会话机制"""
        config = Config(
            session_enabled=True,
            session_dir=self.temp_dir,
            auto_save_enabled=True,
            auto_save_interval=2,  # 每 2 条消息自动触发保存
            trace_enabled=False
        )

        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=config
        )

        # 添加 2 条消息触发自动保存
        agent.add_message(Message("Message 1", "user"))
        agent.add_message(Message("Message 2", "assistant"))

        # 验证自动保存的文件已在磁盘生成
        auto_save_path = Path(self.temp_dir) / "session-auto.json"
        assert auto_save_path.exists()

    def test_compute_tool_schema_hash(self):
        """测试工具集 Schema 16 位哈希指纹的计算与稳定性"""
        from hello_agents.tools.registry import ToolRegistry
        from hello_agents.tools.builtin.calculator import CalculatorTool

        llm = HelloAgentsLLM()
        registry = ToolRegistry()
        registry.register_tool(CalculatorTool())

        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=self.config,
            tool_registry=registry
        )

        # 验证哈希指纹非空且长度为 16 位十六进制
        hash1 = agent._compute_tool_schema_hash()
        assert hash1 != "no-tools"
        assert len(hash1) == 16

        # 相同工具配置应计算出完全相同的哈希
        hash2 = agent._compute_tool_schema_hash()
        assert hash1 == hash2

    def test_session_metadata_tracking(self):
        """测试会话元数据（创建时间、执行耗时、步骤与 Tokens）的持续追踪"""
        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="test_agent",
            llm=llm,
            config=self.config
        )

        # 验证初始元数据字段存在
        assert "created_at" in agent._session_metadata
        assert "total_tokens" in agent._session_metadata
        assert "total_steps" in agent._session_metadata

        # 保存会话
        filepath = agent.save_session("test-metadata")

        # 验证落盘元数据中包含持续时间（duration_seconds）
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert "duration_seconds" in data["metadata"]
        assert data["metadata"]["duration_seconds"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])



