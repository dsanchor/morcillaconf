import json

import httpx

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft

from bff.waiter import RemoteWaiter, WaiterTurn, WaiterTurnLimitError


def _turn() -> WaiterTurn:
    return WaiterTurn(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        message="Hola",
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=0,
        persisted_order_preferences=(),
        session_json=None,
        correlation_id="corr_1",
    )


def _response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        },
    )


async def test_remote_waiter_uses_the_responses_protocol() -> None:
    captured = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _response(
            json.dumps(
                {
                    "status": "completed",
                    "reply": "¿Qué os pongo?",
                    "customer": {"presented_name": "Ana"},
                    "order_draft": {"items": []},
                    "turn_count": 1,
                }
            )
        )

    waiter = RemoteWaiter(
        "http://restaurant-agent:8088",
        timeout_seconds=3,
    )
    await waiter._client.aclose()
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    result = await waiter.take_turn(_turn())

    request = captured["request"]
    payload = json.loads(request.content)
    assert request.url.path == "/responses"
    assert request.headers["x-agent-user-id"] == "ana"
    assert payload["conversation"] == {"id": "conv_1"}
    assert json.loads(payload["input"])["operation"] == "take_turn"
    assert result.reply == "¿Qué os pongo?"
    await waiter.aclose()


async def test_remote_waiter_maps_typed_agent_failures() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return _response(
            json.dumps(
                {
                    "status": "failed",
                    "code": "turn_limit",
                    "message": "limit",
                }
            )
        )

    waiter = RemoteWaiter("http://restaurant-agent:8088")
    await waiter._client.aclose()
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    try:
        await waiter.take_turn(_turn())
        raise AssertionError("Expected WaiterTurnLimitError")
    except WaiterTurnLimitError:
        pass
    finally:
        await waiter.aclose()


def _seating_call():
    from bff.waiter import WaiterSeatingCall

    return WaiterSeatingCall(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        session_json='{"state": {}}',
        correlation_id="corr_2",
        visit_id="visit_1",
    )


async def test_seating_decisions_and_syncs_go_to_the_waiter() -> None:
    from bff.waiter import WaiterNoPendingDecisionError

    sent = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["input"])
        sent.append(payload)
        if payload["operation"] == "decide_seating" and payload["proposal_token"] == "old":
            return _response(json.dumps({"status": "failed", "code": "no_pending_decision", "message": "none"}))
        return _response(
            json.dumps(
                {
                    "status": "completed",
                    "operation": payload["operation"],
                    "reply": "¡Estupendo! Os acompaño a la Mesa 3." if payload["operation"] == "decide_seating" else "",
                    "outcome": "confirmed" if payload["operation"] == "decide_seating" else None,
                    "session_json": '{"state": {"x": 1}}',
                    "seating": {"status": "seated", "token": "t1", "party_size": 3,
                                "place": {"place_id": "table-03", "kind": "table", "label": "Mesa 3", "capacity": 4},
                                "seated_at": "2026-09-29T20:00:00+00:00", "room": []},
                }
            )
        )

    waiter = RemoteWaiter("http://restaurant-agent:8088")
    await waiter._client.aclose()
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        decided = await waiter.decide_seating(_seating_call(), approved=True, proposal_token="t1")
        synced = await waiter.sync_seating(_seating_call())
        try:
            await waiter.decide_seating(_seating_call(), approved=False, proposal_token="old")
            raise AssertionError("Expected WaiterNoPendingDecisionError")
        except WaiterNoPendingDecisionError:
            pass
    finally:
        await waiter.aclose()

    assert (decided.outcome, decided.seating.status) == ("confirmed", "seated")
    assert synced.seating.place.label == "Mesa 3"
    assert [item["operation"] for item in sent] == ["decide_seating", "sync_seating", "decide_seating"]
    assert sent[0]["decision"] == "confirmed" and sent[2]["decision"] == "rejected"
    assert all(item["visit_id"] == "visit_1" for item in sent)
    assert not any(key in json.dumps(sent) for key in ("assignment_id", "seating_context"))
