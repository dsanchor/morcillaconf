import pytest

from frontend.slash_commands import SlashCommand, parse_slash_command


@pytest.mark.parametrize("text", ["Hola", "  ponme una morcilla  ", "", "a/b"])
def test_plain_text_is_a_message(text) -> None:
    assert parse_slash_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/new", SlashCommand("new")),
        ("  /exit ", SlashCommand("exit")),
        ("/memory", SlashCommand("memory")),
        ("/memory list", SlashCommand("memory")),
        ("/MEMORY Clear", SlashCommand("memory-clear")),
        ("/memory delete m2", SlashCommand("memory-delete", memory_id="m2")),
        (
            "/memory correct m1  agua   con gas",
            SlashCommand("memory-correct", memory_id="m1", value="agua con gas"),
        ),
    ],
)
def test_known_commands_are_parsed(text, expected) -> None:
    assert parse_slash_command(text) == expected


@pytest.mark.parametrize(
    "text",
    ["/menu", "/new visita", "/memory delete", "/memory delete m1 m2", "/memory correct m1", "/memory forget"],
)
def test_malformed_commands_are_unknown(text) -> None:
    assert parse_slash_command(text) == SlashCommand("unknown")
