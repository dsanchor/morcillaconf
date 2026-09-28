"""Deterministic arrival greeting, produced by the BFF without calling the model.

Same rules as apps/frontend/src/frontend/greeting.py (the simulated waiter):
names ending in "a" are addressed as "maja", the rest as "majo", with the
usual exceptions. Both test suites share the same cases to keep them aligned.
"""

from __future__ import annotations

import unicodedata

_FEMININE_WITHOUT_A = frozenset(
    {
        "abigail", "amparo", "ane", "asuncion", "beatriz", "belen", "carmen",
        "carol", "consuelo", "dolores", "edurne", "encarnacion", "esther",
        "ester", "garazi", "ines", "ingrid", "irati", "irene", "isabel",
        "itziar", "karen", "leire", "leonor", "lourdes", "luz", "maider",
        "maite", "mar", "mari", "maribel", "marisol", "mercedes", "miriam",
        "montserrat", "nicole", "nieves", "noemi", "paz", "pilar", "rachel",
        "raquel", "rocio", "rosario", "rut", "ruth", "sol", "soledad",
        "trinidad", "uxue", "zoe",
    }
)
_MASCULINE_WITH_A = frozenset(
    {"bautista", "borja", "ezra", "joshua", "josema", "luca", "lucca", "nicola"}
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def form_of_address(name: str) -> str:
    """Return "maja" or "majo" for the customer's first name."""

    words = name.split()
    first = _fold(words[0]) if words else ""
    if first in _MASCULINE_WITH_A:
        return "majo"
    if first in _FEMININE_WITHOUT_A or first.endswith("a"):
        return "maja"
    return "majo"


def greeting(name: str) -> str:
    """Greeting with which the waiter receives the customer at the door."""

    clean = " ".join(name.split())
    return f"Hombre, {clean}, ¿qué tal, {form_of_address(clean)}?"
