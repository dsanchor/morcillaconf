import pytest
from pydantic import ValidationError

from bff.config import BffSettings


def test_remote_is_the_default_and_needs_an_agent_url(monkeypatch) -> None:
    monkeypatch.delenv("WAITER_AGENT_URL", raising=False)
    with pytest.raises(ValidationError) as error:
        BffSettings(_env_file=None)
    assert "WAITER_AGENT_URL" in str(error.value)


def test_settings_come_from_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAITER_AGENT_URL", "http://restaurant-agent:8088")
    monkeypatch.setenv("BFF_DATABASE_PATH", str(tmp_path / "bff.db"))
    monkeypatch.setenv("WAITER_MAX_TURNS", "3")
    settings = BffSettings(_env_file=None)
    assert settings.waiter_max_turns == 3
    assert settings.bff_database_path == tmp_path / "bff.db"


def test_remote_waiter_settings_are_transport_only() -> None:
    settings = BffSettings(
        _env_file=None,
        waiter_agent_url="http://restaurant-agent:8088",
        waiter_agent_timeout_seconds=17,
    )
    assert str(settings.waiter_agent_url) == "http://restaurant-agent:8088/"
    assert settings.waiter_agent_timeout_seconds == 17


def test_the_remote_waiter_is_built_without_constructing_an_agent(tmp_path) -> None:
    from bff.adapters import create_waiter
    from bff.waiter import RemoteWaiter

    settings = BffSettings(
        _env_file=None,
        waiter_agent_url="http://restaurant-agent:8088",
    )

    waiter = create_waiter(settings)
    assert isinstance(waiter, RemoteWaiter)
    assert waiter.mode == "remote"
