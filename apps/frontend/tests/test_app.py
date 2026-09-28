from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "frontend" / "app.py"
GREETING = "Hombre, Ana, ¿qué tal, maja?"


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
    assert at.button(key="cmd-memory").label == "/memory"


def test_messages_and_memory_commands(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "/memory")
    assert "Aún no recuerdo nada de ti." in _markup(at)
    at = _say(at, "Prefiero el agua con gas y soy alérgica a los frutos secos")
    assert not at.exception
    assert "Apuntado&#58; agua con gas." in _markup(at)
    at = at.button(key="cmd-memory").click().run()
    markup = _markup(at)
    assert "Lo que recuerdo de ti&#58;" in markup
    assert "<code>m1</code> Preferencia: agua con gas" in markup
    assert "<code>m2</code> Alergia o restricción: frutos secos" in markup
    at = _say(at, "/memory delete m1")
    assert "He borrado m1." in _markup(at)
    at = at.button(key="cmd-memory-clear").click().run()
    assert "He olvidado todo lo que sabía de ti." in _markup(at)
    at = _say(at, "/pedir")
    assert "Ese comando no lo conozco" in _markup(at)


def test_new_visit_greets_again_and_keeps_memories(app) -> None:
    at = _enter(app, "Ana").run()
    at = _say(at, "Prefiero la tortilla")
    at = at.button(key="cmd-new").click().run()
    markup = _markup(at)
    assert "revelar-quick" in markup and "Prefiero la tortilla" not in markup
    at = _say(at, "/memory")
    assert "tortilla" in _markup(at)


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
