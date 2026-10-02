from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "frontend" / "app.py"
GREETING = "Hombre, Ana, ¿qué tal, maja? ¿Has venido sola o acompañada?"


@pytest.fixture
def app(monkeypatch) -> AppTest:
    monkeypatch.setenv("FRONTEND_BFF_CLIENT", "fake")
    monkeypatch.setenv("FRONTEND_FAKE_PAUSE_SECONDS", "0")
    return AppTest.from_file(str(APP), default_timeout=15).run()


def _markup(at: AppTest) -> str:
    return "\n".join(element.value for element in at.markdown)


def _enter(at: AppTest, name: str) -> AppTest:
    at.text_input(key="name").input(name)
    return at.button(key="enter").click().run()


def _say(at: AppTest, text: str) -> AppTest:
    return at.chat_input(key="redactor").set_value(text).run()


def test_empty_name_keeps_the_door_closed_with_a_hint(app) -> None:
    assert not app.exception
    assert 'class="escena cerrada"' in _markup(app)
    at = app.button(key="enter").click().run()
    assert not at.exception
    markup = _markup(at)
    assert 'class="escena llama"' in markup
    assert "Dinos tu nombre y te abrimos." in markup
    assert at.session_state["stage"] == "outside"
    assert not at.chat_input


def test_a_name_opens_the_door_and_the_waiter_greets(app) -> None:
    at = _enter(app, "Ana")
    assert not at.exception
    markup = _markup(at)
    assert 'class="escena abriendo"' in markup
    assert 'class="planta llegando"' in markup
    assert "revelar-door" in markup and GREETING in markup
    assert "Camarero simulado" in markup
    assert at.session_state["stage"] == "inside"
    at = at.run()
    markup = _markup(at)
    assert "escena" not in markup
    assert 'class="planta atendiendo"' in markup
    assert GREETING in markup and "revelar-" not in markup
    assert at.button(key="cmd-new").label == "/new"
    assert at.button(key="cmd-exit").label == "/exit"


def test_messages_and_unknown_commands(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Prefiero el agua con gas y soy alérgica a los frutos secos")
    assert not at.exception
    assert "Apuntado&#58; agua con gas." in _markup(at)
    at = _say(at, "/pedir")
    assert "Ese comando no lo conozco" in _markup(at)


def test_new_visit_greets_again(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Prefiero la tortilla")
    at = at.button(key="cmd-new").click().run()
    markup = _markup(at)
    assert "revelar-quick" in markup and "Prefiero la tortilla" not in markup
    assert GREETING in markup


def test_exit_returns_to_the_closed_door(app) -> None:
    at = _enter(app, "Ana").run()
    at = at.button(key="cmd-exit").click().run()
    assert at.session_state["stage"] == "outside"
    assert 'class="escena cerrada"' in _markup(at)
    at = _enter(at, "Luis").run()
    at = _say(at, "/exit")
    assert not at.exception
    assert 'class="escena cerrada"' in _markup(at)


def test_entering_again_with_the_same_name_resumes_the_visit(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Prefiero la tortilla")
    at = at.button(key="cmd-exit").click().run()
    at = _enter(at, "Ana").run()
    markup = _markup(at)
    assert "Prefiero la tortilla" in markup
    assert markup.count(GREETING) == 1


def test_an_unreachable_bff_keeps_the_door_closed_with_a_notice(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_BFF_CLIENT", "http")
    monkeypatch.setenv("FRONTEND_BFF_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FRONTEND_BFF_TIMEOUT_SECONDS", "2")
    at = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not at.exception
    at = _enter(at, "Ana")
    assert not at.exception
    assert at.session_state["stage"] == "outside"
    assert "No consigo hablar con el restaurante ahora mismo" in _markup(at)


def test_a_seating_proposal_is_decided_with_buttons(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Venimos tres")
    assert not at.exception
    markup = _markup(at)
    assert "Mesa 3" in markup and "3 de 4 asientos" in markup
    assert "asiento-propio" in markup
    confirm = next(button for button in at.button if button.label == "Confirmar")
    at = confirm.click().run()
    assert not at.exception
    markup = _markup(at)
    assert "Os acompaño a la Mesa 3." in markup
    assert "comensal cliente-propio caminando" in markup and markup.count("acompanante") == 2
    assert not [button for button in at.button if button.label in ("Confirmar", "Rechazar")]
    at = _say(at, "/new")
    assert "Ya estáis sentados en la Mesa 3" in _markup(at)


def test_rejecting_a_proposal_removes_the_card(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Nos sentamos en la barra, somos 2")
    assert "Puestos 1 a 2" in _markup(at)
    reject = next(button for button in at.button if button.label == "Rechazar")
    at = reject.click().run()
    assert not at.exception
    assert "dejo libre la barra, puestos 1 a 2" in _markup(at)
    assert not [button for button in at.button if button.label == "Confirmar"]


def test_the_chefs_plan_appears_between_the_order_and_the_waiters_reply(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Pido una morcilla a la brasa y una hamburguesa sin queso")
    assert not at.exception
    markup = _markup(at)
    order = markup.index("Pido una morcilla a la brasa")
    chef = markup.index('class="msg cocina"')
    reply = markup.index("Cocina ha terminado 1 × Morcilla de Burgos a la brasa.")
    assert order < chef < reply
    assert 'aria-label="Platos cocinados"' in markup
    assert "Los platos posibles ya están cocinados." in markup and "No está en la carta." in markup
    # The toque, not the waiter seen from above.
    assert markup[chef:].split("</svg>", 1)[0].count("<path") == 3


def test_the_plan_is_drawn_before_the_waiter_answers(app, monkeypatch) -> None:
    import frontend.markup as markup
    from frontend.visit import VisitSession

    events: list[str] = []
    draw, send = markup.plan_markup, VisitSession.send_message

    def spy_plan(*args, **kwargs):
        events.append("plan")
        return draw(*args, **kwargs)

    def spy_send(self, *args, **kwargs):
        events.append("send")
        return send(self, *args, **kwargs)

    monkeypatch.setattr(markup, "plan_markup", spy_plan)
    monkeypatch.setattr(VisitSession, "send_message", spy_send)
    at = _enter(app, "Ana").run()
    events.clear()
    at = _say(at, "Venimos tres")
    assert not at.exception
    assert events[:2] == ["plan", "send"]
    # The new proposal is drawn right after the turn, not a refresh later.
    assert events[-1] == "plan" and events.count("plan") >= 2
    assert "asiento-propio" in _markup(at)
