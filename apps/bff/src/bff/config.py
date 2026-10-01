"""BFF configuration, read from environment variables or an optional .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BffSettings(BaseSettings):
    """Everything the BFF needs; no local environment has to be sourced."""

    bff_database_path: Path = Path("data/bff.db")
    memory_database_path: Path = Path("data/memory.db")
    bff_sqlite_journal_mode: Literal["WAL", "DELETE"] = "WAL"
    memory_max_items: int = Field(default=20, ge=1, le=100)
    waiter_max_turns: int = Field(default=20, ge=1, le=100)
    waiter_agent_url: HttpUrl | None = None
    waiter_agent_timeout_seconds: int = Field(default=60, ge=1, le=300)
    app_environment: Literal["development", "test", "production"] = "development"
    bff_session_ttl_hours: float = Field(default=12, gt=0, le=168)
    bff_event_retention: int = Field(default=500, ge=2, le=100_000)
    bff_sse_heartbeat_seconds: float = Field(default=15, gt=0, le=300)
    # Read only to refuse it: the waiter is the only client of the seating MCP.
    seating_mcp_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def the_bff_never_talks_to_the_seating_mcp(self) -> BffSettings:
        if self.seating_mcp_url:
            raise ValueError(
                "SEATING_MCP_URL belongs to the waiter agent, the only client of "
                "the seating MCP; remove it from the BFF environment"
            )
        return self

    @model_validator(mode="after")
    def waiter_needs_an_endpoint(self) -> BffSettings:
        if self.waiter_agent_url is None:
            raise ValueError(
                "WAITER_AGENT_URL is required; the BFF always uses the remote waiter"
            )
        return self
