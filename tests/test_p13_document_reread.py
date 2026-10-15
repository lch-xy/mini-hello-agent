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


def source(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document(
        "杭州馆连接东站。东站连接湖畔步道。湖畔步道连接杭州馆。",
        "fixture://route",
        document_id="route",
    )
    store.add_document("杭州馆预约需要一天。", "fixture://notice", document_id="notice")
    return store



def test_document_reread_never_changes_version(tmp_path):
    store = source(tmp_path)
    old = store.search("杭州馆预约")[0]
    store.add_document("杭州馆预约需要两天。", "fixture://notice", "2", "notice")
    assert store.read_document("notice").version == "2"
    assert store.read_document("notice", "1").active is False
    assert (
        store.read_document("notice", "1").content[old.start : old.end] == old.content
    )
    with pytest.raises(KeyError):
        store.read_document("notice", "missing")
    with pytest.raises(ValueError):
        store.read_document("notice", "")

