"""The carta entries the knowledge base returned to the bar.

A minimal copy of the kitchen's ``evidence.py`` parser (separate projects,
duplicated on purpose; keep both in step). Only the restaurant's own documents
count, never the web, and an entry counts only when its own section declares
«(documento: carta)»: the ingredient sheet shares the headings but is not the
carta. Besides the partida and the allergens, the bar keeps each entry's
description and serving unit to recognize what the customer asked for; it
never reads an amount.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from restaurant_agent.bar.allergens import declared, normalize
from restaurant_agent.knowledge import HOUSE, UNAVAILABLE

# summarize_retrieval writes each passage as «[ref] Origen: <label>» and its text.
_BLOCK = re.compile(r"^\[[^\]\n]*\] Origen: (?P<label>[^\n]*)\n", re.M)
_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_DISH = re.compile(rf"^#{{2,4}}\s+`?(?P<id>{_ID})`?\s+·\s+(?P<name>[^\n]+?)\s*$", re.M)
_SECTION_END = re.compile(r"^#{1,4}\s", re.M)
_FIELD = re.compile(
    r"^- (?P<key>Partida|Contiene|Puede contener|Descripción|Precio):[ \t]*"
    r"(?P<value>[^\n]*(?:\n[ \t]+[^\n-][^\n]*)*)",
    re.M,
)
_CARTA_MARK = "(documento: carta)"
_CARTA_VERSION = re.compile(r"carta de la casa,? versi[oó]n (\d+)", re.I)
# The carta's drinks section, «## Barra: bebidas», and the section after it.
_BAR_SECTION = re.compile(r"^##\s+Barra\b[^\n]*$", re.M)
_NEXT_SECTION = re.compile(r"^#{1,2}\s", re.M)
# The amount of a «Precio» field: only its serving unit («la caña de 20 cl») is kept.
_AMOUNT = re.compile(r"\d+(?:[.,]\d+)?\s*€")
BAR = "barra"


@dataclass
class CartaEntry:
    """One carta entry as the retrieved carta describes it."""

    carta_id: str
    name: str
    station: str | None = None
    contains: str | None = None
    traces: str | None = None
    description: str = ""
    unit: str = ""

    def merge(self, other: CartaEntry) -> None:
        self.station = self.station or other.station
        self.contains = self.contains or other.contains
        self.traces = self.traces or other.traces
        self.description = self.description or other.description
        self.unit = self.unit or other.unit

    @property
    def drink(self) -> bool:
        return self.station == BAR

    @property
    def pending(self) -> bool:
        """The carta cannot confirm its allergens yet."""

        return any(
            value is not None and declared(value) is None for value in (self.contains, self.traces)
        )

    @property
    def verified(self) -> bool:
        """Only an entry with both «Contiene» and «Puede contener» verifies its allergens."""

        return self.contains is not None and self.traces is not None and not self.pending

    @property
    def allergens(self) -> list[str] | None:
        return declared(self.contains)

    @property
    def trace_allergens(self) -> list[str] | None:
        return declared(self.traces)


@dataclass
class BarEvidence:
    retrievals: int = 0
    failures: int = 0
    entries: dict[str, CartaEntry] = field(default_factory=dict)
    versions: set[str] = field(default_factory=set)
    # Whether a passage held the whole drinks section, from its heading to the next one.
    bar_section_complete: bool = False

    @property
    def drinks(self) -> list[CartaEntry]:
        return [entry for entry in self.entries.values() if entry.drink]

    @property
    def dishes(self) -> list[CartaEntry]:
        return [entry for entry in self.entries.values() if not entry.drink]


def station_of(text: str) -> str:
    return " ".join(normalize(text).strip(" .").split())


def parse(results: Iterable[str], evidence: BarEvidence | None = None) -> BarEvidence:
    """The carta entries in the knowledge base results, added to ``evidence``."""

    evidence = evidence or BarEvidence()
    for text in results:
        if text.startswith(UNAVAILABLE.split(":", 1)[0]):
            evidence.failures += 1
            continue
        evidence.retrievals += 1
        for label, content in _blocks(text):
            if not label.startswith(HOUSE):
                continue
            _read_entries(content, evidence)
            heading = _BAR_SECTION.search(content)
            if heading is not None and _NEXT_SECTION.search(content, heading.end()):
                evidence.bar_section_complete = True
    return evidence


def _blocks(text: str) -> list[tuple[str, str]]:
    starts = list(_BLOCK.finditer(text))
    return [
        (
            match["label"].strip(),
            text[match.end() : starts[index + 1].start() if index + 1 < len(starts) else len(text)],
        )
        for index, match in enumerate(starts)
    ]


def _read_entries(content: str, evidence: BarEvidence) -> None:
    for match in _DISH.finditer(content):
        end = _SECTION_END.search(content, match.end())
        section = content[match.end() : end.start() if end else len(content)]
        if _CARTA_MARK not in section:
            continue
        fields = {item["key"]: " ".join(item["value"].split()) for item in _FIELD.finditer(section)}
        entry = CartaEntry(
            carta_id=match["id"],
            name=match["name"].strip(),
            station=station_of(fields["Partida"]) if "Partida" in fields else None,
            contains=fields.get("Contiene"),
            traces=fields.get("Puede contener"),
            description=fields.get("Descripción", ""),
            unit=_AMOUNT.sub(" ", fields.get("Precio", "")),
        )
        known = evidence.entries.get(entry.carta_id)
        if known is None:
            evidence.entries[entry.carta_id] = entry
        else:
            known.merge(entry)
        evidence.versions.update(_CARTA_VERSION.findall(section))
