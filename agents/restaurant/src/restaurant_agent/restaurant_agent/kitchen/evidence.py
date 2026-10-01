"""What the knowledge base told the chef while planning one order.

The chef's plan is checked against this evidence, never against the model's
word: a dish is accepted only if its carta entry was retrieved, and its
partida and allergens are read from that entry. Only the restaurant's own
documents count; web results are ignored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from restaurant_contracts.kitchen import KitchenStation

from restaurant_agent.knowledge import HOUSE, KNOWLEDGE_TOOL, UNAVAILABLE
from restaurant_agent.kitchen.allergens import declared

logger = logging.getLogger(__name__)

# summarize_retrieval writes each passage as «[ref] Origen: <label>» and its text.
_BLOCK = re.compile(r"^\[[^\]\n]*\] Origen: (?P<label>[^\n]*)\n", re.M)
_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_DISH = re.compile(rf"^#{{2,4}}\s+`?(?P<id>{_ID})`?\s+·\s+(?P<name>[^\n]+?)\s*$", re.M)
_SECTION_END = re.compile(r"^#{1,4}\s", re.M)
_FIELD = re.compile(
    r"^- (?P<key>Partida|Contiene|Puede contener):[ \t]*(?P<value>[^\n]*(?:\n[ \t]+[^\n-][^\n]*)*)",
    re.M,
)
_RECIPE = re.compile(
    rf"receta (?P<recipe>R\d+) · plato de la carta: (?P<id>{_ID}) · partida: (?P<station>[^\n·]+)",
    re.I,
)
_VERSIONS = {
    "carta": re.compile(r"carta de la casa,? versi[oó]n (\d+)", re.I),
    "recetario": re.compile(r"recetario de la casa[^\n]*?versi[oó]n (\d+)", re.I),
    "ingredientes": re.compile(r"ingredientes de la casa,? versi[oó]n (\d+)", re.I),
}
_STATIONS = {
    "brasa": KitchenStation.BRASA,
    "fritos": KitchenStation.FRITOS,
    "pinchos frios": KitchenStation.PINCHOS_FRIOS,
    "pinchos fríos": KitchenStation.PINCHOS_FRIOS,
    "barra": KitchenStation.BARRA,
}


@dataclass
class CartaEntry:
    """One dish as the retrieved carta describes it."""

    carta_id: str
    name: str
    station: KitchenStation | None = None
    contains: str | None = None
    traces: str | None = None

    def merge(self, other: CartaEntry) -> None:
        self.station = self.station or other.station
        self.contains = self.contains or other.contains
        self.traces = self.traces or other.traces

    @property
    def pending(self) -> bool:
        """The carta cannot confirm its allergens yet."""

        return any(
            value is not None and declared(value) is None for value in (self.contains, self.traces)
        )

    @property
    def allergens(self) -> list[str] | None:
        return declared(self.contains)

    @property
    def trace_allergens(self) -> list[str] | None:
        return declared(self.traces)


@dataclass(frozen=True)
class RecipeRef:
    recipe: str
    carta_id: str
    station: KitchenStation | None


@dataclass
class Evidence:
    retrievals: int = 0
    failures: int = 0
    dishes: dict[str, CartaEntry] = field(default_factory=dict)
    recipes: dict[str, RecipeRef] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)

    def knows(self, carta_id: str) -> bool:
        """Whether the restaurant's documents retrieved name this carta entry."""

        return carta_id in self.dishes or carta_id in self.recipes

    def station(self, carta_id: str) -> KitchenStation | None:
        dish = self.dishes.get(carta_id)
        recipe = self.recipes.get(carta_id)
        return (dish.station if dish else None) or (recipe.station if recipe else None)


def station_of(text: str) -> KitchenStation | None:
    return _STATIONS.get(" ".join(text.strip(" .").casefold().split()))


def result_text(result: object) -> str:
    """The text of a tool result, whatever shape the framework left it in."""

    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(result_text(item) for item in result)
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else str(result)


def parse(results: Iterable[str]) -> Evidence:
    """The evidence in the knowledge base results the chef received."""

    evidence = Evidence()
    for text in results:
        if text.startswith(UNAVAILABLE.split(":", 1)[0]):
            evidence.failures += 1
            continue
        evidence.retrievals += 1
        for label, content in _blocks(text):
            if not label.startswith(HOUSE):
                continue
            if _is_carta(label, content):
                # Only the carta lists dishes; the ingredient sheet uses the same headings.
                _read_dishes(content, evidence)
            for match in _RECIPE.finditer(content):
                evidence.recipes.setdefault(
                    match["id"],
                    RecipeRef(match["recipe"].upper(), match["id"], station_of(match["station"])),
                )
            for document, pattern in _VERSIONS.items():
                found = pattern.search(f"{label}\n{content}")
                if found:
                    evidence.versions.setdefault(document, found.group(1))
    return evidence


def _is_carta(label: str, content: str) -> bool:
    return "carta" in label.removeprefix(HOUSE).casefold() or "(documento: carta)" in content


def _blocks(text: str) -> list[tuple[str, str]]:
    starts = list(_BLOCK.finditer(text))
    return [
        (match["label"].strip(), text[match.end() : starts[index + 1].start() if index + 1 < len(starts) else len(text)])
        for index, match in enumerate(starts)
    ]


def _read_dishes(content: str, evidence: Evidence) -> None:
    for match in _DISH.finditer(content):
        end = _SECTION_END.search(content, match.end())
        section = content[match.end() : end.start() if end else len(content)]
        fields = {item["key"]: " ".join(item["value"].split()) for item in _FIELD.finditer(section)}
        entry = CartaEntry(
            carta_id=match["id"],
            name=match["name"].strip(),
            station=station_of(fields["Partida"]) if "Partida" in fields else None,
            contains=fields.get("Contiene"),
            traces=fields.get("Puede contener"),
        )
        known = evidence.dishes.get(entry.carta_id)
        if known is None:
            evidence.dishes[entry.carta_id] = entry
        else:
            known.merge(entry)


class EvidenceRecorder(FunctionMiddleware):
    """Keeps the text of every knowledge base retrieval of one chef run."""

    def __init__(self) -> None:
        self.results: list[str] = []

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != KNOWLEDGE_TOOL:
            await call_next()
            return
        try:
            await call_next()
        except BaseException:
            self.results.append(UNAVAILABLE)
            raise
        self.results.append(result_text(context.result))

    def evidence(self) -> Evidence:
        return parse(self.results)
