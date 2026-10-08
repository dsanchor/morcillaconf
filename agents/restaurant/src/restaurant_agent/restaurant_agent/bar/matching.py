"""Which carta drink a requested name is: deterministic, accent-insensitive, no model.

A name resolves to a drink when every significant word of it is in the drink's
carta name or identifier («caña», «cañas», «una cerveza», «cana-de-cerveza»);
failing that, in its description or serving unit («agua mineral», «una
botella de agua»). Either way, at least one of those words must name the drink
itself, so «una sin», «alcohol» or «un vaso» resolve to nothing. Exactly one
drink is a match and several are an ambiguity the waiter must ask about. When
no drink fits, a kitchen dish of the carta is not a drink and anything else is
not on the carta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from restaurant_agent.bar.allergens import normalize
from restaurant_agent.bar.evidence import BarEvidence, CartaEntry

# Words that never tell one drink from another: articles, prepositions,
# courtesy, ordering verbs and how cold it comes («bien fría»). «con» and
# «sin» are kept: they tell «Agua con gas» from «Agua sin gas».
STOPWORDS = frozenset(
    {
        "a", "al", "de", "del", "el", "en", "la", "las", "lo", "los", "para", "por", "que",
        "un", "una", "uno", "e", "o", "u", "y",
        "favor", "porfa", "porfavor", "gracias", "otra", "otro", "mas", "tambien",
        "pon", "ponme", "ponnos", "pongame", "ponganos", "trae", "traeme", "traenos",
        "dame", "danos", "quiero", "queremos", "quisiera", "quisieramos", "tomar", "beber",
        "bien", "muy", "fria", "frio", "fresca", "fresco", "fresquita", "fresquito",
        "helada", "helado",
    }
)
# They tell two names apart but never name a drink on their own.
JOINERS = frozenset({"con", "sin"})
NUMBERS = frozenset(
    {"dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce"}
)
_SPLIT = re.compile(r"[^a-z0-9]+")


def _singular(word: str) -> str:
    """«cañas» and «caña» are the same word; «gas» keeps its s."""

    if len(word) >= 4 and word.endswith("s") and word[-2] in "aeiou":
        return word[:-1]
    return word


def words(text: str) -> tuple[str, ...]:
    """The significant words of a name, lower case, without accents and in singular."""

    found = []
    for token in _SPLIT.split(normalize(text)):
        if not token or token.isdigit() or token in NUMBERS or token in STOPWORDS:
            continue
        singular = _singular(token)
        if singular not in STOPWORDS:
            found.append(singular)
    return tuple(found)


@dataclass(frozen=True)
class Resolution:
    kind: Literal["drink", "ambiguous", "dish", "unknown"]
    entries: tuple[CartaEntry, ...] = ()


def _named(entry: CartaEntry) -> set[str]:
    return {*words(entry.name), *words(entry.carta_id)}


def _described(entry: CartaEntry) -> set[str]:
    return {*_named(entry), *words(entry.description), *words(entry.unit)}


def _fits(wanted: set[str], entry: CartaEntry, vocabulary) -> bool:
    """Every asked word is in the vocabulary and one of them names the entry itself."""

    return wanted <= vocabulary(entry) and bool(wanted & (_named(entry) - JOINERS))


def resolve(name: str, evidence: BarEvidence) -> Resolution:
    """The carta drink a requested name is, if exactly one."""

    wanted = words(name)
    if not wanted:
        return Resolution("unknown")
    drinks = evidence.drinks
    exact = [drink for drink in drinks if wanted in (words(drink.name), words(drink.carta_id))]
    if len(exact) == 1:
        return Resolution("drink", (exact[0],))
    for vocabulary in (_named, _described):
        matches = [drink for drink in drinks if _fits(set(wanted), drink, vocabulary)]
        if matches:
            return Resolution("drink" if len(matches) == 1 else "ambiguous", tuple(matches))
    dishes = [dish for dish in evidence.dishes if _fits(set(wanted), dish, _named)]
    if dishes:
        return Resolution("dish", tuple(dishes))
    return Resolution("unknown")
