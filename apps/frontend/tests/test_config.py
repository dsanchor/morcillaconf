import pytest

from frontend.config import (
    ClientConfigurationError,
    ClientKind,
    FrontendSettings,
    client_connector,
)
from frontend.fake_client import FakeBffClient


def test_fake_client_is_the_explicit_default() -> None:
    settings = FrontendSettings.from_env({})
    assert settings.client_kind is ClientKind.FAKE
    assert settings.fake_pause_seconds > 0


def test_connector_binds_the_door_name_as_authenticated_identity() -> None:
    connect = client_connector(FrontendSettings.from_env({"FRONTEND_FAKE_PAUSE_SECONDS": "0"}))
    client = connect("Ana")
    assert isinstance(client, FakeBffClient)
    assert client.identity.actor_id == "Ana"
    assert client.identity.authenticated is True


def test_http_client_is_configured_from_the_environment() -> None:
    settings = FrontendSettings.from_env(
        {
            "FRONTEND_BFF_CLIENT": "http",
            "FRONTEND_BFF_URL": "https://bff.example.com/",
            "FRONTEND_BFF_TIMEOUT_SECONDS": "12",
        }
    )
    assert settings.client_kind is ClientKind.HTTP
    assert settings.bff_url == "https://bff.example.com/"
    assert settings.timeout_seconds == 12
    assert FrontendSettings.from_env({}).bff_url == "http://127.0.0.1:8000"


def test_fake_client_knows_the_active_visit_and_is_simulated() -> None:
    connect = client_connector(FrontendSettings.from_env({"FRONTEND_FAKE_PAUSE_SECONDS": "0"}))
    client = connect("Ana")
    assert client.simulated is True
    assert client.active_visit_id is None


@pytest.mark.parametrize(
    "environ",
    [
        {"FRONTEND_BFF_CLIENT": "agent"},
        {"FRONTEND_FAKE_PAUSE_SECONDS": "rápido"},
        {"FRONTEND_FAKE_PAUSE_SECONDS": "-1"},
        {"FRONTEND_BFF_URL": "bff:8000"},
        {"FRONTEND_BFF_TIMEOUT_SECONDS": "0"},
        {"FRONTEND_BFF_TIMEOUT_SECONDS": "pronto"},
    ],
)
def test_invalid_configuration_is_rejected_with_a_clear_message(environ) -> None:
    with pytest.raises(ClientConfigurationError):
        FrontendSettings.from_env(environ)
