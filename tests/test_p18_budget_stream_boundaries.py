"""流中断与 GraphRAG 修复重试都必须遵守同一个模型预算。"""

from types import SimpleNamespace

import pytest

from hello_agents import HelloAgentsLLM, RunBudget, BudgetExceeded
from hello_agents.retrieval import HelloAgentsGraphModel


class FixtureAdapter:
    def __init__(self):
        self.closed = False
        self.invocations = 0
        self.last_stats = None

    def invoke(self, messages, **kwargs):
        self.invocations += 1
        return SimpleNamespace(content="invalid JSON", usage={"total_tokens": 1})

    def stream_invoke(self, messages, **kwargs):
        try:
            yield "first"
            yield "second"
            self.last_stats = SimpleNamespace(usage={"total_tokens": 7})
        finally:
            self.closed = True

    async def astream_invoke(self, messages, **kwargs):
        try:
            yield "first"
            yield "second"
            self.last_stats = SimpleNamespace(usage={"total_tokens": 7})
        finally:
            self.closed = True


def fixture_model(budget):
    llm = HelloAgentsLLM(budget=budget)
    llm._adapter = FixtureAdapter()
    return llm


@pytest.mark.parametrize("complete", [True, False])
def test_sync_stream_records_known_or_unknown_usage_and_closes(complete):
    budget = RunBudget(max_tokens=7)
    llm = fixture_model(budget)
    stream = llm.stream_invoke([])
    assert next(stream) == "first"
    if complete:
        assert list(stream) == ["second"]
    stream.close()
    assert llm._adapter.closed
    assert budget.snapshot()["model_calls"] == 1
    assert budget.snapshot()["reported_tokens"] == (7 if complete else 0)
    assert budget.snapshot()["unknown_usage_calls"] == (0 if complete else 1)
    with pytest.raises(BudgetExceeded):
        llm.invoke([])
    assert llm._adapter.invocations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_async_stream_records_known_or_unknown_usage_and_closes(complete):
    budget = RunBudget(max_tokens=7)
    llm = fixture_model(budget)
    stream = llm.astream_invoke([])
    assert await anext(stream) == "first"
    if complete:
        assert [item async for item in stream] == ["second"]
    await stream.aclose()
    assert llm._adapter.closed
    assert budget.snapshot()["reported_tokens"] == (7 if complete else 0)
    assert budget.snapshot()["unknown_usage_calls"] == (0 if complete else 1)
    with pytest.raises(BudgetExceeded):
        await llm.ainvoke([])
    assert llm._adapter.invocations == 0


def test_graph_json_retry_cannot_bypass_scope_budget():
    budget = RunBudget(max_calls=1)
    llm = fixture_model(None)
    graph_model = HelloAgentsGraphModel(llm, max_attempts=2)
    with budget.scope(), pytest.raises(BudgetExceeded):
        graph_model.generate("map", {}, max_input_tokens=5000, max_output_tokens=100)
    assert llm._adapter.invocations == 1
    assert budget.snapshot()["model_calls"] == 1
    assert budget.snapshot()["reported_tokens"] == 1
