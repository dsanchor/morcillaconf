import json

import pytest
from agent_framework import AgentContext, AgentResponse, AgentSession, Message

from restaurant_agent.contracts import (
    CustomerSnapshot,
    DraftItemStatus,
    OrderDraft,
    OrderItemDraft,
    WaiterModelResult,
)
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_agent.memory.middleware import HabitualOrderMiddleware
from restaurant_agent.memory.options import (
    HabitualOrderOption,
    HabitualOrderSelectionError,
    apply_habitual_order,
    loaded_habitual_order_options,
)


OPTIONS = [
    HabitualOrderOption(option_id="water", summary="tortilla, agua con gas"),
    HabitualOrderOption(option_id="both", summary="tortilla, agua con gas, coca cola"),
]


def repeat(option_id: str | None = None) -> WaiterModelResult:
    return WaiterModelResult(
        reply="Propongo ese pedido sin verificar ni confirmar. ¿Sigue tu alergia?",
        customer=CustomerSnapshot(presented_name="David"),
        memory_intent=MemoryIntent.REUSE_LATEST_ORDER,
        habitual_order_option_id=option_id,
        order_draft=OrderDraft(items=[OrderItemDraft(name="inventado", quantity=9)]),
    )


@pytest.mark.parametrize("option_id", [None, "water"])
def test_unique_habitual_order_uses_only_source_products(option_id: str | None) -> None:
    result = repeat(option_id)
    reply = result.reply

    apply_habitual_order(result, OPTIONS[:1])

    assert [item.name for item in result.order_draft.items] == ["tortilla", "agua con gas"]
    assert all(
        item.quantity == 1
        and item.notes == []
        and item.status is DraftItemStatus.UNVERIFIED
        for item in result.order_draft.items
    )
    assert result.reply == reply
    assert result.customer.restrictions == []


@pytest.mark.parametrize("options", [[], OPTIONS])
def test_unresolved_repeat_clears_invented_draft_without_rewriting_question(options) -> None:
    result = repeat()
    result.reply = "¿Quieres las dos bebidas junto con la tortilla?"

    apply_habitual_order(result, options)

    assert result.order_draft.items == []
    assert result.reply == "¿Quieres las dos bebidas junto con la tortilla?"


def test_explicit_choice_of_overlapping_option_keeps_both_drinks() -> None:
    result = repeat("both")

    apply_habitual_order(result, OPTIONS)

    assert [item.name for item in result.order_draft.items] == [
        "tortilla", "agua con gas", "coca cola",
    ]


@pytest.mark.parametrize("options", [[], OPTIONS[:1], OPTIONS])
def test_unknown_stale_or_foreign_id_is_rejected_not_replaced_by_unique_option(options) -> None:
    result = repeat("not-in-this-profile")

    with pytest.raises(HabitualOrderSelectionError, match="current memory context"):
        apply_habitual_order(result, options)


def test_selection_without_repeat_intent_is_rejected() -> None:
    result = repeat("water")
    result.memory_intent = MemoryIntent.NONE

    with pytest.raises(HabitualOrderSelectionError, match="requires a repeat intent"):
        apply_habitual_order(result, OPTIONS)


@pytest.mark.parametrize(
    "message", ["otra de croquetas", "lo habitual pero sin tortilla", "dos cañas más"]
)
def test_new_and_modified_orders_are_not_replaced_by_habitual_memory(message: str) -> None:
    result = WaiterModelResult(
        reply=message,
        customer=CustomerSnapshot(),
        memory_intent=MemoryIntent.NONE,
        order_draft=OrderDraft(
            items=[OrderItemDraft(name="croquetas", quantity=2, notes=["sin salsa"])]
        ),
    )
    before = result.model_dump()

    apply_habitual_order(result, OPTIONS)

    assert result.model_dump() == before


def test_truncated_summary_requires_clarification() -> None:
    options = [HabitualOrderOption(option_id="partial", summary="tortilla, croqu...")]
    result = repeat()

    apply_habitual_order(result, options)

    assert result.order_draft.items == []
    with pytest.raises(HabitualOrderSelectionError, match="incomplete"):
        apply_habitual_order(repeat("partial"), options)


def test_options_survive_json_session_roundtrip() -> None:
    state = {"habitual_order_options": [option.model_dump() for option in OPTIONS]}

    assert loaded_habitual_order_options(json.loads(json.dumps(state))) == OPTIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_middleware_validates_normal_and_streaming_results(stream: bool) -> None:
    result = repeat("both")
    response = AgentResponse(value=result)
    session = AgentSession(session_id="current")
    session.state["habitual_order_options"] = [option.model_dump() for option in OPTIONS]
    context = AgentContext(
        agent=object(),
        messages=[Message("user", ["Las dos bebidas con tortilla"])],
        session=session,
        result=response,
        stream=stream,
    )

    async def call_next() -> None:
        if stream:
            for hook in context.stream_result_hooks:
                assert await hook(response) is response

    await HabitualOrderMiddleware().process(context, call_next)

    assert [item.name for item in result.order_draft.items] == [
        "tortilla", "agua con gas", "coca cola",
    ]
    assert result.reply.startswith("Propongo")


@pytest.mark.asyncio
async def test_middleware_leaves_fixed_seating_response_unchanged() -> None:
    response = AgentResponse(messages=[Message("assistant", ["Mesa confirmada."])])
    context = AgentContext(agent=object(), messages=[], result=response)

    async def call_next() -> None:
        pass

    await HabitualOrderMiddleware().process(context, call_next)

    assert response.text == "Mesa confirmada."
