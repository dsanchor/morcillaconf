"""Deterministic scripted waiter for tests, CI and offline development.

It never calls a model. It reads the prompt that ConversationManager builds,
so the whole application path (turn limit, memory, order guard, presented
name) runs exactly as with the Foundry waiter.
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
from restaurant_agent.memory.contracts import MemoryIntent

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
_ARTICLE = re.compile(r"^(?:el|la|los|las|un|una|unos|unas)\s+", re.IGNORECASE)
_SPLIT_ITEMS = re.compile(r",\s*|\s+y\s+")

Failure = Callable[[str], BaseException | None]


def _clean(value: str) -> str:
    return _ARTICLE.sub("", value.strip(" \t\n\"'¡¿«»,"))[:200].strip()


def _join(values: list[str]) -> str:
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} y {values[-1]}"


class ScriptedWaiterAgent:
    """Implements the StructuredAgent subset used by ConversationManager."""

    def __init__(self, *, delay_seconds: float = 0.0, failure: Failure | None = None) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self._delay = delay_seconds
        self._failure = failure
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
        history = session.state.setdefault("scripted_history", [])
        history.append(message)
        return SimpleNamespace(value=result)

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
