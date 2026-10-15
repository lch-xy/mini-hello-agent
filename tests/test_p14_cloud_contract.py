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


@pytest.fixture
def cloud(monkeypatch):
    local = QdrantClient(":memory:")
    requests = []
    indexes = {}
    failure = {"upload": False}

    def handle(request):
        requests.append(request)
        assert request.headers["api-key"] == "test-key"
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        name = path.split("/")[2] if path.startswith("/collections/") else None
        if path in ("", "/"):
            return httpx.Response(200, json={"title": "qdrant", "version": "1.19.2"})
        if path.endswith("/exists"):
            result = {"exists": local.collection_exists(name)}
        elif path == f"/collections/{name}" and request.method == "PUT":
            result = local.create_collection(
                name, vectors_config=models.VectorParams(**body["vectors"])
            )
        elif path == f"/collections/{name}":
            result = local.get_collection(name).model_dump(mode="json")
            result["payload_schema"] = {
                field: {"data_type": "keyword", "points": 0}
                for field in indexes.get(name, set())
            }
        elif path.endswith("/index") and request.method == "PUT":
            assert body["field_schema"] == "keyword"
            indexes.setdefault(name, set()).add(body["field_name"])
            result = {"operation_id": 0, "status": "completed"}
        elif path.endswith("/points") and request.method == "POST":
            result = [
                r.model_dump(mode="json")
                for r in local.retrieve(
                    name,
                    ids=body["ids"],
                    with_payload=body.get("with_payload", True),
                    with_vectors=body.get("with_vector", False),
                )
            ]
        elif path.endswith("/points") and request.method == "PUT":
            if failure["upload"]:
                return httpx.Response(
                    400, json={"status": {"error": "inference unavailable"}, "time": 0}
                )
            points = []
            for point in body["points"]:
                if isinstance(point["vector"], dict):
                    assert point["vector"]["model"] == "intfloat/multilingual-e5-small"
                    assert point["vector"]["text"]
                    point["vector"] = [1.0] + [0.0] * 383
                points.append(models.PointStruct(**point))
            result = local.upsert(name, points).model_dump(mode="json")
        elif path.endswith("/points/query"):
            assert {c["key"] for c in body["filter"]["must"]} <= indexes.get(name, set())
            assert body["query"]["nearest"]["model"] == "intfloat/multilingual-e5-small"
            result = local.query_points(
                name,
                query=[1.0] + [0.0] * 383,
                query_filter=models.Filter(**body["filter"]),
                limit=body["limit"],
                with_payload=True,
                with_vectors=False,
            ).model_dump(mode="json")
        elif path.endswith("/points/delete"):
            result = local.delete(name, models.FilterSelector(**body)).model_dump(
                mode="json"
            )
        else:
            raise AssertionError((request.method, path, body))
        return httpx.Response(200, json={"result": result, "status": "ok", "time": 0})

    original = QdrantClient

    def client(**kwargs):
        assert kwargs["cloud_inference"] is True
        return original(
            **kwargs, transport=httpx.MockTransport(handle), check_compatibility=False
        )

    monkeypatch.setattr("qdrant_client.QdrantClient", client)
    yield (
        {"url": "https://cloud.example.test", "api_key": "test-key"},
        requests,
        failure,
    )
    local.close()



def test_cloud_documents_are_sent_as_text_and_failed_inference_never_publishes(
    tmp_path, cloud
):
    options, requests, failure = cloud
    store = RAGStore(str(tmp_path / "rag.sqlite"))
    store.add_document("雨天参观博物馆", "source:rain", document_id="rain")
    with QdrantSearch(store, QdrantCloudInference(), **options) as search:
        assert search.sync() == 1 and search.sync() == 0
        assert search.search("雨天活动", 1)[0].source == "source:rain"
        store.add_document("晴天去湖边", "source:lake", document_id="lake")
        failure["upload"] = True
        from qdrant_client.http.exceptions import UnexpectedResponse

        with pytest.raises(UnexpectedResponse):
            search.sync()
        with pytest.raises(IndexStaleError):
            search.search("雨天活动")
        failure["upload"] = False
        assert search.sync() == 2
        assert len(search.search("活动")) == 2
    assert any(b'"model"' in request.content for request in requests)
    created = [json.loads(r.content)["field_name"] for r in requests if r.url.path.endswith("/index")]
    assert set(created) == {"namespace", "kind", "generation", "model_id"}
    with QdrantSearch(store, QdrantCloudInference(), **options) as reopened:
        assert reopened.sync() == 0
    assert len([r for r in requests if r.url.path.endswith("/index")]) == len(created)



@pytest.mark.parametrize(
    "options",
    [
        {"path": ":memory:"},
        {"client": object()},
        {"url": "http://localhost:6333", "api_key": "test"},
        {"url": "https://cloud.example.test"},
    ],
)
def test_cloud_config_rejects_missing_key_or_local_destination_before_network(
    tmp_path, options
):
    with pytest.raises(ValueError):
        QdrantSearch(
            RAGStore(str(tmp_path / "r.sqlite")), QdrantCloudInference(), **options
        )



@pytest.mark.parametrize(
    "kwargs", [{"model": ""}, {"dimension": 0}, {"dimension": True}]
)
def test_invalid_cloud_model_configuration(kwargs):
    with pytest.raises(ValueError):
        QdrantCloudInference(**kwargs)

