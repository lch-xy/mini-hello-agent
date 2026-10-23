"""示例与演示环境支撑模块。

提供以下双模支持与测试夹具构造能力：
1. 双模切换：默认使用离线 ScriptedLLM 预设响应验证协议，传入 `--live` 或 `live=True` 时读取环境变量连接真实大模型；
2. 隔离配置：`demo_config` 默认关闭所有具备副作用的特性（追踪、会话、技能库等），防止演示污染磁盘；
3. 工具调用桩构造：`tool_response` 快速构造标准结构的 ToolCall 响应。
"""

import os
import sys
from dotenv import load_dotenv
from hello_agents import Config, HelloAgentsLLM
from hello_agents.core.llm_response import ToolCall
from examples.agents.runtime_features import ScriptedLLM, response


def demo_config(**overrides):
    """构造示例专用的轻量级 Config 实例。

    默认禁用所有具备外部副作用的功能（如链路追踪、会话持久化、技能自动加载等），
    支持通过关键字参数灵活覆盖特定配置项。
    """
    return Config(
        **(
            dict(
                trace_enabled=False,
                session_enabled=False,
                skills_enabled=False,
                subagent_enabled=False,
                todowrite_enabled=False,
                devlog_enabled=False,
            )
            | overrides
        )
    )


def demo_llm(responses, *, live=None):
    """创建示例运行所需的 LLM 实例（离线替身或在线真实模型）。

    参数：
    - responses: 离线模式下预设的模型响应列表；
    - live: 是否强制启用真实在线模型；若未显式指定，则根据命令行参数 `--live` 自动判定。
    """
    if not ("--live" in sys.argv if live is None else live):
        print("离线模式：预设模型响应，真实执行工具。")
        return ScriptedLLM(responses)
    load_dotenv()
    missing = [
        k for k in ("LLM_MODEL_ID", "LLM_API_KEY", "LLM_BASE_URL") if not os.getenv(k)
    ]
    if missing:
        raise ValueError("需设置：" + ", ".join(missing))
    return HelloAgentsLLM()


def tool_response(name, arguments, *, call_id="demo-call"):
    """便捷构造包含工具调用指令（ToolCall）的模型响应替身。

    参数：
    - name: 调用的工具名称（如 'Calculator' 或 'generate_plan'）；
    - arguments: 传递给工具的实参字典，将序列化为 JSON 字符串；
    - call_id: 工具调用唯一标识符。
    """
    import json

    return response(
        calls=[ToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False))],
        reason="tool_calls",
    )

