"""Print the canonical JSON of a seating layout file.

Used by ``scripts/run-mcp.sh`` to validate and serialize the layout with the
service's own model: ``python -m restaurant_mcp.layout FILE --json``.
"""

import argparse
import json
from pathlib import Path

from restaurant_mcp.seating import SeatingLayout


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true", required=True, help="canonical JSON")
    args = parser.parse_args(argv)
    layout = SeatingLayout.model_validate(json.loads(args.path.read_text(encoding="utf-8")))
    print(layout.canonical_json())


if __name__ == "__main__":
    main()
