"""P13：保留当前阶段依赖已就绪的原始定义。"""
import asyncio
from dataclasses import replace
from copy import deepcopy
import json
import pytest
from hello_agents import SimpleAgent, Config, Message, ToolRegistry
from hello_agents.retrieval import RAGStore, RetrievalPipeline
from hello_agents.tools import RAGTool, ToolStatus
from hello_agents.context import ContextBuilder, ContextBuildResult, RetrievalContextProvider
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse, ToolCall


def test_chinese_retrieval_offsets_versions_and_reopen(tmp_path):
    path = tmp_path / "rag.db"
    store = RAGStore(path)
    original = "杭州旅行🧳\n  博物馆周一闭馆。\n平日无需预约；节假日必须提前预约。"
    store.add_document(
        original, "notice://museum", document_id="museum", chunk_size=22, overlap=6
    )
    results = store.search("博物馆预约")
    assert results
    for result in results:
        assert original[result.start : result.end] == result.content
        assert RAGStore(path).read_chunk(result.chunk_id).content == result.content
    old = results[0]
    store.add_document(
        "博物馆每天需要预约。", "notice://museum", "2", document_id="museum"
    )
    assert {r.version for r in store.search("博物馆预约")} == {"2"}
    assert store.read_chunk(old.chunk_id).version == "1"
    store.add_document(original, "notice://museum", document_id="museum")
    assert {r.version for r in store.search("博物馆")} == {"2"}
    with pytest.raises(ValueError):
        store.add_document("修改旧版", "notice://museum", document_id="museum")
    path.rename(tmp_path / "closed.db")  # Windows 上验证不存在长寿命打开连接。



@pytest.mark.parametrize(
    "size,overlap", [(0, 0), (10, 10), (10, -1), (10, 11), (True, 0)]
)
def test_invalid_chunking_writes_nothing(tmp_path, size, overlap):
    store = RAGStore(tmp_path / "rag.db")
    with pytest.raises(ValueError):
        store.add_document(
            "有效资料", "fixture://source", chunk_size=size, overlap=overlap
        )
    assert store.search("有效资料") == []



def test_rag_no_match_and_tools(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document("杭州公交出行", "fixture://bus")
    assert store.search("zzzz") == []
    registry = ToolRegistry()
    registry.register_tool(RAGTool(store))
    assert registry.list_tools() == ["rag_read", "rag_read_document", "rag_search"]
    response = registry.execute_tool("rag_search", {"query": "杭州公交"})
    assert response.status == ToolStatus.SUCCESS
    hit = response.data["results"][0]
    read = registry.execute_tool("rag_read", {"chunk_id": hit["chunk_id"]})
    assert read.data["result"]["source"] == "fixture://bus"
    assert (
        registry.execute_tool("rag_read", {"chunk_id": "unknown"}).error_info["code"]
        == "NOT_FOUND"
    )
    assert (
        registry.execute_tool("rag_search", {"query": "", "limit": 0}).error_info[
            "code"
        ]
        == "INVALID_PARAM"
    )

