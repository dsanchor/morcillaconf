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
    FORGOTTEN,
    KEEPS_REMEMBERING,
    MESSAGES_CLOSED,
    NOT_ALLOWED,
    NOTHING_REMEMBERED,
    REMEMBERED,
    TOO_LONG_MEMORY,
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
    assert [m.text for m in visit.view().messages] == ["Hombre, Luis, ¿qué tal, majo?"]
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


def test_memory_cards_come_from_the_confirmed_memory_view() -> None:
    visit = _visit()
    visit.read_memory()
    assert visit.cards[-1].title == NOTHING_REMEMBERED
    visit.send_message("Prefiero el agua con gas y soy alérgica a los frutos secos")
    visit.read_memory()
    card = visit.cards[-1]
    assert card.title == REMEMBERED
    assert [m.memory_id for m in card.memories] == ["m1", "m2"]
    assert card.after_message_id == visit.snapshot.messages[-1].message_id
    visit.correct_memory("m1", "agua sin gas")
    assert visit.cards[-1].title == "He corregido m1."
    assert visit.cards[-1].memories[0].value == "agua sin gas"
    visit.delete_memory("m2")
    assert [m.memory_id for m in visit.cards[-1].memories] == ["m1"]
    visit.clear_memory()
    assert (visit.cards[-1].title, visit.cards[-1].note) == (FORGOTTEN, KEEPS_REMEMBERING)
    assert visit.snapshot.memory.memories == []


def test_failures_and_invalid_commands_become_notices() -> None:
    visit = _visit()
    visit.delete_memory("m1")
    assert visit.cards[-1].title == NOT_ALLOWED
    visit.send_message("Prefiero el vino tinto")
    visit.delete_memory("m9")
    assert visit.cards[-1].title == "No recuerdo nada con el identificador m9."
    visit.correct_memory("m1", "x" * 201)
    assert visit.cards[-1].title == TOO_LONG_MEMORY
    visit.reject_unknown_command()
    assert visit.cards[-1].title == UNKNOWN_COMMAND
    assert visit.pending is None


def test_actions_follow_allowed_actions_after_the_turn_limit() -> None:
    visit = _visit(restaurant=FakeRestaurant(max_turns=1))
    visit.send_message("Prefiero el vino tinto")
    visit.send_message("¿Y de postre?")
    assert visit.cards[-1].title == f"{MESSAGES_CLOSED} {TRY_NEW_VISIT}"
    assert [m.role for m in visit.snapshot.messages].count("user") == 1
    visit.delete_memory("m1")
    assert visit.cards[-1].title == "He borrado m1."


def test_new_visit_starts_a_clean_conversation_and_keeps_memories() -> None:
    visit = _visit()
    first = visit.snapshot.conversation_id
    visit.send_message("Prefiero la tortilla")
    visit.read_memory()
    visit.arrive()
    assert visit.snapshot.conversation_id != first
    assert visit.cards == []
    assert len(visit.snapshot.messages) == 1
    assert [m.value for m in visit.snapshot.memory.memories] == ["tortilla"]


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
    visit.read_memory()
    assert visit.pending is None
    assert visit.cursor == visit.snapshot.cursor
    assert visit.cards[-1].title == NOTHING_REMEMBERED


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
    visit.read_memory()
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
    visit.read_memory()
    assert visit.pending is None
    assert visit.cards[-1].title == NOTHING_REMEMBERED


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
    visit.read_memory()
    for _ in range(3):
        visit.resolve_pending()
    assert [card.title for card in visit.cards].count("El BFF no responde.") == 1
    assert visit.pending is not None
