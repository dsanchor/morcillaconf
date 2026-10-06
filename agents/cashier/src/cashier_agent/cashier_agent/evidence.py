"""What the knowledge base told the cashier: carta entries and their prices.

A price counts only when the restaurant's own carta says it: the passage
comes from a house document (never the web) and the dish entry declares its
provenance, «(documento: carta)». The recipe book, the ingredient sheet and
web results never price a dish, whatever the model says. Every price an entry
is seen with is kept, so two different prices for one dish are caught instead
of picking one.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from cashier_agent.knowledge import HOUSE, KNOWLEDGE_TOOL, UNAVAILABLE

# summarize_retrieval writes each passage as «[ref] Origen: <label>» and its text.
_BLOCK = re.compile(r"^\[[^\]\n]*\] Origen: (?P<label>[^\n]*)\n", re.M)
_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_DISH = re.compile(rf"^#{{2,4}}\s+`?(?P<id>{_ID})`?\s+·\s+(?P<name>[^\n]+?)\s*$", re.M)
_SECTION_END = re.compile(r"^#{1,4}\s", re.M)
_PRICE = re.compile(r"^- Precio:[ \t]*(?P<value>[^\n]*)", re.M)
# «8,50 € la ración»: euros with comma cents, as the carta writes them.
_AMOUNT = re.compile(r"^(?P<euros>\d{1,5})(?:,(?P<cents>\d{2}))?[ \t]*€(?![\d,])")
_CARTA_MARK = "(documento: carta)"
_CARTA_VERSION = re.compile(r"carta de la casa,? versi[oó]n (\d+)", re.I)


@dataclass
class PricedEntry:
    """One carta entry and every price the carta passages gave it."""

    carta_id: str
    name: str
    prices: set[Decimal] = field(default_factory=set)
    versions: set[str] = field(default_factory=set)
    unreadable: bool = False


@dataclass
class PriceEvidence:
    retrievals: int = 0
    failures: int = 0
    dishes: dict[str, PricedEntry] = field(default_factory=dict)

    @property
    def versions(self) -> set[str]:
        return {version for entry in self.dishes.values() for version in entry.versions}


def amount(text: str) -> Decimal | None:
    """The euros of a «Precio» field, or ``None`` when it is not a plain amount."""

    match = _AMOUNT.match(text.strip())
    if match is None:
        return None
    return Decimal(f"{match['euros']}.{match['cents'] or '00'}")


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


def parse(results: Iterable[str]) -> PriceEvidence:
    """The carta prices in the knowledge base results the cashier received."""

    evidence = PriceEvidence()
    for text in results:
        if text.startswith(UNAVAILABLE.split(":", 1)[0]):
            evidence.failures += 1
            continue
        evidence.retrievals += 1
        for label, content in _blocks(text):
            if label.startswith(HOUSE):
                _read_prices(content, evidence)
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


def _read_prices(content: str, evidence: PriceEvidence) -> None:
    for match in _DISH.finditer(content):
        end = _SECTION_END.search(content, match.end())
        section = content[match.end() : end.start() if end else len(content)]
        if _CARTA_MARK not in section:
            # The ingredient sheet shares the headings; only carta entries price.
            continue
        prices = [item["value"] for item in _PRICE.finditer(section)]
        if not prices:
            continue
        entry = evidence.dishes.setdefault(
            match["id"], PricedEntry(carta_id=match["id"], name=match["name"].strip())
        )
        for value in prices:
            parsed = amount(value)
            if parsed is None:
                entry.unreadable = True
            else:
                entry.prices.add(parsed)
        entry.versions.update(_CARTA_VERSION.findall(section))


class EvidenceRecorder(FunctionMiddleware):
    """Keeps the text of every knowledge base retrieval of one cashier run."""

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

    def evidence(self) -> PriceEvidence:
        return parse(self.results)
