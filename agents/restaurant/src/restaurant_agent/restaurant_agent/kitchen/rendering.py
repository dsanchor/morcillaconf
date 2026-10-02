"""The kitchen's answer as plain Spanish text, for the conversation and the waiter."""

from __future__ import annotations

from restaurant_contracts.kitchen import (
    RENDERED_TEXT_LIMIT,
    STATION_LABELS,
    AcceptedItem,
    KitchenFailure,
    KitchenPlan,
    KitchenSource,
    StationTask,
)


def render_text(result: KitchenPlan | KitchenFailure) -> str:
    if isinstance(result, KitchenFailure):
        return f"Cocina no ha podido preparar el pedido. {result.message}"
    lines = ["Platos cocinados"]
    if result.accepted:
        lines.append("Listos para servir:")
        lines.extend(_accepted(item) for item in result.accepted)
    if result.rejected:
        lines.append("Rechazado:")
        lines.extend(f"- {item.quantity} × {item.requested}: {item.reason}" for item in result.rejected)
    if result.warnings:
        lines.append("Avisos:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.stations:
        lines.append("Reparto por partidas:")
        for station in result.stations:
            lines.append(f"- {STATION_LABELS[station.station]}:")
            lines.extend(_task(task) for task in station.tasks)
    if result.sources:
        lines.append("Fuentes: " + "; ".join(source_text(source) for source in result.sources) + ".")
    text = "\n".join(lines)
    return text if len(text) <= RENDERED_TEXT_LIMIT else f"{text[: RENDERED_TEXT_LIMIT - 1]}…"


def allergen_text(item: AcceptedItem) -> str:
    if not item.allergens_verified:
        return "alérgenos pendientes de verificar"
    contains = ", ".join(item.allergens) if item.allergens else "ninguno de los 14"
    text = f"alérgenos: {contains}"
    if item.traces:
        text += f"; puede contener {', '.join(item.traces)}"
    return text


def source_text(source: KitchenSource) -> str:
    details = [f"versión {source.version}" if source.version else "", source.detail or ""]
    described = ", ".join(detail for detail in details if detail)
    return f"{source.document} ({described})" if described else source.document


def _accepted(item: AcceptedItem) -> str:
    adapted = "".join(f", {adaptation}" for adaptation in item.adaptations)
    station = STATION_LABELS[item.station].lower()
    return f"- {item.quantity} × {item.name}{adapted} ({station}; {allergen_text(item)})"


def _task(task: StationTask) -> str:
    details = []
    if task.steps:
        details.append("Pasos: " + " ".join(_sentence(step) for step in task.steps))
    if task.omit:
        details.append("Omitir: " + _sentence(", ".join(task.omit)))
    if task.precautions:
        details.append("Precauciones: " + " ".join(_sentence(item) for item in task.precautions))
    head = f"  · {task.quantity} × {task.name}"
    return f"{head}. {' '.join(details)}" if details else head


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."
