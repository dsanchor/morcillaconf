import asyncio
from types import SimpleNamespace

import pytest

from restaurant_agent.contracts import (
    CustomerSnapshot,
    MemoryIntent,
    PendingField,
    WaiterModelResult,
)
from restaurant_agent.conversation import (
    ConversationAccessError,
    ConversationManager,
    ConversationNotFoundError,
    InvalidAgentResponseError,
    TurnLimitExceededError,
)


class FakeAgent:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.sessions: list[str | None] = []
        self.prompts: list[str] = []

    def create_session(self, *, session_id: str | None = None) -> object:
        self.sessions.append(session_id)
        return SimpleNamespace(state={})

    async def run(
        self,
        messages: str,
        *,
        session: object,
        options: dict[str, object],
    ) -> object:
        await asyncio.sleep(0)
        self.prompts.append(messages)
        assert options["response_format"] is WaiterModelResult
        return SimpleNamespace(value=self.results.pop(0))


@pytest.mark.asyncio
async def test_two_turns_keep_data_and_apply_corrections() -> None:
    agent = FakeAgent(
        WaiterModelResult(
            reply="Gracias, Majo.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        ),
        WaiterModelResult(
            reply="Actualizado a tres personas.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=3),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        ),
    )
    manager = ConversationManager(agent)
    conversation_id = manager.start_conversation(actor_id="customer-1")

    first = await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Soy Majo y venimos dos.",
    )
    second = await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Al final somos tres.",
    )

    assert first.customer.party_size == 2
    assert second.customer.presented_name == "Majo"
    assert second.customer.party_size == 3
    assert second.turn_number == 2
    assert '"party_size": 2' in agent.prompts[1]


@pytest.mark.asyncio
async def test_two_sessions_do_not_share_customer_data() -> None:
    agent = FakeAgent(
        WaiterModelResult(
            reply="Gracias, Majo.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        ),
        WaiterModelResult(
            reply="¿A nombre de quién y para cuántas personas?",
            customer=CustomerSnapshot(party_size=None),
            pending_fields=[
                PendingField.CUSTOMER_NAME,
                PendingField.PARTY_SIZE,
            ],
            memory_intent=MemoryIntent.NONE,
        ),
    )
    manager = ConversationManager(agent)
    first_id = manager.start_conversation(actor_id="customer-1")
    second_id = manager.start_conversation(actor_id="customer-2")

    await manager.send_message(
        conversation_id=first_id,
        actor_id="customer-1",
        message="Soy Majo y venimos dos.",
    )
    second = await manager.send_message(
        conversation_id=second_id,
        actor_id="customer-2",
        message="Queremos cenar.",
    )

    assert second.customer.presented_name is None
    assert "Majo" not in agent.prompts[1]
    assert agent.sessions == [first_id, second_id]


@pytest.mark.asyncio
async def test_conversation_is_scoped_to_its_actor() -> None:
    manager = ConversationManager(FakeAgent())
    conversation_id = manager.start_conversation(actor_id="owner")

    with pytest.raises(ConversationAccessError):
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="other",
            message="Hola",
        )


@pytest.mark.asyncio
async def test_unknown_conversation_is_visible() -> None:
    manager = ConversationManager(FakeAgent())

    with pytest.raises(ConversationNotFoundError):
        await manager.send_message(
            conversation_id="conv_missing",
            actor_id="owner",
            message="Hola",
        )


@pytest.mark.asyncio
async def test_turn_limit_is_enforced_before_calling_model() -> None:
    agent = FakeAgent(
        WaiterModelResult(
            reply="¿Cuántas personas sois?",
            customer=CustomerSnapshot(presented_name="Majo", party_size=None),
            pending_fields=[PendingField.PARTY_SIZE],
            memory_intent=MemoryIntent.NONE,
        )
    )
    manager = ConversationManager(agent, max_turns=1)
    conversation_id = manager.start_conversation(actor_id="owner")

    await manager.send_message(
        conversation_id=conversation_id,
        actor_id="owner",
        message="Soy Majo.",
    )
    with pytest.raises(TurnLimitExceededError):
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Somos dos.",
        )

    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_concurrent_turns_are_serialized_before_limit_check() -> None:
    agent = FakeAgent(
        WaiterModelResult(
            reply="Gracias, Majo.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        )
    )
    manager = ConversationManager(agent, max_turns=1)
    conversation_id = manager.start_conversation(actor_id="owner")

    results = await asyncio.gather(
        manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Soy Majo.",
        ),
        manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Somos dos.",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, TurnLimitExceededError) for result in results) == 1
    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_invalid_agent_output_is_not_silently_accepted() -> None:
    manager = ConversationManager(FakeAgent(None))
    conversation_id = manager.start_conversation(actor_id="owner")

    with pytest.raises(InvalidAgentResponseError):
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Hola",
        )
