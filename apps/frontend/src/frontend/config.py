"""Explicit selection of the BFF client adapter used by the view."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from restaurant_contracts.application import ActorContext
from restaurant_contracts.client import BffClient

from frontend.fake_client import FakeRestaurant

CLIENT_VARIABLE = "FRONTEND_BFF_CLIENT"
PAUSE_VARIABLE = "FRONTEND_FAKE_PAUSE_SECONDS"
DEFAULT_PAUSE_SECONDS = 0.9

Connector = Callable[[str], BffClient]


class ClientKind(StrEnum):
    FAKE = "fake"
    HTTP = "http"


class ClientConfigurationError(RuntimeError):
    """The configured adapter cannot be used; the message is shown in the view."""


@dataclass(frozen=True)
class FrontendSettings:
    client_kind: ClientKind = ClientKind.FAKE
    fake_pause_seconds: float = DEFAULT_PAUSE_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> FrontendSettings:
        raw_kind = environ.get(CLIENT_VARIABLE, ClientKind.FAKE).strip().lower()
        try:
            kind = ClientKind(raw_kind)
        except ValueError:
            raise ClientConfigurationError(
                f"{CLIENT_VARIABLE}={raw_kind!r} no es válido: usa 'fake' (por defecto) "
                "o 'http' cuando exista el BFF del carril 3C."
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
        return cls(client_kind=kind, fake_pause_seconds=pause)


def client_connector(settings: FrontendSettings) -> Connector:
    """Return a function that binds a client to the identity entered at the door.

    The name is a synthetic development identity: the adapter binds it and
    commands never carry it. The HTTP/SSE adapter belongs to lane 3C.
    """

    if settings.client_kind is ClientKind.HTTP:
        raise ClientConfigurationError(
            "El cliente HTTP del BFF todavía no existe: llega con el carril 3C. "
            f"Usa {CLIENT_VARIABLE}=fake para el camarero simulado."
        )
    restaurant = FakeRestaurant(pause_seconds=settings.fake_pause_seconds)

    def connect(name: str) -> BffClient:
        return restaurant.client(ActorContext(actor_id=name, authenticated=True))

    return connect
