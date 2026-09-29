"""BFF configuration, read from environment variables or an optional .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class BffSettings(BaseSettings):
    """Everything the BFF needs; no local environment has to be sourced."""

    bff_waiter: Literal["remote", "scripted"] = "remote"
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
    bff_scripted_delay_seconds: float = Field(default=0, ge=0, le=30)
    seating_mcp_url: HttpUrl | None = None
    seating_mcp_timeout_seconds: int = Field(default=5, ge=1, le=60)
    bff_room_cache_seconds: float = Field(default=1, ge=0, le=30)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def remote_waiter_needs_an_endpoint(self) -> BffSettings:
        if self.bff_waiter != "remote":
            return self
        if self.waiter_agent_url is None:
            raise ValueError(
                "BFF_WAITER=remote needs WAITER_AGENT_URL "
                "(or use BFF_WAITER=scripted for local development)"
            )
        return self
