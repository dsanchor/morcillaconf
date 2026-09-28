"""BFF configuration, read from environment variables or an optional .env file."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from restaurant_agent.config import Settings as AgentSettings


class BffSettings(BaseSettings):
    """Everything the BFF needs; no local environment has to be sourced."""

    bff_waiter: Literal["foundry", "scripted"] = "foundry"
    bff_database_path: Path = Path("data/bff.db")
    memory_database_path: Path = Path("data/memory.db")
    memory_max_items: int = Field(default=20, ge=1, le=100)
    waiter_max_turns: int = Field(default=20, ge=1, le=100)
    foundry_project_endpoint: HttpUrl | None = None
    azure_ai_model_deployment_name: str | None = None
    app_environment: Literal["development", "test", "production"] = "development"
    bff_session_ttl_hours: float = Field(default=12, gt=0, le=168)
    bff_event_retention: int = Field(default=500, ge=2, le=100_000)
    bff_sse_heartbeat_seconds: float = Field(default=15, gt=0, le=300)
    bff_scripted_delay_seconds: float = Field(default=0, ge=0, le=30)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def foundry_needs_a_project(self) -> BffSettings:
        if self.bff_waiter != "foundry":
            return self
        missing = []
        if self.foundry_project_endpoint is None:
            missing.append("FOUNDRY_PROJECT_ENDPOINT")
        if not self.azure_ai_model_deployment_name:
            missing.append("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        if missing:
            raise ValueError(
                "BFF_WAITER=foundry needs "
                + ", ".join(missing)
                + " (or use BFF_WAITER=scripted for the simulated waiter)"
            )
        return self

    def agent_settings(self) -> AgentSettings:
        """Waiter settings built explicitly, with the fake dev identity always off."""

        from restaurant_agent.config import Settings

        return Settings(
            _env_file=None,
            foundry_project_endpoint=self.foundry_project_endpoint,
            azure_ai_model_deployment_name=self.azure_ai_model_deployment_name,
            waiter_max_turns=self.waiter_max_turns,
            memory_database_path=self.memory_database_path,
            memory_max_items=self.memory_max_items,
            app_environment=self.app_environment,
            enable_dev_fake_identity=False,
            dev_fake_actor_id=None,
        )
