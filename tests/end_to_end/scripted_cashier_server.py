"""The external cashier for the cashier end to end, without Foundry (test code only).

The real A2A cashier app (``cashier_agent.app.create_app``), with its real
pricing and payment ledger; only the model's lookup in the knowledge base is
replaced by the versioned carta itself, passed through the same parser as a
knowledge base passage. Prices therefore come from ``data/knowledge/menu/carta.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from cashier_agent.app import create_app
from cashier_agent.config import Settings
from cashier_agent.evidence import parse
from cashier_agent.ledger import Ledger
from cashier_agent.pricing import price_bill
from cashier_agent.progress import Step
from restaurant_contracts.cashier import Bill, BillRequest, CashierFailure, euros

CARTA = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "menu" / "carta.md"
LABEL = "documento de la casa: carta.md, tipo carta, versión 1"


class CartaLookup:
    """The cashier's pricing with the carta as the only retrieval."""

    def __init__(self) -> None:
        self._retrieval = f"[1] Origen: {LABEL}\n{CARTA.read_text(encoding='utf-8')}"

    async def price(self, request: BillRequest) -> Bill | CashierFailure:
        step = await Step("caja", "Caja: consulta precios en la carta", "Carta versionada").start()
        result = price_bill(request, parse([self._retrieval]))
        detail = euros(result.total) if isinstance(result, Bill) else result.code.value
        await step.finish(detail, failed=isinstance(result, CashierFailure))
        return result


def main() -> None:
    port = int(os.environ["PORT"])
    settings = Settings(
        _env_file=None,
        foundry_project_endpoint="https://scripted.invalid/api/projects/scripted",
        azure_ai_model_deployment_name="scripted",
        cashier_a2a_public_url=f"http://127.0.0.1:{port}/",
    )
    app = create_app(settings, service=CartaLookup(), ledger=Ledger())
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
