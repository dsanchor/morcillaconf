"""In-memory seating MCP served over real Streamable HTTP for BFF tests.

It mirrors the seating service's tool names, outputs and error codes; the
rules themselves are tested in services/mcp.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP


@dataclass
class StubSeating:
    tables: dict[str, int] = field(default_factory=lambda: {"table-01": 2, "table-03": 4})
    assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    delay_seconds: float = 0.0
    fail_confirm: bool = False

    def hold(self, visit_id: str, party_size: int, preference: str, idempotency_key: str) -> dict[str, Any]:
        if idempotency_key in self.keys:
            return self.assignments[self.keys[idempotency_key]]
        busy = {a["resource_id"] for a in self.assignments.values() if a["status"] in ("held", "occupied")}
        for resource_id, capacity in sorted(self.tables.items(), key=lambda item: item[1]):
            if capacity >= party_size and resource_id not in busy and preference != "bar":
                number = len(self.assignments) + 1
                assignment = {
                    "assignment_id": f"seat_{number}", "visit_id": visit_id, "resource_id": resource_id,
                    "resource_kind": "table", "seat_ids": [], "party_size": party_size, "status": "held",
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), "version": 1,
                    "resource_label": f"Mesa {resource_id[-1]}",
                }
                self.assignments[assignment["assignment_id"]] = assignment
                self.keys[idempotency_key] = assignment["assignment_id"]
                return assignment
        raise ValueError("no_seating: no compatible seating is available")

    def change(self, assignment_id: str, visit_id: str, version: int, status: str) -> dict[str, Any]:
        if status == "occupied" and self.fail_confirm:
            raise RuntimeError("database is locked")
        item = self.assignments.get(assignment_id)
        if item is None or item["visit_id"] != visit_id:
            raise ValueError("not_found: assignment does not exist")
        if item["status"] == "expired":
            raise ValueError("expired: assignment hold has expired")
        if item["status"] != "held" or item["version"] != version:
            raise ValueError("conflict: assignment cannot change")
        item.update(status=status, version=version + 1, expires_at=None)
        return item


def build_server(stub: StubSeating, port: int) -> FastMCP:
    server = FastMCP("Stub seating", host="127.0.0.1", port=port, streamable_http_path="/mcp")

    @server.tool()
    def get_seating_availability() -> list[dict[str, object]]:
        stub.calls.append(("get_seating_availability", {}))
        return [{"resource_id": key, "kind": "table", "label": key, "capacity": value, "available_seats": value, "largest_group": value} for key, value in stub.tables.items()]

    @server.tool()
    def hold_seating(party_size: int, preference: str, visit_id: str = "", idempotency_key: str = "") -> dict[str, object]:
        stub.calls.append(("hold_seating", {"party_size": party_size, "preference": preference, "visit_id": visit_id, "idempotency_key": idempotency_key}))
        if stub.delay_seconds:
            time.sleep(stub.delay_seconds)
        return stub.hold(visit_id, party_size, preference, idempotency_key)

    @server.tool()
    def confirm_seating(assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        stub.calls.append(("confirm_seating", {"assignment_id": assignment_id, "visit_id": visit_id, "expected_version": expected_version, "idempotency_key": idempotency_key}))
        return stub.change(assignment_id, visit_id, expected_version, "occupied")

    @server.tool()
    def confirm_solo_seating(assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        stub.calls.append(("confirm_solo_seating", {"assignment_id": assignment_id, "visit_id": visit_id, "expected_version": expected_version, "idempotency_key": idempotency_key}))
        return stub.change(assignment_id, visit_id, expected_version, "occupied")

    @server.tool()
    def cancel_seating_hold(assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        stub.calls.append(("cancel_seating_hold", {"assignment_id": assignment_id, "visit_id": visit_id}))
        return stub.change(assignment_id, visit_id, expected_version, "cancelled")

    @server.tool()
    def get_seating_map(visit_id: str = "") -> dict[str, object]:
        stub.calls.append(("get_seating_map", {"visit_id": visit_id}))
        mine = [a for a in stub.assignments.values() if a["visit_id"] == visit_id]
        resources = []
        for order, (resource_id, capacity) in enumerate(sorted(stub.tables.items())):
            active = [a for a in stub.assignments.values() if a["resource_id"] == resource_id and a["status"] in ("held", "occupied")]
            resources.append({
                "resource_id": resource_id, "kind": "table", "label": f"Mesa {resource_id[-1]}", "capacity": capacity,
                "display_order": order, "state": active[0]["status"] if active else "free",
                "party_size": active[0]["party_size"] if active else None,
                "mine": any(a["visit_id"] == visit_id for a in active),
                "expires_at": active[0]["expires_at"] if active and active[0]["status"] == "held" else None,
                "seats": [],
            })
        return {"layout_id": "stub", "resources": resources, "visit": mine[-1] if mine else None}

    return server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RunningServer:
    """Runs the stub in a thread; stop() and start() emulate an MCP restart."""

    def __init__(self, stub: Any, port: int | None = None, builder: Any = None) -> None:
        self.stub = stub
        self.port = port or free_port()
        self._builder = builder or build_server
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> None:
        app = self._builder(self.stub, self.port).streamable_http_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("stub MCP did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)
        self._server = None
        self._thread = None
