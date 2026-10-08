"""Runtime configuration for the waiter agent."""

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator
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
    seating_mcp_url: HttpUrl | None = None
    seating_mcp_timeout_seconds: int = Field(default=5, ge=1, le=60)
    # Foundry IQ knowledge base (Azure AI Search). Both or neither: without
    # them the waiter has no carta tool.
    azure_search_endpoint: HttpUrl | None = None
    knowledge_base_name: str | None = Field(
        default=None, pattern=r"^[a-z0-9]([a-z0-9-]{0,126}[a-z0-9])?$"
    )
    knowledge_base_timeout_seconds: int = Field(default=20, ge=1, le=60)
    # The bar reads the drinks of the carta from that knowledge base; its
    # whole round (one or two retrievals) is bounded by this.
    bar_timeout_seconds: int = Field(default=15, ge=1, le=60)
    # The waiter reaches the independently deployed kitchen through A2A.
    kitchen_a2a_url: HttpUrl | None = None
    kitchen_a2a_token_scope: str | None = None
    kitchen_timeout_seconds: int = Field(default=30, ge=1, le=120)
    # And the independently deployed cashier, through A2A as well.
    cashier_a2a_url: HttpUrl | None = None
    cashier_a2a_token_scope: str | None = None
    cashier_timeout_seconds: int = Field(default=30, ge=1, le=120)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "azure_search_endpoint",
        "knowledge_base_name",
        "kitchen_a2a_url",
        "kitchen_a2a_token_scope",
        "cashier_a2a_url",
        "cashier_a2a_token_scope",
        mode="before",
    )
    @classmethod
    def empty_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

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
        if (self.azure_search_endpoint is None) != (self.knowledge_base_name is None):
            raise ValueError(
                "AZURE_SEARCH_ENDPOINT and KNOWLEDGE_BASE_NAME must be set together"
            )
        return self
