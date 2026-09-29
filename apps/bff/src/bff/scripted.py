"""Deterministic scripted waiter for tests, CI and offline development.

It never calls a model. It reads the prompt that ConversationManager builds,
so the whole application path (turn limit, memory, order guard, presented
name) runs exactly as with the Foundry waiter. With a seating gateway it holds
places like the real waiter's tool call: «somos N», «barra» and «mesa».
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from agent_framework import AgentSession

from restaurant_contracts.customer import CustomerSnapshot, OrderDraft, OrderItemDraft
from restaurant_contracts.memory import MemoryCandidate, MemoryKind

from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.conversation import SeatingUnavailableError
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_agent.seating import (
    SEATING_CONTEXT_KEY,
    VISIT_CONTEXT_KEY,
    next_hold_key,
    pending_proposal,
    store_proposal,
)
from restaurant_agent.seating_gateway import (
    NoSeatingAvailable,
    SeatingAssignment,
    SeatingConflict,
    SeatingGateway,
    SeatingGatewayError,
    SeatingUnavailable,
)

_STATE = re.compile(r"Estado confirmado antes de este turno:\n(?P<state>.*?)\n\n", re.S)
_MESSAGE = re.compile(
    r"Mensaje actual del cliente[^\n]*:\n(?P<message>.*?)\n\nAplica el mensaje", re.S
)
_FIXED = re.compile(r"Contexto fijado por la aplicación:\n(?P<context>[^\n]*)\n")
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
_ARTICLE = re.compile(r"^(?:el|la|los|las|un|una|unos|unas)\s+", re.IGNORECASE)
_SPLIT_ITEMS = re.compile(r",\s*|\s+y\s+")

Failure = Callable[[str], BaseException | None]


def _clean(value: str) -> str:
    return _ARTICLE.sub("", value.strip(" \t\n\"'¡¿«»,"))[:200].strip()


def _as_result(held: SeatingAssignment) -> dict[str, Any]:
    return {
        "assignment_id": held.assignment_id,
        "resource_id": held.resource_id,
        "resource_kind": held.resource_kind,
        "resource_label": held.resource_label or held.resource_id,
        "seat_ids": list(held.seat_ids),
        "party_size": held.party_size,
        "status": held.status,
        "version": held.version,
        "expires_at": held.expires_at.isoformat() if held.expires_at else None,
    }


def seat_positions(seat_ids: Any) -> list[int]:
    """Bar stool positions from the seating service's ``<prefix>-NN`` ids."""

    positions = []
    for seat_id in seat_ids or ():
        suffix = str(seat_id).rsplit("-", 1)[-1]
        if suffix.isdigit():
            positions.append(int(suffix))
    return positions


def stools_text(positions: list[int]) -> str:
    if not positions:
        return "la barra"
    if len(positions) == 1:
        return f"la barra, puesto {positions[0]}"
    return f"la barra, puestos {positions[0]} a {positions[-1]}"


def _describe(held: SeatingAssignment, preference: str) -> str:
    buttons = "Confirmadlo o rechazadlo con los botones."
    if held.resource_kind == "bar":
        place = stools_text(seat_positions(held.seat_ids))
        lead = f"No queda mesa libre para {held.party_size}; os propongo {place}." if preference == "any" else f"Os propongo {place}."
        return f"{lead} {buttons}"
    return f"Os propongo la {held.resource_label or held.resource_id} para {held.party_size}. {buttons}"


def _join(values: list[str]) -> str:
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} y {values[-1]}"


class ScriptedWaiterAgent:
    """Implements the StructuredAgent subset used by ConversationManager."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        failure: Failure | None = None,
        seating: SeatingGateway | None = None,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self._delay = delay_seconds
        self._failure = failure
        self._seating = seating
        self.prompts: list[str] = []

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    async def run(
        self,
        messages: str,
        *,
        session: Any,
        options: dict[str, Any],
    ) -> Any:
        self.prompts.append(messages)
        if self._delay:
            await asyncio.sleep(self._delay)
        message = self._match(_MESSAGE, messages, "message")
        if self._failure is not None:
            failure = self._failure(message)
            if failure is not None:
                raise failure
        state = json.loads(self._match(_STATE, messages, "state"))
        fixed = _FIXED.search(messages)
        fixed_name = json.loads(fixed["context"])["presented_name"] if fixed else None
        result = self._reply(
            message,
            CustomerSnapshot.model_validate(state["customer"]),
            OrderDraft.model_validate(state["order_draft"]),
            fixed_name,
        )
        note = await self._seat(message, result.customer, session.state)
        if note:
            reply = note if result.reply.startswith("Tomo nota") else f"{result.reply} {note}"
            result = result.model_copy(update={"reply": reply[:2_000]})
        history = session.state.setdefault("scripted_history", [])
        history.append(message)
        return SimpleNamespace(value=result)

    async def _seat(
        self, message: str, customer: CustomerSnapshot, state: dict[str, Any]
    ) -> str | None:
        """Hold a place the way the real waiter calls its seating tool."""

        if self._seating is None or VISIT_CONTEXT_KEY not in state:
            return None
        context = state.get(SEATING_CONTEXT_KEY) or {"status": "none"}
        status = context.get("status")
        party, bar, table = (
            _PARTY.search(message), _BAR.search(message), _TABLE.search(message)
        )
        if party:
            state["scripted_party_known"] = True
        if status == "proposed" and not (party or bar or table) and _YES.search(message):
            return "Para confirmar la propuesta usa el botón «Confirmar»; si no os convence, «Rechazar»."
        if not (party or bar or table):
            return None
        if status == "seated":
            return f"Ya estáis sentados en {context.get('place', 'vuestro sitio')}."
        if table and not party and not state.get("scripted_party_known"):
            return "¿Cuántos sois?"
        size = customer.party_size or 1
        preference = "bar" if bar else "table" if table else "any"
        pending = pending_proposal(state)
        if (
            status == "proposed"
            and pending is not None
            and pending.get("fingerprint") == f"{size}:{preference}"
        ):
            return "Ya tenéis una propuesta: usad los botones «Confirmar» o «Rechazar»."
        visit_id, key, fingerprint = next_hold_key(state, size, preference)
        try:
            held = await self._seating.hold(
                visit_id=visit_id, party_size=size, preference=preference, idempotency_key=key
            )
        except NoSeatingAvailable:
            if preference == "table":
                return f"No queda ninguna mesa libre para {size}. Si queréis, os busco sitio en la barra."
            return f"Lo siento, ahora mismo no hay sitio para {size}."
        except SeatingUnavailable as exc:
            raise SeatingUnavailableError("The seating service could not be reached") from exc
        except SeatingConflict:
            return "Ya tenéis sitio en esta visita."
        except SeatingGatewayError:
            return "Ahora mismo no puedo reservaros sitio."
        store_proposal(state, _as_result(held), idempotency_key=key, fingerprint=fingerprint)
        return _describe(held, preference)

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
    def _match(pattern: re.Pattern[str], prompt: str, group: str) -> str:
        match = pattern.search(prompt)
        if match is None:
            raise RuntimeError(f"Scripted waiter: prompt without {group}")
        return match[group]
