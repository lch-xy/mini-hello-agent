"""索引发布、混合资料一致性和模型重排准入。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hello_agents.retrieval import HybridSearch, LLMReranker, OpenAIEmbeddingProvider, QdrantSearch, RAGStore


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 2]])
def test_embedding_service_missing_or_duplicate_indices_are_rejected(indices):
    client = SimpleNamespace(embeddings=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=[1.0, 0.0]) for index in indices]
    ))))
    provider = OpenAIEmbeddingProvider(client, "fixture-model", 2)
    with pytest.raises(ValueError, match="索引"):
        provider.embed_documents(["a", "b"])
    client.embeddings.create.assert_called_once()


@pytest.mark.parametrize("reason", ["length", "max_tokens", "max_output_tokens"])
def test_reranking_limits_apply_before_call_and_before_accepting_order(reason):
    model = Mock()
    candidates = [SimpleNamespace(content="evidence")]
    with pytest.raises(ValueError, match="max_input_chars"):
        LLMReranker(model, max_input_chars=1).rerank("query", candidates)
    model.invoke.assert_not_called()
    model.invoke.return_value = SimpleNamespace(content="[0]", finish_reason=reason)
    with pytest.raises(ValueError, match="truncated"):
        LLMReranker(model).rerank("query", candidates)


def test_hybrid_rejects_revision_change_between_backends(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document("museum evidence", "fixture://first")
    rows = store.snapshot()[1]
    first = SimpleNamespace(store=store, search=lambda query, limit: rows)

    def mutate(query, limit):
        store.add_document("new evidence", "fixture://second")
        return rows

    second = SimpleNamespace(store=store, search=mutate)
    with pytest.raises(RuntimeError, match="资料发生变化"):
        HybridSearch([first, second]).search("museum")


def test_old_generation_cleanup_failure_does_not_hide_new_manifest(tmp_path, monkeypatch):
    store = RAGStore(tmp_path / "rag.db")
    store.add_document("first evidence", "fixture://first", document_id="first")
    fixture = SimpleNamespace(model_id="fixture-v1", dimension=2,
                              embed_documents=lambda texts: [[1.0, 0.0] for _ in texts],
                              embed_query=lambda text: [1.0, 0.0])
    with QdrantSearch(store, fixture, path=":memory:") as search:
        search.sync()
        previous = search._manifest()
        store.add_document("second evidence", "fixture://second", document_id="second")

        def fail(*args, **kwargs):
            raise RuntimeError("cleanup unavailable")

        monkeypatch.setattr(search.client, "delete", fail)
        with pytest.warns(RuntimeWarning, match="旧代次回收失败"):
            assert search.sync() == 2
        assert search._manifest()["generation"] != previous["generation"]
        hits = search.search("evidence")
        assert len(hits) == 2 and {hit.document_id for hit in hits} == {"first", "second"}
