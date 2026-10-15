"""P01 核心模型契约。"""

# core 提供模型层的配置、输入消息和输出对象；具体协议细节封装在适配器内。
from .config import Config
from .exceptions import HelloAgentsException
from .llm import HelloAgentsLLM
from .llm_response import LLMResponse, StreamStats
from .message import Message

# StreamStats 目前只是数据结构，真正填充流式统计的流程将在 P04 实现。
__all__ = ["Config", "HelloAgentsException", "HelloAgentsLLM", "LLMResponse", "StreamStats", "Message"]
