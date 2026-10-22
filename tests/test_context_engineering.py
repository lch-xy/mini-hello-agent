"""上下文工程（Context Engineering）核心模块综合测试套件。

本套件全面验收上下文层的基础设施与协同行为：
1. Message 类扩展：支持 summary 特殊角色标识、携带 metadata 字典的序列化与反序列化；
2. HistoryManager 历史管理器：消息追加与获取、多轮对话轮次估算（estimate_rounds）、
   轮次边界探测（find_round_boundaries）、滑动窗口压缩（保留最近 N 轮并将早期历史折叠为 summary 消息）、
   轮次不足时的防早衰保护及状态序列化恢复；
3. ObservationTruncator 观察截断器：对冗长工具输出按行数或字节进行 head / tail / head_tail
   多方向截断，生成轻量预览，并将完整原始输出持久化至本地磁盘供回溯审计；
4. Agent 端到端集成：验证 SimpleAgent 自动触发基于 Token 阈值的历史压缩与截断器协同。
"""

import pytest
import os
import json
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock


# 加载环境变量


from hello_agents.core.message import Message
from hello_agents.context.history import HistoryManager
from hello_agents.context.truncator import ObservationTruncator
from hello_agents.core.config import Config


class TestMessage:
    """测试 Message 类的元数据与摘要角色支持。"""
    
    def test_summary_role(self):
        """验证 Message 支持特殊的 summary 角色类型，用于承载历史压缩摘要。"""
        msg = Message("这是摘要", "summary")
        assert msg.role == "summary"
        assert msg.content == "这是摘要"
    
    def test_to_dict_with_metadata(self):
        """验证 to_dict 序列化输出包含 role、content、metadata 与 timestamp 等完整字段。"""
        msg = Message(
            "测试内容",
            "user",
            metadata={"key": "value"}
        )
        data = msg.to_dict()
        
        assert data["role"] == "user"
        assert data["content"] == "测试内容"
        assert data["metadata"] == {"key": "value"}
        assert "timestamp" in data
    
    def test_from_dict(self):
        """验证 from_dict 能从字典结构精确还原 Message 实例及其元数据。"""
        data = {
            "role": "assistant",
            "content": "回复内容",
            "timestamp": "2025-01-18T12:00:00",
            "metadata": {"test": True}
        }
        
        msg = Message.from_dict(data)
        assert msg.role == "assistant"
        assert msg.content == "回复内容"
        assert msg.metadata == {"test": True}
    
    def test_to_text(self):
        """验证 to_text 能够生成形如 '[role] content' 的标准纯文本表示。"""
        msg = Message("hello", "user")
        assert msg.to_text() == "[user] hello"


class TestHistoryManager:
    """测试 HistoryManager 历史管理器的数据流转、轮次划分与压缩机制。"""
    
    def test_append_and_get(self):
        """验证消息列表按序追加与读取的完整性。"""
        manager = HistoryManager()
        
        msg1 = Message("hello", "user")
        msg2 = Message("hi", "assistant")
        
        manager.append(msg1)
        manager.append(msg2)
        
        history = manager.get_history()
        assert len(history) == 2
        assert history[0].content == "hello"
        assert history[1].content == "hi"
    
    def test_estimate_rounds(self):
        """验证根据 user 消息数量精准估算对话轮次的能力。"""
        manager = HistoryManager()
        
        # 第 1 轮
        manager.append(Message("问题1", "user"))
        manager.append(Message("回答1", "assistant"))
        
        # 第 2 轮
        manager.append(Message("问题2", "user"))
        manager.append(Message("回答2", "assistant"))
        manager.append(Message("工具结果", "tool"))
        
        # 第 3 轮
        manager.append(Message("问题3", "user"))
        
        # 断言：估算轮次为 3 轮（检测到 3 条 user 消息）
        assert manager.estimate_rounds() == 3
    
    def test_find_round_boundaries(self):
        """验证能够正确识别每轮对话中首条 user 消息在历史列表中的索引边界。"""
        manager = HistoryManager()
        
        manager.append(Message("问题1", "user"))  # index 0
        manager.append(Message("回答1", "assistant"))
        manager.append(Message("问题2", "user"))  # index 2
        manager.append(Message("回答2", "assistant"))
        
        boundaries = manager.find_round_boundaries()
        assert boundaries == [0, 2]
    
    def test_compress(self):
        """验证滑动窗口压缩：折叠早期轮次为 summary 角色消息，严格保留最近 min_retain_rounds 轮。"""
        manager = HistoryManager(min_retain_rounds=2)
        
        # 创建 5 轮对话
        for i in range(5):
            manager.append(Message(f"问题{i+1}", "user"))
            manager.append(Message(f"回答{i+1}", "assistant"))
        
        assert manager.estimate_rounds() == 5
        
        # 压缩历史：将前 3 轮替换为摘要文本
        manager.compress("前面3轮的摘要")
        
        # 断言 1：首条消息转变为 summary 角色且包含摘要内容
        history = manager.get_history()
        assert history[0].role == "summary"
        assert "前面3轮的摘要" in history[0].content
        
        # 断言 2：保留了最近 2 轮明细（共 4 条消息）
        assert manager.estimate_rounds() == 2
        assert history[-1].content == "回答5"
    
    def test_compress_insufficient_rounds(self):
        """验证：当现有轮次未达到保留轮次阈值时，压缩操作直接忽略且不改变原始历史。"""
        manager = HistoryManager(min_retain_rounds=10)
        
        # 仅有 3 轮，小于 min_retain_rounds(10)
        for i in range(3):
            manager.append(Message(f"问题{i+1}", "user"))
            manager.append(Message(f"回答{i+1}", "assistant"))
        
        original_len = len(manager.get_history())
        
        # 触发压缩尝试
        manager.compress("摘要")
        
        # 断言：历史长度与内容未发生任何改变
        assert len(manager.get_history()) == original_len
    
    def test_to_dict_and_load(self):
        """验证 HistoryManager 的字典导出 (to_dict) 与状态反序列化 (load_from_dict) 往返一致性。"""
        manager = HistoryManager()
        
        manager.append(Message("hello", "user"))
        manager.append(Message("hi", "assistant"))
        
        # 序列化为字典快照
        data = manager.to_dict()
        assert "history" in data
        assert "created_at" in data
        assert "rounds" in data
        assert data["rounds"] == 1
        
        # 从字典快照重新反序列化
        new_manager = HistoryManager()
        new_manager.load_from_dict(data)
        
        history = new_manager.get_history()
        assert len(history) == 2
        assert history[0].content == "hello"
    
    def test_clear(self):
        """验证清空历史记录功能。"""
        manager = HistoryManager()
        manager.append(Message("test", "user"))
        
        assert len(manager.get_history()) == 1
        
        manager.clear()
        assert len(manager.get_history()) == 0


class TestObservationTruncator:
    """测试 ObservationTruncator 观察截断器的行数/字节截断与文件持久化机制。"""
    
    def setup_method(self):
        """为每个测试用例初始化截断器并指定临时输出目录。"""
        self.test_output_dir = "test-tool-output"
        self.truncator = ObservationTruncator(
            max_lines=10,
            max_bytes=1000,
            truncate_direction="head",
            output_dir=self.test_output_dir
        )
    
    def teardown_method(self):
        """在测试完成后清理临时输出目录中的文件。"""
        # 清理测试输出目录
        if os.path.exists(self.test_output_dir):
            for file in os.listdir(self.test_output_dir):
                os.remove(os.path.join(self.test_output_dir, file))
            os.rmdir(self.test_output_dir)
    
    def test_no_truncation_needed(self):
        """验证未超出阈值的简短输出直接原样返回，不标记截断且不写磁盘。"""
        output = "短输出\n只有几行"
        
        result = self.truncator.truncate("test_tool", output)
        
        assert result["truncated"] is False
        assert result["preview"] == output
        assert result["full_output_path"] is None
        assert "stats" in result
    
    def test_truncation_by_lines(self):
        """验证超出行数限制时触发截断，保留指定行数并保存全量输出到磁盘。"""
        # 生成 20 行输出（阈值为 10 行）
        lines = [f"Line {i+1}" for i in range(20)]
        output = "\n".join(lines)
        
        result = self.truncator.truncate("test_tool", output)
        
        # 断言 1：截断标志为 True，统计数据准确
        assert result["truncated"] is True
        assert result["stats"]["original_lines"] == 20
        assert result["stats"]["kept_lines"] == 10
        assert result["full_output_path"] is not None
        
        # 断言 2：完整内容文件已成功落地保存
        assert os.path.exists(result["full_output_path"])
        
        # 断言 3：预览文本仅保留头部前 10 行
        preview_lines = result["preview"].split("\n")
        assert len(preview_lines) == 10
        assert preview_lines[0] == "Line 1"
    
    def test_truncation_direction_tail(self):
        """验证 tail 截断方向：优先保留输出尾部的最后 N 行。"""
        truncator = ObservationTruncator(
            max_lines=5,
            truncate_direction="tail",
            output_dir=self.test_output_dir
        )
        
        lines = [f"Line {i+1}" for i in range(10)]
        output = "\n".join(lines)
        
        result = truncator.truncate("test_tool", output)
        
        preview_lines = result["preview"].split("\n")
        assert len(preview_lines) == 5
        assert preview_lines[0] == "Line 6"  # 保留最后 5 行
        assert preview_lines[-1] == "Line 10"
    
    def test_truncation_direction_head_tail(self):
        """验证 head_tail 截断方向：保留首尾两端，并在中间插入省略提示。"""
        truncator = ObservationTruncator(
            max_lines=6,  # 前3行 + 中间省略 + 后3行
            truncate_direction="head_tail",
            output_dir=self.test_output_dir
        )
        
        lines = [f"Line {i+1}" for i in range(20)]
        output = "\n".join(lines)
        
        result = truncator.truncate("test_tool", output)
        
        preview_lines = result["preview"].split("\n")
        assert "...(中间省略)..." in preview_lines
    
    def test_save_full_output(self):
        """验证将完整工具执行结果及元数据（工具名、时间戳等）序列化保存为 JSON。"""
        output = "\n".join([f"Line {i+1}" for i in range(20)])
        metadata = {"query": "test", "timestamp": "2025-01-18"}
        
        result = self.truncator.truncate("search_tool", output, metadata)
        
        # 验证文件存在且内容符合规范
        filepath = result["full_output_path"]
        assert os.path.exists(filepath)
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert data["tool"] == "search_tool"
        assert data["output"] == output
        assert data["metadata"] == metadata
        assert "timestamp" in data


class TestAgentIntegration:
    """Agent 与上下文工程组件（HistoryManager、ObservationTruncator）集成测试。"""

    def test_agent_history_manager_integration(self):
        """验证 Agent 默认初始化并集成 HistoryManager 实例。"""
        from hello_agents import SimpleAgent, HelloAgentsLLM

        llm = Mock(model="offline-components")
        config = Config(min_retain_rounds=2)
        agent = SimpleAgent("测试助手", llm, config=config)

        # 验证 HistoryManager 已就绪且能够正常追加消息
        assert hasattr(agent, 'history_manager')
        assert isinstance(agent.history_manager, HistoryManager)

        agent.add_message(Message("test", "user"))
        assert len(agent.get_history()) == 1
        assert agent.history_manager.get_history()[0].content == "test"

    def test_agent_auto_compression(self):
        """验证消息累计跨越 Token 预算阈值时，Agent 自动触发滑动窗口压缩折叠早期历史。"""
        from hello_agents import SimpleAgent

        llm = Mock()
        llm.model = "test-capture"
        # 配置较小窗口以便触发压缩：window=240, threshold=0.75 (>= 180 tokens 触发)
        config = Config(
            context_window=240, compression_threshold=0.75, min_retain_rounds=2,
            trace_enabled=False, skills_enabled=False, session_enabled=False,
            subagent_enabled=False, todowrite_enabled=False, devlog_enabled=False,
        )
        agent = SimpleAgent("测试助手", llm, config=config)
        agent.add_message(Message("短问题", "user"))
        agent.add_message(Message("短回复", "assistant"))
        # 初始未达到阈值，不存在 summary 角色
        assert all(msg.role != "summary" for msg in agent.get_history())

        # 注入多条大体积消息，跨越 Token 阈值
        added = []
        for i in range(4):
            question = Message(f"question-{i}: " + "evidence " * 50, "user")
            answer = Message(f"answer-{i}: " + "result " * 50, "assistant")
            added.extend([question, answer])
            agent.add_message(question)
            agent.add_message(answer)

        # 断言 1：历史被自动压缩，首条消息为 summary
        history = agent.get_history()
        assert history[0].role == "summary"
        # 断言 2：保留了最近 2 轮对话内容
        assert [msg.content for msg in history[-4:]] == [msg.content for msg in added[-4:]]
        assert agent.history_manager.estimate_rounds() == 2
        llm.invoke.assert_not_called()

    def test_agent_truncator_integration(self):
        """验证 Agent 默认初始化并持有 ObservationTruncator 观察截断器实例。"""
        from hello_agents import SimpleAgent, HelloAgentsLLM

        llm = Mock(model="offline-components")
        agent = SimpleAgent("测试助手", llm)

        assert hasattr(agent, 'truncator')
        assert isinstance(agent.truncator, ObservationTruncator)

    @pytest.mark.live
    def test_agent_real_conversation_with_compression(self, live_llm_config):
        """验证真实模型交互后追加本地大体量历史能够正确触发滑动窗口压缩。"""
        from hello_agents import SimpleAgent, HelloAgentsLLM
        config = Config(min_retain_rounds=1, context_window=500,
                        compression_threshold=0.5, enable_smart_compression=False,
                        trace_enabled=False, session_enabled=False, skills_enabled=False,
                        subagent_enabled=False, todowrite_enabled=False, devlog_enabled=False)
        agent = SimpleAgent("上下文服务检查", HelloAgentsLLM(**live_llm_config), config=config)
        assert agent.run("用一句中文解释什么是上下文。")
        assert agent.last_run.status == "completed"
        for i in range(4):
            agent.add_message(Message(f"资料{i}: " + "evidence " * 150, "user"))
            agent.add_message(Message(f"确认{i}: " + "checked " * 150, "assistant"))
        # 断言：已触发压缩生成了 summary 角色消息，轮次受控
        assert any(message.role == "summary" for message in agent.get_history())
        assert agent.history_manager.estimate_rounds() <= config.min_retain_rounds


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])



@pytest.fixture(autouse=True)
def isolated_context_outputs(tmp_path, monkeypatch):
    """自动生效 Fixture：将测试产生的相对路径输出严格限制在 pytest 的临时目录中，避免污染工作区。"""
    monkeypatch.chdir(tmp_path)
