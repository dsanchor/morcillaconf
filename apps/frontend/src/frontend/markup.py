"""HTML fragments of the Streamlit view; every dynamic text is escaped here.

Streamlit renders them with ``st.markdown(..., unsafe_allow_html=True)``
because ``st.html`` sanitizes SVG away. Each fragment is one line that starts
with ``<div`` so Markdown treats it as a raw HTML block and never parses the
customer's text as Markdown, math or shortcodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

from restaurant_contracts.memory import MemoryKind

from frontend.facade import facade_html
from frontend.floor_plan import floor_plan_svg, waiter_icon_svg
from frontend.visit import Card, ConversationView

KIND_LABELS = {
    MemoryKind.PREFERENCE: "Preferencia",
    MemoryKind.RESTRICTION: "Alergia o restricción",
}
DOOR_HINT = "Dinos tu nombre y te abrimos."
SIMULATED_LABEL = "Camarero simulado"
TYPING_LABEL = "El camarero está escribiendo"


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


def plan_markup(name: str, waiter: str, *, entering: bool = False) -> str:
    classes = "planta-marco entrando" if entering else "planta-marco"
    return f'<div class="{classes}">{_one_line(floor_plan_svg(name, waiter))}</div>'


def conversation_markup(view: ConversationView, *, reveal: Reveal | None = None) -> str:
    """The message log in the conversation window, anchored to its latest line."""

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
    if card.memories:
        items = "".join(
            f"<li><code>{text_html(memory.memory_id)}</code> "
            f"{KIND_LABELS[memory.kind]}: {text_html(memory.value)}</li>"
            for memory in card.memories
        )
        parts.append(f"<ul>{items}</ul>")
    if card.note:
        parts.append(f'<p class="nota">{text_html(card.note)}</p>')
    return f'<div class="tarjeta">{"".join(parts)}</div>'


def _one_line(markup: str) -> str:
    return " ".join(markup.split("\n"))
