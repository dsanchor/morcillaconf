import pytest

from frontend.slash_commands import SlashCommand, parse_slash_command


@pytest.mark.parametrize(
    "text",
    ["Hola", "  ponme una morcilla  ", "", "a/b", "/memory", "/memory clear"],
)
def test_plain_text_is_a_message(text) -> None:
    assert parse_slash_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/new", SlashCommand("new")),
        ("  /exit ", SlashCommand("exit")),
    ],
)
def test_known_commands_are_parsed(text, expected) -> None:
    assert parse_slash_command(text) == expected


@pytest.mark.parametrize(
    "text",
    ["/menu", "/new visita"],
)
def test_malformed_commands_are_unknown(text) -> None:
    assert parse_slash_command(text) == SlashCommand("unknown")
