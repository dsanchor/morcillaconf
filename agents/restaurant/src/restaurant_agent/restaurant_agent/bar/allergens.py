"""The 14 EU allergens as the carta names them, and the customer's words for them.

A copy of the kitchen's ``allergens.py``: the waiter and the kitchen are
separate projects and containers, so the bar applies the same rule from its
own copy instead of a shared library, as each agent does with
``knowledge.py``. Keep both copies in step.

Only what the carta declares counts: an allergen is never deduced from the
ingredients, and «información pendiente de verificar» stays unknown.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

ALLERGENS = (
    "cereales con gluten",
    "crustáceos",
    "huevos",
    "pescado",
    "cacahuetes",
    "soja",
    "leche",
    "frutos de cáscara",
    "apio",
    "mostaza",
    "sésamo",
    "sulfitos",
    "altramuces",
    "moluscos",
)
PENDING = "pendiente de verificar"

# How a customer may name each allergen when declaring an allergy or
# intolerance: word starts, or whole words where a start would be ambiguous.
_CUSTOMER_WORDS = {
    "cereales con gluten": (r"\bgluten", r"\bceliac", r"\bceliaqu", r"\btrigo", r"\bcebada", r"\bcenteno"),
    "crustáceos": (r"\bcrustace", r"\bmarisco", r"\bgamba", r"\blangostino", r"\bcangrejo"),
    "huevos": (r"\bhuevo",),
    "pescado": (r"\bpescado", r"\banchoa"),
    "cacahuetes": (r"\bcacahuete", r"\bmani\b"),
    "soja": (r"\bsoja\b",),
    "leche": (r"\bleche\b", r"\blactosa", r"\blacteo"),
    "frutos de cáscara": (
        r"\bfrutos? secos?\b", r"\bfrutos de cascara", r"\bnuez", r"\bnueces", r"\balmendra",
        r"\bavellana", r"\bpistacho", r"\banacardo",
    ),
    "apio": (r"\bapio\b",),
    "mostaza": (r"\bmostaza",),
    "sésamo": (r"\bsesamo",),
    "sulfitos": (r"\bsulfito",),
    "altramuces": (r"\baltramu",),
    "moluscos": (r"\bmolusco", r"\bmarisco", r"\bmejillon", r"\balmeja", r"\bcalamar", r"\bpulpo"),
}


def normalize(text: str) -> str:
    """Lower case without accents, so «Celíaco» and «celiaco» match."""

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def declared(text: str | None) -> list[str] | None:
    """The allergens a carta line declares; None when unknown or pending."""

    if text is None or PENDING in normalize(text):
        return None
    normalized = normalize(text)
    return [
        allergen
        for allergen in ALLERGENS
        if re.search(rf"\b{re.escape(normalize(allergen))}\b", normalized)
    ]


def restricted(restrictions: Iterable[str]) -> dict[str, str]:
    """Each allergen the declared restrictions exclude, with the restriction that names it."""

    found: dict[str, str] = {}
    for restriction in restrictions:
        words = normalize(restriction)
        for allergen, patterns in _CUSTOMER_WORDS.items():
            if any(re.search(pattern, words) for pattern in patterns):
                found.setdefault(allergen, restriction)
    return found
