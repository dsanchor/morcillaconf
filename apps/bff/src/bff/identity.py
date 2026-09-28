"""Demo identity derived from the name typed at the restaurant door.

The name is the only customer identity of the demo. It isolates simulated
resources; it does not prove who the person is.
"""

from __future__ import annotations

import unicodedata

MAX_NAME_LENGTH = 40
_COMBINING_TILDE = "\u0303"


class InvalidNameError(ValueError):
    """The door name cannot be used as an identity."""


def presented_name(raw: str) -> str:
    """The name as typed, with surrounding and repeated spaces removed."""

    name = " ".join(raw.split())
    if not name:
        raise InvalidNameError("El nombre no puede estar vacío.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidNameError(
            f"El nombre no puede tener más de {MAX_NAME_LENGTH} caracteres."
        )
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise InvalidNameError("El nombre contiene caracteres no válidos.")
    return name


def actor_id_for(raw: str) -> str:
    """Stable actor id: ignores case, accents and repeated spaces, keeps ñ."""

    kept: list[str] = []
    for char in unicodedata.normalize("NFD", presented_name(raw)):
        if unicodedata.combining(char):
            if char == _COMBINING_TILDE and kept and kept[-1] in "nN":
                kept.append(char)
            continue
        kept.append(char)
    return unicodedata.normalize("NFC", "".join(kept)).casefold()
