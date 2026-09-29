"""Development-only in-process waiter adapter.

Production uses the remote adapter in ``bff.waiter``. This module remains for
deterministic tests and the explicit local scripted mode.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from restaurant_agent.contracts import SessionState
from restaurant_agent.conversation import (
    AgentUnavailableError,
    ConversationError,
    ConversationManager,
    InvalidAgentResponseError,
    SeatingUnavailableError,
    StructuredAgent,
    TurnLimitExceededError,
)
from restaurant_agent.memory.store import DurableMemoryRepository
from restaurant_agent.seating import (
    bind_visit,
    clear_proposal,
    history_source_id,
    pending_proposal,
    record_waiter_note,
    set_seating_context,
)

from bff.waiter import (
    WaiterInvalidResponseError,
    WaiterMode,
    WaiterSeatingUnavailableError,
    WaiterTurn,
    WaiterTurnLimitError,
    WaiterTurnResult,
    WaiterUnavailableError,
)

logger = logging.getLogger(__name__)


class SessionCodec(Protocol):
    def load(self, data: str | None) -> Any | None: ...

    def dump(self, session: Any) -> str | None: ...


class AgentSessionCodec:
    """Agent Framework sessions as JSON, so the waiter keeps its history."""

    def load(self, data: str | None) -> Any | None:
        if not data:
            return None
        from agent_framework import AgentSession

        try:
            return AgentSession.from_dict(json.loads(data))
        except (KeyError, TypeError, ValueError):
            logger.warning("Stored waiter session could not be restored; starting fresh")
            return None

    def dump(self, session: Any) -> str | None:
        try:
            return json.dumps(session.to_dict(), ensure_ascii=False)
        except (AttributeError, TypeError, ValueError):
            logger.warning("Waiter session could not be serialized; its history is dropped")
            return None


class LocalWaiter:
    """Runs one turn through ConversationManager, restored from the BFF state.

    The manager stays the single implementation of the turn limit, automatic
    memory and the once-per-conversation order summary guard.
    """

    def __init__(
        self,
        agent: StructuredAgent,
        *,
        mode: WaiterMode,
        max_turns: int,
        memory_store: DurableMemoryRepository,
        codec: SessionCodec | None = None,
    ) -> None:
        self.mode = mode
        self._agent = agent
        self._max_turns = max_turns
        self._memory_store = memory_store
        self._codec = codec or AgentSessionCodec()

    async def take_turn(self, turn: WaiterTurn) -> WaiterTurnResult:
        session = self._codec.load(turn.session_json)
        if turn.visit_id is not None:
            if session is None:
                session = self._agent.create_session(session_id=turn.conversation_id)
            bind_visit(session.state, turn.visit_id)
            set_seating_context(session.state, turn.seating_context)
            pending = pending_proposal(session.state)
            if pending is not None and pending["assignment_id"] != turn.pending_assignment_id:
                # Decided, expired or reset: the next hold needs a fresh key.
                clear_proposal(session.state)
            source_id = history_source_id(self._agent)
            for note in turn.history_notes:
                record_waiter_note(session.state, note, source_id)
        manager = ConversationManager(
            self._agent,
            max_turns=self._max_turns,
            memory_store=self._memory_store,
        )
        manager.restore_conversation(
            conversation_id=turn.conversation_id,
            actor_id=turn.actor.actor_id,
            authenticated=turn.actor.authenticated,
            presented_name=turn.presented_name,
            agent_session=session,
            state=SessionState(
                customer=turn.customer,
                order_draft=turn.order_draft,
                turn_count=turn.turn_count,
            ),
            persisted_order_preferences=turn.persisted_order_preferences,
        )
        try:
            response = await manager.send_message(
                conversation_id=turn.conversation_id,
                actor_id=turn.actor.actor_id,
                message=turn.message,
                correlation_id=turn.correlation_id,
            )
        except TurnLimitExceededError as exc:
            raise WaiterTurnLimitError(str(exc)) from exc
        except SeatingUnavailableError as exc:
            raise WaiterSeatingUnavailableError(str(exc)) from exc
        except AgentUnavailableError as exc:
            raise WaiterUnavailableError(str(exc)) from exc
        except InvalidAgentResponseError as exc:
            raise WaiterInvalidResponseError(str(exc)) from exc
        except ConversationError as exc:
            raise WaiterError(str(exc)) from exc
        exported = manager.export_conversation(
            conversation_id=turn.conversation_id, actor_id=turn.actor.actor_id
        )
        proposal = pending_proposal(exported.agent_session.state)
        return WaiterTurnResult(
            reply=response.reply,
            customer=exported.state.customer,
            order_draft=exported.state.order_draft,
            turn_count=exported.state.turn_count,
            persisted_order_preferences=tuple(exported.persisted_order_preferences),
            session_json=self._codec.dump(exported.agent_session),
            seating_proposal=dict(proposal) if proposal is not None else None,
        )

    async def aclose(self) -> None:
        """Close the waiter's MCP tools, connected lazily on its first turn."""

        if getattr(self._agent, "mcp_tools", None):
            await self._agent.__aexit__(None, None, None)
