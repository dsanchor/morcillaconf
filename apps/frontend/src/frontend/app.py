"""Customer view: the door, the dining room and the waiter behind the BFF.

A single Streamlit view driven by ``st.session_state``: ``outside`` (the
closed door and the name form), ``opening`` (one render with the entrance
choreography) and ``inside`` (commands, conversation and plan). The view only
talks to the BffClient contract and renders confirmed snapshots.
"""

from __future__ import annotations

import streamlit as st

from restaurant_contracts.application import Action
from restaurant_contracts.client import BffClientError

from frontend.config import ClientConfigurationError, FrontendSettings, client_connector
from frontend.markup import (
    Reveal,
    command_row_markup,
    conversation_markup,
    door_hint_markup,
    facade_markup,
    identity_markup,
    plan_markup,
    simulated_markup,
)
from frontend.slash_commands import SlashCommand, parse_slash_command
from frontend.stylesheets import base_stylesheet, stage_stylesheet
from frontend.visit import VisitSession

OUTSIDE, OPENING, INSIDE = "outside", "opening", "inside"
ARRIVING_NOTICE = "Un momento, que ya te abrimos."
SIDEBAR_BUTTONS = {
    "new": ("/new", "Nueva visita", Action.ARRIVE),
    "memory": ("/memory", "Lo que recuerdo de ti", Action.READ_MEMORY),
    "memory-clear": ("/memory clear", "Olvidar todo", Action.CLEAR_MEMORY),
    "exit": ("/exit", "Salir", None),
}
SIDEBAR_ORDER = (
    "new",
    "memory",
    "/memory correct <id> <texto>",
    "/memory delete <id>",
    "memory-clear",
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
    with st.container(key="sala", gap=10):
        with st.container(key="ventana", gap=None):
            window = st.empty()
            text = st.chat_input("Escribe al camarero", key="redactor", max_chars=2_000)
        if state.get("simulated", True):
            st.markdown(simulated_markup(), unsafe_allow_html=True)
        st.markdown(
            plan_markup(visit.name, "llegando" if opening else "atendiendo", entering=opening),
            unsafe_allow_html=True,
        )
    if opening:
        with st.container(key="puerta"):
            st.markdown(facade_markup("abriendo"), unsafe_allow_html=True)

    def render() -> None:
        greeting = visit.greeting_id
        reveal = Reveal(greeting, reveal_pace) if reveal_pace and greeting else None
        window.markdown(conversation_markup(visit.view(), reveal=reveal), unsafe_allow_html=True)

    if text is not None:
        command = parse_slash_command(text)
        if command is None:
            reveal_pace = None
            render()
            visit.send_message(text, on_update=render)
        else:
            reveal_pace = _apply_slash(visit, command) or reveal_pace
    render()


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
        _leave()
        st.rerun()
    if command.action == "new":
        visit.arrive()
        return "quick"
    if command.action == "memory":
        visit.read_memory()
    elif command.action == "memory-clear":
        visit.clear_memory()
    elif command.action == "memory-delete" and command.memory_id:
        visit.delete_memory(command.memory_id)
    elif command.action == "memory-correct" and command.memory_id and command.value:
        visit.correct_memory(command.memory_id, command.value)
    else:
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
    if action == "exit" or visit is None:
        _leave()
    elif action == "new":
        visit.arrive()
        state.reveal = "quick"
    elif action == "memory":
        visit.read_memory()
    elif action == "memory-clear":
        visit.clear_memory()


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
