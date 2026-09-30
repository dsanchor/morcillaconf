"""Configuration for the MCP seating service."""

import json
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from restaurant_mcp.seating import SeatingLayout


class Settings(BaseSettings):
    seating_database_path: Path = Path("./data/seating.db")
    seating_layout_id: str
    seating_layout_json: str
    seating_hold_minutes: int = Field(default=5, ge=1, le=60)
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8080, ge=1, le=65535)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_layout(self) -> "Settings":
        try:
            SeatingLayout.model_validate(json.loads(self.seating_layout_json))
        except json.JSONDecodeError as error:
            raise ValueError("SEATING_LAYOUT_JSON must be valid JSON") from error
        return self

    def layout(self) -> SeatingLayout:
        return SeatingLayout.model_validate(json.loads(self.seating_layout_json))
