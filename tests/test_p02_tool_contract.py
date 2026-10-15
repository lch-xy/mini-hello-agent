"""P02 原始契约断言，提取自固定快照的 test_tool_contract_fixes.py。

保留所选函数、装饰器和辅助类原文；其他阶段依赖的用例在对应阶段接通。
"""

import asyncio
import threading
import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from hello_agents.tools import Tool, ToolParameter, ToolRegistry, ToolResponse, tool_action
from hello_agents.tools.circuit_breaker import CircuitBreaker
from hello_agents.tools.builtin.calculator import CalculatorTool

@pytest.mark.parametrize(
    "expression, expected", [("2+3*4", 14), ("-1.5", -1.5), ("sqrt(16)", 4)]
)
def test_calculator_numeric_constants_without_deprecated_ast(expression, expected):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        response = CalculatorTool().run({"input": expression})
    assert response.status.value == "success"
    assert response.data["result"] == expected


@pytest.mark.parametrize("expression", ['"abc"', '"a" * 10', "True", "None"])
def test_calculator_rejects_non_numeric_constants(expression):
    response = CalculatorTool().run({"input": expression})
    assert response.status.value == "error"


def test_to_dict_preserves_legacy_shape_and_supplied_schema():
    assert "json_schema" not in Echo().to_dict()["parameters"][0]

    class Structured(Echo):
        def get_parameters(self):
            return [
                ToolParameter(
                    name="input",
                    type="string",
                    description="value",
                    json_schema={"enum": ["a"]},
                )
            ]

    assert Structured().to_dict()["parameters"][0]["json_schema"] == {"enum": ["a"]}


class NestedItem(BaseModel):
    label: str


def test_action_nested_model_schema_references_resolve():
    class NestedAction(Echo):
        @tool_action("nested")
        def nested(self, items: list[NestedItem]):
            return items

    tool = NestedAction()
    tool.expandable = True
    schema = tool.get_expanded_tools()[0].to_openai_schema()["function"]["parameters"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"items": [{"label": "ok"}]})
    assert not validator.is_valid({"items": [{"label": 123}]})


def test_registry_fork_filters_tools_and_functions_without_parent_mutation():
    registry = ToolRegistry()
    echo = Echo()
    registry.register_tool(echo)
    registry.register_function(lambda query: query, name="read_func")
    registry.register_function(lambda query: query, name="write_func")
    registry.read_metadata_cache["a"] = {"nested": {"mtime": 1}}
    child = registry.fork(name for name in ["echo", "read_func"])
    assert set(child.list_tools()) == {"echo", "read_func"}
    assert child.get_tool("echo") is echo
    assert child.circuit_breaker is registry.circuit_breaker
    assert child.execute_tool("write_func", "x").error_info["code"] == "NOT_FOUND"
    child._functions["read_func"]["description"] = "changed"
    child.read_metadata_cache["a"]["nested"]["mtime"] = 2
    child.unregister("echo")
    child.register_function(lambda query: "new", name="read_func", replace=True)
    assert set(registry.list_tools()) == {"echo", "read_func", "write_func"}
    assert registry._functions["read_func"]["description"] != "changed"
    assert registry.read_metadata_cache["a"]["nested"]["mtime"] == 1
    assert registry.execute_tool("read_func", "original").text == "original"
    assert set(registry.fork().list_tools()) == set(registry.list_tools())
    assert registry.fork([]).list_tools() == []
    with pytest.raises(ValueError, match="未注册"):
        registry.fork(["missing"])
    with pytest.raises(TypeError):
        registry.fork("echo")


class Echo(Tool):
    def __init__(self, name="echo"):
        super().__init__(name, "测试回声")

    def get_parameters(self):
        return [ToolParameter(name="input", type="string", description="输入")]

    def run(self, parameters):
        return ToolResponse.success(parameters["input"])


def test_explicit_schema_is_preserved_without_mutation():
    fragment = {
        "type": "object",
        "properties": {"mode": {"enum": ["read"]}},
        "required": ["mode"],
        "additionalProperties": False,
    }

    class Structured(Echo):
        def get_parameters(self):
            return [
                ToolParameter(
                    name="input",
                    type="object",
                    description="结构",
                    json_schema=fragment,
                )
            ]

    tool = Structured()
    first = tool.to_openai_schema()
    assert (
        first["function"]["parameters"]["properties"]["input"]["additionalProperties"]
        is False
    )
    first["function"]["parameters"]["properties"]["input"]["properties"].clear()
    assert "mode" in fragment["properties"]
    assert (
        "mode"
        in tool.to_openai_schema()["function"]["parameters"]["properties"]["input"][
            "properties"
        ]
    )


def test_action_annotations_preserve_nested_array_types():
    class Actions(Echo):
        def __init__(self):
            super().__init__()
            self.expandable = True

        @tool_action("sum_values")
        def total(self, values: list[int]):
            return sum(values)

    schema = (
        Actions().get_expanded_tools()[0].to_openai_schema()["function"]["parameters"]
    )
    assert schema["properties"]["values"]["items"] == {"type": "integer"}


def test_registry_unique_name_and_explicit_cross_kind_replacement():
    registry = ToolRegistry()
    registry.register_tool(Echo("same"))
    with pytest.raises(ValueError):
        registry.register_function(lambda text: "hidden", name="same")
    assert registry.list_tools() == ["same"]
    assert registry.get_function("same") is None
    registry.register_function(lambda text: "replacement", name="same", replace=True)
    assert registry.get_tool("same") is None
    assert registry.execute_tool("same", "x").text == "replacement"
    with pytest.raises(ValueError):
        registry.register_tool(Echo("same"))
    registry.unregister("same")
    assert registry.list_tools() == []
    assert registry.execute_tool("same", "x").error_info["code"] == "NOT_FOUND"


def test_expanded_registration_is_all_or_nothing():
    class Bundle(Echo):
        def __init__(self):
            super().__init__()
            self.expandable = True

        def get_expanded_tools(self):
            return [Echo("new"), Echo("existing")]

    registry = ToolRegistry()
    registry.register_tool(Echo("existing"))
    with pytest.raises(ValueError):
        registry.register_tool(Bundle())
    assert registry.list_tools() == ["existing"]


def test_schema_failure_is_not_silently_registered():
    class Broken(Echo):
        def get_parameters(self):
            raise ValueError("bad contract")

    registry = ToolRegistry()
    with pytest.raises(ValueError, match="bad contract"):
        registry.register_tool(Broken())
    assert registry.list_tools() == []


def test_async_function_really_awaited_and_sync_rejected():
    calls = []

    async def native(text):
        await asyncio.sleep(0)
        calls.append(text)
        return ToolResponse.partial("partial", data={"source": "fixture://async"})

    registry = ToolRegistry()
    registry.register_function(native)
    assert registry.execute_tool("native", "sync").error_info["code"] == "INVALID_PARAM"
    assert calls == []
    result = asyncio.run(registry.aexecute_tool("native", "async"))
    assert calls == ["async"]
    assert (
        result.status.value == "partial" and result.data["source"] == "fixture://async"
    )


def test_async_action_really_awaited_and_signature_errors_preserved():
    class Actions(Echo):
        def __init__(self):
            super().__init__()
            self.expandable = True

        @tool_action("lookup")
        async def lookup(self, query: str):
            await asyncio.sleep(0)
            return {"query": query}

    registry = ToolRegistry()
    registry.register_tool(Actions())
    assert (
        registry.execute_tool("lookup", {"query": "sync"}).error_info["code"]
        == "INVALID_PARAM"
    )
    result = asyncio.run(registry.aexecute_tool("lookup", {"query": "async"}))
    assert result.data["output"] == {"query": "async"}
    assert (
        asyncio.run(registry.aexecute_tool("lookup", {})).error_info["code"]
        == "INVALID_PARAM"
    )


def test_sync_function_in_async_path_does_not_block_event_loop():
    release = threading.Event()

    def blocking(text):
        return release.wait(timeout=1)

    registry = ToolRegistry()
    registry.register_function(blocking)

    async def run():
        task = asyncio.create_task(registry.aexecute_tool("blocking", "x"))
        await asyncio.sleep(0.01)
        release.set()
        return await task

    assert asyncio.run(run()).data["output"] is True


def test_async_function_failure_participates_in_circuit():
    async def failing(text):
        raise RuntimeError("local failure")

    registry = ToolRegistry(CircuitBreaker(failure_threshold=1))
    registry.register_function(failing)
    assert (
        asyncio.run(registry.aexecute_tool("failing", "x")).error_info["code"]
        == "EXECUTION_ERROR"
    )
    assert (
        asyncio.run(registry.aexecute_tool("failing", "x")).error_info["code"]
        == "CIRCUIT_OPEN"
    )

