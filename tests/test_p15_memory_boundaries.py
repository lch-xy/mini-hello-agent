"""核对近期读取与非空语义召回在索引过期时的不同承诺。"""

import pytest

from hello_agents.context import MemoryContextProvider
from hello_agents.memory import MemoryStore, SemanticMemorySearch
from hello_agents.retrieval import IndexStaleError
from hello_agents.tools import MemoryTool


class FixtureEmbedding:
    model_id, dimension = "p15-boundary-fixture", 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def test_recent_context_sees_revision_while_query_rejects_stale_index(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.db"), "alice", "travel")
    old = store.add("walk at most eight km", "message:1")
    with SemanticMemorySearch(store, FixtureEmbedding(), path=":memory:") as search:
        search.sync()
        current = store.revise(old.memory_id, "walk at most five km", "message:2")
        assert [record.memory_id for record in search.search("")] == [current.memory_id]
        recent = MemoryContextProvider(store, search_backend=search)
        assert [packet.content for packet in recent.get_context("walk")] == [current.content]
        queried = MemoryContextProvider(store, search_backend=search, use_query=True)
        with pytest.raises(IndexStaleError):
            queried.get_context("walk")
        tool = MemoryTool(store, search_backend=search)
        assert tool.search("walk").error_info["code"] == "CONFLICT"
        receipt = tool.retract(current.memory_id)
        assert receipt.data["records"][0]["content"] == current.content
        assert receipt.data["records"][0]["status"] == "retracted"
        assert recent.get_context("walk") == []
        assert store.get(old.memory_id, include_inactive=True).status == "superseded"
        assert store.get(current.memory_id, include_inactive=True).status == "retracted"
        search.sync()
        assert search.search("walk") == []
