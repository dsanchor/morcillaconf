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


def test_http_client_fails_until_lane_3c() -> None:
    settings = FrontendSettings.from_env({"FRONTEND_BFF_CLIENT": "http"})
    with pytest.raises(ClientConfigurationError, match="3C"):
        client_connector(settings)


@pytest.mark.parametrize(
    "environ",
    [
        {"FRONTEND_BFF_CLIENT": "agent"},
        {"FRONTEND_FAKE_PAUSE_SECONDS": "rápido"},
        {"FRONTEND_FAKE_PAUSE_SECONDS": "-1"},
    ],
)
def test_invalid_configuration_is_rejected_with_a_clear_message(environ) -> None:
    with pytest.raises(ClientConfigurationError):
        FrontendSettings.from_env(environ)
