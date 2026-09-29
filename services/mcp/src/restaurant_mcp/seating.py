"""Deterministic seating domain and SQLite persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeatingError(RuntimeError):
    """Base error exposed as a deterministic MCP failure.

    The message starts with a stable code (``no_seating: ...``) so application
    clients can map the tool error without parsing free text.
    """

    code = "seating_error"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class NoSeatingAvailable(SeatingError):
    code = "no_seating"


class SeatingConflict(SeatingError):
    code = "conflict"


class SeatingExpired(SeatingConflict):
    code = "expired"


class SeatingNotFound(SeatingConflict):
    code = "not_found"


class IdempotencyConflict(SeatingError):
    code = "idempotency_conflict"


class SeatingResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(min_length=1, max_length=100)
    kind: Literal["table", "bar"]
    label: str = Field(min_length=1, max_length=100)
    capacity: int = Field(ge=1, le=100)
    display_order: int = Field(ge=0)
    enabled: bool = True
    seat_prefix: str | None = None

    @model_validator(mode="after")
    def validate_bar(self) -> "SeatingResource":
        if self.kind == "bar" and not self.seat_prefix:
            raise ValueError("bar resources require seat_prefix")
        if self.kind == "table" and self.seat_prefix is not None:
            raise ValueError("table resources cannot define seat_prefix")
        return self


class SeatingLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[SeatingResource]

    @model_validator(mode="after")
    def resource_ids_are_unique(self) -> "SeatingLayout":
        ids = [item.resource_id for item in self.resources]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("layout must contain unique resources")
        seat_ids = [
            f"{resource.seat_prefix}-{position:02d}"
            for resource in self.resources
            if resource.kind == "bar"
            for position in range(1, resource.capacity + 1)
        ]
        if len(seat_ids) != len(set(seat_ids)):
            raise ValueError("generated bar seat IDs must be globally unique")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class SeatingAssignment:
    assignment_id: str
    visit_id: str
    resource_id: str
    resource_kind: str
    seat_ids: list[str]
    party_size: int
    status: str
    expires_at: str | None
    version: int


class SQLiteSeatingRepository:
    """Owns atomic table/bar allocation and layout lifecycle in a separate SQLite DB."""

    def __init__(self, database_path: Path, *, layout_id: str, layout: SeatingLayout, expected_hash: str, hold_minutes: int = 5) -> None:
        if hold_minutes < 1:
            raise ValueError("hold_minutes must be at least 1")
        if layout.fingerprint() != expected_hash:
            raise ValueError("SEATING_LAYOUT_SHA256 does not match SEATING_LAYOUT_JSON")
        self._path = database_path.expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._layout_id, self._layout, self._hash = layout_id.strip(), layout, expected_hash
        if not self._layout_id:
            raise ValueError("SEATING_LAYOUT_ID cannot be empty")
        self._hold = timedelta(minutes=hold_minutes)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS layout_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), layout_id TEXT NOT NULL, layout_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resources (resource_id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, capacity INTEGER NOT NULL, display_order INTEGER NOT NULL, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS bar_seats (seat_id TEXT PRIMARY KEY, bar_id TEXT NOT NULL, position INTEGER NOT NULL, UNIQUE(bar_id, position));
                CREATE TABLE IF NOT EXISTS assignments (assignment_id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, resource_id TEXT NOT NULL, resource_kind TEXT NOT NULL, seat_ids TEXT NOT NULL, party_size INTEGER NOT NULL, status TEXT NOT NULL, expires_at TEXT, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS assignment_seats (assignment_id TEXT NOT NULL, seat_id TEXT NOT NULL, PRIMARY KEY(assignment_id, seat_id), FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id));
                CREATE TABLE IF NOT EXISTS idempotency (operation TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, assignment_id TEXT NOT NULL, PRIMARY KEY(operation, idempotency_key));
                """
            )
            db.execute("BEGIN IMMEDIATE")
            self._backfill_assignment_seats(db)
            current = db.execute("SELECT layout_id, layout_hash FROM layout_metadata WHERE singleton = 1").fetchone()
            if current is None or current["layout_id"] != self._layout_id or current["layout_hash"] != self._hash:
                db.execute("DELETE FROM idempotency")
                db.execute("DELETE FROM assignment_seats")
                db.execute("DELETE FROM assignments")
                db.execute("DELETE FROM bar_seats")
                db.execute("DELETE FROM resources")
                db.execute("DELETE FROM layout_metadata")
                for resource in self._layout.resources:
                    db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, ?, ?)", (resource.resource_id, resource.kind, resource.label, resource.capacity, resource.display_order, resource.enabled))
                    if resource.kind == "bar":
                        for position in range(1, resource.capacity + 1):
                            db.execute("INSERT INTO bar_seats VALUES (?, ?, ?)", (f"{resource.seat_prefix}-{position:02d}", resource.resource_id, position))
                db.execute("INSERT INTO layout_metadata VALUES (1, ?, ?)", (self._layout_id, self._hash))
            db.commit()

    @staticmethod
    def _backfill_assignment_seats(db: sqlite3.Connection) -> None:
        """Migrate pre-normalization bar allocations before exposing the service."""

        rows = db.execute(
            "SELECT assignment_id, seat_ids FROM assignments WHERE seat_ids != '[]'"
        ).fetchall()
        for row in rows:
            seat_ids = json.loads(row["seat_ids"])
            db.executemany(
                "INSERT OR IGNORE INTO assignment_seats VALUES (?, ?)",
                [(row["assignment_id"], seat_id) for seat_id in seat_ids],
            )

    def _expire(self, db: sqlite3.Connection, now: datetime) -> None:
        db.execute("UPDATE assignments SET status = 'expired', version = version + 1 WHERE status = 'held' AND expires_at <= ?", (now.isoformat(),))

    def hold(self, *, visit_id: str, party_size: int, preference: Literal["table", "bar", "any"], idempotency_key: str, now: datetime | None = None) -> SeatingAssignment:
        if not visit_id.strip() or not idempotency_key.strip() or party_size < 1:
            raise ValueError("visit_id, idempotency_key and positive party_size are required")
        now = now or datetime.now(UTC)
        payload_hash = hashlib.sha256(f"{visit_id}|{party_size}|{preference}".encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            cached = db.execute("SELECT payload_hash, assignment_id FROM idempotency WHERE operation='hold' AND idempotency_key=?", (idempotency_key,)).fetchone()
            if cached:
                if cached["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("idempotency key was previously used with another request")
                return self._assignment(db, cached["assignment_id"])
            active = db.execute("SELECT assignment_id, status FROM assignments WHERE visit_id=? AND status IN ('held','occupied')", (visit_id,)).fetchall()
            if any(row["status"] == "occupied" for row in active):
                raise SeatingConflict("visit already has active seating")
            # A new request of the same visit replaces its pending hold in this
            # transaction; if nothing fits, the rollback keeps the old hold.
            db.executemany(
                "UPDATE assignments SET status='replaced', expires_at=NULL, version=version+1 WHERE assignment_id=?",
                [(row["assignment_id"],) for row in active],
            )
            choice = self._find_choice(db, party_size, preference)
            if choice is None:
                raise NoSeatingAvailable("no compatible seating is available")
            resource_id, kind, seats = choice
            assignment_id = f"seat_{uuid4().hex}"
            expires = (now + self._hold).isoformat()
            db.execute("INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?, 'held', ?, 1)", (assignment_id, visit_id, resource_id, kind, json.dumps(seats), party_size, expires))
            db.executemany(
                "INSERT INTO assignment_seats VALUES (?, ?)",
                [(assignment_id, seat_id) for seat_id in seats],
            )
            db.execute("INSERT INTO idempotency VALUES ('hold', ?, ?, ?)", (idempotency_key, payload_hash, assignment_id))
            db.commit()
            return self._assignment(db, assignment_id)

    def _find_choice(self, db: sqlite3.Connection, size: int, preference: str) -> tuple[str, str, list[str]] | None:
        if preference in ("table", "any"):
            # One group per table: a held or occupied table is unavailable to
            # every other visit, even with free chairs.
            row = db.execute(
                """SELECT r.resource_id FROM resources r WHERE r.kind='table' AND r.enabled=1 AND r.capacity >= ?
                AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.resource_id=r.resource_id AND a.status IN ('held','occupied'))
                ORDER BY r.capacity, r.display_order, r.resource_id LIMIT 1""",
                (size,),
            ).fetchone()
            if row:
                return row["resource_id"], "table", []
        if preference in ("bar", "any"):
            candidates = []
            for bar_id, display_order, run in self._free_bar_runs(db):
                if len(run) >= size:
                    # Smallest leftover gap first, then the lowest position.
                    candidates.append((len(run) - size, run[0][1], display_order, bar_id, [seat_id for seat_id, _ in run[:size]]))
            if candidates:
                _, _, _, bar, selected = min(candidates)
                return bar, "bar", selected
        return None

    @staticmethod
    def _taken_seats(db: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        rows = db.execute(
            """SELECT allocated.seat_id, a.status, a.visit_id FROM assignment_seats allocated
            JOIN assignments a ON a.assignment_id=allocated.assignment_id WHERE a.status IN ('held','occupied')"""
        ).fetchall()
        return {row["seat_id"]: row for row in rows}

    def _free_bar_runs(self, db: sqlite3.Connection) -> list[tuple[str, int, list[tuple[str, int]]]]:
        """Maximal runs of contiguous free stools of every enabled bar."""

        taken = self._taken_seats(db)
        rows = db.execute(
            """SELECT s.seat_id, s.bar_id, s.position, r.display_order FROM bar_seats s
            JOIN resources r ON r.resource_id=s.bar_id WHERE r.enabled=1 ORDER BY r.display_order, s.bar_id, s.position"""
        ).fetchall()
        runs: list[tuple[str, int, list[tuple[str, int]]]] = []
        current: list[tuple[str, int]] = []
        previous: sqlite3.Row | None = None
        for row in rows:
            contiguous = previous is not None and previous["bar_id"] == row["bar_id"] and previous["position"] + 1 == row["position"]
            if current and (row["seat_id"] in taken or not contiguous):
                runs.append((previous["bar_id"], previous["display_order"], current))
                current = []
            if row["seat_id"] not in taken:
                current.append((row["seat_id"], row["position"]))
            previous = row
        if current and previous is not None:
            runs.append((previous["bar_id"], previous["display_order"], current))
        return runs

    def _assignment(self, db: sqlite3.Connection, assignment_id: str) -> SeatingAssignment:
        row = db.execute("SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
        if row is None:
            raise SeatingNotFound("assignment does not exist")
        return SeatingAssignment(row["assignment_id"], row["visit_id"], row["resource_id"], row["resource_kind"], json.loads(row["seat_ids"]), row["party_size"], row["status"], row["expires_at"], row["version"])

    def confirm(self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str, now: datetime | None = None) -> SeatingAssignment:
        now = now or datetime.now(UTC)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); self._expire(db, now)
            payload_hash = hashlib.sha256(f"{assignment_id}|{visit_id}|{expected_version}".encode()).hexdigest()
            cached = db.execute("SELECT payload_hash, assignment_id FROM idempotency WHERE operation='confirm' AND idempotency_key=?", (idempotency_key,)).fetchone()
            if cached:
                if cached["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("idempotency key was previously used with another request")
                return self._assignment(db, cached["assignment_id"])
            item = self._assignment(db, assignment_id)
            if item.visit_id != visit_id:
                raise SeatingNotFound("assignment does not exist")
            if item.status == "expired":
                raise SeatingExpired("assignment hold has expired")
            if item.status != "held" or item.version != expected_version:
                raise SeatingConflict("assignment cannot be confirmed")
            db.execute("UPDATE assignments SET status='occupied', expires_at=NULL, version=version+1 WHERE assignment_id=?", (assignment_id,))
            db.execute("INSERT INTO idempotency VALUES ('confirm', ?, ?, ?)", (idempotency_key, payload_hash, assignment_id))
            db.commit(); return self._assignment(db, assignment_id)

    def release(self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> SeatingAssignment:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            payload_hash = hashlib.sha256(f"{assignment_id}|{visit_id}|{expected_version}".encode()).hexdigest()
            cached = db.execute("SELECT payload_hash, assignment_id FROM idempotency WHERE operation='release' AND idempotency_key=?", (idempotency_key,)).fetchone()
            if cached:
                if cached["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("idempotency key was previously used with another request")
                return self._assignment(db, cached["assignment_id"])
            item = self._assignment(db, assignment_id)
            if item.visit_id != visit_id or item.status != "occupied" or item.version != expected_version:
                raise SeatingConflict("assignment cannot be released")
            db.execute("UPDATE assignments SET status='released', version=version+1 WHERE assignment_id=?", (assignment_id,))
            db.execute("INSERT INTO idempotency VALUES ('release', ?, ?, ?)", (idempotency_key, payload_hash, assignment_id))
            db.commit(); return self._assignment(db, assignment_id)

    def cancel(self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str, now: datetime | None = None) -> SeatingAssignment:
        """Cancel a pending hold of its own visit so the place is free at once."""

        now = now or datetime.now(UTC)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); self._expire(db, now)
            payload_hash = hashlib.sha256(f"{assignment_id}|{visit_id}|{expected_version}".encode()).hexdigest()
            cached = db.execute("SELECT payload_hash, assignment_id FROM idempotency WHERE operation='cancel' AND idempotency_key=?", (idempotency_key,)).fetchone()
            if cached:
                if cached["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("idempotency key was previously used with another request")
                return self._assignment(db, cached["assignment_id"])
            item = self._assignment(db, assignment_id)
            if item.visit_id != visit_id:
                raise SeatingNotFound("assignment does not exist")
            if item.status == "expired":
                raise SeatingExpired("assignment hold has expired")
            if item.status != "held" or item.version != expected_version:
                raise SeatingConflict("assignment cannot be cancelled")
            db.execute("UPDATE assignments SET status='cancelled', expires_at=NULL, version=version+1 WHERE assignment_id=?", (assignment_id,))
            db.execute("INSERT INTO idempotency VALUES ('cancel', ?, ?, ?)", (idempotency_key, payload_hash, assignment_id))
            db.commit(); return self._assignment(db, assignment_id)

    def availability(self) -> list[dict[str, object]]:
        """Free capacity per resource; ``largest_group`` is the biggest group that fits now."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, datetime.now(UTC))
            busy_tables = {row["resource_id"] for row in db.execute("SELECT DISTINCT resource_id FROM assignments WHERE status IN ('held','occupied')")}
            runs: dict[str, list[int]] = {}
            for bar_id, _, run in self._free_bar_runs(db):
                runs.setdefault(bar_id, []).append(len(run))
            result = []
            for r in db.execute("SELECT * FROM resources ORDER BY display_order, resource_id").fetchall():
                if r["kind"] == "table":
                    free = r["capacity"] if r["enabled"] and r["resource_id"] not in busy_tables else 0
                    largest = free
                else:
                    free = sum(runs.get(r["resource_id"], []))
                    largest = max(runs.get(r["resource_id"], [0]))
                result.append({"resource_id": r["resource_id"], "kind": r["kind"], "label": r["label"], "capacity": r["capacity"], "available_seats": free, "largest_group": largest})
            db.commit()
            return result

    def seating_map(self, visit_id: str = "", now: datetime | None = None) -> dict[str, object]:
        """Anonymised room state for application code.

        Other visits never appear by id: only states, group sizes and ``mine``
        flags for ``visit_id``. ``visit`` is that visit's latest assignment in
        any status, so a client can reconcile an expired or cancelled hold.
        """

        now = now or datetime.now(UTC)
        visit_id = visit_id.strip()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            active = db.execute("SELECT resource_id, visit_id, party_size, status FROM assignments WHERE status IN ('held','occupied')").fetchall()
            taken = self._taken_seats(db)
            resources = []
            for r in db.execute("SELECT * FROM resources WHERE enabled=1 ORDER BY display_order, resource_id").fetchall():
                item: dict[str, object] = {"resource_id": r["resource_id"], "kind": r["kind"], "label": r["label"], "capacity": r["capacity"], "display_order": r["display_order"]}
                if r["kind"] == "table":
                    groups = [a for a in active if a["resource_id"] == r["resource_id"]]
                    state = "occupied" if any(a["status"] == "occupied" for a in groups) else "held" if groups else "free"
                    item.update(state=state, party_size=sum(a["party_size"] for a in groups) or None, mine=bool(visit_id) and any(a["visit_id"] == visit_id for a in groups), seats=[])
                else:
                    seats = []
                    for seat in db.execute("SELECT seat_id, position FROM bar_seats WHERE bar_id=? ORDER BY position", (r["resource_id"],)).fetchall():
                        owner = taken.get(seat["seat_id"])
                        seats.append({"seat_id": seat["seat_id"], "position": seat["position"], "state": owner["status"] if owner else "free", "mine": bool(visit_id) and owner is not None and owner["visit_id"] == visit_id})
                    free = sum(1 for seat in seats if seat["state"] == "free")
                    state = "free" if free else "occupied" if any(seat["state"] == "occupied" for seat in seats) else "held"
                    item.update(state=state, party_size=None, mine=any(seat["mine"] for seat in seats), seats=seats)
                resources.append(item)
            latest = None
            if visit_id:
                row = db.execute("SELECT assignment_id FROM assignments WHERE visit_id=? ORDER BY rowid DESC LIMIT 1", (visit_id,)).fetchone()
                if row:
                    latest = asdict(self._assignment(db, row["assignment_id"]))
            db.commit()
            return {"layout_id": self._layout_id, "resources": resources, "visit": latest}
