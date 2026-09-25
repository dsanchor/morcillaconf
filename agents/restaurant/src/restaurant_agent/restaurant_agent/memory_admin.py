"""Administrative CLI for local durable memories."""

import argparse
import os
from pathlib import Path
from typing import Sequence

from restaurant_agent.memory.store import DurableMemoryError, SQLiteMemoryStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lista o elimina memorias locales de una identidad.",
    )
    parser.add_argument(
        "--actor-id",
        required=True,
        help="Identidad cuyas memorias se administran.",
    )
    parser.add_argument(
        "--database",
        help="Ruta SQLite. Por defecto usa MEMORY_DATABASE_PATH.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="Lista IDs, categorías y valores.")

    delete_parser = subparsers.add_parser(
        "delete",
        help="Elimina una o varias memorias concretas.",
    )
    delete_parser.add_argument(
        "memory_ids",
        nargs="+",
        help="IDs mostrados por la operación list.",
    )

    clear_parser = subparsers.add_parser(
        "clear",
        help="Elimina todas las memorias de la identidad.",
    )
    clear_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirma la eliminación total.",
    )
    return parser


def resolve_database_path(explicit_path: str | None) -> Path:
    selected_path = explicit_path or os.getenv("MEMORY_DATABASE_PATH")
    if not selected_path:
        raise ValueError(
            "Define MEMORY_DATABASE_PATH mediante source o usa --database."
        )
    return Path(selected_path)


def run(args: argparse.Namespace) -> int:
    store = SQLiteMemoryStore(
        resolve_database_path(args.database),
        max_memories=int(os.getenv("MEMORY_MAX_ITEMS", "20")),
    )
    if args.action == "list":
        memories = store.list_memories(args.actor_id)
        if not memories:
            print(f"No hay memorias para {args.actor_id}.")
            return 0
        for memory in memories:
            print(
                f"{memory.preference_id}\t{memory.kind.value}\t"
                f"{memory.occurrence_count}x\t{memory.value}"
            )
        return 0

    if args.action == "delete":
        store.delete_memories(
            args.actor_id,
            preference_ids=args.memory_ids,
        )
        for memory_id in dict.fromkeys(args.memory_ids):
            print(f"Eliminada: {memory_id}")
        return 0

    if not args.yes:
        raise ValueError("La operación clear requiere --yes.")
    deleted = store.delete_all_memories(args.actor_id)
    print(f"Eliminadas {deleted} memorias de {args.actor_id}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (DurableMemoryError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
