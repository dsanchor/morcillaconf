import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from restaurant_contracts.application import COMMAND_ADAPTER, Command

from restaurant_contracts.memory_store import SQLiteMemoryStore

from bff.config import BffSettings
from bff.scripted import ScriptedWaiterAgent
from bff.service import RestaurantService
from bff.storage import Database
from bff.local_waiter import LocalWaiter
from bff.scripted_seating import ScriptedSeating


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


class Commands:
    """Builds valid version-one commands; identity never travels in them."""

    @staticmethod
    def _command(**fields: object) -> Command:
        return COMMAND_ADAPTER.validate_python(
            {
                "schema_version": 1,
                "event_id": fields.pop("event_id", None) or f"cmd_{uuid.uuid4().hex}",
                "occurred_at": "2026-09-28T12:00:00Z",
                **fields,
            }
        )

    def arrive(self, resume_visit_id: str | None = None, *, event_id: str | None = None) -> Command:
        return self._command(
            event_id=event_id,
            event_type="customer.arrived",
            payload={"resume_visit_id": resume_visit_id},
        )

    def say(self, conversation_id: str, text: str, *, event_id: str | None = None) -> Command:
        return self._command(
            event_id=event_id,
            event_type="conversation.message_sent",
            conversation_id=conversation_id,
            payload={"message": text},
        )

    def read_memory(self, conversation_id: str) -> Command:
        return self._command(
            event_type="memory.read_requested", conversation_id=conversation_id, payload={}
        )

    def correct_memory(self, conversation_id: str, memory_id: str, value: str) -> Command:
        return self._command(
            event_type="memory.correction_requested",
            conversation_id=conversation_id,
            payload={"memory_id": memory_id, "value": value},
        )

    def delete_memory(self, conversation_id: str, memory_id: str) -> Command:
        return self._command(
            event_type="memory.deletion_requested",
            conversation_id=conversation_id,
            payload={"memory_id": memory_id},
        )

    def decide(
        self,
        conversation_id: str,
        proposal_id: str,
        version: int = 1,
        decision: str = "confirmed",
        *,
        event_id: str | None = None,
    ) -> Command:
        return self._command(
            event_id=event_id,
            event_type="table.confirmation_decided",
            conversation_id=conversation_id,
            payload={"proposal_id": proposal_id, "version": version, "decision": decision},
        )

    def clear_memory(self, conversation_id: str) -> Command:
        return self._command(
            event_type="memory.clear_requested", conversation_id=conversation_id, payload={}
        )


class GatedAgent(ScriptedWaiterAgent):
    """Scripted waiter that waits until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def run(self, messages, *, session, options):
        await self.gate.wait()
        return await super().run(messages, session=session, options=options)


@pytest.fixture
def commands() -> Commands:
    return Commands()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def settings(tmp_path: Path) -> BffSettings:
    return BffSettings(
        _env_file=None,
        waiter_agent_url="http://test-waiter.invalid",
        bff_database_path=tmp_path / "bff.db",
        memory_database_path=tmp_path / "memory.db",
        bff_sse_heartbeat_seconds=0.2,
    )


ServiceFactory = Callable[..., RestaurantService]


@pytest.fixture
def waiter_factory():
    def build(config, memory_store):
        return LocalWaiter(
            ScriptedWaiterAgent(),
            mode="scripted",
            max_turns=config.waiter_max_turns,
            memory_store=memory_store,
        )

    return build


@pytest.fixture
def make_service(settings: BffSettings, clock: Clock) -> ServiceFactory:
    def build(
        agent: ScriptedWaiterAgent | None = None,
        *,
        max_turns: int = 20,
        retention: int = 500,
        seating: ScriptedSeating | None = None,
    ) -> RestaurantService:
        memory_store = SQLiteMemoryStore(settings.memory_database_path)
        waiter = LocalWaiter(
            agent or ScriptedWaiterAgent(seating=seating),
            mode="scripted",
            max_turns=max_turns,
            memory_store=memory_store,
            seating=seating,
        )
        return RestaurantService(
            database=Database(settings.bff_database_path, event_retention=retention),
            memory_store=memory_store,
            waiter=waiter,
            max_turns=max_turns,
            heartbeat_seconds=0.2,
            clock=clock,
        )

    return build
