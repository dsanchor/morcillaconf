"""Slash commands typed in the conversation, parsed without touching the client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SlashAction = Literal["new", "exit", "unknown"]

COMMAND_LIST = (
    "/new",
    "/exit",
)


@dataclass(frozen=True)
class SlashCommand:
    action: SlashAction


def parse_slash_command(text: str) -> SlashCommand | None:
    """Return the command for text starting with «/», or ``None`` for a message."""

    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return None
    head = parts[0].casefold()
    if head == "/new" and len(parts) == 1:
        return SlashCommand("new")
    if head == "/exit" and len(parts) == 1:
        return SlashCommand("exit")
    if head == "/memory":
        return None
    return SlashCommand("unknown")
