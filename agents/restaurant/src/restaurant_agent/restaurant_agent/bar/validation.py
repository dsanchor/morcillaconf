"""Deterministic decision on a round of drinks, from the carta the bar retrieved.

No model takes part. The allergen rule and its reasons are a copy of the
kitchen's ``validation.py`` (separate projects, duplicated on purpose; keep
both in step), with «la barra» where the kitchen says «cocina»:

- a drink is served only when its name resolves to exactly one drink of the
  carta, and it is served as that carta entry, with the asked quantity;
- a declared allergy or intolerance never meets a drink that contains, may
  contain or has not verified that allergen;
- an ambiguous name, a kitchen dish or anything not on the carta is not
  served, always with its reason;
- only the carta actually retrieved is cited.
"""

from __future__ import annotations

from collections.abc import Iterable

from restaurant_contracts.bar import BarRequest, BarRound, RejectedDrink, ServedDrink
from restaurant_contracts.kitchen import KitchenSource

from restaurant_agent.bar.allergens import restricted
from restaurant_agent.bar.evidence import BarEvidence, CartaEntry
from restaurant_agent.bar.matching import resolve

TEXT_LIMIT = 300
CARTA = "carta de la casa"

NOT_IN_CARTA = "No está en la carta."
NOT_A_DRINK = "No es una bebida de la barra: en la carta es un plato de cocina."
NOT_A_DRINK_NAMED = "No es una bebida de la barra: «{name}» es un plato de cocina."
AMBIGUOUS = "En la carta hay varias: {options}."
UNCHECKED_ALLERGENS = (
    "Has indicado {restriction} y la barra no ha podido comprobar sus alérgenos en la carta."
)
PENDING_ALLERGENS = (
    "Sus alérgenos están pendientes de verificar y has indicado {restriction}: "
    "la barra no puede garantizarlo."
)
CONTAINS = "Contiene {allergen} según la carta y has indicado {restriction}."
MAY_CONTAIN = "Puede contener trazas de {allergen} según la carta y has indicado {restriction}."
PENDING_WARNING = "{name}: alérgenos pendientes de verificar; la barra no puede confirmarlos."


def clip(text: str | None, limit: int = TEXT_LIMIT) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 1].rstrip()}…"


def options(names: Iterable[str]) -> str:
    listed = list(names)
    return listed[0] if len(listed) == 1 else f"{', '.join(listed[:-1])} o {listed[-1]}"


def build_round(request: BarRequest, evidence: BarEvidence) -> BarRound:
    """What the bar serves of the round; raises ValidationError if it cannot be built."""

    excluded = restricted(request.restrictions)
    served: list[ServedDrink] = []
    rejected: list[RejectedDrink] = []
    warnings: list[str] = []
    for item in request.items:
        asked = clip(item.name)
        resolution = resolve(item.name, evidence)
        if resolution.kind == "ambiguous":
            names = [clip(entry.name) for entry in resolution.entries][:10]
            rejected.append(
                RejectedDrink(
                    line=item.line, requested=asked, quantity=item.quantity,
                    reason=clip(AMBIGUOUS.format(options=options(names))), options=names,
                )
            )
            continue
        if resolution.kind == "dish":
            reason = (
                NOT_A_DRINK_NAMED.format(name=resolution.entries[0].name)
                if len(resolution.entries) == 1
                else NOT_A_DRINK
            )
            carta_id = resolution.entries[0].carta_id if len(resolution.entries) == 1 else None
            rejected.append(
                RejectedDrink(
                    line=item.line, requested=asked, quantity=item.quantity,
                    reason=clip(reason), carta_id=carta_id,
                )
            )
            continue
        if resolution.kind == "unknown":
            rejected.append(
                RejectedDrink(line=item.line, requested=asked, quantity=item.quantity, reason=NOT_IN_CARTA)
            )
            continue
        (entry,) = resolution.entries
        conflict = allergen_conflict(entry, excluded)
        if conflict is not None:
            rejected.append(
                RejectedDrink(
                    line=item.line, requested=asked, quantity=item.quantity,
                    reason=clip(conflict), carta_id=entry.carta_id,
                )
            )
            continue
        name = clip(entry.name)
        served.append(ServedDrink(line=item.line, carta_id=entry.carta_id, name=name, quantity=item.quantity))
        warning = clip(PENDING_WARNING.format(name=name))
        if not entry.verified and warning not in warnings:
            warnings.append(warning)
    return BarRound(
        round_id=request.round_id,
        served=served,
        rejected=rejected,
        warnings=warnings[:20],
        sources=[
            KitchenSource(document=CARTA, version=", ".join(sorted(evidence.versions)) or None)
        ],
    )


def allergen_conflict(entry: CartaEntry, excluded: dict[str, str]) -> str | None:
    """Why a drink cannot be served given the declared restrictions, if it cannot."""

    if not excluded:
        return None
    restriction = next(iter(excluded.values()))
    if entry.contains is None or entry.traces is None:
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
