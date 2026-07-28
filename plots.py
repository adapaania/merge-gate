"""Save the challenge-set policy frontier used as a static fallback."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from evaluation import evaluation_table
from model import load_decisions


def main() -> None:
    table = evaluation_table(load_decisions("data/heldout_decisions.json"))
    figure, axis = plt.subplots(figsize=(9, 6))
    for row in table.to_dict("records"):
        x = row["False escalations"]
        y = row["Missed escalations"]
        axis.scatter(x, y)
        axis.annotate(row["Policy"], (x, y), xytext=(5, 5), textcoords="offset points")

    axis.set_xlabel("False escalations (unnecessary human review)")
    axis.set_ylabel("Missed escalations (unsafe autonomy)")
    axis.set_title("Merge Gate policy frontier — lower left is better")
    axis.grid(alpha=0.25)
    Path("results").mkdir(exist_ok=True)
    figure.tight_layout()
    figure.savefig("results/frontier.png", dpi=160)


if __name__ == "__main__":
    main()
