"""Interactive development CLI for the waiter."""

import argparse
import asyncio
import json
import shlex

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.conversation import ConversationError, ConversationManager
from restaurant_agent.memory import create_memory_store
from restaurant_agent.memory.contracts import MemorySnapshot
from restaurant_agent.memory.store import DurableMemoryError


def print_memory(snapshot: MemorySnapshot) -> None:
    print(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2))


def handle_memory_command(
    manager: ConversationManager,
    *,
    conversation_id: str,
    actor_id: str,
    command: str,
) -> None:
    parts = shlex.split(command)
    action = parts[1] if len(parts) > 1 else "list"

    if action == "list" and len(parts) in (1, 2):
        snapshot = manager.memory_snapshot(
            conversation_id=conversation_id,
            actor_id=actor_id,
        )
    elif action == "correct" and len(parts) >= 4:
        snapshot = manager.correct_memory(
            conversation_id=conversation_id,
            actor_id=actor_id,
            preference_id=parts[2],
            value=" ".join(parts[3:]),
        )
    elif action == "delete" and len(parts) == 3:
        snapshot = manager.delete_memory(
            conversation_id=conversation_id,
            actor_id=actor_id,
            preference_id=parts[2],
        )
    elif action == "clear" and len(parts) == 2:
        snapshot = manager.clear_memories(
            conversation_id=conversation_id,
            actor_id=actor_id,
        )
    else:
        raise ValueError(
            "Uso: /memory list|correct <id> <texto>|delete <id>|clear"
        )
    print_memory(snapshot)


async def run_cli(actor_id: str, *, authenticated: bool) -> None:
    settings = Settings()
    memory_store = create_memory_store(settings)
    manager = ConversationManager(
        create_waiter_agent(settings, memory_store=memory_store),
        max_turns=settings.waiter_max_turns,
        memory_store=memory_store,
    )
    conversation_id = manager.start_conversation(
        actor_id=actor_id,
        authenticated=authenticated,
    )

    print(f"Conversación: {conversation_id}")
    identity_type = "autenticada" if authenticated else "invitada"
    print(f"Identidad: {actor_id} ({identity_type})")
    print("Escribe /new, /memory, o /exit.")

    while True:
        message = input("Cliente> ").strip()
        if message == "/exit":
            return
        if message == "/new":
            conversation_id = manager.start_conversation(
                actor_id=actor_id,
                authenticated=authenticated,
            )
            print(f"Nueva conversación: {conversation_id}")
            continue
        if message.startswith("/memory"):
            try:
                handle_memory_command(
                    manager,
                    conversation_id=conversation_id,
                    actor_id=actor_id,
                    command=message,
                )
            except (ConversationError, DurableMemoryError, ValueError) as exc:
                print(f"Error: {exc}")
            continue
        if not message:
            continue

        try:
            response = await manager.send_message(
                conversation_id=conversation_id,
                actor_id=actor_id,
                message=message,
            )
        except ConversationError as exc:
            print(f"Error: {exc}")
            continue

        print(f"Camarero> {response.reply}")
        print(
            json.dumps(
                response.model_dump(mode="json", exclude={"reply"}),
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI del camarero")
    parser.add_argument("--actor-id", default="local-guest")
    parser.add_argument(
        "--authenticated",
        action="store_true",
        help="Usa memoria duradera automática para esta identidad local.",
    )
    args = parser.parse_args()
    asyncio.run(run_cli(args.actor_id, authenticated=args.authenticated))


if __name__ == "__main__":
    main()
