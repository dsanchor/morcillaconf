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
