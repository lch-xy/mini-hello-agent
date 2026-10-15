"""验证全局归约不收敛时明确失败，不用删输入冒充完整答案。"""

import pytest

from examples.retrieval.graphrag_demo import FixtureGraphModel
from hello_agents.retrieval import RAGStore, GraphRAGIndex, GraphConfig
from hello_agents.retrieval.graph import GraphBudgetError


class NonConvergingModel(FixtureGraphModel):
    model_id = "p17-nonconverging-protocol-fixture"

    def generate(self, stage, payload, **kwargs):
        if stage == "map":
            self.calls[stage] += 1
            citation = payload["report"]["claims"][0]["citations"]
            return {"claims": [
                {"text": ("token " * 400) + str(i), "citations": citation}
                for i in range(3)
            ]}
        if stage == "reduce":
            self.calls[stage] += 1
            return {"claims": payload["claims"]}
        return super().generate(stage, payload, **kwargs)


def test_nonconverging_reduce_fails_after_bounded_rounds(tmp_path):
    store = RAGStore(tmp_path / "source.db")
    store.add_document("Advance booking is required.", "fixture://booking")
    model = NonConvergingModel()
    index = GraphRAGIndex(store, str(tmp_path / "graph.db"), model,
        config=GraphConfig(max_input_tokens=3000, max_reduce_rounds=2))
    index.build()
    with pytest.raises(GraphBudgetError, match="converge"):
        index.global_search("booking")
    assert model.calls["map"] == 1
    assert model.calls["reduce"] == 6
    assert index.inspect()["build_report"]["chunks"] == 1
