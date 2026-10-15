"""P09：保留固定目标的当前原始集成定义。"""
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
    marker = '<img src=x onerror="window.marker=1">'
    with TraceLogger(str(tmp_path)) as logger:
        logger.log_event("tool_call", {"tool_name": marker, "api_key": "opaque-value", "nested": {"raw_response": "private-raw"}})
        logger.log_event("error", {"message": marker, "error_type": marker, "token": "tvly-dev-synthetic", "text": "Bearer first.second.third"})
    logger.finalize()
    html = logger.html_path.read_text(encoding="utf-8")
    raw = logger.jsonl_path.read_text(encoding="utf-8")
    assert '<img' not in html and '&lt;img' in html
    assert 'private-raw' not in html and 'private-raw' in raw
    assert not any(value in raw for value in ('opaque-value', 'tvly-dev-synthetic', 'first.second.third'))



def test_trace_custom_sanitizer_and_raw_opt_in(tmp_path):
    def redact(event):
        event['payload']['personal'] = '[REMOVED]'
        return event
    with TraceLogger(str(tmp_path), html_include_raw_response=True, sanitizer=redact) as logger:
        logger.log_event('test', {'raw_response': 'visible', 'personal': 'private'})
    assert 'visible' in logger.html_path.read_text(encoding='utf-8')
    assert 'private' not in logger.jsonl_path.read_text(encoding='utf-8')



def test_import_does_not_change_host_logging():
    result = subprocess.run([sys.executable, '-c', 'import logging; logging.getLogger("httpx").setLevel(10); import hello_agents; assert logging.getLogger("httpx").level == 10'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

