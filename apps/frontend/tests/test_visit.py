from restaurant_contracts.application import (
    ActorContext,
    ArriveCommand,
    ErrorCode,
    PendingCommandResult,
    PublicError,
)
from restaurant_contracts.client import BffClientError

from frontend.fake_client import FakeRestaurant
from frontend.visit import (
    MESSAGES_CLOSED,
    TRY_NEW_VISIT,
    UNKNOWN_COMMAND,
    VisitSession,
)


def _visit(name: str = "Ana", restaurant: FakeRestaurant | None = None) -> VisitSession:
    restaurant = restaurant or FakeRestaurant()
    visit = VisitSession(restaurant.client(ActorContext(actor_id=name, authenticated=True)))
    visit.arrive()
    return visit


def test_arrival_projects_the_confirmed_snapshot() -> None:
    visit = _visit("Luis")
    assert visit.name == "Luis"
    assert visit.pending is None
    assert visit.greeting_id == visit.snapshot.messages[0].message_id
    assert [m.text for m in visit.view().messages] == [
        "Hombre, Luis, ¿qué tal, majo? ¿Has venido solo o acompañado?"
    ]
    assert visit.cursor == visit.snapshot.cursor


def test_message_shows_sending_waiting_provisional_and_confirmed_states() -> None:
    visit = _visit()
    views = []
    visit.send_message("Hola", on_update=lambda: views.append(visit.view()))
    assert views[0].outgoing == "Hola" and views[0].waiting
    assert any(v.outgoing is None and v.waiting and not v.provisional for v in views)
    assert any(v.provisional and not v.waiting for v in views)
    final = visit.view()
    assert final.outgoing is None and not final.waiting and final.provisional == ()
    assert [m.role for m in final.messages] == ["assistant", "user", "assistant"]
    assert visit.pending is None
    assert visit.cursor >= visit.snapshot.cursor


def test_failures_and_invalid_commands_become_notices() -> None:
    visit = _visit()
    visit.reject_unknown_command()
    assert visit.cards[-1].title == UNKNOWN_COMMAND
    assert visit.pending is None


def test_actions_follow_allowed_actions_after_the_turn_limit() -> None:
    visit = _visit(restaurant=FakeRestaurant(max_turns=1))
    visit.send_message("Prefiero el vino tinto")
    visit.send_message("¿Y de postre?")
    assert visit.cards[-1].title == f"{MESSAGES_CLOSED} {TRY_NEW_VISIT}"
    assert [m.role for m in visit.snapshot.messages].count("user") == 1


def test_new_visit_starts_a_clean_conversation() -> None:
    visit = _visit()
    first = visit.snapshot.conversation_id
    visit.arrive()
    assert visit.snapshot.conversation_id != first
    assert visit.cards == []
    assert len(visit.snapshot.messages) == 1


def test_interrupted_command_is_resolved_without_resending() -> None:
    restaurant = FakeRestaurant()
    visit = _visit(restaurant=restaurant)

    class Rerun(BaseException):
        pass

    def interrupt() -> None:
        if visit.view().outgoing is None:
            raise Rerun

    try:
        visit.send_message("Hola", on_update=interrupt)
    except Rerun:
        pass
    assert visit.pending is not None
    pending_id = visit.pending.event_id
    visit.resolve_pending()
    assert visit.pending is None
    user_messages = [m for m in visit.snapshot.messages if m.role == "user"]
    assert [m.command_event_id for m in user_messages] == [pending_id]


def test_expired_cursor_recovers_with_a_new_snapshot() -> None:
    visit = _visit(restaurant=FakeRestaurant(retained_events=2))
    visit.cursor = 0
    visit.send_message("Hola")
    assert visit.pending is None
    assert visit.cursor == visit.snapshot.cursor


def test_transport_errors_are_explicit_and_keep_retryable_commands() -> None:
    class Unavailable:
        async def submit(self, command):
            raise BffClientError(
                PublicError(
                    code=ErrorCode.UNAVAILABLE,
                    message="El BFF no responde.",
                    correlation_id="corr_down",
                    recovery="retry_same_command",
                )
            )

    visit = _visit()
    visit._client = Unavailable()
    visit.send_message("Hola")
    assert visit.pending is not None
    assert visit.cards[-1].title == "El BFF no responde."


def test_pending_arrival_is_resolved_later_without_a_second_visit() -> None:
    restaurant = FakeRestaurant()

    class SlowArrival:
        def __init__(self) -> None:
            self.inner = restaurant.client(ActorContext(actor_id="Ana", authenticated=True))

        async def submit(self, command):
            result = await self.inner.submit(command)
            if isinstance(command, ArriveCommand):
                return PendingCommandResult(
                    schema_version=1, event_id=result.event_id,
                    correlation_id=result.correlation_id, status="pending",
                )
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    visit = VisitSession(SlowArrival())
    visit.arrive()
    assert visit.snapshot is None and visit.pending is not None
    visit.resolve_pending()
    assert visit.pending is None
    assert visit.greeting_id is not None
    assert len(restaurant._conversations) == 1


def test_completed_command_stops_reading_an_open_stream() -> None:
    restaurant = FakeRestaurant()
    inner = restaurant.client(ActorContext(actor_id="Ana", authenticated=True))

    class OpenStream:
        async def submit(self, command):
            return await inner.submit(command)

        async def get_result(self, event_id):
            return await inner.get_result(event_id)

        async def get_snapshot(self, conversation_id):
            return await inner.get_snapshot(conversation_id)

        async def events(self, conversation_id, *, after_cursor):
            async for event in inner.events(conversation_id, after_cursor=after_cursor):
                yield event
            raise AssertionError("an open SSE stream would block here")

    visit = VisitSession(OpenStream())
    visit.arrive()
    visit.send_message("Hola")
    visit.send_message("Otra cosa")
    assert visit.pending is None


def test_retryable_transport_error_is_reported_once() -> None:
    down = BffClientError(
        PublicError(
            code=ErrorCode.UNAVAILABLE, message="El BFF no responde.",
            correlation_id="corr_down", recovery="retry_same_command",
        )
    )

    class Down:
        async def submit(self, command):
            raise down

        async def get_result(self, event_id):
            raise down

    visit = _visit()
    visit._client = Down()
    visit.send_message("Hola")
    for _ in range(3):
        visit.resolve_pending()
    assert [card.title for card in visit.cards].count("El BFF no responde.") == 1
    assert visit.pending is not None


def test_resumed_visit_follows_a_turn_still_running() -> None:
    from datetime import UTC, datetime

    from restaurant_contracts.application import Action, ChatMessage, SnapshotUpdated

    restaurant = FakeRestaurant()
    inner = restaurant.client(ActorContext(actor_id="Ana", authenticated=True))
    first = VisitSession(inner)
    first.arrive()

    class StillAnswering:
        """The BFF is still running a message sent before the reload."""

        async def submit(self, command):
            return await inner.submit(command)

        async def get_snapshot(self, conversation_id):
            snapshot = await inner.get_snapshot(conversation_id)
            return snapshot.model_copy(
                update={"process_status": "processing", "allowed_actions": [Action.ARRIVE]}
            )

        async def events(self, conversation_id, *, after_cursor):
            snapshot = await inner.get_snapshot(conversation_id)
            reply = ChatMessage(
                message_id="msg_reply", role="assistant", text="Aquí tienes.",
                occurred_at=datetime.now(UTC), command_event_id="cmd_before_reload",
            )
            yield SnapshotUpdated(
                schema_version=1, event_id="evt_idle", conversation_id=conversation_id,
                command_event_id="cmd_before_reload", correlation_id="corr_x",
                occurred_at=datetime.now(UTC), cursor=after_cursor + 1,
                event_type="snapshot.updated",
                snapshot=snapshot.model_copy(
                    update={"cursor": after_cursor + 1, "messages": [*snapshot.messages, reply]}
                ),
            )
            raise AssertionError("the view must stop once the waiter is idle")

    resumed = VisitSession(StillAnswering())
    resumed.arrive(inner.active_visit_id)

    assert resumed.snapshot.process_status == "idle"
    assert resumed.view().messages[-1].text == "Aquí tienes."
    assert resumed.allows(Action.SEND_MESSAGE)
    assert len(restaurant._conversations) == 1
