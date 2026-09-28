import pytest
from pydantic import ValidationError

from bff.config import BffSettings


def test_foundry_is_the_default_and_needs_a_project(monkeypatch) -> None:
    monkeypatch.delenv("BFF_WAITER", raising=False)
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    with pytest.raises(ValidationError) as error:
        BffSettings(_env_file=None)
    assert "FOUNDRY_PROJECT_ENDPOINT" in str(error.value)
    assert "AZURE_AI_MODEL_DEPLOYMENT_NAME" in str(error.value)


def test_scripted_waiter_needs_no_project() -> None:
    settings = BffSettings(_env_file=None, bff_waiter="scripted")
    assert settings.waiter_max_turns == 20


def test_settings_come_from_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BFF_WAITER", "foundry")
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo"
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.6-luna")
    monkeypatch.setenv("BFF_DATABASE_PATH", str(tmp_path / "bff.db"))
    monkeypatch.setenv("WAITER_MAX_TURNS", "3")
    settings = BffSettings(_env_file=None)
    assert settings.bff_waiter == "foundry"
    assert settings.waiter_max_turns == 3
    assert settings.bff_database_path == tmp_path / "bff.db"


def test_agent_settings_never_enable_the_fake_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENABLE_DEV_FAKE_IDENTITY", "true")
    monkeypatch.setenv("DEV_FAKE_ACTOR_ID", "Cliente local")
    settings = BffSettings(
        _env_file=None,
        bff_waiter="foundry",
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="gpt-5.6-luna",
        memory_database_path=tmp_path / "memory.db",
        waiter_max_turns=7,
    )
    agent_settings = settings.agent_settings()
    assert agent_settings.enable_dev_fake_identity is False
    assert agent_settings.dev_fake_actor_id is None
    assert agent_settings.waiter_max_turns == 7
    assert agent_settings.memory_database_path == tmp_path / "memory.db"
