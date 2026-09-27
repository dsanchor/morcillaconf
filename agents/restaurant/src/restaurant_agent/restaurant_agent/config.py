"""Runtime configuration for the waiter agent."""

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    foundry_project_endpoint: HttpUrl | None = None
    azure_ai_model_deployment_name: str | None = None
    waiter_max_turns: int = Field(default=20, ge=1, le=100)
    memory_database_path: Path | None = None
    memory_max_items: int = Field(default=20, ge=1, le=100)
    app_environment: Literal["development", "test", "production"] = "development"
    enable_dev_fake_identity: bool = False
    dev_fake_actor_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_foundry_configuration(self) -> "Settings":
        missing = []
        if self.foundry_project_endpoint is None:
            missing.append("FOUNDRY_PROJECT_ENDPOINT")
        if not self.azure_ai_model_deployment_name:
            missing.append("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        if self.memory_database_path is None:
            missing.append("MEMORY_DATABASE_PATH")
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Missing required Foundry configuration: {names}")
        if self.enable_dev_fake_identity:
            if self.app_environment != "development":
                raise ValueError(
                    "Fake identity can only be enabled in development"
                )
            if not self.dev_fake_actor_id or not self.dev_fake_actor_id.strip():
                raise ValueError(
                    "DEV_FAKE_ACTOR_ID is required when fake identity is enabled"
                )
        return self
