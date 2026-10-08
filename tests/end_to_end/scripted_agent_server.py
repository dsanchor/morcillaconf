"""The standalone waiter for the seating end-to-end check, without Foundry.

dsanchor's ``create_server`` and ``RemoteWaiterService`` with the real waiter
agent (its MCP connection, approval, provider and middleware); only the model
is replaced, through ``agent_factory``, by a scripted chat client. With
``E2E_BAR_CARTA`` the bar reads that versioned carta instead of the knowledge
base: its own parser, matcher and allergen rule run on it. Test code only:
there is no production flag for it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripted_chat_client import ScriptedChatClient  # noqa: E402

from restaurant_agent.agent import create_waiter_agent  # noqa: E402
from restaurant_agent.config import Settings  # noqa: E402
from restaurant_agent.remote import RemoteWaiterService, create_server  # noqa: E402


class VersionedCarta:
    """The bar's knowledge base lookup, answered with the versioned carta as one house passage."""

    def __init__(self, path: Path) -> None:
        self._text = f"[1] Origen: documento de la casa: carta.md\n{path.read_text(encoding='utf-8')}"

    async def retrieve(self, query_variants: list[str]) -> str:
        return self._text


def main() -> None:
    settings = Settings(_env_file=None)
    model = ScriptedChatClient()
    carta_path = os.environ.get("E2E_BAR_CARTA")
    carta = VersionedCarta(Path(carta_path)) if carta_path else None

    def agent_factory(settings: Settings, *, memory_store):
        return create_waiter_agent(settings, memory_store=memory_store, client=model, bar_carta=carta)

    service = RemoteWaiterService(settings, agent_factory=agent_factory)
    # Like main.py: the host reads PORT from the environment.
    create_server(settings, service=service).run()


if __name__ == "__main__":
    main()
