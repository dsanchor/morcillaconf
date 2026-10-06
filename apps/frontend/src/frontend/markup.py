"""HTML fragments of the Streamlit view; every dynamic text is escaped here.

Streamlit renders them with ``st.markdown(..., unsafe_allow_html=True)``
because ``st.html`` sanitizes SVG away. Each fragment is one line that starts
with ``<div`` so Markdown treats it as a raw HTML block and never parses the
customer's text as Markdown, math or shortcodes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Literal

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import ChatMessage
from restaurant_contracts.cashier import (
    PAYMENT_LABELS,
    Bill,
    BillLine,
    BillSource,
    BillStage,
    BillView,
    CashierFailure,
    Receipt,
    euros,
)
from restaurant_contracts.kitchen import (
    STATION_LABELS,
    AcceptedItem,
    KitchenFailure,
    KitchenPlan,
    KitchenSource,
    StationTask,
)
from restaurant_contracts.seating import RoomView, SeatingProposal, SeatingView

from frontend.facade import facade_html
from frontend.floor_plan import floor_plan_svg, waiter_icon_svg
from frontend.kitchen_plan import kitchen_plan_svg
from frontend.visit import Card, ConversationView


DOOR_HINT = "Dinos tu nombre y te abrimos."
SIMULATED_LABEL = "Camarero simulado"
TYPING_LABEL = "El camarero está escribiendo"
KITCHEN_LABEL = "Platos cocinados"
KITCHEN_VERDICTS = {
    "accepted": "Pedido cocinado y listo para servir.",
    "partial": "Los platos posibles ya están cocinados.",
    "rejected": "Cocina no ha podido preparar el pedido.",
}
KITCHEN_FAILED = "Cocina no ha podido revisar el pedido."
CASHIER_LABEL = "Cuenta de caja"
BILL_STATES = {
    "pending": "Pendiente de pago.",
    "paid": "Pagada.",
    "void": "Anulada: pedid la cuenta otra vez.",
}


@dataclass(frozen=True)
class Reveal:
    """Delayed appearance of the greeting: after the door opens or after /new."""

    message_id: str
    pace: Literal["door", "quick"] = "door"


def text_html(value: str) -> str:
    """Escape text so it is shown literally inside a raw HTML Markdown block."""

    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    escaped = (
        escape(line, quote=True).replace("$", "&#36;").replace(":", "&#58;") for line in lines
    )
    return "<br/>".join(escaped)


def facade_markup(state: str, knocks: int = 0) -> str:
    """Door scene; alternating the wrapper lets each empty «Entrar» rattle again."""

    return f'<div class="golpe-{knocks % 2}">{_one_line(facade_html(state))}</div>'


def door_hint_markup(message: str = DOOR_HINT) -> str:
    return f'<div class="umbral-aviso"><p class="aviso" role="alert">{text_html(message)}</p></div>'


def identity_markup(name: str) -> str:
    return f'<div class="quien"><span class="nombre">{text_html(name)}</span></div>'


def command_row_markup(command: str) -> str:
    """Command with arguments, shown for typing; arguments are dimmed and italic."""

    base, *arguments = command.split(" <")
    shown = text_html(base) + "".join(
        f' <span class="arg">&lt;{text_html(argument.rstrip(">"))}&gt;</span>'
        for argument in arguments
    )
    return f'<div class="comando estatico"><code>{shown}</code></div>'


def simulated_markup() -> str:
    return f'<div class="simulado-linea"><p class="simulado">{SIMULATED_LABEL}</p></div>'


COMPONENT_LABELS = {
    "camarero": "Camarero",
    "memoria": "Memoria",
    "mcp": "MCP mesas",
    "cocina": "Cocina A2A",
    "foundry_iq": "Foundry IQ",
    "chef": "Chef",
    "especialista": "Especialista",
    "entrega": "Entrega",
    "caja": "Caja",
}
ACTIVITY_TURNS = 3


def activity_markup(messages: Sequence[ChatMessage]) -> str:
    """The system's steps behind the latest messages, newest first."""

    turns = [message for message in messages if message.activity][-ACTIVITY_TURNS:]
    if not turns:
        body = '<p class="capo-vacio">Escribe al camarero para ver qué ocurre por debajo.</p>'
    else:
        body = "".join(_activity_turn(message) for message in reversed(turns))
    return f'<div class="capo-panel" aria-live="polite" aria-label="Bajo el capó">{body}</div>'


def _activity_turn(message: ChatMessage) -> str:
    if message.role == "user":
        text = " ".join(message.text.split())
        title = f"«{text[:38]}…»" if len(text) > 38 else f"«{text}»"
    elif message.role == "cashier":
        title = "Cobro en caja"
    else:
        title = "Entrega en mesa"
    steps = "".join(_activity_step(step) for step in message.activity)
    return (
        f'<section class="capo-turno"><p class="capo-titulo">{text_html(title)}</p>'
        f"<ol>{steps}</ol></section>"
    )


def _activity_step(step: ActivityStep) -> str:
    if step.status == "running":
        timing = "en curso"
    elif step.duration_ms is None:
        timing = ""
    elif step.duration_ms < 1000:
        timing = f"{step.duration_ms} ms"
    else:
        timing = f"{step.duration_ms / 1000:.1f} s".replace(".", ",")
    detail = f'<span class="capo-detalle">{text_html(step.detail)}</span>' if step.detail else ""
    return (
        f'<li class="capo-paso {step.component} {step.status}">'
        f'<span class="capo-componente">{COMPONENT_LABELS[step.component]}</span>'
        f'<span class="capo-tiempo">{timing}</span>'
        f'<span class="capo-etiqueta">{text_html(step.label)}</span>{detail}</li>'
    )


def plan_markup(
    name: str,
    waiter: str,
    *,
    entering: bool = False,
    room: RoomView | None = None,
    seating: SeatingView | None = None,
    walk_elapsed: float | None = None,
    kitchen_active: bool = False,
    kitchen_plan: KitchenPlan | None = None,
    kitchen_served: bool = False,
    served_dishes: int = 0,
    serve_elapsed: float | None = None,
    bill_pending: bool = False,
) -> str:
    classes = "planta-marco entrando" if entering else "planta-marco"
    svg = floor_plan_svg(
        name,
        waiter,
        room=room,
        seating=seating,
        walk_elapsed=walk_elapsed,
        served_dishes=served_dishes,
        serve_elapsed=serve_elapsed,
        bill_pending=bill_pending,
    )
    kitchen = kitchen_plan_svg(kitchen_active, kitchen_plan, kitchen_served)
    return f'<div class="{classes}">{_one_line(svg)}{_one_line(kitchen)}</div>'


def proposal_markup(proposal: SeatingProposal) -> str:
    """The pending place: what it is and how many seats, never an identifier."""

    place = proposal.place
    if place.kind == "bar":
        seats = place.seats
        title = "Barra"
        detail = f"Puesto {seats[0]}" if len(seats) == 1 else f"Puestos {seats[0]} a {seats[-1]}"
        kept = "te los guardo unos minutos" if len(seats) > 1 else "te lo guardo unos minutos"
    else:
        title = place.label
        detail = f"{proposal.party_size} de {place.capacity} asientos"
        kept = "te la guardo unos minutos"
    return (
        '<div class="propuesta-mesa" role="group" aria-label="Propuesta de sitio">'
        f'<p class="titulo">{text_html(title)}</p>'
        f"<p>{text_html(detail)} · {kept}</p></div>"
    )


def bill_card_markup(bill: BillView) -> str:
    """The pending bill above its two buttons: card or cash, never a phrase."""

    return (
        '<div class="cuenta-pendiente" role="group" aria-label="Cuenta pendiente de pago">'
        f'<p class="titulo">Total {text_html(euros(bill.total))}</p>'
        "<p>Elige cómo pagar.</p></div>"
    )


def conversation_markup(view: ConversationView, *, reveal: Reveal | None = None) -> str:
    """The message log in the conversation window, anchored to its latest line."""

    paid = {
        message.cashier.bill_id
        for message in view.messages
        if message.cashier is not None and isinstance(message.cashier.result, Receipt)
    }
    placed: dict[str | None, list[Card]] = {}
    for card in view.cards:
        placed.setdefault(card.after_message_id, []).append(card)
    known = {message.message_id for message in view.messages}
    rows = [_card(card) for card in placed.pop(None, [])]
    for message in view.messages:
        if message.role == "assistant":
            if reveal is not None and reveal.message_id == message.message_id:
                rows.append(_revealed_greeting(message.text, reveal.pace))
            else:
                rows.append(_waiter_row(text_html(message.text)))
        elif message.role == "kitchen":
            rows.append(kitchen_row(message))
        elif message.role == "cashier":
            bill_id = message.cashier.bill_id if message.cashier is not None else None
            state = (
                "pending" if bill_id == view.pending_bill else "paid" if bill_id in paid else "void"
            )
            rows.append(cashier_row(message, state=state))
        else:
            rows.append(_customer_row(message.text))
        rows.extend(_card(card) for card in placed.pop(message.message_id, []))
    rows.extend(_card(card) for anchor, cards in placed.items() if anchor not in known for card in cards)
    if view.outgoing is not None:
        rows.append(_customer_row(view.outgoing, extra=" enviando"))
    rows.extend(_waiter_row(text_html(text), extra=" provisional") for _, text in view.provisional)
    if view.waiting:
        rows.append(_typing_row())
    return (
        '<div class="mensajes" role="log" aria-live="polite" '
        'aria-label="Conversación con el camarero">'
        f'<div class="hilo">{"".join(rows)}</div></div>'
    )


def _waiter_row(body: str, *, extra: str = "", hidden: bool = False) -> str:
    aria = ' aria-hidden="true"' if hidden else ""
    return (
        f'<div class="msg camarero{extra}"{aria}>{waiter_icon_svg()}'
        f'<div class="burbuja">{body}</div></div>'
    )


def chef_icon_svg() -> str:
    """The chef's toque, white on the kitchen's blue, for the conversation."""

    return (
        '<svg class="icono" viewBox="-16 -16 32 32" aria-hidden="true" focusable="false">'
        '<path d="M-8.5 4.5 C-14.5 4.5 -15 -5.5 -8.5 -6 C-8 -13.5 1 -14.5 3 -9.5 '
        'C6.5 -13 13.5 -10 11.5 -4.5 C15.5 -2.5 13.5 4.5 8.5 4.5 Z" '
        'fill="#fbf8f1" stroke="#0e2747" stroke-width="1.4" stroke-linejoin="round"/>'
        '<path d="M-3.5 4 C-4.2 0.5 -3.6 -2.5 -2.2 -5 M3 4 C3.6 0.8 3.4 -1.8 2.2 -4.2" '
        'fill="none" stroke="#7d93b3" stroke-width="1.1" stroke-linecap="round"/>'
        '<rect x="-8.5" y="3.5" width="17" height="7.5" rx="1.6" '
        'fill="#fbf8f1" stroke="#0e2747" stroke-width="1.4"/>'
        '<path d="M-8.5 7.2 H8.5" stroke="#7d93b3" stroke-width="1.1"/></svg>'
    )


def kitchen_row(message: ChatMessage) -> str:
    """The chef's own bubble: the kitchen's plan or why it could not make one."""

    report = message.kitchen
    if report is None:
        body = f"<p>{text_html(message.text)}</p>"
    elif isinstance(report.result, KitchenFailure):
        body = f'<p class="titular">{KITCHEN_FAILED}</p><p>{text_html(report.result.message)}</p>'
    else:
        body = _plan_body(report.result)
    return (
        f'<div class="msg cocina">{chef_icon_svg()}'
        f'<div class="burbuja plan-cocina" role="group" aria-label="{KITCHEN_LABEL}">'
        f"{body}</div></div>"
    )


def cashier_icon_svg() -> str:
    """The till, white on the cashier's green, drawn like the chef's toque."""

    return (
        '<svg class="icono" viewBox="-16 -16 32 32" aria-hidden="true" focusable="false">'
        '<path d="M2.5 -6 V-3" stroke="#0f2e20" stroke-width="1.6"/>'
        '<rect x="-3" y="-13" width="12" height="7" rx="1.4" '
        'fill="#fbf8f1" stroke="#0f2e20" stroke-width="1.4"/>'
        '<path d="M-0.5 -9.5 H6" stroke="#6f9c80" stroke-width="1.2" stroke-linecap="round"/>'
        '<path d="M-11.5 -3 H11.5 L13 5 H-13 Z" '
        'fill="#fbf8f1" stroke="#0f2e20" stroke-width="1.4" stroke-linejoin="round"/>'
        '<path d="M-8 -0.2 H-6 M-3.5 -0.2 H-1.5 M1 -0.2 H3 M-8.5 2.4 H-6.5 M-4 2.4 H-2 M0.5 2.4 H2.5" '
        'stroke="#6f9c80" stroke-width="1.4" stroke-linecap="round"/>'
        '<rect x="6.2" y="-1.4" width="4" height="4.8" rx=".8" fill="#6f9c80"/>'
        '<rect x="-13" y="5" width="26" height="7" rx="1.6" '
        'fill="#fbf8f1" stroke="#0f2e20" stroke-width="1.4"/>'
        '<path d="M-3 8.5 H3" stroke="#6f9c80" stroke-width="1.4" stroke-linecap="round"/></svg>'
    )


def cashier_row(message: ChatMessage, *, state: str = "void") -> str:
    """The cashier's own bubble: the bill, the receipt or why caja could not do it.

    ``state`` says what became of a bill: ``pending`` (waiting for card or
    cash), ``paid`` or ``void`` (superseded by new dishes or closed by caja).
    """

    report = message.cashier
    extra = ""
    if report is None:
        body = f"<p>{text_html(message.text)}</p>"
    elif isinstance(report.result, CashierFailure):
        action = "preparar la cuenta" if report.request is not None else "cobrar"
        body = (
            f'<p class="titular">{text_html(f"Caja no ha podido {action}.")}</p>'
            f"<p>{text_html(report.result.message)}</p>"
        )
    elif isinstance(report.result, Receipt):
        body = _receipt_body(report.result)
    else:
        body = _bill_body(report.result, state)
        extra = f" {state}"
    return (
        f'<div class="msg caja{extra}">{cashier_icon_svg()}'
        f'<div class="burbuja cuenta-caja" role="group" aria-label="{CASHIER_LABEL}">'
        f"{body}</div></div>"
    )


def _bill_body(bill: Bill, state: str) -> str:
    lines = "".join(_bill_line(line) for line in bill.lines)
    parts = [
        '<p class="titular">Cuenta</p>',
        f'<ul class="lineas">{lines}</ul>',
        f'<p class="total"><span>Total</span><span class="importe">{text_html(euros(bill.total))}</span></p>',
        f'<p class="nota">{text_html(bill.note)}</p>',
    ]
    if bill.stage is BillStage.AWAITING_REVIEW:
        parts.append('<p class="estado">Pendiente de revisión en caja.</p>')
    else:
        parts.append(f'<p class="estado">{text_html(BILL_STATES[state])}</p>')
    cited = " · ".join(_bill_source(source) for source in bill.sources)
    parts.append(f'<p class="fuentes">{text_html("Fuentes: " + cited)}</p>')
    return "".join(parts)


def _bill_line(line: BillLine) -> str:
    unit = (
        f'<span class="unidad">{text_html(euros(line.unit_price) + " cada una")}</span>'
        if line.quantity > 1
        else ""
    )
    return (
        f'<li><span class="concepto">{text_html(f"{line.quantity} × {line.name}")}{unit}</span>'
        f'<span class="importe">{text_html(euros(line.line_total))}</span></li>'
    )


def _bill_source(source: BillSource) -> str:
    return f"{source.document} (versión {source.version})" if source.version else source.document


def _receipt_body(receipt: Receipt) -> str:
    method = PAYMENT_LABELS[receipt.method].lower()
    when = receipt.paid_at.strftime("%d/%m/%Y %H:%M UTC")
    return (
        f'<p class="titular">{text_html(f"Pagado con {method}")}</p>'
        f'<p class="total"><span>Total</span><span class="importe">{text_html(euros(receipt.amount))}</span></p>'
        f'<p class="nota">{text_html(f"Referencia {receipt.reference} · {when}")}</p>'
    )


def _plan_body(plan: KitchenPlan) -> str:
    parts = [f'<p class="titular">{KITCHEN_VERDICTS[plan.verdict]}</p>']
    if plan.accepted:
        parts.append(_section("Listo para servir", [_accepted(item) for item in plan.accepted]))
    if plan.rejected:
        parts.append(
            _section(
                "Rechazado",
                [_item(f"{item.quantity} × {item.requested}", [item.reason]) for item in plan.rejected],
            )
        )
    if plan.warnings:
        parts.append(_section("Avisos", [_item(warning) for warning in plan.warnings]))
    if plan.stations:
        parts.append(
            _section(
                "Partidas",
                [
                    _item(STATION_LABELS[station.station], [_task(task) for task in station.tasks])
                    for station in plan.stations
                ],
            )
        )
    if plan.sources:
        cited = " · ".join(_source(source) for source in plan.sources)
        parts.append(f'<p class="fuentes">{text_html("Fuentes: " + cited)}</p>')
    return "".join(parts)


def _section(title: str, items: list[str]) -> str:
    return f'<p class="apartado">{text_html(title)}</p><ul>{"".join(items)}</ul>'


def _item(head: str, details: Sequence[str] = ()) -> str:
    lines = "".join(f'<span class="detalle">{text_html(detail)}</span>' for detail in details if detail)
    return f'<li><span class="plato">{text_html(head)}</span>{lines}</li>'


def _accepted(item: AcceptedItem) -> str:
    station = STATION_LABELS[item.station]
    if not item.allergens_verified:
        allergens = "alérgenos pendientes de verificar"
    else:
        allergens = "alérgenos: " + (", ".join(item.allergens) or "ninguno de los 14")
        if item.traces:
            allergens += "; puede contener " + ", ".join(item.traces)
    detail = " · ".join([station, *item.adaptations, allergens])
    return _item(f"{item.quantity} × {item.name}", [detail])


def _task(task: StationTask) -> str:
    text = f"{task.quantity} × {task.name}"
    if task.steps:
        text += ". " + " ".join(_sentence(step) for step in task.steps)
    if task.omit:
        text += " Omitir: " + _sentence(", ".join(task.omit))
    if task.precautions:
        text += " Precauciones: " + " ".join(_sentence(item) for item in task.precautions)
    return text


def _source(source: KitchenSource) -> str:
    details = ", ".join(
        detail for detail in (f"versión {source.version}" if source.version else "", source.detail or "") if detail
    )
    return f"{source.document} ({details})" if details else source.document


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def _customer_row(text: str, *, extra: str = "") -> str:
    return f'<div class="msg cliente{extra}"><div class="burbuja">{text_html(text)}</div></div>'


def _typing_row() -> str:
    return (
        f'<div class="msg camarero">{waiter_icon_svg()}'
        f'<div class="burbuja escribiendo" role="status" aria-label="{TYPING_LABEL}">'
        "<i></i><i></i><i></i></div></div>"
    )


def _revealed_greeting(text: str, pace: str) -> str:
    dots = (
        f'<div class="msg camarero aguarda" aria-hidden="true">{waiter_icon_svg()}'
        '<div class="burbuja escribiendo"><i></i><i></i><i></i></div></div>'
    )
    greeting = _waiter_row(text_html(text), extra=" saludo")
    return f'<div class="pila revelar-{pace}">{dots}{greeting}</div>'


def _card(card: Card) -> str:
    parts = [f"<p>{text_html(card.title)}</p>"]
    if card.note:
        parts.append(f'<p class="nota">{text_html(card.note)}</p>')
    return f'<div class="tarjeta">{"".join(parts)}</div>'


def _one_line(markup: str) -> str:
    return " ".join(markup.split("\n"))
