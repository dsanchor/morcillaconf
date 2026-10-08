"""The bar's round as plain Spanish text, for the conversation and the waiter."""

from __future__ import annotations

from restaurant_contracts.bar import BarFailure, BarRound
from restaurant_contracts.kitchen import RENDERED_TEXT_LIMIT

from restaurant_agent.kitchen.rendering import source_text

TITLE = "Barra"


def render_text(result: BarRound | BarFailure) -> str:
    if isinstance(result, BarFailure):
        return f"La barra no ha podido servir las bebidas. {result.message}"
    lines = [TITLE]
    if result.served:
        lines.append("Servido:")
        lines.extend(f"- {item.quantity} × {item.name}" for item in result.served)
    if result.rejected:
        lines.append("No servido:")
        lines.extend(f"- {item.quantity} × {item.requested}: {item.reason}" for item in result.rejected)
    if result.warnings:
        lines.append("Avisos:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.sources:
        lines.append("Fuentes: " + "; ".join(source_text(source) for source in result.sources) + ".")
    text = "\n".join(lines)
    return text if len(text) <= RENDERED_TEXT_LIMIT else f"{text[: RENDERED_TEXT_LIMIT - 1]}…"
