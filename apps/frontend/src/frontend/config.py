"""Configuration of the frontend's HTTP connection to the BFF."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from restaurant_contracts.client import BffClient

from frontend.http_client import HttpBffClient

URL_VARIABLE = "FRONTEND_BFF_URL"
TIMEOUT_VARIABLE = "FRONTEND_BFF_TIMEOUT_SECONDS"
FAREWELL_VARIABLE = "FRONTEND_FAREWELL_SECONDS"
DEFAULT_BFF_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
# After paying, the receipt and the goodbye stay on screen this long before the door.
DEFAULT_FAREWELL_SECONDS = 4.0


class DoorClient(BffClient, Protocol):
    """A client bound at the door: it also knows the customer's active visit."""

    @property
    def active_visit_id(self) -> str | None: ...


Connector = Callable[[str], DoorClient]


class ClientConfigurationError(RuntimeError):
    """The configured adapter cannot be used; the message is shown in the view."""


@dataclass(frozen=True)
class FrontendSettings:
    bff_url: str = DEFAULT_BFF_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    farewell_seconds: float = DEFAULT_FAREWELL_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> FrontendSettings:
        url = environ.get(URL_VARIABLE, DEFAULT_BFF_URL).strip()
        if not url.startswith(("http://", "https://")):
            raise ClientConfigurationError(
                f"{URL_VARIABLE} debe empezar por http:// o https://."
            )
        raw_timeout = environ.get(TIMEOUT_VARIABLE, str(DEFAULT_TIMEOUT_SECONDS))
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = -1.0
        if not 1 <= timeout <= 300:
            raise ClientConfigurationError(
                f"{TIMEOUT_VARIABLE} debe ser un número de segundos entre 1 y 300."
            )
        raw_farewell = environ.get(FAREWELL_VARIABLE, str(DEFAULT_FAREWELL_SECONDS))
        try:
            farewell = float(raw_farewell)
        except ValueError:
            farewell = -1.0
        if not 0 <= farewell <= 30:
            raise ClientConfigurationError(
                f"{FAREWELL_VARIABLE} debe ser un número de segundos entre 0 y 30."
            )
        return cls(
            bff_url=url,
            timeout_seconds=timeout,
            farewell_seconds=farewell,
        )


def client_connector(settings: FrontendSettings) -> Connector:
    """Bind the door name to a BFF-backed session."""

    def connect(name: str) -> DoorClient:
        return HttpBffClient.open(
            settings.bff_url, name, timeout=settings.timeout_seconds
        )

    return connect
