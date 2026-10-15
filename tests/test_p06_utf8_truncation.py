"""P06 当前可运行的原始上下文契约，保留原定义。"""
import asyncio
from copy import deepcopy
import json
import pytest
from hello_agents import Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBuilder, ContextConfig, ContextPacket, ContextBudgetExceeded, ContextBuildResult
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall


@pytest.mark.parametrize("direction", ["head", "tail", "head_tail"])
def test_truncation_limits_utf8_bytes_and_preserves_direction(tmp_path, direction):
    from hello_agents.context.truncator import ObservationTruncator

    tool = ObservationTruncator(
        max_bytes=40,
        max_lines=3,
        truncate_direction=direction,
        output_dir=str(tmp_path),
    )
    value = "开头" + "长" * 100 + "结尾"
    result = tool.truncate("source", value)
    assert len(result["preview"].encode("utf8")) <= 40
    assert (
        json.loads(
            __import__("pathlib")
            .Path(result["full_output_path"])
            .read_text(encoding="utf8")
        )["output"]
        == value
    )
    if direction != "tail":
        assert result["preview"].startswith("开头")
    if direction != "head":
        assert result["preview"].endswith("结尾")

