from pathlib import Path

import pytest
from pydantic import ValidationError

from restaurant_agent.config import Settings


def settings(**overrides: object) -> Settings:
    values = {
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "azure_ai_model_deployment_name": "model",
        "memory_database_path": Path("/tmp/memory.db"),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_fake_identity_requires_development_environment() -> None:
    with pytest.raises(ValidationError):
        settings(
            app_environment="production",
            enable_dev_fake_identity=True,
            dev_fake_actor_id="customer-1",
        )


def test_fake_consent_requires_fake_identity() -> None:
    with pytest.raises(ValidationError):
        settings(dev_fake_memory_consent=True)


def test_fake_identity_requires_actor_id() -> None:
    with pytest.raises(ValidationError):
        settings(enable_dev_fake_identity=True)
