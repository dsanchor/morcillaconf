"""Customer view: the door, the dining room and the waiter behind the BFF.

A single Streamlit view driven by ``st.session_state``: ``outside`` (the
closed door and the name form), ``opening`` (one render with the entrance
choreography) and ``inside`` (commands, conversation and plan). The view only
talks to the BffClient contract and renders confirmed snapshots.
"""

from __future__ import annotations

import time

import streamlit as st

from restaurant_contracts.application import Action
from restaurant_contracts.client import BffClientError

from frontend.config import ClientConfigurationError, FrontendSettings, client_connector
from frontend.markup import (
    Reveal,
    activity_markup,
    command_row_markup,
    conversation_markup,
    door_hint_markup,
    facade_markup,
    identity_markup,
    plan_markup,
    proposal_markup,
    simulated_markup,
)
from frontend.slash_commands import SlashCommand, parse_slash_command
from frontend.stylesheets import base_stylesheet, stage_stylesheet
from frontend.visit import VisitSession

OUTSIDE, OPENING, INSIDE = "outside", "opening", "inside"
ARRIVING_NOTICE = "Un momento, que ya te abrimos."
# The plan refreshes by itself so parallel customers see each other.
PLAN_REFRESH_SECONDS = 3
ENTRANCE_SECONDS = 4.5
SIDEBAR_BUTTONS = {
    "new": ("/new", "Nueva visita", Action.ARRIVE),
    "exit": ("/exit", "Salir", Action.END_VISIT),
}
SIDEBAR_ORDER = (
    "new",
    "exit",
)


def main() -> None:
    st.set_page_config(
        page_title="Entrada al restaurante", layout="wide", initial_sidebar_state="locked"
    )
    _style(base_stylesheet())
    state = st.session_state
    if "connect" not in state:
        try:
            state.connect = client_connector(FrontendSettings.from_env())
        except ClientConfigurationError as exc:
            st.error(str(exc))
            st.stop()
    if "stage" not in state:
        state.stage = OUTSIDE
        state.knocks = 0
    if state.stage != OUTSIDE and state.get("visit") is None:
        _leave()
    if state.stage == OUTSIDE:
        _render_outside()
    elif state.stage == OPENING:
        state.stage = INSIDE
        _render_inside(opening=True)
    else:
        _render_inside(opening=False)


def _render_outside() -> None:
    state = st.session_state
    knocks = state.knocks
    notice = state.get("door_notice")
    _style(stage_stylesheet(OUTSIDE, knocked=knocks > 0))
    with st.container(key="puerta"):
        st.markdown(facade_markup("llama" if knocks else "cerrada", knocks), unsafe_allow_html=True)
    with st.container(key="umbral", gap=10):
        if notice:
            st.markdown(door_hint_markup(notice), unsafe_allow_html=True)
        elif knocks:
            st.markdown(door_hint_markup(), unsafe_allow_html=True)
        with st.form("puerta-nombre", border=False, enter_to_submit=True):
            with st.container(horizontal=True, key="umbral-fila", gap=10, vertical_alignment="center"):
                st.text_input(
                    "Tu nombre",
                    key="name",
                    max_chars=40,
                    placeholder="Tu nombre",
                    label_visibility="collapsed",
                    autocomplete="given-name",
                )
                st.form_submit_button("Entrar", key="enter", type="primary", on_click=_enter)


def _render_inside(*, opening: bool) -> None:
    state = st.session_state
    visit: VisitSession = state.visit
    if opening:
        _style(stage_stylesheet(OPENING))
    visit.resolve_pending()
    reveal_pace = "door" if opening else state.pop("reveal", None)
    with st.sidebar:
        with st.container(key="lateral", gap=26):
            st.markdown(identity_markup(visit.name), unsafe_allow_html=True)
            with st.container(key="comandos", gap=2):
                for item in SIDEBAR_ORDER:
                    if item in SIDEBAR_BUTTONS:
                        _command_button(visit, item)
                    else:
                        st.markdown(command_row_markup(item), unsafe_allow_html=True)
            with st.container(key="capo", gap=8):
                st.toggle("Bajo el capó", key="capo_visible", value=True)
                activity = st.empty()
    if opening:
        state.opened_at = time.monotonic()
    with st.container(key="sala", gap=10):
        with st.container(key="ventana", gap=None):
            window = st.empty()
            card = st.empty()
            text = st.chat_input("Escribe al camarero", key="redactor", max_chars=2_000)
        if state.get("simulated", True):
            st.markdown(simulated_markup(), unsafe_allow_html=True)
        # Drawn before any blocking work (a waiter turn, /new) so it never
        # disappears while the waiter thinks.
        _live_plan(opening)
    if opening:
        with st.container(key="puerta"):
            st.markdown(facade_markup("abriendo"), unsafe_allow_html=True)

    def render() -> None:
        greeting = visit.greeting_id
        reveal = Reveal(greeting, reveal_pace) if reveal_pace and greeting else None
        window.markdown(conversation_markup(visit.view(), reveal=reveal), unsafe_allow_html=True)
        if state.get("capo_visible", True):
            activity.markdown(activity_markup(visit.view().messages), unsafe_allow_html=True)
        else:
            activity.empty()

    seating_before = _seating_of(visit)
    if text is not None:
        command = parse_slash_command(text)
        if command is None:
            reveal_pace = None
            render()
            visit.send_message(text, on_update=render)
        else:
            reveal_pace = _apply_slash(visit, command) or reveal_pace
    render()
    _proposal_card(card, visit)
    if text is not None and _seating_of(visit) != seating_before:
        # The plan was drawn before the turn: redraw it now with the new place.
        if reveal_pace:
            state.reveal = reveal_pace
        st.rerun()


def _seating_of(visit: VisitSession) -> object:
    snapshot = visit.snapshot
    return None if snapshot is None else (snapshot.conversation_id, snapshot.seating)


def _proposal_card(slot, visit: VisitSession) -> None:
    """The pending place with its two explicit decisions; a phrase never confirms."""

    snapshot = visit.snapshot
    proposal = snapshot.seating.proposal if snapshot is not None else None
    if proposal is None or not visit.allows(Action.DECIDE_TABLE):
        slot.empty()
        return
    with slot.container(key="propuesta"):
        st.markdown(proposal_markup(proposal), unsafe_allow_html=True)
        with st.container(horizontal=True, key="propuesta-botones", gap=10):
            st.button(
                "Confirmar",
                key=f"mesa-si-{proposal.proposal_id}",
                type="primary",
                on_click=_decide_table,
                args=("confirmed",),
            )
            st.button(
                "Rechazar",
                key=f"mesa-no-{proposal.proposal_id}",
                type="secondary",
                on_click=_decide_table,
                args=("rejected",),
            )


@st.fragment(run_every=PLAN_REFRESH_SECONDS)
def _live_plan(opening: bool) -> None:
    """The plan reads the room by itself, without touching conversation or composer."""

    state = st.session_state
    visit: VisitSession | None = state.get("visit")
    if visit is None or visit.snapshot is None:
        return
    if visit.refresh_room() or visit.refresh_service():
        st.rerun()
    entering = opening and time.monotonic() - state.get("opened_at", 0.0) < ENTRANCE_SECONDS
    seating = visit.snapshot.seating
    waiter = "llegando" if entering else "barra" if seating.status == "seated" else "atendiendo"
    cooking = (
        visit.snapshot.process_status == "processing"
        and bool(visit.snapshot.order_draft.items)
    )
    cooked_plans = [
        message.kitchen.result
        for message in visit.snapshot.messages
        if message.kitchen is not None
        and message.kitchen.result.status == "cooked"
        and message.kitchen.result.accepted
    ]
    cooked = cooked_plans[-1] if cooked_plans else None
    served = set(visit.snapshot.served_orders)
    served_dishes = sum(
        item.quantity
        for plan in cooked_plans
        if plan.order_id in served
        for item in plan.accepted
    )
    st.markdown(
        plan_markup(
            visit.name,
            waiter,
            entering=entering,
            room=visit.room,
            seating=seating,
            walk_elapsed=visit.walk_elapsed(),
            kitchen_active=cooking,
            kitchen_plan=None if cooking else cooked,
            kitchen_served=cooked is not None and cooked.order_id in served,
            served_dishes=served_dishes,
            serve_elapsed=visit.serve_elapsed(),
        ),
        unsafe_allow_html=True,
    )


def _decide_table(decision: str) -> None:
    visit: VisitSession | None = st.session_state.get("visit")
    if visit is not None:
        visit.decide_table(decision)


def _command_button(visit: VisitSession, action: str) -> None:
    label, help_text, contract_action = SIDEBAR_BUTTONS[action]
    st.button(
        label,
        key=f"cmd-{action}",
        help=help_text,
        type="tertiary",
        width="stretch",
        disabled=contract_action is not None and not visit.allows(contract_action),
        on_click=_run_command,
        args=(action,),
    )


def _apply_slash(visit: VisitSession, command: SlashCommand) -> str | None:
    """Run a typed command; returns the greeting reveal pace for a new visit."""

    if command.action == "exit":
        if visit.exit():
            _leave()
            st.rerun()
        return None
    if command.action == "new":
        visit.arrive()
        return "quick"
    visit.reject_unknown_command()
    return None


def _enter() -> None:
    state = st.session_state
    name = " ".join(str(state.get("name") or "").split())
    state.door_notice = None
    if not name:
        state.knocks += 1
        return
    arriving = state.get("arriving")
    if arriving is not None and arriving[0] == name:
        visit = arriving[1]
        visit.resolve_pending()
    else:
        try:
            client = state.connect(name)
        except BffClientError as exc:
            state.door_notice = exc.error.message
            return
        state.simulated = client.simulated
        visit = VisitSession(client)
        # The same name resumes its active visit: reloading or leaving and
        # entering again never opens a new one. Only /new does.
        resume = client.active_visit_id
        visit.arrive(resume)
        if resume is not None and visit.snapshot is None and visit.pending is None:
            visit.cards.clear()
            visit.arrive()
    state.arriving = None
    if visit.snapshot is None:
        if visit.pending is not None:
            state.arriving = (name, visit)
            state.door_notice = ARRIVING_NOTICE
        elif visit.cards:
            state.door_notice = visit.cards[-1].title
        return
    state.visit = visit
    state.knocks = 0
    state.stage = OPENING


def _run_command(action: str) -> None:
    state = st.session_state
    visit: VisitSession | None = state.get("visit")
    if visit is None:
        _leave()
    elif action == "exit":
        if visit.exit():
            _leave()
    elif action == "new":
        visit.arrive()
        state.reveal = "quick"


def _leave() -> None:
    state = st.session_state
    state.stage = OUTSIDE
    state.visit = None
    state.knocks = 0
    state.door_notice = None
    state.arriving = None
    state.pop("reveal", None)


def _style(css: str) -> None:
    if css.strip():
        st.html(f"<style>{css}</style>")


if __name__ == "__main__":
    main()
