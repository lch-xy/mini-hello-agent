"""模型层的统一输出契约，隔离不同 SDK 的响应类型。

这些 dataclass 用于承载数据，不会像 Pydantic BaseModel 一样自动校验字段类型。
适配器负责把服务商响应转换为这里的对象，上层无需直接读取 SDK 私有结构。
"""

from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """模型提出的一次工具调用请求；创建该对象不会执行任何工具。"""
    # id 用来把本次请求与之后的工具结果关联起来，不能只靠工具名称配对。
    id: str
    # name 是模型选择的工具名，后续由工具注册表查找对应实现。
    name: str
    # 参数保留为 JSON 字符串，由后续工具执行层负责解析、校验和执行。
    arguments: str


@dataclass
class LLMToolResponse:
    """一次支持工具选择的模型响应，可同时包含文本与多个工具请求。

    模型可能直接回答，因此 tool_calls 可以为空；也可能只请求工具，此时
    content 可以为 None。是否继续调用模型，由后续 Agent 循环决定。
    """
    content: Optional[str]
    tool_calls: List[ToolCall]
    model: str
    # default_factory 为每个响应创建独立字典，避免不同请求共享统计数据。
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    # 保留服务商的终止原因；不能仅凭这个字段认定用户任务已经成功。
    finish_reason: Optional[str] = None
    # 仅保存服务商显式返回的推理文本；缺失时不自行生成。
    reasoning_content: Optional[str] = None


@dataclass
class LLMResponse:
    """
    统一的LLM响应对象
    
    用于普通的非流式响应。不要混淆 content（回答）与 reasoning_content
    （服务商可选返回的推理文本），它们可能同时存在。
    """
    
    content: str
    """回复内容"""
    
    model: str
    """适配器填写的模型名称"""
    # 该值由适配器填写；普通调用目前填入请求的模型名，不保证是服务端解析后的名称。
    
    usage: Dict[str, Any] = field(default_factory=dict)
    """Token使用统计: {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}"""
    # 可以保留服务商附加或嵌套指标。指标缺失表示未报告，不应据此推断用量为零。
    
    latency_ms: int = 0
    """调用耗时（毫秒）"""
    
    # 只有服务商响应带有该信息时才会填充，不代表框架主动开启了推理模式。
    reasoning_content: Optional[str] = None
    # 如 stop、length、tool_calls 等；名称和含义取决于服务商协议。
    finish_reason: Optional[str] = None
    
    def __str__(self) -> str:
        """直接打印回答文本。"""
        return self.content
    
    def __repr__(self) -> str:
        """详细信息展示"""
        # 调试摘要仅显示长度、耗时和统计，不在 repr 中展开整段回答或推理文本。
        parts = [
            f"LLMResponse(model={self.model}",
            f"latency={self.latency_ms}ms",
            f"tokens={self.usage.get('total_tokens', 0)}",
        ]
        if self.reasoning_content:
            parts.append("has_reasoning=True")
        parts.append(f"content_length={len(self.content)})")
        return ", ".join(parts)
    
    def to_dict(self) -> Dict:
        """转换为字典格式，方便日志记录"""
        result = {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
        }
        # None 和空字符串都省略，避免把未报告的推理文本写成一段有效内容。
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        return result


@dataclass
class StreamStats:
    """
    流式调用的统计信息
    
    P04 将在流式调用结束后填充 llm.last_call_stats；P01 仅预留此数据结构，
    当前还没有流式调用方法。文本分块与最终统计是两种不同的数据。
    """
    
    model: str
    """实际使用的模型名称"""
    
    usage: Dict[str, Any] = field(default_factory=dict)
    """Token使用统计"""
    
    latency_ms: int = 0
    """调用耗时（毫秒）"""
    
    # 完整推理文本与终止原因会随流式调用结束而汇总。
    reasoning_content: Optional[str] = None
    finish_reason: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        result = {
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
        }
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        return result
