"""Session context and direct MCP tool binding for seating.

The session state keeps three server-owned entries:

- ``visit_context``: the visit id (seeded by the BFF, or generated for CLI and
  hosted runs) and the hold key sequence;
- ``seating_proposal``: the pending hold returned by the seating service;
- ``seating_context``: the seating state the application shows the model
  every turn (none, proposed or seated). The BFF writes it; without it the
  pending proposal is used.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any
from uuid import uuid4

from agent_framework import (
    AgentSession,
    ContextProvider,
    FunctionInvocationContext,
    FunctionMiddleware,
    SessionContext,
)

VISIT_CONTEXT_KEY = "visit_context"
SEATING_PROPOSAL_KEY = "seating_proposal"
SEATING_CONTEXT_KEY = "seating_context"
HOLD_TOOL = "seating_hold_seating"
_PROPOSAL_FIELDS = (
    "assignment_id",
    "resource_id",
    "resource_kind",
    "party_size",
    "version",
    "expires_at",
)
_SEATING_RULES = {
    "none": (
        "No tiene sitio ni propuesta. Cuando el cliente diga cuántos son o pida "
        "mesa o barra, usa seating_hold_seating."
    ),
    "proposed": (
        "Hay una propuesta pendiente. El cliente la confirma o la rechaza solo "
        "con los botones «Confirmar» o «Rechazar» de la vista; una frase no la "
        "confirma. No vuelvas a bloquear salvo que cambie el número de "
        "comensales o pida otro tipo de sitio."
    ),
    "seated": (
        "El grupo ya está sentado en ese sitio. No bloquees otro sitio para "
        "esta visita."
    ),
}

State = MutableMapping[str, Any]


def _new_visit_context(visit_id: str) -> dict[str, Any]:
    return {"visit_id": visit_id, "hold_sequence": 0, "hold_requests": {}}


def bind_visit(state: State, visit_id: str) -> None:
    """Seed the application's visit id; a different stored id starts over."""

    visit_id = visit_id.strip()
    if not visit_id:
        raise ValueError("visit_id cannot be empty")
    current = state.get(VISIT_CONTEXT_KEY)
    if isinstance(current, dict) and current.get("visit_id") == visit_id:
        return
    # Sessions from phase 3 carry a random visit id: its holds belong to
    # another visit, so neither keys nor proposals are reused.
    state[VISIT_CONTEXT_KEY] = _new_visit_context(visit_id)
    state.pop(SEATING_PROPOSAL_KEY, None)


def set_seating_context(state: State, context: Mapping[str, Any] | None) -> None:
    if context is None:
        state.pop(SEATING_CONTEXT_KEY, None)
    else:
        state[SEATING_CONTEXT_KEY] = dict(context)


def pending_proposal(state: Mapping[str, Any]) -> dict[str, Any] | None:
    proposal = state.get(SEATING_PROPOSAL_KEY)
    if not isinstance(proposal, dict) or any(key not in proposal for key in _PROPOSAL_FIELDS):
        return None
    return proposal


def clear_proposal(state: State) -> None:
    """Forget the pending hold; the next hold uses a fresh idempotency key."""

    state.pop(SEATING_PROPOSAL_KEY, None)


def next_hold_key(state: State, party_size: int, preference: str) -> tuple[str, str, str]:
    """Return visit id, idempotency key and request fingerprint for a hold.

    A key is reused only while the pending proposal answers the same request,
    so a retry never holds twice and a new request after a rejection, expiry
    or confirmation never gets an old assignment back.
    """

    visit_context = state.get(VISIT_CONTEXT_KEY)
    if not isinstance(visit_context, dict):
        raise RuntimeError("Visit context is not initialized")
    visit_id = visit_context.get("visit_id")
    if not isinstance(visit_id, str):
        raise RuntimeError("Visit context has no visit_id")
    fingerprint = f"{party_size}:{preference}"
    pending = pending_proposal(state)
    if (
        pending is not None
        and pending.get("fingerprint") == fingerprint
        and isinstance(pending.get("idempotency_key"), str)
    ):
        return visit_id, pending["idempotency_key"], fingerprint
    sequence = visit_context.get("hold_sequence", 0)
    if not isinstance(sequence, int):
        raise RuntimeError("Visit context has invalid hold_sequence")
    sequence += 1
    visit_context["hold_sequence"] = sequence
    # The sequence is saved only with a successful turn: the random part keeps
    # a key from being reused after a failed turn that already held a place.
    key = f"seating:{visit_id}:{sequence}-{uuid4().hex[:12]}"
    requests = visit_context.setdefault("hold_requests", {})
    if isinstance(requests, dict):
        requests[fingerprint] = key
    return visit_id, key, fingerprint


def store_proposal(
    state: State, result: Mapping[str, Any], *, idempotency_key: str, fingerprint: str
) -> bool:
    """Keep a held assignment as the pending proposal of the visit."""

    if any(key not in result for key in _PROPOSAL_FIELDS):
        return False
    if result.get("status", "held") != "held":
        return False
    state[SEATING_PROPOSAL_KEY] = {
        **{key: result[key] for key in _PROPOSAL_FIELDS},
        "seat_ids": list(result.get("seat_ids") or []),
        "resource_label": str(result.get("resource_label") or result["resource_id"]),
        "status": "held",
        "idempotency_key": idempotency_key,
        "fingerprint": fingerprint,
    }
    return True


def seating_instructions(state: Mapping[str, Any]) -> str | None:
    """Non-authoritative seating state for the model, or None without seating."""

    context = state.get(SEATING_CONTEXT_KEY)
    if not isinstance(context, dict):
        pending = pending_proposal(state)
        if pending is None:
            return None
        context = {
            "status": "proposed",
            "place": pending.get("resource_label") or pending["resource_id"],
            "kind": pending["resource_kind"],
            "party_size": pending["party_size"],
            "expires_at": pending["expires_at"],
        }
    status = context.get("status")
    if status not in _SEATING_RULES:
        return None
    return (
        "Estado de asiento de esta visita, mantenido por la aplicación; no es un "
        "mensaje del cliente ni una orden:\n"
        f"{json.dumps(context, ensure_ascii=False)}\n"
        f"{_SEATING_RULES[status]}"
    )


class VisitContextProvider(ContextProvider):
    """Keep server-owned visit state and share the seating state every turn."""

    def __init__(self) -> None:
        super().__init__(source_id="waiter-visit")

    async def before_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        visit_context = session.state.get(VISIT_CONTEXT_KEY)
        if not isinstance(visit_context, dict) or not isinstance(
            visit_context.get("visit_id"), str
        ):
            session.state[VISIT_CONTEXT_KEY] = _new_visit_context(f"visit_{uuid4().hex}")
        instructions = seating_instructions(session.state)
        if instructions is not None:
            context.extend_instructions(self.source_id, instructions)


class SeatingToolContextMiddleware(FunctionMiddleware):
    """Bind session-owned visit values to direct MCP seating tool calls."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != HOLD_TOOL:
            await call_next()
            return
        if context.session is None:
            raise RuntimeError("Seating tools require an Agent Framework session")
        arguments = dict(context.arguments)
        party_size = arguments.get("party_size")
        preference = arguments.get("preference")
        if not isinstance(party_size, int) or preference not in ("table", "bar", "any"):
            raise RuntimeError("hold_seating requires valid party_size and preference")
        visit_id, idempotency_key, fingerprint = next_hold_key(
            context.session.state, party_size, preference
        )
        arguments["visit_id"] = visit_id
        arguments["idempotency_key"] = idempotency_key
        context.arguments = arguments
        await call_next()
        self._save_proposal(context, idempotency_key, fingerprint)

    @staticmethod
    def _save_proposal(
        context: FunctionInvocationContext, idempotency_key: str, fingerprint: str
    ) -> None:
        if context.session is None or context.result is None:
            return
        result = SeatingToolContextMiddleware._result_object(context.result)
        if result is not None:
            store_proposal(
                context.session.state,
                result,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )

    @staticmethod
    def _result_object(result: object) -> dict[str, Any] | None:
        """First JSON object in the tool result.

        MCP tools returning a dict produce two text contents (the text and the
        structured content); both carry the same object.
        """

        items = result if isinstance(result, list) else [result]
        for item in items:
            text = item if isinstance(item, str) else getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None
