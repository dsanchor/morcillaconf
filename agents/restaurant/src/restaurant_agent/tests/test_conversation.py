import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from restaurant_agent.contracts import (
    CustomerSnapshot,
    MemoryIntent,
    PendingField,
    WaiterModelResult,
)
from restaurant_agent.conversation import (
    AgentUnavailableError,
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
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return SimpleNamespace(value=result)


class FakeChatClientException(Exception):
    """Stands in for agent_framework.exceptions.ChatClientException."""


FakeChatClientException.__module__ = "agent_framework.exceptions"


class FakeBadRequestError(Exception):
    """Stands in for openai.BadRequestError."""

    status_code = 400


FakeBadRequestError.__module__ = "openai._exceptions"


def wrapped_by_chat_client(cause: BaseException) -> FakeChatClientException:
    try:
        raise FakeChatClientException("service failed to complete the prompt") from cause
    except FakeChatClientException as exc:
        return exc


def contract_violation() -> FakeChatClientException:
    try:
        WaiterModelResult.model_validate_json(
            '{"reply": "Recuerdo tu alergia a los frutos secos", "unexpected": true}'
        )
    except ValidationError as exc:
        return wrapped_by_chat_client(exc)
    raise AssertionError("The model output should violate the contract")


def service_failure() -> FakeChatClientException:
    try:
        raise FakeBadRequestError("text.format json_schema is not supported")
    except FakeBadRequestError as exc:
        return wrapped_by_chat_client(exc)


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


@pytest.mark.asyncio
async def test_contract_violation_is_visible_without_customer_data() -> None:
    agent = FakeAgent(
        contract_violation(),
        WaiterModelResult(
            reply="Gracias, Majo.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            memory_intent=MemoryIntent.NONE,
        ),
    )
    manager = ConversationManager(agent)
    conversation_id = manager.start_conversation(actor_id="owner")

    with pytest.raises(InvalidAgentResponseError) as error:
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Hola",
        )
    retried = await manager.send_message(
        conversation_id=conversation_id,
        actor_id="owner",
        message="Soy Majo y venimos dos.",
    )

    assert "frutos secos" not in str(error.value)
    assert retried.turn_number == 1


@pytest.mark.asyncio
async def test_model_service_failure_is_visible_without_its_details() -> None:
    manager = ConversationManager(FakeAgent(service_failure()))
    conversation_id = manager.start_conversation(actor_id="owner")

    with pytest.raises(AgentUnavailableError) as error:
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Hola",
        )

    assert "FakeBadRequestError, HTTP 400" in str(error.value)
    assert "json_schema" not in str(error.value)


@pytest.mark.asyncio
async def test_unexpected_programming_errors_are_not_masked() -> None:
    manager = ConversationManager(FakeAgent(TypeError("bug in a middleware")))
    conversation_id = manager.start_conversation(actor_id="owner")

    with pytest.raises(TypeError):
        await manager.send_message(
            conversation_id=conversation_id,
            actor_id="owner",
            message="Hola",
        )
