"""框架内部的消息对象：保存内容、角色、时间和附加信息。"""

from typing import Optional, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel

# Literal 限定角色取值，Pydantic 在创建对象时也会校验这一约束。
# summary 是框架内部的摘要角色，发给模型前需要由上下文层整理为协议支持的消息。
MessageRole = Literal["user", "assistant", "system", "tool", "summary"]

class Message(BaseModel):
    """会话中的一条消息，用于框架内管理和持久化。

    它与 HelloAgentsLLM.invoke 接收的 API 消息字典有不同职责：本类还携带
    timestamp 和 metadata，不能把其 to_dict() 结果直接视为通用请求协议。
    """

    # BaseModel 根据类型声明校验字段；与后面的响应 dataclass 不同。
    content: str
    role: MessageRole
    # 直接创建时默认记录当前本地时间。类型仍是 datetime，显式传 None 会校验失败，
    # 即使这里声明了默认 None，也不等于使用 Optional[datetime]。
    timestamp: datetime = None
    # 存放业务附加信息。后续阶段也会用它保留工具交互所需的原始协议字段。
    metadata: Optional[Dict[str, Any]] = None

    def __init__(self, content: str, role: MessageRole, **kwargs):
        # 每次调用单独生成默认时间和字典，避免多条消息共享同一个可变默认值。
        # kwargs.get 只有在键缺失时才使用默认值，显式的 None 会原样送入字段校验。
        super().__init__(
            content=content,
            role=role,
            timestamp=kwargs.get('timestamp', datetime.now()),
            metadata=kwargs.get('metadata', {})
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为存储字典；包含框架字段，并非直接发送给模型的请求。"""
        # datetime 转为 ISO 字符串，使结果便于写入 JSON 或会话文件。
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """恢复存储字典，完成 to_dict 的反向转换。"""
        timestamp = data.get("timestamp")
        # 兼容已经是 datetime 的输入；字符串才需要解析。
        if timestamp and isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        # content、role 是必需键；缺失时会抛 KeyError，不会静默生成空消息。
        # 当前实现要求有效时间：缺少 timestamp 会显式传入 None 并校验失败，
        # 不会自动补当前时间。这里保持目标代码行为，仅说明这个边界。
        return cls(
            content=data["content"],
            role=data["role"],
            timestamp=timestamp,
            metadata=data.get("metadata")
        )

    def to_text(self) -> str:
        """格式化为文本（用于上下文构建）"""
        # 这是便于阅读的文本表示，不包含工具调用 ID 等完整协议数据。
        return f"[{self.role}] {self.content}"

    def __str__(self) -> str:
        # print(message) 和 str(message) 使用这个表示。
        return f"[{self.role}] {self.content}"
