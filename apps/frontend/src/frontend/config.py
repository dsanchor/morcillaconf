"""Explicit selection of the BFF client adapter used by the view."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from restaurant_contracts.application import ActorContext
from restaurant_contracts.client import BffClient

from frontend.fake_client import FakeRestaurant
from frontend.http_client import HttpBffClient

CLIENT_VARIABLE = "FRONTEND_BFF_CLIENT"
PAUSE_VARIABLE = "FRONTEND_FAKE_PAUSE_SECONDS"
URL_VARIABLE = "FRONTEND_BFF_URL"
TIMEOUT_VARIABLE = "FRONTEND_BFF_TIMEOUT_SECONDS"
FAREWELL_VARIABLE = "FRONTEND_FAREWELL_SECONDS"
DEFAULT_PAUSE_SECONDS = 0.9
DEFAULT_BFF_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
# After paying, the receipt and the goodbye stay on screen this long before the door.
DEFAULT_FAREWELL_SECONDS = 4.0


class DoorClient(BffClient, Protocol):
    """A client bound at the door: it also knows the customer's active visit."""

    @property
    def active_visit_id(self) -> str | None: ...

    @property
    def simulated(self) -> bool: ...


Connector = Callable[[str], DoorClient]


class ClientKind(StrEnum):
    FAKE = "fake"
    HTTP = "http"


class ClientConfigurationError(RuntimeError):
    """The configured adapter cannot be used; the message is shown in the view."""


@dataclass(frozen=True)
class FrontendSettings:
    client_kind: ClientKind = ClientKind.FAKE
    fake_pause_seconds: float = DEFAULT_PAUSE_SECONDS
    bff_url: str = DEFAULT_BFF_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    farewell_seconds: float = DEFAULT_FAREWELL_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> FrontendSettings:
        raw_kind = environ.get(CLIENT_VARIABLE, ClientKind.FAKE).strip().lower()
        try:
            kind = ClientKind(raw_kind)
        except ValueError:
            raise ClientConfigurationError(
                f"{CLIENT_VARIABLE}={raw_kind!r} no es válido: usa 'fake' (por defecto) "
                "o 'http' para hablar con el BFF."
            ) from None
        raw_pause = environ.get(PAUSE_VARIABLE, str(DEFAULT_PAUSE_SECONDS))
        try:
            pause = float(raw_pause)
        except ValueError:
            pause = -1.0
        if not 0 <= pause <= 10:
            raise ClientConfigurationError(
                f"{PAUSE_VARIABLE} debe ser un número de segundos entre 0 y 10."
            )
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
            client_kind=kind,
            fake_pause_seconds=pause,
            bff_url=url,
            timeout_seconds=timeout,
            farewell_seconds=farewell,
        )


def client_connector(settings: FrontendSettings) -> Connector:
    """Return a function that binds a client to the identity entered at the door.

    With the fake, the name is bound locally. With the BFF, the name opens a
    server-side demo session; commands never carry it. Connecting to the BFF
    may raise ``BffClientError``, which the door shows to the customer.
    """

    if settings.client_kind is ClientKind.HTTP:

        def connect_http(name: str) -> DoorClient:
            return HttpBffClient.open(
                settings.bff_url, name, timeout=settings.timeout_seconds
            )

        return connect_http
    restaurant = FakeRestaurant(pause_seconds=settings.fake_pause_seconds)

    def connect(name: str) -> DoorClient:
        return restaurant.client(ActorContext(actor_id=name, authenticated=True))

    return connect
