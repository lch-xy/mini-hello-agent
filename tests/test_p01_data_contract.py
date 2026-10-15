"""提取 Message、位置参数、Config v2 和三 SDK 构造的原始断言。"""

import warnings
import pytest
from pydantic.warnings import PydanticDeprecatedSince20
from hello_agents import Message, Config
from hello_agents.core.llm_response import LLMResponse

class TestMessage:
    """测试 Message 类的增强功能"""
    
    def test_summary_role(self):
        """测试 summary role 支持"""
        msg = Message("这是摘要", "summary")
        assert msg.role == "summary"
        assert msg.content == "这是摘要"
    
    def test_to_dict_with_metadata(self):
        """测试 to_dict 包含完整信息"""
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
        """测试 from_dict 反序列化"""
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
        """测试 to_text 格式化"""
        msg = Message("hello", "user")
        assert msg.to_text() == "[user] hello"

def test_response_positional_compatibility():
    assert LLMResponse("x", "m", {}, 1, "reasoning").reasoning_content == "reasoning"

def test_config_to_dict_uses_pydantic_v2_serialization():
    with warnings.catch_warnings():
        warnings.simplefilter("error", PydanticDeprecatedSince20)
        data = Config().to_dict()

    assert data["default_model"] is None

@pytest.mark.parametrize("module,adapter_name,url", [
    ("openai", "OpenAIAdapter", "https://example.invalid/v1"),
    ("anthropic", "AnthropicAdapter", "https://api.anthropic.com"),
    ("google.genai", "GeminiAdapter", "https://generativelanguage.googleapis.com"),
])
def test_locked_sdk_client_constructors(module, adapter_name, url):
    # Actual SDK initialization, without sending prompts or opening a connection.
    pytest.importorskip(module)
    from hello_agents.core import llm_adapters
    adapter = getattr(llm_adapters, adapter_name)("offline-test-key", url, 10, "test-model")
    client = adapter.create_client()
    assert client is not None
    client.close()
