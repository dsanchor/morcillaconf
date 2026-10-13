import pytest

import frontend.config
from frontend.config import ClientConfigurationError, FrontendSettings, client_connector


def test_http_client_is_the_only_connector(monkeypatch) -> None:
    opened: list[tuple[str, str, float]] = []
    expected = object()

    def open_client(url: str, name: str, *, timeout: float):
        opened.append((url, name, timeout))
        return expected

    monkeypatch.setattr(frontend.config.HttpBffClient, "open", open_client)
    settings = FrontendSettings.from_env(
        {
            "FRONTEND_BFF_URL": "https://bff.example.com/",
            "FRONTEND_BFF_TIMEOUT_SECONDS": "12",
        }
    )

    assert client_connector(settings)("Ana") is expected
    assert opened == [("https://bff.example.com/", "Ana", 12)]


def test_local_bff_is_the_default() -> None:
    assert FrontendSettings.from_env({}).bff_url == "http://127.0.0.1:8000"


def test_the_farewell_after_paying_lasts_a_few_seconds_by_default() -> None:
    assert FrontendSettings.from_env({}).farewell_seconds == 4
    assert FrontendSettings.from_env({"FRONTEND_FAREWELL_SECONDS": "0"}).farewell_seconds == 0


@pytest.mark.parametrize(
    "environ",
    [
        {"FRONTEND_BFF_URL": "bff:8000"},
        {"FRONTEND_BFF_TIMEOUT_SECONDS": "0"},
        {"FRONTEND_BFF_TIMEOUT_SECONDS": "pronto"},
        {"FRONTEND_FAREWELL_SECONDS": "-1"},
        {"FRONTEND_FAREWELL_SECONDS": "31"},
        {"FRONTEND_FAREWELL_SECONDS": "luego"},
    ],
)
def test_invalid_configuration_is_rejected_with_a_clear_message(environ) -> None:
    with pytest.raises(ClientConfigurationError):
        FrontendSettings.from_env(environ)
