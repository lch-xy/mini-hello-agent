"""保存状态替换和工具完整接口指纹。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from hello_agents import AgentComponents, Message, SimpleAgent, ToolRegistry
from hello_agents.core.session_store import SessionStore


@pytest.mark.parametrize("field", ["metadata", "read_cache"])
def test_bad_snapshot_object_does_not_replace_history_or_read_cache(tmp_path, field):
    store, registry = SessionStore(str(tmp_path)), ToolRegistry()
    agent = SimpleAgent("restore", Mock(model="offline"), tool_registry=registry,
                        components=AgentComponents(session_store=store))
    agent.add_message(Message("current history", "user"))
    registry.cache_read_metadata("current.txt", {"file_mtime_ms": 12})
    path = Path(store.save({}, [], "no-tools", {}, {}, "bad"))
    snapshot = json.loads(path.read_text())
    snapshot[field] = []
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="对象"):
        agent.load_session(str(path), check_consistency=False)
    assert [m.content for m in agent.get_history()] == ["current history"]
    assert registry.get_read_metadata("current.txt") == {"file_mtime_ms": 12}


def test_function_schema_hash_ignores_order_and_detects_description_change():
    first, second, changed = ToolRegistry(), ToolRegistry(), ToolRegistry()
    for name in ["alpha", "beta"]:
        first.register_function(lambda value: value, name=name, description="same")
    for name in ["beta", "alpha"]:
        second.register_function(lambda value: value, name=name, description="same")
        changed.register_function(lambda value: value, name=name, description="different" if name == "alpha" else "same")
    agents = [SimpleAgent("hash", Mock(model="offline"), tool_registry=registry)
              for registry in [first, second, changed]]
    hashes = [agent._compute_tool_schema_hash() for agent in agents]
    assert hashes[0] == hashes[1] and hashes[0] != hashes[2]
