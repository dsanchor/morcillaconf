"""The in-memory seating gateway served as a Streamable HTTP MCP server.

Lets BFF tests drive the real waiter tool path (MCPStreamableHTTPTool and the
seating middleware) against the same state the BFF decides on.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from seating_fake import FakeSeatingGateway


def _as_dict(assignment: Any) -> dict[str, Any]:
    return {
        "assignment_id": assignment.assignment_id,
        "visit_id": assignment.visit_id,
        "resource_id": assignment.resource_id,
        "resource_kind": assignment.resource_kind,
        "seat_ids": list(assignment.seat_ids),
        "party_size": assignment.party_size,
        "status": assignment.status,
        "expires_at": assignment.expires_at.isoformat() if assignment.expires_at else None,
        "version": assignment.version,
        "resource_label": assignment.resource_label,
    }


class SeatingToolServer:
    def __init__(self, gateway: FakeSeatingGateway) -> None:
        self.gateway = gateway
        self.holds: list[tuple[dict[str, Any], dict[str, Any] | str]] = []
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        server = FastMCP("Fake seating", host="127.0.0.1", port=self.port, streamable_http_path="/mcp")

        @server.tool()
        async def get_seating_availability() -> list[dict[str, object]]:
            room = await gateway.room()
            return [
                {"resource_id": r.resource_id, "kind": r.kind, "label": r.label, "capacity": r.capacity}
                for r in room.resources
            ]

        @server.tool()
        async def hold_seating(
            party_size: int, preference: str, visit_id: str = "", idempotency_key: str = ""
        ) -> dict[str, object]:
            request = {"party_size": party_size, "preference": preference, "visit_id": visit_id, "idempotency_key": idempotency_key}
            try:
                result = _as_dict(await gateway.hold(**request))
            except Exception as exc:
                self.holds.append((request, f"{type(exc).__name__}"))
                raise ValueError(f"{exc}: {type(exc).__name__}") from exc
            self.holds.append((request, result))
            return result

        self._app = server.streamable_http_app()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> None:
        config = uvicorn.Config(self._app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake seating MCP did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)
