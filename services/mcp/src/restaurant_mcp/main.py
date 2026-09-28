"""Streamable HTTP MCP adapter for deterministic seating operations."""

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from restaurant_mcp.config import Settings
from restaurant_mcp.seating import SQLiteSeatingRepository


def create_server(settings: Settings) -> FastMCP:
    repository = SQLiteSeatingRepository(
        settings.seating_database_path,
        layout_id=settings.seating_layout_id,
        layout=settings.layout(),
        expected_hash=settings.seating_layout_sha256,
        hold_minutes=settings.seating_hold_minutes,
    )
    server = FastMCP(
        "MorcillaConf Seating",
        instructions="Deterministic seating authority. Never infer availability outside these tools.",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
    )

    @server.tool()
    def get_seating_availability() -> list[dict[str, object]]:
        """Return confirmed available capacity for tables and bar."""
        return repository.availability()

    @server.tool()
    def hold_seating(
        party_size: int,
        preference: str,
        visit_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, object]:
        """Temporarily hold one table or contiguous bar seats for a visit."""
        if preference not in ("table", "bar", "any"):
            raise ValueError("preference must be table, bar or any")
        return asdict(repository.hold(visit_id=visit_id, party_size=party_size, preference=preference, idempotency_key=idempotency_key))

    @server.tool()
    def confirm_seating(assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        """Confirm a still-valid seating hold and turn it into an occupancy."""
        return asdict(repository.confirm(assignment_id=assignment_id, visit_id=visit_id, expected_version=expected_version, idempotency_key=idempotency_key))

    @server.tool()
    def release_seating(assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        """Release an occupied seating assignment after payment was verified upstream."""
        return asdict(repository.release(assignment_id=assignment_id, visit_id=visit_id, expected_version=expected_version, idempotency_key=idempotency_key))

    return server


def main() -> None:
    settings = Settings()
    create_server(settings).run(transport="streamable-http")


if __name__ == "__main__":
    main()
