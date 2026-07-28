from __future__ import annotations

import unittest

from policies import GateAction, incident_code_rule, raw_evidence_result
from tests.helpers import decision


class DeterministicPolicyTests(unittest.TestCase):
    def test_failed_ci_blocks(self) -> None:
        result = raw_evidence_result(decision(ci_passed=False))
        self.assertEqual(result.action, GateAction.BLOCK)

    def test_sensitive_executable_change_requires_review(self) -> None:
        result = raw_evidence_result(
            decision(
                title="Extend admin role",
                files_touched=["src/auth/roles.py"],
                diff_excerpt="+ ADMIN.add('export_all')",
            )
        )
        self.assertEqual(result.action, GateAction.HUMAN_REVIEW)

    def test_docs_about_sensitive_domain_can_still_be_low_risk(self) -> None:
        result = raw_evidence_result(
            decision(
                title="Clarify auth guide",
                files_touched=["docs/auth/troubleshooting.md"],
                path_risk="high",
            )
        )
        self.assertEqual(result.action, GateAction.AUTO_MERGE_CANDIDATE)

    def test_path_risk_label_is_not_used(self) -> None:
        result = raw_evidence_result(decision(path_risk="high"))
        self.assertEqual(result.action, GateAction.AUTO_MERGE_CANDIDATE)

    def test_incident_rule_returns_a_boolean(self) -> None:
        self.assertTrue(incident_code_rule(decision(touches_incident_code=True)))
        self.assertFalse(incident_code_rule(decision(touches_incident_code=False)))

    def test_unknown_ci_requires_review(self) -> None:
        result = raw_evidence_result(decision(ci_passed=None))
        self.assertEqual(result.action, GateAction.HUMAN_REVIEW)
        self.assertIn("pending or unavailable", result.reason)

    def test_removed_test_assertion_requires_review(self) -> None:
        result = raw_evidence_result(
            decision(
                title="Simplify widget test",
                files_touched=["tests/test_widget.py"],
                diff_excerpt=(
                    "@@ -4,2 +4 @@\n"
                    "-    assert response.status_code == 403\n"
                    "+    response = get_widget()\n"
                ),
            )
        )
        self.assertEqual(result.action, GateAction.HUMAN_REVIEW)

    def test_incomplete_diff_requires_review(self) -> None:
        result = raw_evidence_result(
            decision(
                files_touched=["docs/guide.md"],
                diff_complete=False,
            )
        )
        self.assertEqual(result.action, GateAction.HUMAN_REVIEW)


if __name__ == "__main__":
    unittest.main()
