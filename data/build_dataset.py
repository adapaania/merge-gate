"""Validate and normalize the committed Merge Gate evaluation fixtures.

The datasets are committed so demo results are reproducible. Running this
script validates every row with the production schema and writes stable,
pretty-printed JSON rather than inventing new random examples.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalize(path: Path) -> tuple[int, int]:
    # Import after defining PROJECT_ROOT so this also works as a direct script.
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from model import load_decisions

    decisions = load_decisions(path)
    labeled = sum(decision.should_escalate is not None for decision in decisions)
    path.write_text(
        json.dumps(
            [decision.model_dump(mode="json", exclude_defaults=True) for decision in decisions],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(decisions), labeled


def main() -> None:
    for relative_path in ("data/decisions.json", "data/heldout_decisions.json"):
        path = PROJECT_ROOT / relative_path
        total, labeled = normalize(path)
        print(f"{relative_path}: {total} valid rows, {labeled} labeled")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ValidationError) as exc:
        raise SystemExit(f"Dataset validation failed: {exc}") from exc
