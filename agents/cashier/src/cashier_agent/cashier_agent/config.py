"""Runtime configuration for the external cashier agent."""

from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    foundry_project_endpoint: HttpUrl | None = None
    azure_ai_model_deployment_name: str | None = None
    app_environment: Literal["development", "test", "production"] = "development"
    azure_search_endpoint: HttpUrl | None = None
    knowledge_base_name: str | None = Field(
        default=None, pattern=r"^[a-z0-9]([a-z0-9-]{0,126}[a-z0-9])?$"
    )
    knowledge_base_timeout_seconds: int = Field(default=20, ge=1, le=60)
    cashier_timeout_seconds: int = Field(default=30, ge=1, le=120)
    # The hook for SPECS' staff review: when on, the bill first waits for a
    # person at the till before offering card or cash. No interface yet.
    cashier_require_review: bool = False
    cashier_a2a_public_url: HttpUrl = HttpUrl("http://localhost:8090/")
    cashier_host: str = "0.0.0.0"
    cashier_port: int = Field(default=8090, ge=1, le=65535)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "foundry_project_endpoint",
        "azure_search_endpoint",
        "knowledge_base_name",
        "azure_ai_model_deployment_name",
        mode="before",
    )
    @classmethod
    def empty_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        missing = []
        if self.foundry_project_endpoint is None:
            missing.append("FOUNDRY_PROJECT_ENDPOINT")
        if not self.azure_ai_model_deployment_name:
            missing.append("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        if missing:
            raise ValueError(
                f"Missing required Foundry configuration: {', '.join(missing)}"
            )
        if (self.azure_search_endpoint is None) != (self.knowledge_base_name is None):
            raise ValueError(
                "AZURE_SEARCH_ENDPOINT and KNOWLEDGE_BASE_NAME must be set together"
            )
        return self
