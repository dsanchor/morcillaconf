from datetime import UTC, datetime

from restaurant_contracts.application import (
    CommandStatusChanged,
    PendingCommandResult,
)

from bff.storage import ConversationRow, Database

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def conversation(db: Database) -> ConversationRow:
    row = ConversationRow(
        conversation_id="conv_1",
        visit_id="visit_1",
        actor_id="ana",
        presented_name="Ana",
        created_at=NOW,
        updated_at=NOW,
    )
    with db.write() as tx:
        tx.insert_visit(visit_id="visit_1", actor_id="ana", presented_name="Ana", created_at=NOW)
        tx.insert_conversation(row)
    return row


def pending_event(cursor: int) -> CommandStatusChanged:
    return CommandStatusChanged(
        schema_version=1,
        event_id=f"evt_{cursor}",
        conversation_id="conv_1",
        command_event_id="cmd_1",
        correlation_id="corr_1",
        occurred_at=NOW,
        cursor=cursor,
        event_type="command.status_changed",
        result=PendingCommandResult(
            schema_version=1, event_id="cmd_1", correlation_id="corr_1", status="pending"
        ),
    )


def test_events_advance_the_cursor_and_keep_a_bounded_window(tmp_path) -> None:
    db = Database(tmp_path / "bff.db", event_retention=3)
    row = conversation(db)
    with db.write() as tx:
        for _ in range(5):
            tx.append_event(row, pending_event)

    with db.read() as tx:
        assert tx.get_conversation("conv_1").cursor == 5
        assert tx.oldest_cursor("conv_1") == 3
        assert [cursor for cursor, _ in tx.events_after("conv_1", 3)] == [4, 5]


def test_a_failed_transaction_leaves_no_trace(tmp_path) -> None:
    db = Database(tmp_path / "bff.db")
    row = conversation(db)
    try:
        with db.write() as tx:
            tx.append_event(row, pending_event)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.read() as tx:
        assert tx.get_conversation("conv_1").cursor == 0
        assert tx.events_after("conv_1", 0) == []


def test_memory_aliases_are_stable_and_never_reused(tmp_path) -> None:
    db = Database(tmp_path / "bff.db")
    with db.write() as tx:
        first = tx.memory_aliases("ana", ["pref_a", "pref_b"])
        again = tx.memory_aliases("ana", ["pref_b", "pref_a"])
        later = tx.memory_aliases("ana", ["pref_c"])
        other = tx.memory_aliases("luis", ["pref_x"])
        assert first == {"pref_a": "m1", "pref_b": "m2"}
        assert again == first
        assert later["pref_c"] == "m3"
        assert other == {"pref_x": "m1"}
        assert tx.memory_for_alias("ana", "M2") == "pref_b"
        assert tx.memory_for_alias("luis", "m2") is None


def test_the_latest_visit_is_the_active_one(tmp_path) -> None:
    db = Database(tmp_path / "bff.db")
    with db.write() as tx:
        tx.insert_visit(visit_id="visit_1", actor_id="ana", presented_name="Ana", created_at=NOW)
        tx.insert_visit(visit_id="visit_2", actor_id="ana", presented_name="Ana", created_at=NOW)
        tx.insert_visit(visit_id="visit_3", actor_id="luis", presented_name="Luis", created_at=NOW)
    with db.read() as tx:
        assert tx.latest_visit_id("ana") == "visit_2"
        assert tx.latest_visit_id("nadie") is None
