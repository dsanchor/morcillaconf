from __future__ import annotations

from decimal import Decimal

from restaurant_contracts.cashier import Bill, BillStage, CashierFailure, CashierFailureCode, PaymentMethod

from cashier_agent.evidence import amount, parse
from cashier_agent.knowledge import UNAVAILABLE
from cashier_agent.pricing import price_bill

from carta_fixtures import (
    CARTA,
    CARTA_LABEL,
    CROQUETAS,
    MORCILLA,
    RECIPES_LABEL,
    SERVED,
    TOTAL,
    WEB_LABEL,
    block,
    request,
    retrieval,
    served,
)


def test_the_bill_prices_each_served_line_from_the_carta_with_exact_decimals() -> None:
    bill = price_bill(SERVED, parse([CARTA]))

    assert isinstance(bill, Bill)
    assert [(line.carta_id, line.unit_price, line.line_total) for line in bill.lines] == [
        ("morcilla-de-burgos-a-la-brasa", Decimal("8.50"), Decimal("8.50")),
        ("croquetas-de-morcilla", Decimal("9.00"), Decimal("9.00")),
    ]
    assert bill.total == TOTAL and isinstance(bill.total, Decimal)
    assert (bill.currency, bill.stage) == ("EUR", BillStage.AWAITING_PAYMENT)
    assert bill.payment_options == [PaymentMethod.CARD, PaymentMethod.CASH]
    assert bill.note == "Solo platos de cocina; las bebidas todavía no se cobran."
    source = bill.sources[0]
    assert (source.document, source.version) == ("carta de la casa", "1")
    assert "croquetas-de-morcilla" in (source.detail or "")


def test_every_served_line_is_priced_once_with_its_served_quantity() -> None:
    lines = request(
        served("croquetas-de-morcilla", "Croquetas de morcilla", quantity=3),
        served("croquetas-de-morcilla", "Croquetas de morcilla", order_id="ko_2"),
    )
    bill = price_bill(lines, parse([CARTA, CARTA]))

    assert [(line.order_id, line.quantity, line.line_total) for line in bill.lines] == [
        ("ko_1", 3, Decimal("27.00")),
        ("ko_2", 1, Decimal("9.00")),
    ]
    assert bill.total == Decimal("36.00")


def test_a_web_price_never_prices_a_dish() -> None:
    web = block("w", WEB_LABEL, CROQUETAS.replace("9,00 €", "1,00 €"))

    result = price_bill(SERVED, parse([retrieval(block("1", CARTA_LABEL, MORCILLA), web)]))

    assert isinstance(result, CashierFailure)
    assert result.code is CashierFailureCode.PRICE_MISSING
    assert "Croquetas de morcilla" in result.message
    with_carta = price_bill(SERVED, parse([retrieval(web, CARTA)]))
    assert with_carta.total == TOTAL


def test_the_recipe_book_and_entries_without_the_carta_mark_never_price() -> None:
    recipe = block("r", RECIPES_LABEL, CROQUETAS.replace(" (documento: carta)", ""))
    unmarked = block("3", CARTA_LABEL, CROQUETAS.replace(" (documento: carta)", ""))

    for passages in ([recipe], [unmarked]):
        result = price_bill(
            SERVED, parse([retrieval(block("1", CARTA_LABEL, MORCILLA), *passages)])
        )
        assert isinstance(result, CashierFailure)
        assert result.code is CashierFailureCode.PRICE_MISSING


def test_two_prices_or_an_unreadable_one_are_inconsistent() -> None:
    other = block("9", CARTA_LABEL, CROQUETAS.replace("9,00 €", "9,50 €"))
    unreadable = block("9", CARTA_LABEL, CROQUETAS.replace("9,00 € la ración", "según mercado"))

    for extra in (other, unreadable):
        result = price_bill(SERVED, parse([retrieval(CARTA, extra)]))
        assert isinstance(result, CashierFailure)
        assert result.code is CashierFailureCode.PRICE_INCONSISTENT
    same = price_bill(SERVED, parse([CARTA, CARTA]))
    assert isinstance(same, Bill) and same.total == TOTAL


def test_no_retrieval_or_an_unavailable_base_is_an_explicit_failure() -> None:
    assert price_bill(SERVED, parse([])).code is CashierFailureCode.CARTA_NOT_CONSULTED
    assert price_bill(SERVED, parse([UNAVAILABLE])).code is CashierFailureCode.KNOWLEDGE_UNAVAILABLE
    nothing = price_bill(SERVED, parse(["no_results: nada"]))
    assert nothing.code is CashierFailureCode.PRICE_MISSING


def test_with_staff_review_the_bill_waits_without_payment_options() -> None:
    bill = price_bill(SERVED, parse([CARTA]), stage=BillStage.AWAITING_REVIEW)

    assert bill.stage is BillStage.AWAITING_REVIEW
    assert bill.payment_options == [] and bill.total == TOTAL


def test_amounts_are_read_only_as_the_carta_writes_them() -> None:
    assert amount("8,50 € la ración de cuatro rodajas") == Decimal("8.50")
    assert amount("19,00 € la ración") == Decimal("19.00")
    assert amount("7 € la ración") == Decimal("7.00")
    for value in ("8.50 €", "8,5 €", "consultar", "€ 8,50", ""):
        assert amount(value) is None, value
