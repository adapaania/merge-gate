from __future__ import annotations

import unittest

from evaluation import evaluation_table
from model import load_decisions
from scorer import detailed_score
from tests.helpers import decision


class EvaluationTests(unittest.TestCase):
    def test_metric_counts(self) -> None:
        decisions = [
            decision(id="a", should_escalate=True),
            decision(id="b", should_escalate=False),
        ]
        metrics = detailed_score(lambda _: True, decisions)
        self.assertEqual(metrics.true_escalations, 1)
        self.assertEqual(metrics.false_escalations, 1)
        self.assertEqual(metrics.missed_escalations, 0)
        self.assertEqual(metrics.critical_recall, 1.0)
        self.assertEqual(metrics.autonomous_coverage, 0.0)

    def test_challenge_set_breaks_legacy_path_risk_ceiling(self) -> None:
        table = evaluation_table(load_decisions("data/heldout_decisions.json"))
        row = table.loc[table["Policy"] == "Legacy path-risk ceiling"].iloc[0]
        self.assertGreater(row["Missed escalations"], 0)
        self.assertGreater(row["False escalations"], 0)

    def test_raw_evidence_gate_has_no_challenge_set_misses(self) -> None:
        table = evaluation_table(load_decisions("data/heldout_decisions.json"))
        row = table.loc[table["Policy"] == "Raw-evidence gate"].iloc[0]
        self.assertEqual(row["Missed escalations"], 0)


if __name__ == "__main__":
    unittest.main()
