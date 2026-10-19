"""离线展示一次模型→真实函数→回执→模型的 Runtime 闭环。

本示例通过轻量级 ScriptedModel 与纯内存 CharacterCounter，
演示 P03 的最小闭环：
1. 用户输入："计算 2+3"
2. 第 1 次模型调用：返回 ToolCall(id="sum-1", name="add", arguments='{"input":"2,3"}')
3. 真实函数执行：调用本地 Python 函数 add("2,3")，计算结果 5
4. 回执注入：Runtime 将回执追加到 messages，精确配对 tool_call_id="sum-1"
5. 第 2 次模型调用：大模型读取回执，生成最终文本答复 "结果为 5"
6. 生命周期收尾：历史记录形成 [user, assistant, tool, assistant]，状态标为 completed。
"""

from copy import deepcopy

from hello_agents import AgentComponents, SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMToolResponse, ToolCall


class CharacterCounter:
    """演示用字符估算器。
    
    实现 TokenCounterProtocol 鸭子类型接口，避免启动外部 Token 编码资源下载。
    """

    def count_message(self, message):
        """估算单条消息字符数。"""
        return len(message.content)

    def count_messages(self, messages):
        """估算多条消息累计字符数。"""
        return sum(self.count_message(message) for message in messages)

    def clear_cache(self):
        """清除计数缓存（空实现）。"""
        pass


class ScriptedModel:
    """脚本化 Mock 大模型。
    
    用于在离线环境下确定性地模拟大模型在两轮中的输出：
    - 第 1 次调用：决定调用工具 add；
    - 第 2 次调用：核验工具回执并产出最终答复。
    """
    model = "offline-demo"

    def __init__(self):
        self.requests = []  # 记录收到的全部请求历史快照

    def invoke_with_tools(self, messages, tools, **kwargs):
        """模拟支持工具调用的模型推理接口。"""
        self.requests.append(deepcopy(messages))
        # 第一次请求：返回加法工具意图
        if len(self.requests) == 1:
            return LLMToolResponse(None, [
                ToolCall("sum-1", "add", '{"input":"2,3"}'),
            ], self.model)
        # 第二次请求：断言上一条消息必须是 ID 配对的 tool 回执
        assert messages[-1]["tool_call_id"] == "sum-1"
        return LLMToolResponse("结果为 5", [], self.model)


def main():
    executed = []  # 记录函数执行产生的实际外部副作用

    def add(value):
        """真实的本地 Python 加法函数。"""
        left, right = map(int, value.split(","))
        result = left + right
        executed.append(result)  # 记录副作用
        return result

    # 1. 创建工具注册表并注册本地函数
    registry = ToolRegistry()
    registry.register_function(add)
    
    # 2. 实例化脚本模型与轻量级 Agent 实例
    model = ScriptedModel()
    agent = SimpleAgent(
        "P03", model, tool_registry=registry,
        components=AgentComponents(token_counter=CharacterCounter()),
    )
    
    # 3. 发起运行并验证执行结果
    print(agent.run("计算 2+3"))
    print("模型调用:", agent.last_run.model_calls, "工具调用:", agent.last_run.tool_calls)
    print("历史角色:", [message.role for message in agent.get_history()])
    
    # 4. 断言验证：函数产生真实副作用，状态确认为 completed
    assert executed == [5] and agent.last_run.status == "completed"


if __name__ == "__main__":
    main()
