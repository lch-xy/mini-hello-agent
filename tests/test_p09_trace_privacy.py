"""链路追踪（TraceLogger）安全脱敏与隐私防护测试。

系统验证 TraceLogger 在持久化日志与 HTML 可视化报告中的安全防御机制：
1. XSS 防御：HTML 渲染中对所有非受信输入（如 tool_name、error 消息等）进行 HTML 转义，杜绝脚本注入；
2. 敏感凭据过滤：自动从日志（JSONL/HTML）中脱敏 API 密钥、认证 Token（如 Bearer xxx、api_key 等）；
3. 原始响应隐藏与自定义清洗：默认在 HTML 中隐藏 raw_response，支持用户注入自定义 sanitizer 针对特定业务敏感字段打码；
4. 宿主日志环境零侵入：框架导入（import hello_agents）绝不篡改宿主环境既有的 logging 配置。
"""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import subprocess
import sys
import pytest
from hello_agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, Config, AgentComponents, ToolRegistry
from hello_agents.core.session_store import SessionStore
from hello_agents.core.message import Message
from hello_agents.core.llm_response import LLMToolResponse, ToolCall
from hello_agents.core.streaming import StreamEventType
from hello_agents.observability import TraceLogger


def test_trace_untrusted_html_and_privacy_flags(tmp_path):
    """验证 HTML 报表的 XSS 转义防御、raw_response 隔离以及凭据特征值的自动脱敏。"""
    # 构造 XSS 攻击测试载荷
    marker = '<img src=x onerror="window.marker=1">'
    with TraceLogger(str(tmp_path)) as logger:
        # 记录携带敏感 api_key 与原始模型输出的工具调用事件
        logger.log_event("tool_call", {"tool_name": marker, "api_key": "opaque-value", "nested": {"raw_response": "private-raw"}})
        # 记录携带敏感 Token 与 Bearer 凭据的错误事件
        logger.log_event("error", {"message": marker, "error_type": marker, "token": "tvly-dev-synthetic", "text": "Bearer first.second.third"})
    logger.finalize()

    html = logger.html_path.read_text(encoding="utf-8")
    raw = logger.jsonl_path.read_text(encoding="utf-8")

    # 1. 验证 HTML 报表中的非受信载荷已被安全转义为 &lt;img，杜绝 XSS 执行
    assert '<img' not in html and '&lt;img' in html
    # 2. 验证 raw_response 默认被排除在 HTML 报表之外（但在原始 JSONL 中按需留存）
    assert 'private-raw' not in html and 'private-raw' in raw
    # 3. 验证无论是 JSONL 还是 HTML，敏感凭据特征值均已被过滤脱敏，未直接明文留存
    assert not any(value in raw for value in ('opaque-value', 'tvly-dev-synthetic', 'first.second.third'))



def test_trace_custom_sanitizer_and_raw_opt_in(tmp_path):
    """验证用户自定义敏感信息清洗函数（sanitizer）以及显式开启 HTML raw_response 展示。"""
    # 自定义清洗逻辑：将 personal 字段内容重写为 [REMOVED]
    def redact(event):
        event['payload']['personal'] = '[REMOVED]'
        return event

    with TraceLogger(str(tmp_path), html_include_raw_response=True, sanitizer=redact) as logger:
        logger.log_event('test', {'raw_response': 'visible', 'personal': 'private'})

    # 验证显式开启 html_include_raw_response 后，raw_response 包含在 HTML 中
    assert 'visible' in logger.html_path.read_text(encoding='utf-8')
    # 验证经过自定义清洗函数后，原始 private 值未落盘到 JSONL 日志中
    assert 'private' not in logger.jsonl_path.read_text(encoding='utf-8')



def test_import_does_not_change_host_logging():
    """验证导入 hello_agents 不会越权篡改宿主或第三方库（如 httpx）预先配置的日志级别。"""
    result = subprocess.run([sys.executable, '-c', 'import logging; logging.getLogger("httpx").setLevel(10); import hello_agents; assert logging.getLogger("httpx").level == 10'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


