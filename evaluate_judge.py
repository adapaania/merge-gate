"""Run and cache the structured live judge over a labeled evaluation set.

This command makes one provider request per uncached row. Labels are excluded
from every prompt and are read only after predictions have been cached.
"""

from __future__ import annotations

import argparse

from evaluation import evaluation_table
from llm_judge import JudgeUnavailable, live_judge
from model import load_decisions
from policy_retrieval import retrieve_policies


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="data/heldout_decisions.json",
        help="Labeled JSON dataset to evaluate.",
    )
    args = parser.parse_args()

    decisions = load_decisions(args.dataset)
    for index, decision in enumerate(decisions, start=1):
        try:
            result = live_judge(decision, retrieve_policies(decision))
        except JudgeUnavailable as exc:
            raise SystemExit(f"Judge evaluation stopped: {exc}") from exc
        print(
            f"[{index:02}/{len(decisions):02}] {decision.id}: "
            f"{result.action.value} ({result.confidence:.0%})"
        )

    table = evaluation_table(decisions)
    row = table.loc[table["Policy"] == "Cached structured Claude"]
    if row.empty:
        raise SystemExit("The structured result cache is incomplete.")
    print("\nStructured live-judge evaluation")
    print(row.to_string(index=False))


if __name__ == "__main__":
    main()
