"""Session context and direct MCP tool binding for seating."""

import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

from agent_framework import (
    AgentSession,
    ContextProvider,
    FunctionInvocationContext,
    FunctionMiddleware,
    SessionContext,
)

_VISIT_CONTEXT_KEY = "visit_context"
_SEATING_PROPOSAL_KEY = "seating_proposal"


class VisitContextProvider(ContextProvider):
    """Initialize server-owned visit state for each Responses conversation."""

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
        visit_context = session.state.get(_VISIT_CONTEXT_KEY)
        if not isinstance(visit_context, dict) or not isinstance(
            visit_context.get("visit_id"), str
        ):
            session.state[_VISIT_CONTEXT_KEY] = {
                "visit_id": f"visit_{uuid4().hex}",
                "hold_sequence": 0,
                "hold_requests": {},
            }


class SeatingToolContextMiddleware(FunctionMiddleware):
    """Bind session-owned visit values to direct MCP seating tool calls."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != "seating_hold_seating":
            await call_next()
            return
        if context.session is None:
            raise RuntimeError("Seating tools require an Agent Framework session")
        visit_context = context.session.state.get(_VISIT_CONTEXT_KEY)
        if not isinstance(visit_context, dict):
            raise RuntimeError("Visit context is not initialized")
        visit_id = visit_context.get("visit_id")
        if not isinstance(visit_id, str):
            raise RuntimeError("Visit context has no visit_id")

        arguments = dict(context.arguments)
        party_size = arguments.get("party_size")
        preference = arguments.get("preference")
        if not isinstance(party_size, int) or preference not in ("table", "bar", "any"):
            raise RuntimeError("hold_seating requires valid party_size and preference")

        fingerprint = f"{party_size}:{preference}"
        requests = visit_context.setdefault("hold_requests", {})
        if not isinstance(requests, dict):
            raise RuntimeError("Visit context has invalid hold_requests")
        idempotency_key = requests.get(fingerprint)
        if not isinstance(idempotency_key, str):
            sequence = visit_context.get("hold_sequence", 0)
            if not isinstance(sequence, int):
                raise RuntimeError("Visit context has invalid hold_sequence")
            sequence += 1
            visit_context["hold_sequence"] = sequence
            idempotency_key = f"seating:{visit_id}:{sequence}"
            requests[fingerprint] = idempotency_key

        arguments["visit_id"] = visit_id
        arguments["idempotency_key"] = idempotency_key
        context.arguments = arguments
        await call_next()
        self._save_proposal(context)

    @staticmethod
    def _save_proposal(context: FunctionInvocationContext) -> None:
        if context.session is None or context.result is None:
            return
        text = SeatingToolContextMiddleware._result_text(context.result)
        if text is None:
            return
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            return
        required = (
            "assignment_id",
            "resource_id",
            "resource_kind",
            "party_size",
            "version",
            "expires_at",
        )
        if not isinstance(result, dict) or any(key not in result for key in required):
            return
        context.session.state[_SEATING_PROPOSAL_KEY] = {
            key: result[key] for key in required
        }

    @staticmethod
    def _result_text(result: object) -> str | None:
        if isinstance(result, str):
            return result
        if isinstance(result, list) and len(result) == 1:
            text = getattr(result[0], "text", None)
            return text if isinstance(text, str) else None
        return None
