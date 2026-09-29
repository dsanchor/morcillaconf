"""Seating for the waiter: the agent is the only client of the seating MCP.

The model holds a place with ``seating_hold_seating`` and asks the customer to
confirm it with ``seating_confirm_seating``, a tool that requires approval: the
run pauses (HITL) until the customer's explicit decision arrives as a function
approval response. Everything else happens deterministically inside the
agent's own runs, through the same ``MCPStreamableHTTPTool`` connection:

- ``VisitContextProvider`` binds the visit, cancels the hold when the customer
  rejects, reads the anonymised room map before and after every run, derives
  the visit's own seating and shares it with the model as context;
- ``SeatingToolContextMiddleware`` injects the authoritative ids into hold and
  confirm calls and checks the proposal is still current before confirming;
- ``SeatingApprovalChatMiddleware`` makes sure a hold always ends in exactly
  one approval request and answers decisions with fixed replies.

Session state keys (JSON-serializable, restored with the session):

- ``visit_context``: visit id and hold key sequence;
- ``seating_proposal``: the pending hold;
- ``seating_seated``: the confirmed place;
- ``seating_last_outcome``: what happened to the latest proposal;
- ``seating_room``: the latest anonymised room map;
- ``seating_report``: what the application shows after the run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_framework import (
    AgentSession,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    Content,
    ContextProvider,
    FunctionInvocationContext,
    FunctionMiddleware,
    Message,
    SessionContext,
)

VISIT_CONTEXT_KEY = "visit_context"
SEATING_PROPOSAL_KEY = "seating_proposal"
SEATED_KEY = "seating_seated"
LAST_OUTCOME_KEY = "seating_last_outcome"
ROOM_KEY = "seating_room"
REPORT_KEY = "seating_report"
DECISION_KEY = "seating_decision"
FRESH_HOLD_KEY = "seating_fresh_hold"
CONFIRM_CALL_KEY = "seating_confirm_call"
CONTROL_KEY = "seating_control"
LAST_PLACE_KEY = "seating_last_place"

HOLD_TOOL = "seating_hold_seating"
CONFIRM_TOOL = "seating_confirm_seating"
CONFIRM_NAMES = ("confirm_seating", CONFIRM_TOOL)
_PROPOSAL_FIELDS = (
    "assignment_id",
    "resource_id",
    "resource_kind",
    "party_size",
    "version",
    "expires_at",
)
_ERROR_CODE = re.compile(r"\b(no_seating|expired|not_found|conflict|idempotency_conflict): ")

REPLIES = {
    "confirmed": "¡Estupendo! Os acompaño a {place}.",
    "rejected": "Sin problema, dejo libre {place}. ¿Preferís otro sitio?",
    "expired": (
        "La reserva de {place} ha caducado y ya está libre. "
        "Si queréis, pedidme sitio otra vez."
    ),
    "stale": "Esa propuesta ya no está vigente.",
    "unavailable": (
        "El servicio de mesas no responde ahora mismo. "
        "Inténtalo de nuevo en un momento."
    ),
}
CARD_REPLY = (
    "Os propongo {place} para {party}. Confirmadlo o rechazadlo con los botones."
)
_SEATING_RULES = {
    "none": (
        "No tiene sitio ni propuesta pendiente, aunque el historial mencione "
        "una anterior. Cuando el cliente diga cuántos son o pida mesa o barra, "
        "llama a seating_hold_seating y describe solo lo que devuelva."
    ),
    "proposed": (
        "Hay una propuesta pendiente de la decisión del cliente con los botones "
        "«Confirmar» o «Rechazar»; es la única que puedes describir. Una frase "
        "no la confirma."
    ),
    "seated": (
        "El grupo ya está sentado en ese sitio. No bloquees otro sitio para "
        "esta visita."
    ),
}
_OUTCOMES = {
    "rejected": "el cliente la rechazó",
    "superseded": (
        "el cliente escribió en lugar de pulsar un botón y la aplicación la "
        "retiró; si sigue queriendo sitio, vuelve a bloquear con el mismo "
        "número de comensales y preferencia salvo que haya cambiado de idea"
    ),
    "expired": "caducó sin confirmar",
    "cancelled": "se anuló",
    "confirmed": "el cliente la confirmó",
}
_ONLY_CONTEXT = (
    "Solo puedes presentar como propuesta la que aparezca en este estado; "
    "nunca una que solo esté en el historial."
)

State = MutableMapping[str, Any]


# Visit, keys and proposals


def _new_visit_context(visit_id: str) -> dict[str, Any]:
    return {"visit_id": visit_id, "hold_sequence": 0, "hold_requests": {}}


def visit_id_of(state: Mapping[str, Any]) -> str | None:
    context = state.get(VISIT_CONTEXT_KEY)
    visit_id = context.get("visit_id") if isinstance(context, dict) else None
    return visit_id if isinstance(visit_id, str) else None


def bind_visit(state: State, visit_id: str) -> None:
    """Seed the application's visit id; a different stored id starts over."""

    visit_id = visit_id.strip()
    if not visit_id:
        raise ValueError("visit_id cannot be empty")
    if visit_id_of(state) == visit_id:
        return
    # Sessions from phase 3 carry a random visit id: its holds belong to
    # another visit, so neither keys nor places are reused.
    state[VISIT_CONTEXT_KEY] = _new_visit_context(visit_id)
    for key in (SEATING_PROPOSAL_KEY, SEATED_KEY, LAST_OUTCOME_KEY, REPORT_KEY):
        state.pop(key, None)


def pending_proposal(state: Mapping[str, Any]) -> dict[str, Any] | None:
    proposal = state.get(SEATING_PROPOSAL_KEY)
    if not isinstance(proposal, dict) or any(key not in proposal for key in _PROPOSAL_FIELDS):
        return None
    return proposal


def clear_proposal(state: State) -> None:
    """Forget the pending hold; the next hold uses a fresh idempotency key."""

    state.pop(SEATING_PROPOSAL_KEY, None)
    state.pop(FRESH_HOLD_KEY, None)


def next_hold_key(state: State, party_size: int, preference: str) -> tuple[str, str, str]:
    """Return visit id, idempotency key and request fingerprint for a hold.

    A key is reused only while the pending proposal answers the same request.
    The random part keeps a key from being reused after a lost turn, whose
    sequence was never saved.
    """

    visit_id = visit_id_of(state)
    if visit_id is None:
        raise RuntimeError("Visit context is not initialized")
    visit_context = state[VISIT_CONTEXT_KEY]
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


def seat_positions(seat_ids: Iterable[Any]) -> list[int]:
    """Bar stool positions from the seating service's ``<prefix>-NN`` ids."""

    positions = []
    for seat_id in seat_ids or ():
        suffix = str(seat_id).rsplit("-", 1)[-1]
        if suffix.isdigit():
            positions.append(int(suffix))
    return positions


def place_text(kind: str, label: str, seats: list[int]) -> str:
    if kind == "bar":
        if not seats:
            return "la barra"
        if len(seats) == 1:
            return f"la barra, puesto {seats[0]}"
        return f"la barra, puestos {seats[0]} a {seats[-1]}"
    return f"la {label}"


def _proposal_place(proposal: Mapping[str, Any]) -> str:
    return place_text(
        proposal.get("resource_kind", "table"),
        proposal.get("resource_label") or proposal.get("resource_id", ""),
        seat_positions(proposal.get("seat_ids") or ()),
    )


def place_token(assignment_id: str) -> str:
    """Opaque token the application uses to name a place without MCP ids."""

    return hashlib.sha256(assignment_id.encode()).hexdigest()[:24]


def decision_outcome(state: Mapping[str, Any]) -> str | None:
    """How the last seating decision of this run ended (confirmed, rejected…)."""

    decision = state.get(DECISION_KEY)
    return decision.get("decision") if isinstance(decision, dict) else None


def confirm_arguments(proposal: Mapping[str, Any], visit_id: str) -> dict[str, Any]:
    """Authoritative confirm arguments; the model never supplies them."""

    return {
        "assignment_id": proposal["assignment_id"],
        "visit_id": visit_id,
        "expected_version": proposal["version"],
        "idempotency_key": f"confirm:{proposal['assignment_id']}:{proposal['version']}",
    }


def card_reply(state: Mapping[str, Any]) -> str | None:
    proposal = pending_proposal(state)
    if proposal is None:
        return None
    return CARD_REPLY.format(place=_proposal_place(proposal), party=proposal["party_size"])


# Parsing tool output


def result_object(result: object) -> dict[str, Any] | None:
    """First JSON object in a tool result.

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


def error_code(error: BaseException | str) -> str | None:
    """Stable seating error code (``expired``, ``conflict``…) in an error chain."""

    texts: list[str] = []
    if isinstance(error, str):
        texts.append(error)
    else:
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            texts.append(str(current))
            current = current.__cause__ or current.__context__
    for text in texts:
        match = _ERROR_CODE.search(text)
        if match:
            return match.group(1)
    return None


# Room and seating state


def _anonymous_room(room: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The map without the caller's marks or seat ids, shareable with any viewer."""

    places = []
    for item in room.get("resources") or ():
        places.append(
            {
                "place_id": item["resource_id"],
                "kind": item["kind"],
                "label": item["label"],
                "capacity": item["capacity"],
                "display_order": item["display_order"],
                "state": item["state"],
                "party_size": item.get("party_size"),
                "expires_at": item.get("expires_at"),
                "seats": [
                    {
                        "position": seat["position"],
                        "state": seat["state"],
                        "expires_at": seat.get("expires_at"),
                    }
                    for seat in item.get("seats") or ()
                ],
            }
        )
    return places


def _resource(room: Mapping[str, Any], resource_id: str) -> Mapping[str, Any] | None:
    return next(
        (item for item in room.get("resources") or () if item.get("resource_id") == resource_id),
        None,
    )


def _positions_in_room(room: Mapping[str, Any], resource_id: str, seat_ids: list[str]) -> list[int]:
    resource = _resource(room, resource_id)
    if resource is None:
        return seat_positions(seat_ids)
    by_id = {seat["seat_id"]: seat["position"] for seat in resource.get("seats") or ()}
    return [by_id[seat_id] for seat_id in seat_ids if seat_id in by_id] or seat_positions(seat_ids)


def set_outcome(state: State, decision: str, place: str) -> None:
    state[LAST_OUTCOME_KEY] = {"decision": decision, "place": place}


def apply_room(state: State, room: Mapping[str, Any]) -> None:
    """Adopt the seating service's truth for the visit's own place."""

    state[ROOM_KEY] = dict(room)
    visit = room.get("visit") if isinstance(room.get("visit"), dict) else None
    proposal = pending_proposal(state)
    seated = state.get(SEATED_KEY) if isinstance(state.get(SEATED_KEY), dict) else None
    if proposal is not None:
        same = visit is not None and visit.get("assignment_id") == proposal["assignment_id"]
        label = proposal.get("resource_label") or proposal["resource_id"]
        if same and visit.get("status") == "held":
            proposal["version"] = visit.get("version", proposal["version"])
            proposal["expires_at"] = visit.get("expires_at", proposal["expires_at"])
        elif same and visit.get("status") == "occupied":
            _seat(state, visit, room)
            set_outcome(state, "confirmed", label)
        else:
            clear_proposal(state)
            expired = same and visit.get("status") == "expired"
            set_outcome(state, "expired" if expired else "cancelled", label)
            state[LAST_PLACE_KEY] = _proposal_place(proposal)
    elif visit is not None and visit.get("status") == "occupied":
        if seated is None or seated.get("assignment_id") != visit.get("assignment_id"):
            _seat(state, visit, room)
    elif seated is not None:
        # Released or reset: the place is no longer the visit's.
        state.pop(SEATED_KEY, None)
        set_outcome(state, "cancelled", seated.get("label", ""))


def _seat(state: State, assignment: Mapping[str, Any], room: Mapping[str, Any]) -> None:
    resource = _resource(room, assignment["resource_id"])
    label = (resource or {}).get("label") or assignment.get("resource_label") or assignment["resource_id"]
    state[SEATED_KEY] = {
        "assignment_id": assignment["assignment_id"],
        "resource_id": assignment["resource_id"],
        "kind": assignment.get("resource_kind", "table"),
        "label": label,
        "capacity": (resource or {}).get("capacity", assignment.get("party_size", 1)),
        "seats": _positions_in_room(room, assignment["resource_id"], list(assignment.get("seat_ids") or ())),
        "party_size": assignment["party_size"],
        "version": assignment.get("version", 1),
        "seated_at": state.get(SEATED_KEY, {}).get("seated_at") if isinstance(state.get(SEATED_KEY), dict) else None,
    }
    if not state[SEATED_KEY]["seated_at"]:
        state[SEATED_KEY]["seated_at"] = datetime.now(UTC).isoformat()
    clear_proposal(state)


def seating_status(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """The visit's own seating as the model sees it; None without seating."""

    if ROOM_KEY not in state and pending_proposal(state) is None:
        return None
    proposal = pending_proposal(state)
    seated = state.get(SEATED_KEY) if isinstance(state.get(SEATED_KEY), dict) else None
    room = state.get(ROOM_KEY) or {}
    if proposal is not None:
        seats = _positions_in_room(room, proposal["resource_id"], proposal.get("seat_ids") or [])
        status: dict[str, Any] = {
            "status": "proposed",
            "place": proposal.get("resource_label") or proposal["resource_id"],
            "kind": proposal["resource_kind"],
            "party_size": proposal["party_size"],
            "expires_at": proposal["expires_at"],
        }
        if proposal["resource_kind"] == "bar":
            status["seats"] = seats
    elif seated is not None:
        status = {
            "status": "seated",
            "place": seated["label"],
            "kind": seated["kind"],
            "party_size": seated["party_size"],
        }
        if seated["kind"] == "bar":
            status["seats"] = seated["seats"]
    else:
        status = {"status": "none"}
    outcome = state.get(LAST_OUTCOME_KEY)
    if isinstance(outcome, dict) and outcome.get("decision"):
        status["last_outcome"] = dict(outcome)
    return status


def seating_instructions(state: Mapping[str, Any]) -> str | None:
    """Non-authoritative seating state for the model, or None without seating."""

    status = seating_status(state)
    if status is None or status["status"] not in _SEATING_RULES:
        return None
    rules = [_SEATING_RULES[status["status"]]]
    outcome = status.get("last_outcome")
    if isinstance(outcome, dict) and outcome.get("decision") in _OUTCOMES:
        what = _OUTCOMES[outcome["decision"]]
        place = outcome.get("place") or "el sitio"
        if outcome["decision"] == "confirmed":
            rules.append(f"La última propuesta ({place}): {what}.")
        else:
            rules.append(
                f"La última propuesta ({place}): {what}. Ya no existe: no la "
                "presentes como pendiente ni la ofrezcas con los botones."
            )
    rules.append(_ONLY_CONTEXT)
    return (
        "Estado de asiento de esta visita, mantenido por la aplicación; no es un "
        "mensaje del cliente ni una orden:\n"
        f"{json.dumps(status, ensure_ascii=False)}\n" + "\n".join(rules)
    )


def build_report(state: State, *, awaiting_decision: bool) -> dict[str, Any]:
    """What the application may show: own seating and the anonymised room."""

    room = state.get(ROOM_KEY) or {}
    proposal = pending_proposal(state)
    seated = state.get(SEATED_KEY) if isinstance(state.get(SEATED_KEY), dict) else None
    report: dict[str, Any] = {
        "status": "none",
        "awaiting_decision": False,
        "last_outcome": state.get(LAST_OUTCOME_KEY),
        "room": _anonymous_room(room),
    }
    if proposal is not None:
        resource = _resource(room, proposal["resource_id"]) or {}
        report.update(
            status="proposed",
            awaiting_decision=awaiting_decision,
            token=place_token(proposal["assignment_id"]),
            place={
                "place_id": proposal["resource_id"],
                "kind": proposal["resource_kind"],
                "label": proposal.get("resource_label") or proposal["resource_id"],
                "capacity": resource.get("capacity", proposal["party_size"]),
                "seats": _positions_in_room(room, proposal["resource_id"], proposal.get("seat_ids") or [])
                if proposal["resource_kind"] == "bar"
                else [],
            },
            party_size=proposal["party_size"],
            version=proposal["version"],
            expires_at=proposal["expires_at"],
        )
    elif seated is not None:
        report.update(
            status="seated",
            token=place_token(seated["assignment_id"]),
            place={
                "place_id": seated["resource_id"],
                "kind": seated["kind"],
                "label": seated["label"],
                "capacity": seated["capacity"],
                "seats": seated["seats"] if seated["kind"] == "bar" else [],
            },
            party_size=seated["party_size"],
            seated_at=seated["seated_at"],
        )
    state[REPORT_KEY] = report
    return report


# Approvals


def is_confirm_call(content: Any) -> bool:
    return getattr(content, "name", None) in CONFIRM_NAMES


def confirm_approval_requests(requests: Iterable[Any]) -> list[Any]:
    return [
        request
        for request in requests
        if getattr(request, "type", None) == "function_approval_request"
        and is_confirm_call(getattr(request, "function_call", None))
    ]


def pending_confirm_request(state: Mapping[str, Any]) -> Content | None:
    """The confirm approval request Agent Framework keeps while the run is paused.

    Read from the framework's own session state (``tool_approval``), so an
    approval can only answer a request the agent really issued.
    """

    bag = state.get("tool_approval")
    raw = bag.get("pending_approval_requests") if isinstance(bag, Mapping) else None
    for item in raw or ():
        request = item if isinstance(item, Content) else Content.from_dict(item) if isinstance(item, Mapping) else None
        if request is not None and request.type == "function_approval_request" and is_confirm_call(request.function_call):
            return request
    return None


def rejected_confirmations(messages: Iterable[Message]) -> list[Content]:
    return [
        content
        for message in messages
        for content in message.contents
        if content.type == "function_approval_response"
        and not content.approved
        and is_confirm_call(content.function_call)
    ]


class VisitContextProvider(ContextProvider):
    """Server-owned visit state; with seating, the agent's own map, cancel and context."""

    def __init__(self, seating_tool: Any | None = None) -> None:
        super().__init__(source_id="waiter-visit")
        self._tool = seating_tool

    async def before_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        if visit_id_of(session.state) is None:
            session.state[VISIT_CONTEXT_KEY] = _new_visit_context(f"visit_{uuid4().hex}")
        session.state.pop(DECISION_KEY, None)
        session.state.pop(FRESH_HOLD_KEY, None)
        if self._tool is not None:
            await self._connect()
            rejected = rejected_confirmations(context.input_messages)
            if rejected:
                superseded = any(
                    content.type == "text" and content.text
                    for message in context.input_messages
                    for content in message.contents
                )
                await self._cancel(session.state, "superseded" if superseded else "rejected")
            await self.refresh(session.state)
        instructions = seating_instructions(session.state)
        if instructions is not None:
            context.extend_instructions(self.source_id, instructions)

    async def after_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        if self._tool is None:
            return
        await self.refresh(session.state)
        build_report(session.state, awaiting_decision=pending_confirm_request(session.state) is not None)

    async def refresh(self, state: State) -> None:
        visit_id = visit_id_of(state)
        if self._tool is None or visit_id is None:
            return
        room = result_object(await self._tool.call_tool("get_seating_map", visit_id=visit_id))
        if room is not None:
            apply_room(state, room)

    async def _connect(self) -> None:
        if not self._tool.is_connected:
            await self._tool.connect()

    async def _cancel(self, state: State, decision: str) -> None:
        """Free the rejected or superseded hold at once, deterministically."""

        proposal = pending_proposal(state)
        visit_id = visit_id_of(state)
        if proposal is None or visit_id is None:
            return
        place = _proposal_place(proposal)
        try:
            await self._tool.call_tool(
                "cancel_seating_hold",
                assignment_id=proposal["assignment_id"],
                visit_id=visit_id,
                expected_version=proposal["version"],
                idempotency_key=f"cancel:{proposal['assignment_id']}:{proposal['version']}",
            )
            outcome = decision
        except Exception as exc:
            code = error_code(exc)
            if code is None:
                raise
            # Expired or already changed: the map tells the truth below.
            outcome = "expired" if code == "expired" else decision
        clear_proposal(state)
        set_outcome(state, outcome, proposal.get("resource_label") or proposal["resource_id"])
        state[DECISION_KEY] = {"decision": outcome, "place": place}
        if decision == "superseded":
            # A new message follows: the model answers it, no fixed reply.
            state.pop(CONFIRM_CALL_KEY, None)


class SeatingToolContextMiddleware(FunctionMiddleware):
    """Bind session-owned values to seating tool calls; the model never supplies them."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        name = context.function.name
        if name == HOLD_TOOL:
            await self._hold(context, call_next)
        elif name in CONFIRM_NAMES:
            await self._confirm(context, call_next)
        else:
            await call_next()

    async def _hold(
        self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        if context.session is None:
            raise RuntimeError("Seating tools require an Agent Framework session")
        state = context.session.state
        arguments = dict(context.arguments)
        party_size = arguments.get("party_size")
        preference = arguments.get("preference")
        if not isinstance(party_size, int) or preference not in ("table", "bar", "any"):
            raise RuntimeError("hold_seating requires valid party_size and preference")
        if isinstance(state.get(SEATED_KEY), dict):
            context.result = "conflict: el grupo ya está sentado en esta visita"
            return
        visit_id, idempotency_key, fingerprint = next_hold_key(state, party_size, preference)
        arguments["visit_id"] = visit_id
        arguments["idempotency_key"] = idempotency_key
        context.arguments = arguments
        await call_next()
        result = result_object(context.result)
        if result is not None and store_proposal(
            state, result, idempotency_key=idempotency_key, fingerprint=fingerprint
        ):
            state[FRESH_HOLD_KEY] = True
            state.pop(LAST_OUTCOME_KEY, None)

    async def _confirm(
        self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        """Runs only after the customer's approval; checks the proposal first."""

        if context.session is None:
            raise RuntimeError("Seating tools require an Agent Framework session")
        state = context.session.state
        proposal = pending_proposal(state)
        visit_id = visit_id_of(state)
        requested = dict(context.arguments)
        if (
            proposal is None
            or visit_id is None
            or requested.get("assignment_id") != proposal["assignment_id"]
            or requested.get("expected_version") != proposal["version"]
        ):
            outcome = state.get(LAST_OUTCOME_KEY)
            if proposal is None and isinstance(outcome, dict) and outcome.get("decision") == "expired":
                # The service expired it before the click reached the agent.
                state[DECISION_KEY] = {"decision": "expired", "place": state.get(LAST_PLACE_KEY, "")}
                context.result = "expired: la reserva ha caducado"
                return
            state[DECISION_KEY] = {"decision": "stale", "place": ""}
            context.result = "conflict: la propuesta ya no está vigente"
            return
        place = _proposal_place(proposal)
        label = proposal.get("resource_label") or proposal["resource_id"]
        expires = proposal.get("expires_at")
        if isinstance(expires, str) and datetime.fromisoformat(expires) <= self._clock():
            state[DECISION_KEY] = {"decision": "expired", "place": place}
            clear_proposal(state)
            set_outcome(state, "expired", label)
            context.result = "expired: la reserva ha caducado"
            return
        context.arguments = confirm_arguments(proposal, visit_id)
        try:
            await call_next()
        except Exception as exc:
            code = error_code(exc)
            decision = {"expired": "expired", None: "unavailable"}.get(code, "stale")
            state[DECISION_KEY] = {"decision": decision, "place": place}
            if decision != "unavailable":
                clear_proposal(state)
                set_outcome(state, decision if decision == "expired" else "cancelled", label)
            raise
        result = result_object(context.result)
        if result is not None and result.get("status") == "occupied":
            state[DECISION_KEY] = {"decision": "confirmed", "place": place}
            _seat(state, result | {"resource_label": label}, state.get(ROOM_KEY) or {})
            set_outcome(state, "confirmed", label)
            return
        code = error_code(str(context.result))
        decision = "expired" if code == "expired" else "unavailable" if code is None else "stale"
        state[DECISION_KEY] = {"decision": decision, "place": place}
        if decision != "unavailable":
            clear_proposal(state)
            set_outcome(state, decision if decision == "expired" else "cancelled", label)


class SeatingApprovalChatMiddleware(ChatMiddleware):
    """Deterministic safeguards around the model's seating calls.

    1. A confirm call never shares a response with other calls: the hold runs
       first and the confirmation is requested afterwards.
    2. Confirm calls carry the authoritative arguments, and only while a
       fresh hold is pending.
    3. A hold made in this run always ends in one approval request, even if
       the model forgot to ask.
    4. After a decision the waiter answers with a fixed reply, without
       calling the model, so the model's history matches the customer's.
    5. Sync runs never call the model.
    """

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        session = context.session
        if session is None:
            await call_next()
            return
        state = session.state
        if state.get(CONTROL_KEY) == "sync":
            context.result = ChatResponse(messages=[])
            return
        decided = self._decision_reply(state, context.messages)
        if decided is not None:
            context.result = decided
            return
        await call_next()
        response = context.result
        if isinstance(response, ChatResponse):
            self._guard(state, response)

    @staticmethod
    def _decision_reply(state: State, messages: list[Message]) -> ChatResponse | None:
        call_id = state.get(CONFIRM_CALL_KEY)
        decision = state.get(DECISION_KEY)
        if not isinstance(call_id, str) or not isinstance(decision, dict) or not messages:
            return None
        last = messages[-1]
        if not any(
            content.type == "function_result" and content.call_id == call_id
            for content in last.contents
        ):
            return None
        state.pop(CONFIRM_CALL_KEY, None)
        outcome = decision.get("decision")
        text = REPLIES.get(outcome, REPLIES["stale"]).format(place=decision.get("place") or "el sitio")
        contents: list[Content] = [Content.from_text(text)]
        proposal = pending_proposal(state)
        visit_id = visit_id_of(state)
        if outcome == "unavailable" and proposal is not None and visit_id is not None:
            # The approval was spent but the hold is still there: ask again.
            contents.append(_confirm_call(state, proposal, visit_id))
        return ChatResponse(messages=[Message(role="assistant", contents=contents)])

    @staticmethod
    def _guard(state: State, response: ChatResponse) -> None:
        proposal = pending_proposal(state)
        visit_id = visit_id_of(state)
        fresh = bool(state.get(FRESH_HOLD_KEY))
        calls = [
            content
            for message in response.messages
            for content in message.contents
            if content.type == "function_call"
        ]
        confirms = [call for call in calls if is_confirm_call(call)]
        others = [call for call in calls if not is_confirm_call(call)]
        drop = set()
        if confirms and (others or not fresh or proposal is None or visit_id is None):
            drop.update(id(call) for call in confirms)
        else:
            for call in confirms[1:]:
                drop.add(id(call))
        if drop:
            for message in response.messages:
                message.contents = [content for content in message.contents if id(content) not in drop]
            confirms = [call for call in confirms if id(call) not in drop]
        if confirms and proposal is not None and visit_id is not None:
            confirms[0].arguments = confirm_arguments(proposal, visit_id)
            state[CONFIRM_CALL_KEY] = confirms[0].call_id
            state.pop(FRESH_HOLD_KEY, None)
            return
        if fresh and not others and proposal is not None and visit_id is not None:
            if not response.messages:
                response.messages.append(Message(role="assistant", contents=[]))
            response.messages[-1].contents.append(_confirm_call(state, proposal, visit_id))
            state.pop(FRESH_HOLD_KEY, None)


def _confirm_call(state: State, proposal: Mapping[str, Any], visit_id: str) -> Content:
    call_id = f"seating-confirm-{uuid4().hex[:16]}"
    state[CONFIRM_CALL_KEY] = call_id
    return Content.from_function_call(
        call_id=call_id,
        name=CONFIRM_TOOL,
        arguments=confirm_arguments(proposal, visit_id),
    )
