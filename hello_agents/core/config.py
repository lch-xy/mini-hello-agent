"""框架级配置的数据模型；声明默认值并校验部分数值范围。"""

import os
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

class Config(BaseModel):
    """集中描述框架可用的配置项。

    P01 为对齐目标版本保留了后续模块的字段，但声明字段不等于功能已经实现。
    本类也不会自动把配置传给 HelloAgentsLLM；调用处需要显式读取和传递。
    """

    # 校验时拒绝 NaN、正无穷和负无穷，避免阈值比较、耗时控制失去意义。
    model_config = ConfigDict(allow_inf_nan=False)

    # LLM配置
    # 这是框架的默认模型选择；LLM_MODEL_ID 等连接参数由 HelloAgentsLLM 单独读取。
    default_model: Optional[str] = None
    default_provider: str = "openai"
    temperature: float = 0.7
    # None 表示没有在此设置上限，不表示模型可以无限输出。
    max_tokens: Optional[int] = None

    # 系统配置
    debug: bool = False
    log_level: str = "INFO"

    # 上下文工程配置
    # context_window 单位为 Token，min_retain_rounds 单位为轮数。
    # gt=0 表示严格大于零；阈值 0.8 表示达到窗口的 80%。
    # 本阶段只校验配置，实际计数、压缩及保留完整工具交互的逻辑将在后续实现。
    context_window: int = Field(default=128000, gt=0)
    compression_threshold: float = Field(default=0.8, gt=0, le=1)
    min_retain_rounds: int = Field(default=10, gt=0)
    enable_smart_compression: bool = False  # 是否启用智能摘要（需要额外LLM调用）

    # 智能摘要配置
    # 将摘要模型与主模型分开配置，便于在接入摘要模块时控制成本与输出长度。
    summary_llm_provider: Optional[str] = None  # 摘要专用 LLM 提供商
    summary_llm_model: Optional[str] = None  # 摘要专用 LLM 模型
    summary_max_tokens: int = Field(default=800, gt=0)  # 摘要最大 Token 数
    summary_temperature: float = 0.3  # 摘要生成温度（更确定性）

    # 工具输出截断配置
    # 行数和字节数是两种限制。截断给模型看的内容，完整输出可另存文件供回读。
    tool_output_max_lines: int = Field(default=2000, gt=0)
    tool_output_max_bytes: int = Field(default=51200, gt=0)
    tool_output_dir: str = "tool-output"  # 完整输出保存目录
    tool_output_truncate_direction: str = "head"  # 截断方向：head/tail/head_tail

    # 可观测性配置
    # 这些开关供后续 Trace 模块消费；仅创建 Config 不会生成任何日志文件。
    trace_enabled: bool = False  # 是否启用 Trace 记录
    trace_dir: str = "memory/traces"  # Trace 文件保存目录
    trace_sanitize: bool = True  # 是否脱敏敏感信息
    trace_html_include_raw_response: bool = False  # HTML 是否包含原始响应

    # Skills 知识外化配置
    # Skills 是后续按需加载知识的模块，不是当前模型适配器的一部分。
    skills_enabled: bool = False  # 是否启用 Skills 系统
    skills_dir: str = "skills"  # Skills 目录路径
    skills_auto_register: bool = True  # 是否自动注册 SkillTool

    # 熔断器配置
    # 连续失败计数达到阈值后暂时阻止调用；恢复等待时间单位为秒。
    # 此处只声明参数，工具注册表与熔断器的接线需要在对应阶段实现。
    circuit_enabled: bool = True  # 是否启用熔断器
    circuit_failure_threshold: int = Field(default=3, gt=0)  # 连续失败多少次后熔断
    circuit_recovery_timeout: int = Field(default=300, gt=0)  # 熔断后恢复时间（秒）

    # 会话持久化配置
    # enabled 是功能开关，dir 是数据位置；配置对象本身不创建目录或写会话。
    session_enabled: bool = False  # 是否启用会话持久化
    session_dir: str = "memory/sessions"  # 会话文件保存目录
    auto_save_enabled: bool = False  # 是否启用自动保存
    auto_save_interval: int = Field(default=10, gt=0)

    # 子代理机制配置
    # 子代理会有自己的执行步数上限；这里的 max_steps 不是 Token 上限。
    subagent_enabled: bool = False  # 是否启用子代理机制
    subagent_max_steps: int = Field(default=15, gt=0)  # 子代理默认最大步数
    subagent_use_light_llm: bool = False  # 是否使用轻量模型（默认关闭，沿用主模型）
    subagent_light_llm_provider: Optional[str] = None  # 轻量模型提供商
    subagent_light_llm_model: Optional[str] = None  # 轻量模型名称

    # TodoWrite 进度管理配置
    todowrite_enabled: bool = False  # 是否启用 TodoWrite 工具
    todowrite_persistence_dir: str = "memory/todos"  # 任务列表持久化目录

    # DevLog 开发日志配置
    devlog_enabled: bool = False  # 是否启用 DevLog 工具
    devlog_persistence_dir: str = "memory/devlogs"  # 开发日志持久化目录

    # 异步生命周期配置
    # 工具并发数控制同时执行数量；其余三个超时字段单位为秒，分别约束不同阶段。
    # llm_async_timeout 属于上层异步等待策略，与 SDK 单次请求 timeout 分开配置。
    max_concurrent_tools: int = Field(default=3, gt=0)
    hook_timeout_seconds: float = Field(default=5.0, gt=0)
    llm_async_timeout: float = Field(default=120, gt=0)
    tool_async_timeout: float = Field(default=30, gt=0)

    # 流式输出配置
    # P04 接入流式路径后再使用这些字段；它们不会让普通 invoke 自动变成流式调用。
    stream_include_thinking: bool = True  # 是否包含思考过程
    stream_include_tool_calls: bool = True  # 是否包含工具调用

    @classmethod
    def from_env(cls) -> "Config":
        """只读取下列四项环境变量，其余字段仍使用类中默认值。

        DEBUG 只有忽略大小写后恰好为 true 才启用；数值字符串先转换为
        float/int，再由 Pydantic 校验。这里没有扫描全部同名环境变量。
        """
        return cls(
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("MAX_TOKENS")) if os.getenv("MAX_TOKENS") else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        # model_dump 是 Pydantic v2 的序列化入口，返回所有配置字段的当前值。
        return self.model_dump()
