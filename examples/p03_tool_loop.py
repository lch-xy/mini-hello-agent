"""离线展示一次模型→真实函数→回执→模型的 Runtime 闭环。"""

from copy import deepcopy

from hello_agents import AgentComponents, SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


class CharacterCounter:
    """演示用字符估算，避免启动外部 Token 编码资源下载。"""

    def count_message(self, message):
        return len(message.content)

    def count_messages(self, messages):
        return sum(self.count_message(message) for message in messages)

    def clear_cache(self):
        pass


class ScriptedModel:
    model = "offline-demo"

    def __init__(self):
        self.requests = []

    def invoke_with_tools(self, messages, tools, **kwargs):
        self.requests.append(deepcopy(messages))
        if len(self.requests) == 1:
            return LLMToolResponse(None, [
                ToolCall("sum-1", "add", '{"input":"2,3"}'),
            ], self.model)
        assert messages[-1]["tool_call_id"] == "sum-1"
        return LLMToolResponse("结果为 5", [], self.model)


def main():
    executed = []

    def add(value):
        left, right = map(int, value.split(","))
        result = left + right
        executed.append(result)
        return result

    registry = ToolRegistry()
    registry.register_function(add)
    model = ScriptedModel()
    agent = SimpleAgent(
        "P03", model, tool_registry=registry,
        components=AgentComponents(token_counter=CharacterCounter()),
    )
    print(agent.run("计算 2+3"))
    print("模型调用:", agent.last_run.model_calls, "工具调用:", agent.last_run.tool_calls)
    print("历史角色:", [message.role for message in agent.get_history()])
    assert executed == [5] and agent.last_run.status == "completed"


if __name__ == "__main__":
    main()
