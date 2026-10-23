"""会话持久化快照校验边界与工具接口指纹哈希测试。

验证两项核心持久化与一致性保障：
1. 快照恢复原子性与无副作用防护：当持久化文件中的 metadata 或 read_cache 被非法篡改为非字典对象时，
   加载逻辑在校验失败时抛出 ValueError，且严格不破坏内存中现有的历史消息与工具读缓存；
2. 工具 Schema 指纹哈希计算的健壮性：
   - 顺序无关性：工具在注册表中的注册顺序不影响最终哈希值；
   - 变更敏感性：任一工具的描述或参数发生变更，哈希值必然随之变化，用于检测会话恢复时的工具一致性。
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from hello_agents import AgentComponents, Message, SimpleAgent, ToolRegistry
from hello_agents.core.session_store import SessionStore


@pytest.mark.parametrize("field", ["metadata", "read_cache"])
def test_bad_snapshot_object_does_not_replace_history_or_read_cache(tmp_path, field):
    """验证非法结构快照加载失败时，既有内存中的对话历史与读缓存状态不受任何破坏。"""
    store, registry = SessionStore(str(tmp_path)), ToolRegistry()
    agent = SimpleAgent("restore", Mock(model="offline"), tool_registry=registry,
                        components=AgentComponents(session_store=store))
    # 填充当前智能体的内存状态
    agent.add_message(Message("current history", "user"))
    registry.cache_read_metadata("current.txt", {"file_mtime_ms": 12})

    # 创建一个快照文件，并人为将其中的 metadata 或 read_cache 篡改为非法列表类型 []
    path = Path(store.save({}, [], "no-tools", {}, {}, "bad"))
    snapshot = json.loads(path.read_text())
    snapshot[field] = []
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    # 尝试恢复损坏的快照，预期抛出格式校验异常
    with pytest.raises(ValueError, match="对象"):
        agent.load_session(str(path), check_consistency=False)

    # 核心验证：恢复失败后，智能体现有的内存历史与读缓存依然原封不动
    assert [m.content for m in agent.get_history()] == ["current history"]
    assert registry.get_read_metadata("current.txt") == {"file_mtime_ms": 12}


def test_function_schema_hash_ignores_order_and_detects_description_change():
    """验证工具签名哈希计算具备顺序无关性（注册先后顺序一致）和描述敏感性。"""
    first, second, changed = ToolRegistry(), ToolRegistry(), ToolRegistry()
    # first 注册顺序：alpha, beta
    for name in ["alpha", "beta"]:
        first.register_function(lambda value: value, name=name, description="same")
    # second 注册顺序：beta, alpha（顺序颠倒，但内容完全相同）
    # changed 同样顺序，但将 alpha 的 description 做了修改
    for name in ["beta", "alpha"]:
        second.register_function(lambda value: value, name=name, description="same")
        changed.register_function(lambda value: value, name=name, description="different" if name == "alpha" else "same")

    agents = [SimpleAgent("hash", Mock(model="offline"), tool_registry=registry)
              for registry in [first, second, changed]]
    hashes = [agent._compute_tool_schema_hash() for agent in agents]

    # 验证顺序无关性（hashes[0] == hashes[1]）以及描述变更的敏感性（hashes[0] != hashes[2]）
    assert hashes[0] == hashes[1] and hashes[0] != hashes[2]

