"""P06 观察截断器 UTF-8 字符边界与截断方向专项测试。

验证 ObservationTruncator 在处理多字节 UTF-8 字符（如中文 3 字节）时的安全截断与持久化：
1. 字节上限严格受控（preview 字节数不超过 max_bytes）；
2. 杜绝因粗暴字节切片将多字节字符切碎导致的 UnicodeDecodeError（字符边界安全对齐）；
3. 截断方向（head / tail / head_tail）语义正确保留头部、尾部或首尾内容；
4. 全量原始内容完整无损写入磁盘文件。
"""
import asyncio
from copy import deepcopy
import json
import pytest
from hello_agents import Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context import ContextBuilder, ContextConfig, ContextPacket, ContextBudgetExceeded, ContextBuildResult
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall


@pytest.mark.parametrize("direction", ["head", "tail", "head_tail"])
def test_truncation_limits_utf8_bytes_and_preserves_direction(tmp_path, direction):
    """验证：按字节截断多字节中文字符串时严格保证字节上限、字符边界安全与方向语义。"""
    from hello_agents.context.truncator import ObservationTruncator

    tool = ObservationTruncator(
        max_bytes=40,
        max_lines=3,
        truncate_direction=direction,
        output_dir=str(tmp_path),
    )
    # 构造包含大量中文字符的超长字符串（每个汉字占用 3 个 UTF-8 字节）
    value = "开头" + "长" * 100 + "结尾"
    result = tool.truncate("source", value)
    
    # 断言 1：生成的预览字符串 UTF-8 编码字节数不超过设定的 40 字节
    assert len(result["preview"].encode("utf8")) <= 40
    
    # 断言 2：持久化到磁盘的 JSON 记录包含完整无损的原始字符串
    assert (
        json.loads(
            __import__("pathlib")
            .Path(result["full_output_path"])
            .read_text(encoding="utf8")
        )["output"]
        == value
    )
    
    # 断言 3：截断方向前缀/后缀保留语义符合预期
    if direction != "tail":
        assert result["preview"].startswith("开头")
    if direction != "head":
        assert result["preview"].endswith("结尾")

