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


def test_metrics_have_known_values_and_no_answer_is_undefined():
    values=ranking_metrics(['irrelevant','a','b'],{'a':1,'b':1},k=2)
    assert values['recall_at_k']==0.5 and values['mrr_at_k']==0.5
    assert 0 < values['ndcg_at_k'] < 1
    assert ranking_metrics(['a'],{})['recall_at_k'] is None
    assert ranking_metrics(['a','b'],{'a':3,'b':1})['ndcg_at_k']==1
    with pytest.raises(ValueError): ranking_metrics(['a','a'],{'a':1})
    backend=Mock()
    backend.search.return_value=[SimpleNamespace(chunk_id='a')]
    report=evaluate_retrieval(backend,[{'query':'query','relevance':{'a':1}}])
    assert report['mean']['mrr_at_k']==1 and report['evaluated_queries']==1



@pytest.mark.parametrize('text',['[0,0]','[true,0]','[2,0]','{"a":1}'])
def test_reranker_rejects_invalid_model_order(text):
    llm=Mock()
    llm.invoke.return_value=SimpleNamespace(content=text,finish_reason='stop')
    with pytest.raises(ValueError): LLMReranker(llm).rerank('q',[SimpleNamespace(content='a'),SimpleNamespace(content='b')])



def test_reranker_retains_exact_evidence_objects():
    llm=Mock()
    llm.invoke.return_value=SimpleNamespace(content='[1,0]',finish_reason='stop')
    candidates=[SimpleNamespace(content='a'),SimpleNamespace(content='b')]
    assert LLMReranker(llm).rerank('q',candidates)==candidates[::-1]

