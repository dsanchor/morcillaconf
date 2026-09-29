"""Print the canonical JSON or the SHA-256 of a seating layout file.

Used by ``scripts/run-mcp.sh`` so the hash always comes from the service's own
canonicalisation: ``python -m restaurant_mcp.layout FILE --json|--sha256``.
"""

import argparse
import json
from pathlib import Path

from restaurant_mcp.seating import SeatingLayout


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--json", action="store_true", help="canonical JSON")
    output.add_argument("--sha256", action="store_true", help="fingerprint")
    args = parser.parse_args(argv)
    layout = SeatingLayout.model_validate(json.loads(args.path.read_text(encoding="utf-8")))
    print(layout.canonical_json() if args.json else layout.fingerprint())


if __name__ == "__main__":
    main()
