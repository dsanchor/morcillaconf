"""The bill, computed by code from the carta prices the cashier retrieved.

No language model touches an amount: each served line is priced exactly once,
with its served quantity, from the single price its carta entry declares, and
the total is their exact ``Decimal`` sum in euros. A dish without a readable
carta price, or with two different ones, makes the whole bill an explicit
``CashierFailure``; nothing is estimated in its place.
"""

from __future__ import annotations

from decimal import Decimal

from restaurant_contracts.cashier import (
    FAILURE_MESSAGES,
    PAYMENT_OPTIONS,
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    cashier_failure,
)

from cashier_agent.evidence import PriceEvidence

CARTA = "carta de la casa"


def _failure(request: BillRequest, code: CashierFailureCode, dish: str) -> CashierFailure:
    message = f"{FAILURE_MESSAGES[code]} Plato: {dish}."
    return CashierFailure(bill_id=request.bill_id, code=code, message=message[:300])


def price_bill(
    request: BillRequest,
    evidence: PriceEvidence,
    *,
    stage: BillStage = BillStage.AWAITING_PAYMENT,
) -> Bill | CashierFailure:
    """Price every served line from the carta, or explain why caja cannot."""

    if evidence.retrievals == 0:
        code = (
            CashierFailureCode.KNOWLEDGE_UNAVAILABLE
            if evidence.failures
            else CashierFailureCode.CARTA_NOT_CONSULTED
        )
        return cashier_failure(request.bill_id, code)
    lines: list[BillLine] = []
    for served in request.lines:
        entry = evidence.dishes.get(served.carta_id)
        if entry is None or not (entry.prices or entry.unreadable):
            return _failure(request, CashierFailureCode.PRICE_MISSING, served.name)
        if entry.unreadable or len(entry.prices) != 1:
            return _failure(request, CashierFailureCode.PRICE_INCONSISTENT, served.name)
        (unit,) = entry.prices
        lines.append(
            BillLine(
                order_id=served.order_id,
                line=served.line,
                carta_id=served.carta_id,
                name=served.name,
                quantity=served.quantity,
                unit_price=unit,
                line_total=unit * served.quantity,
            )
        )
    priced = sorted({line.carta_id for line in lines})
    versions = sorted(
        {version for carta_id in priced for version in evidence.dishes[carta_id].versions}
    )
    return Bill(
        bill_id=request.bill_id,
        stage=stage,
        lines=lines,
        total=sum((line.line_total for line in lines), Decimal(0)),
        payment_options=[] if stage is BillStage.AWAITING_REVIEW else list(PAYMENT_OPTIONS),
        sources=[
            BillSource(
                document=CARTA,
                version=", ".join(versions) or None,
                detail=f"Precios de {', '.join(priced)}"[:300],
            )
        ],
    )
