"""Print reproducible policy evaluations without requiring a model API key."""

from __future__ import annotations

from evaluation import evaluation_table
from model import load_decisions


def main() -> None:
    datasets = {
        "Original adversarial stress test": "data/decisions.json",
        "Held-out challenge set": "data/heldout_decisions.json",
    }
    for name, path in datasets.items():
        decisions = load_decisions(path)
        table = evaluation_table(decisions).copy()
        for column in ("Critical recall", "Autonomous coverage", "Escalation precision"):
            table[column] = table[column].map(lambda value: f"{value:.0%}")
        print(f"\n{name} ({len(decisions)} examples)")
        print(table.to_string(index=False))


if __name__ == "__main__":
    main()
