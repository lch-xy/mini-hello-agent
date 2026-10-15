"""P14：保留固定目标的当前检索定义与断言。"""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from qdrant_client import QdrantClient, models
from hello_agents import ToolRegistry
from hello_agents.context import RetrievalContextProvider
from hello_agents.retrieval import RAGStore, QdrantSearch, QdrantCloudInference, IndexStaleError, RetrievalPipeline, ranking_metrics, evaluate_retrieval, LLMReranker
from hello_agents.tools import RAGTool


def source(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document(
        "杭州馆连接东站。东站连接湖畔步道。湖畔步道连接杭州馆。",
        "fixture://route",
        document_id="route",
    )
    store.add_document("杭州馆预约需要一天。", "fixture://notice", document_id="notice")
    return store



def test_pipeline_injection_preserves_evidence(tmp_path):
    store = source(tmp_path)

    class Reverse:
        def rerank(self, query, candidates):
            return list(reversed(candidates))

    pipeline = RetrievalPipeline(store, Reverse(), candidate_limit=5)
    original = store.search("杭州", 5)
    assert pipeline.search("杭州", 2) == list(reversed(original))[:2]
    provider = RetrievalContextProvider(pipeline, limit=2)
    assert len(list(provider.get_context("杭州"))) == 2
    registry = ToolRegistry()
    registry.register_tool(RAGTool(store, search_backend=pipeline))
    response = registry.execute_tool("rag_search", {"query": "杭州", "limit": 2})
    assert response.data["results"][0]["chunk_id"] == original[-1].chunk_id
    document = registry.execute_tool(
        "rag_read_document", {"document_id": "notice", "version": "1"}
    )
    assert document.data["document"]["active"] is True
    invalid = registry.execute_tool(
        "rag_read_document", {"document_id": "notice", "version": None}
    )
    assert invalid.error_info["code"] == "INVALID_PARAM"



@pytest.mark.parametrize("change", ["drop", "duplicate", "rewrite"])
def test_reranker_cannot_silently_change_candidates(tmp_path, change):
    class BadReranker:
        def rerank(self, query, candidates):
            if change == "drop":
                return candidates[:-1]
            if change == "duplicate":
                return [candidates[0]] * len(candidates)
            return [replace(candidates[0], content="伪造内容"), *candidates[1:]]

    with pytest.raises(ValueError):
        RetrievalPipeline(source(tmp_path), BadReranker()).search("杭州")

