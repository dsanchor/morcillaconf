import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from restaurant_agent.cli import handle_memory_command
from restaurant_agent.conversation import ConversationManager, ConversationAccessError
from restaurant_agent.memory.contracts import MemoryKind
from restaurant_agent.memory.store import SQLiteMemoryStore


class SessionOnlyAgent:
    def create_session(self, *, session_id=None):
        return SimpleNamespace(state={})


def test_cli_queries_corrects_deletes_and_clears_without_consent(tmp_path: Path, capsys) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(SessionOnlyAgent(), memory_store=store)
    conversation_id = manager.start_conversation(actor_id="customer", authenticated=True)
    memory = store.remember_memory(
        "customer", kind=MemoryKind.PREFERENCE, value="agua",
        source_conversation_id=conversation_id,
    )

    def command(text):
        handle_memory_command(
            manager, conversation_id=conversation_id, actor_id="customer", command=text,
        )
        return json.loads(capsys.readouterr().out)

    assert command("/memory")["memories"][0]["value"] == "agua"
    assert command(f"/memory correct {memory.preference_id} agua con gas")["memories"][0]["value"] == "agua con gas"
    assert command(f"/memory delete {memory.preference_id}")["memories"] == []
    store.remember_memory(
        "customer", kind=MemoryKind.RESTRICTION, value="sin gluten",
        source_conversation_id=conversation_id,
    )
    assert command("/memory clear")["memories"] == []
    assert set(command("/memory list")) == {"memories", "order_history"}
    with pytest.raises(ConversationAccessError):
        manager.clear_memories(conversation_id=conversation_id, actor_id="other")
    for retired in ("/memory consent", "/memory revoke"):
        with pytest.raises(ValueError, match="Uso:"):
            command(retired)
