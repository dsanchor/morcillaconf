"""Opt-in smoke test against the configured Foundry model."""

import asyncio
from uuid import uuid4

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.contracts import PendingField
from restaurant_agent.conversation import ConversationManager
from restaurant_agent.memory import create_memory_store


async def run_smoke() -> None:
    settings = Settings()
    memory_store = create_memory_store(settings)
    manager = ConversationManager(
        create_waiter_agent(settings, memory_store=memory_store),
        memory_store=memory_store,
    )

    first_id = manager.start_conversation(actor_id="smoke-majo")
    first = await manager.send_message(
        conversation_id=first_id,
        actor_id="smoke-majo",
        message="Soy Majo y venimos dos.",
    )
    assert first.customer.presented_name == "Majo"
    assert first.customer.party_size == 2
    assert not first.pending_fields

    corrected = await manager.send_message(
        conversation_id=first_id,
        actor_id="smoke-majo",
        message="Corrección: finalmente somos tres.",
    )
    assert corrected.customer.party_size == 3

    second_id = manager.start_conversation(actor_id="smoke-other")
    second = await manager.send_message(
        conversation_id=second_id,
        actor_id="smoke-other",
        message="Queremos cenar.",
    )
    assert second.customer.presented_name is None
    assert second.customer.party_size == 1
    assert second.pending_fields == [PendingField.CUSTOMER_NAME]

    unsupported = await manager.send_message(
        conversation_id=first_id,
        actor_id="smoke-majo",
        message="Resérvame una mesa y confirma el pedido.",
    )
    normalized_reply = unsupported.reply.casefold()
    forbidden_claims = (
        "he reservado",
        "mesa reservada",
        "pedido confirmado",
        "comanda confirmada",
    )
    assert not any(claim in normalized_reply for claim in forbidden_claims)

    memory_actor = f"smoke-memory-{uuid4().hex}"
    memory_id = manager.start_conversation(
        actor_id=memory_actor,
        authenticated=True,
    )
    try:
        await manager.send_message(
            conversation_id=memory_id,
            actor_id=memory_actor,
            message="Prefiero el agua con gas.",
        )
        persisted = memory_store.list_memories(memory_actor)
        assert len(persisted) == 1
        assert "agua con gas" in persisted[0].value.casefold()

        recreated_store = create_memory_store(settings)
        recreated = recreated_store.list_memories(memory_actor)
        assert len(recreated) == 1
        assert "agua con gas" in recreated[0].value.casefold()
    finally:
        memory_store.delete_all_memories(memory_actor)

    print("Foundry smoke test passed.")


def main() -> None:
    asyncio.run(run_smoke())


if __name__ == "__main__":
    main()
