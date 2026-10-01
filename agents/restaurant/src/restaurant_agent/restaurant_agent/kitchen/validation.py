"""Deterministic validation that turns the chef's draft into a plan.

The model proposes; this code decides what the waiter may say:

- every order line is decided exactly once, with the ordered quantity;
- an accepted line names a carta entry that the knowledge base returned, and
  its partida and allergens are the ones that entry declares;
- a declared allergy or intolerance never meets a dish that contains, may
  contain or has not verified that allergen;
- the stations are the fixed partidas, filled from the accepted lines;
- only the restaurant documents actually retrieved are cited.

Anything the evidence cannot back becomes a rejection with its reason, never
an acceptance.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from restaurant_contracts.kitchen import (
    STATION_ORDER,
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenSource,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)

from restaurant_agent.kitchen.agent import ChefDraft, ChefLine
from restaurant_agent.kitchen.allergens import normalize, restricted
from restaurant_agent.kitchen.evidence import CartaEntry, Evidence

TEXT_LIMIT = 300
CARTA_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

NOT_EVALUATED = "Cocina no ha evaluado esta línea; vuelve a pedirla."
NOT_IN_CARTA = "No está en la carta consultada."
NO_STATION = "Cocina no ha podido asignarlo a una partida."
DEFAULT_REJECTION = "Cocina no puede prepararlo."
UNCHECKED_ALLERGENS = (
    "Has indicado {restriction} y cocina no ha podido comprobar sus alérgenos en la carta."
)
PENDING_ALLERGENS = (
    "Sus alérgenos están pendientes de verificar y has indicado {restriction}: "
    "cocina no puede garantizarlo."
)
CONTAINS = "Contiene {allergen} según la carta y has indicado {restriction}."
MAY_CONTAIN = "Puede contener trazas de {allergen} según la carta y has indicado {restriction}."
ASKED_WITHOUT = "Según la carta {declaration} {allergen} y has pedido «{modification}»."
UNCONFIRMED_CHANGES = "Cocina no ha confirmado el cambio pedido ({changes})."
PENDING_WARNING = "{name}: alérgenos pendientes de verificar; cocina no puede confirmarlos."
DOCUMENTS = {
    "carta": "carta de la casa",
    "recetario": "recetario de la casa",
    "ingredientes": "ingredientes de la casa",
}


def clip(text: str | None, limit: int = TEXT_LIMIT) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 1].rstrip()}…"


def clip_all(values: Iterable[str], count: int) -> list[str]:
    kept: list[str] = []
    for value in values:
        text = clip(value)
        if text and text not in kept:
            kept.append(text)
    return kept[:count]


def requested(line: KitchenOrderLine) -> str:
    return clip(", ".join([line.name, *line.modifications]))


def build_plan(order: KitchenOrder, draft: ChefDraft, evidence: Evidence) -> KitchenPlan:
    """The plan the waiter may communicate; raises ValidationError if it cannot be built."""

    excluded = restricted(order.restrictions)
    accepted: list[AcceptedItem] = []
    rejected: list[RejectedItem] = []
    tasks: dict[KitchenStation, list[StationTask]] = {}
    warnings = clip_all(draft.warnings, 20)
    for line in order.lines:
        decision = _decision(line.line, draft.lines)
        if decision is None:
            rejected.append(_reject(line, NOT_EVALUATED))
            continue
        carta_id = carta_identifier(decision.carta_id)
        if decision.decision == "rejected":
            known = carta_id if _known(carta_id, evidence) else None
            rejected.append(_reject(line, decision.reason or DEFAULT_REJECTION, known))
            continue
        if not _known(carta_id, evidence):
            rejected.append(_reject(line, NOT_IN_CARTA))
            continue
        entry = evidence.dishes.get(carta_id)
        station = evidence.station(carta_id) or (
            KitchenStation(decision.station) if decision.station else None
        )
        if station is None:
            rejected.append(_reject(line, NO_STATION, carta_id))
            continue
        conflict = _allergen_conflict(entry, excluded) or _modification_conflict(entry, line)
        if conflict is not None:
            rejected.append(_reject(line, conflict, carta_id))
            continue
        if line.modifications and not clip_all(decision.adaptations, 10):
            rejected.append(
                _reject(line, UNCONFIRMED_CHANGES.format(changes=", ".join(line.modifications)), carta_id)
            )
            continue
        name = clip(entry.name if entry else line.name)
        allergens, traces, verified = _declared(entry)
        accepted.append(
            AcceptedItem(
                line=line.line,
                carta_id=carta_id,
                name=name,
                quantity=line.quantity,
                station=station,
                adaptations=clip_all(decision.adaptations, 10),
                allergens=allergens,
                traces=traces,
                allergens_verified=verified,
            )
        )
        if not verified and not _warned_pending(name, warnings):
            warnings.append(clip(PENDING_WARNING.format(name=name)))
        tasks.setdefault(station, []).append(
            StationTask(
                line=line.line,
                carta_id=carta_id,
                name=name,
                quantity=line.quantity,
                steps=[] if station is KitchenStation.BARRA else clip_all(decision.steps, 6),
                omit=clip_all(decision.omit, 10),
                precautions=clip_all(decision.precautions, 10),
            )
        )
    return KitchenPlan(
        order_id=order.order_id,
        accepted=accepted,
        rejected=rejected,
        warnings=warnings[:20],
        stations=[
            StationPlan(station=station, tasks=tasks[station])
            for station in STATION_ORDER
            if station in tasks
        ],
        sources=_sources(draft, evidence, [item.carta_id for item in accepted]),
    )


def carta_identifier(value: str | None) -> str:
    """The identifier part of what the chef copied: «id · Nombre» or `id` become id."""

    return (value or "").split("·", 1)[0].strip().strip("`").strip().casefold()


def _warned_pending(name: str, warnings: list[str]) -> bool:
    """Whether the chef already warned that this dish's allergens are pending."""

    dish = normalize(name).split()[0]
    return any("pendiente" in (text := normalize(warning)) and dish in text for warning in warnings)


def _decision(number: int, lines: list[ChefLine]) -> ChefLine | None:
    """The chef's decision on a line; a contradictory one counts as a rejection."""

    candidates = [line for line in lines if line.line == number]
    rejected = [line for line in candidates if line.decision == "rejected"]
    return (rejected or candidates or [None])[0]


def _known(carta_id: str | None, evidence: Evidence) -> bool:
    return bool(carta_id) and CARTA_ID.fullmatch(carta_id) is not None and evidence.knows(carta_id)


def _declared(entry: CartaEntry | None) -> tuple[list[str], list[str], bool]:
    """Allergens and traces as the retrieved carta declares them, or unverified.

    Only a carta entry with both «Contiene» and «Puede contener» verifies them;
    the chef's own lists never do.
    """

    if entry is None or entry.contains is None or entry.traces is None or entry.pending:
        return [], [], False
    return entry.allergens or [], entry.trace_allergens or [], True


def _reject(line: KitchenOrderLine, reason: str, carta_id: str | None = None) -> RejectedItem:
    return RejectedItem(
        line=line.line,
        requested=requested(line),
        quantity=line.quantity,
        reason=clip(reason) or DEFAULT_REJECTION,
        carta_id=carta_id,
    )


def _allergen_conflict(entry: CartaEntry | None, excluded: dict[str, str]) -> str | None:
    """Why a dish cannot be served given the declared restrictions, if it cannot."""

    if not excluded:
        return None
    restriction = next(iter(excluded.values()))
    if entry is None or entry.contains is None or entry.traces is None:
        return UNCHECKED_ALLERGENS.format(restriction=restriction)
    if entry.pending:
        return PENDING_ALLERGENS.format(restriction=restriction)
    for allergen in entry.allergens or []:
        if allergen in excluded:
            return CONTAINS.format(allergen=allergen, restriction=excluded[allergen])
    for allergen in entry.trace_allergens or []:
        if allergen in excluded:
            return MAY_CONTAIN.format(allergen=allergen, restriction=excluded[allergen])
    return None


def _modification_conflict(entry: CartaEntry | None, line: KitchenOrderLine) -> str | None:
    """A change such as «sin gluten» cannot be promised for an allergen the carta declares."""

    for modification in line.modifications:
        for allergen in restricted([modification]):
            if entry is None or entry.contains is None or entry.traces is None or entry.pending:
                return ASKED_WITHOUT.format(
                    declaration="no se puede comprobar si lleva", allergen=allergen, modification=modification
                )
            if allergen in (entry.allergens or []):
                return ASKED_WITHOUT.format(declaration="contiene", allergen=allergen, modification=modification)
            if allergen in (entry.trace_allergens or []):
                return ASKED_WITHOUT.format(
                    declaration="puede contener trazas de", allergen=allergen, modification=modification
                )
    return None


def _sources(draft: ChefDraft, evidence: Evidence, accepted: list[str]) -> list[KitchenSource]:
    """The house documents the chef cited and actually retrieved; the carta always when used."""

    if evidence.retrievals and not evidence.versions:
        # The knowledge base answered without any restaurant document: the
        # carta was consulted, and that is what a «not on the carta» rests on.
        return [KitchenSource(document=DOCUMENTS["carta"], detail="consultada sin resultados")]
    cited = {source.document for source in draft.sources}
    if evidence.dishes:
        cited.add("carta")
    recipes = sorted(
        {evidence.recipes[carta_id].recipe for carta_id in accepted if carta_id in evidence.recipes}
    )
    if recipes:
        cited.add("recetario")
    sources = []
    for document in ("carta", "recetario", "ingredientes"):
        if document not in cited or document not in evidence.versions:
            continue
        detail = None
        if document == "recetario" and recipes:
            detail = ("receta " if len(recipes) == 1 else "recetas ") + ", ".join(recipes)
        sources.append(
            KitchenSource(document=DOCUMENTS[document], version=evidence.versions[document], detail=detail)
        )
    return sources
