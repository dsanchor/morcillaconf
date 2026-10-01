"""Deterministic scripted model for the seating end-to-end check (test code only).

It replaces only the model: the standalone waiter server builds the real
waiter agent (tools, middleware, context providers and its own MCP
connection) and this chat client answers its model calls. It reads the prompt that
ConversationManager builds, so the whole application path (turn limit,
memory, order guard, presented name) runs exactly as with Foundry. With the
seating tools it behaves like the waiter's instructions: «somos N», «barra»
and «mesa» hold a place and the answer asks for the confirmation.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)

from restaurant_contracts.customer import CustomerSnapshot, OrderDraft, OrderItemDraft
from restaurant_contracts.memory import MemoryCandidate, MemoryKind

from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.kitchen_tool import KITCHEN_TOOL
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_agent.memory.intent import MemoryIntentDecision
from restaurant_agent.memory.options import HabitualOrderQuestion
from restaurant_agent.seating import place_text, seat_positions

_STATE = re.compile(r"Estado confirmado antes de este turno:\n(?P<state>.*?)\n\n", re.S)
_MESSAGE = re.compile(
    r"Mensaje actual del cliente[^\n]*:\n(?P<message>.*?)\n\nAplica el mensaje", re.S
)
_FIXED = re.compile(r"Contexto fijado por la aplicación:\n(?P<context>[^\n]*)\n")
_SEATING = re.compile(r"Estado de asiento de esta visita[^\n]*\n(?P<status>\{[^\n]*\})")
_NUMBERS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
_PARTY = re.compile(
    r"\b(?:somos|venimos|seremos)\s+(?P<count>\d{1,2}|" + "|".join(_NUMBERS) + r")\b",
    re.IGNORECASE,
)
_NAME = re.compile(r"\b(?:soy|me llamo)\s+(?P<name>[A-ZÁÉÍÓÚÑ][\wáéíóúñü]+)")
_END = r"(?=[.;!?]|,?\s+y\s+(?:soy|tengo|me|quiero|prefiero|somos|venimos)\b|$)"
_PREFERENCE = re.compile(r"\bprefiero\s+(?P<value>[^.;!?]+?)" + _END, re.IGNORECASE)
_RESTRICTION = re.compile(
    r"\b(?:al[eé]rgic[oa]s?|intolerante)\s+(?:a|al)\s+(?P<value>[^.;!?]+?)" + _END,
    re.IGNORECASE,
)
_ORDER = re.compile(
    r"\b(?:quiero|ponme|p[oó]nme|tr[aá]eme)\s+(?P<value>[^.;!?]+?)" + _END,
    re.IGNORECASE,
)
_BAR = re.compile(r"\bbarra\b", re.IGNORECASE)
_TABLE = re.compile(r"\bmesa\b", re.IGNORECASE)
_YES = re.compile(r"^\W*(?:s[ií]|vale|ok|de acuerdo|confirm\w*)\b", re.IGNORECASE)
_USUAL = re.compile(r"\blo (?:de siempre|habitual)\b|\bcomo siempre\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:el|la|los|las|un|una|unos|unas)\s+", re.IGNORECASE)
_SPLIT_ITEMS = re.compile(r",\s*|\s+y\s+")
_KITCHEN_ORDER = re.compile(r"^\s*(?:pido|pedimos)\s+(?P<items>[^.;!?]+)", re.IGNORECASE)
_HOLD = "seating_hold_seating"
_CONFIRM = "seating_confirm_seating"
_BUTTONS = "Confirmadlo o rechazadlo con los botones."

Failure = Callable[[str], BaseException | None]


def _clean(value: str) -> str:
    return _ARTICLE.sub("", value.strip(" \t\n\"'¡¿«»,"))[:200].strip()


def _join(values: list[str]) -> str:
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} y {values[-1]}"


def _text(message: Message) -> str:
    return "".join(content.text or "" for content in message.contents if content.type == "text")


class ScriptedChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The model of the scripted waiter; everything around it is the real agent."""

    def __init__(self, *, delay_seconds: float = 0.0, failure: Failure | None = None) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        super().__init__()
        self._delay = delay_seconds
        self._failure = failure
        self.prompts: list[str] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(list(messages), dict(options or {}))

    async def _respond(self, messages: list[Message], options: dict[str, Any]) -> ChatResponse:
        response_format = options.get("response_format")
        if response_format is MemoryIntentDecision:
            usual = any(_USUAL.search(_text(message)) for message in messages[-1:])
            intent = MemoryIntent.REUSE_LATEST_ORDER if usual else MemoryIntent.NONE
            return self._json({"memory_intent": intent.value})
        if response_format is HabitualOrderQuestion:
            return self._json({"question": "¿Qué os apetece hoy de lo de siempre?"})
        index = next(
            (
                i
                for i in range(len(messages) - 1, -1, -1)
                if messages[i].role == "user" and _MESSAGE.search(_text(messages[i]))
            ),
            None,
        )
        if index is None:
            raise RuntimeError("Scripted waiter: prompt without message")
        prompt = _text(messages[index])
        after = messages[index + 1 :]
        hold_result = next(
            (
                content
                for message in after
                for content in message.contents
                if content.type == "function_result"
                and (content.call_id or "").startswith("scripted-hold")
            ),
            None,
        )
        message = _MESSAGE.search(prompt)["message"]
        if not after:
            self.prompts.append(prompt)
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._failure is not None:
                failure = self._failure(message)
                if failure is not None:
                    raise failure
        state = json.loads(_STATE.search(prompt)["state"])
        fixed = _FIXED.search(prompt)
        fixed_name = json.loads(fixed["context"])["presented_name"] if fixed else None
        result = self._reply(
            message,
            CustomerSnapshot.model_validate(state["customer"]),
            OrderDraft.model_validate(state["order_draft"]),
            fixed_name,
        )
        seating = self._seating(options)
        tools = {getattr(tool, "name", None) for tool in options.get("tools") or []}
        # «Pido …» is a clear order: it goes to the kitchen before the answer.
        kitchen_result = next(
            (
                content
                for message in after
                for content in message.contents
                if content.type == "function_result" and content.call_id == "scripted-kitchen"
            ),
            None,
        )
        order = _KITCHEN_ORDER.search(message)
        if order and KITCHEN_TOOL in tools:
            if kitchen_result is None:
                items = [_clean(item) for item in _SPLIT_ITEMS.split(order["items"])]
                request = Content.from_function_call(
                    call_id="scripted-kitchen",
                    name=KITCHEN_TOOL,
                    arguments=json.dumps({"items": [{"name": item} for item in items if item]}),
                )
                return ChatResponse(messages=[Message(role="assistant", contents=[request])])
            answer = str(kitchen_result.result or "")
            reply = (
                "Cocina no ha podido revisar el pedido ahora mismo."
                if answer.startswith("kitchen_failed")
                else "Cocina ha revisado el pedido."
            )
            result = result.model_copy(update={"reply": reply})
        if seating is not None and _HOLD in tools:
            if hold_result is None:
                request = self._hold_request(message, result.customer, seating)
                if request is not None:
                    return ChatResponse(
                        messages=[Message(role="assistant", contents=[request])]
                    )
            note, held = self._after_hold(hold_result, seating, message)
            if note:
                reply = note if result.reply.startswith("Tomo nota") else f"{result.reply} {note}"
                result = result.model_copy(update={"reply": reply[:2_000]})
            if held:
                customer = result.customer.model_copy(update={"party_size": held})
                result = result.model_copy(update={"customer": customer})
                confirm = Content.from_function_call(
                    call_id="scripted-confirm", name=_CONFIRM, arguments={}
                )
                return self._json(result.model_dump(mode="json"), confirm)
        return self._json(result.model_dump(mode="json"))

    # Seating, as the instructions tell the waiter

    @staticmethod
    def _seating(options: dict[str, Any]) -> dict[str, Any] | None:
        match = _SEATING.search(str(options.get("instructions") or ""))
        return json.loads(match["status"]) if match else None

    @staticmethod
    def _hold_request(
        message: str, customer: CustomerSnapshot, seating: dict[str, Any]
    ) -> Content | None:
        party, bar, table = _PARTY.search(message), _BAR.search(message), _TABLE.search(message)
        # A pending proposal stays: hold again only for another size or place.
        if seating.get("status") == "seated" or not (party or bar or table):
            return None
        if table and not party and customer.party_size in (None, 1):
            return None
        preference = "bar" if bar else "table" if table else "any"
        return Content.from_function_call(
            call_id="scripted-hold",
            name=_HOLD,
            arguments={"party_size": customer.party_size or 1, "preference": preference},
        )

    @staticmethod
    def _after_hold(
        hold_result: Content | None, seating: dict[str, Any], message: str
    ) -> tuple[str | None, int | None]:
        status = seating.get("status")
        if hold_result is None:
            if status == "seated":
                if _PARTY.search(message) or _BAR.search(message) or _TABLE.search(message):
                    return f"Ya estáis sentados en {seating.get('place', 'vuestro sitio')}.", None
                return None, None
            if status == "none" and _TABLE.search(message) and not _PARTY.search(message):
                return "¿Cuántos sois?", None
            if status == "proposed" and seating.get("awaiting_buttons_again") and _YES.search(message):
                return (
                    f"Para confirmar hay que pulsar «Confirmar»; {seating.get('place')} "
                    "sigue reservada para vosotros."
                ), None
            return None, None
        text = str(hold_result.result or "")
        try:
            held = json.loads(text) if text.lstrip().startswith("{") else None
        except json.JSONDecodeError:
            held = None
        if not isinstance(held, dict) or held.get("status") != "held":
            if "no_seating" in text:
                wanted_table = _TABLE.search(message) and not _BAR.search(message)
                size = _PARTY.search(message)
                count = size["count"] if size else "vosotros"
                if wanted_table:
                    return (
                        f"No queda ninguna mesa libre para {count}. "
                        "Si queréis, os busco sitio en la barra."
                    ), None
                return f"Lo siento, ahora mismo no hay sitio para {count}.", None
            return "Ahora mismo no puedo reservaros sitio.", None
        kind = held.get("resource_kind")
        place = place_text(kind, held.get("resource_label") or "", seat_positions(held.get("seat_ids") or ()))
        size = held["party_size"]
        if kind == "bar" and not _BAR.search(message):
            text = f"No queda mesa libre para {size}; os propongo {place}. {_BUTTONS}"
        else:
            text = f"Os propongo {place} para {size}. {_BUTTONS}"
        return text, size

    # Customer data

    def _reply(
        self,
        message: str,
        customer: CustomerSnapshot,
        draft: OrderDraft,
        fixed_name: str | None,
    ) -> WaiterModelResult:
        updates: dict[str, Any] = {}
        parts: list[str] = []
        party = _PARTY.search(message)
        if party:
            raw = party["count"].casefold()
            count = min(max(int(raw) if raw.isdigit() else _NUMBERS[raw], 1), 20)
            updates["party_size"] = count
            parts.append(f"Sois {count}, anotado.")
        name = _NAME.search(message)
        if name:
            # Behaves like a model that obeys the chat; the application keeps
            # the name from the door.
            updates["presented_name"] = name["name"]
        preferences = [_clean(m["value"]) for m in _PREFERENCE.finditer(message)]
        restrictions = [_clean(m["value"]) for m in _RESTRICTION.finditer(message)]
        preferences = [value for value in preferences if value][:5]
        restrictions = [value for value in restrictions if value][:5]
        if preferences:
            updates["preferences"] = [*customer.preferences, *preferences][-20:]
            parts.append(f"Recordaré que prefieres {_join(preferences)}.")
        if restrictions:
            updates["restrictions"] = [*customer.restrictions, *restrictions][-20:]
            parts.append(
                f"Tendré en cuenta tu alergia a {_join(restrictions)}; "
                "te la volveré a preguntar en cada visita."
            )
        items = [
            _clean(item)
            for match in _ORDER.finditer(message)
            for item in _SPLIT_ITEMS.split(match["value"])
        ]
        items = [item for item in items if item]
        if items:
            draft = OrderDraft(
                items=[*draft.items, *(OrderItemDraft(name=item) for item in items)][-50:]
            )
            parts.append(f"Apunto {_join(items)} en el borrador, sin confirmar.")
        if not parts:
            addressed = fixed_name or customer.presented_name
            parts.append(f"Tomo nota, {addressed}." if addressed else "Tomo nota.")
        return WaiterModelResult(
            reply=" ".join(parts)[:2_000],
            customer=customer.model_copy(update=updates),
            order_draft=draft,
            memory_candidates=[
                *(MemoryCandidate(kind=MemoryKind.PREFERENCE, value=v) for v in preferences),
                *(MemoryCandidate(kind=MemoryKind.RESTRICTION, value=v) for v in restrictions),
            ],
            memory_intent=MemoryIntent.NONE,
        )

    @staticmethod
    def _json(value: dict[str, Any], *calls: Content) -> ChatResponse:
        contents: list[Content] = [Content.from_text(json.dumps(value, ensure_ascii=False))]
        contents.extend(calls)
        return ChatResponse(messages=[Message(role="assistant", contents=contents)])
