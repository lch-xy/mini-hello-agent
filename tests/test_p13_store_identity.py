"""资料身份、版本幂等、修订号与跨进程回读。"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from hello_agents.retrieval import RAGStore
from hello_agents.storage import SchemaVersionError


def test_revision_snapshot_and_inactive_version_are_not_reactivated_by_reimport(tmp_path):
    path = tmp_path / "rag.db"
    store = RAGStore(path)
    namespace = store.namespace
    assert store.snapshot() == (0, [])
    store.add_document("原文 空白\n杭州预约", "fixture://notice", "v10", "notice", chunk_size=8, overlap=2)
    revision, old = store.snapshot()
    assert revision == 1 and old
    assert RAGStore(path).namespace == namespace
    store.add_document("原文 空白\n杭州预约", "fixture://notice", "v10", "notice", chunk_size=30, overlap=0)
    assert store.snapshot() == (revision, old)  # 同版本不因切片参数变化重建。
    with pytest.raises(ValueError, match="不可变"):
        store.add_document("修改旧版", "fixture://notice", "v10", "notice")
    assert store.revision == 1
    store.add_document("杭州预约更新", "fixture://notice", "v2", "notice")
    assert store.revision == 2 and {item.version for item in store.snapshot()[1]} == {"v2"}
    assert store.read_chunk(old[0].chunk_id) == old[0]
    assert store.read_document("notice", "v10").active is False
    assert store.deactivate_document("notice") and store.snapshot() == (3, [])
    assert not store.deactivate_document("notice") and store.revision == 3
    store.add_document("杭州预约更新", "fixture://notice", "v2", "notice")
    assert store.snapshot() == (3, [])
    assert store.read_document("notice", "v2").content == "杭州预约更新"
    with pytest.raises(KeyError):
        store.read_document("notice")


def test_store_reopens_in_new_process_with_same_namespace_and_evidence(tmp_path):
    path = tmp_path / "rag.db"
    store = RAGStore(path)
    store.add_document("杭州预约资料", "fixture://notice")
    hit = store.search("杭州预约")[0]
    code = """import json, sys
from hello_agents.retrieval import RAGStore
store = RAGStore(sys.argv[1])
print(json.dumps({'namespace': store.namespace, 'revision': store.revision, 'hit': store.read_chunk(sys.argv[2]).to_dict()}))
"""
    root = Path(__file__).resolve().parents[1]
    output = subprocess.check_output([sys.executable, "-c", code, str(path), hit.chunk_id],
                                     cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root)), text=True)
    restored = json.loads(output)
    assert restored["namespace"] == store.namespace and restored["revision"] == store.revision
    assert restored["hit"]["content"] == hit.content and restored["hit"]["source"] == hit.source


def test_bm25_ties_have_stable_ids_and_query_duplicates_do_not_change_rank(tmp_path):
    store = RAGStore(tmp_path / "rag.db")
    for name in ["second", "first"]:
        store.add_document("alpha beta", "fixture://" + name, document_id=name)
    hits = store.search("alpha")
    assert len(hits) == 2 and hits[0].score == hits[1].score
    assert [hit.chunk_id for hit in hits] == sorted(hit.chunk_id for hit in hits)
    assert store.search("alpha alpha") == hits and store.search("no_overlap") == []


def test_unknown_schema_version_is_rejected_before_using_store(tmp_path):
    path = tmp_path / "rag.db"
    RAGStore(path)
    db = sqlite3.connect(path)
    try:
        db.execute("UPDATE helloagents_schema SET version=99 WHERE component='rag'")
        db.commit()
    finally:
        db.close()
    with pytest.raises(SchemaVersionError):
        RAGStore(path)
    with pytest.raises(ValueError, match=":memory:"):
        RAGStore(":memory:")
