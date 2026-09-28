"""Slash commands typed in the conversation, parsed without touching the client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SlashAction = Literal[
    "new", "exit", "memory", "memory-correct", "memory-delete", "memory-clear", "unknown"
]

COMMAND_LIST = (
    "/new",
    "/memory",
    "/memory correct <id> <texto>",
    "/memory delete <id>",
    "/memory clear",
    "/exit",
)


@dataclass(frozen=True)
class SlashCommand:
    action: SlashAction
    memory_id: str | None = None
    value: str | None = None


def parse_slash_command(text: str) -> SlashCommand | None:
    """Return the command for text starting with «/», or ``None`` for a message."""

    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return None
    head = parts[0].casefold()
    sub = parts[1].casefold() if len(parts) > 1 else None
    if head == "/new" and len(parts) == 1:
        return SlashCommand("new")
    if head == "/exit" and len(parts) == 1:
        return SlashCommand("exit")
    if head == "/memory":
        if sub is None or (sub == "list" and len(parts) == 2):
            return SlashCommand("memory")
        if sub == "clear" and len(parts) == 2:
            return SlashCommand("memory-clear")
        if sub == "delete" and len(parts) == 3:
            return SlashCommand("memory-delete", memory_id=parts[2])
        if sub == "correct" and len(parts) >= 4:
            return SlashCommand("memory-correct", memory_id=parts[2], value=" ".join(parts[3:]))
    return SlashCommand("unknown")
