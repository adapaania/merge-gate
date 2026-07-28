from __future__ import annotations

import unittest
from pathlib import Path

from engine import analyze_decision
from policies import GateAction
from project_policy import (
    evaluate_project_policy,
    parse_project_policy,
    project_policy_matches,
    load_project_policy,
)
from tests.helpers import decision


POLICY = """
[project]
name = "ClearLedger"
version = 1
default_action = "human_review"
default_reason = "Unclassified application changes require owner review."

[[rules]]
id = "PAY-01"
title = "Payout behavior"
action = "human_review"
reason = "Money movement changes require finance-owner review."
paths = ["src/clearledger/payouts.py"]

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**", "README.md"]
path_match = "all"

[[rules]]
id = "GOV-01"
title = "Merge policy governance"
action = "block"
reason = "Merge requirements cannot change without a separate governance process."
paths = [".merge-gate/**"]
"""
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEARLEDGER = PROJECT_ROOT / "clearledger-demo-repo"


class ProjectPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = parse_project_policy(POLICY)

    def test_docs_rule_can_mark_a_change_as_candidate(self) -> None:
        evaluation = evaluate_project_policy(
            self.policy,
            decision(files_touched=["docs/runbook.md"]),
        )
        self.assertEqual(
            evaluation.result.action,
            GateAction.AUTO_MERGE_CANDIDATE,
        )
        self.assertEqual(evaluation.matched_rule_ids, ("DOC-01",))

    def test_payment_rule_requires_review(self) -> None:
        value = decision(
            title="Raise payout limit",
            files_touched=["src/clearledger/payouts.py"],
        )
        evaluation = evaluate_project_policy(self.policy, value)
        matches = project_policy_matches(self.policy, value)
        self.assertEqual(evaluation.result.action, GateAction.HUMAN_REVIEW)
        self.assertEqual([match.policy_id for match in matches], ["PAY-01"])

    def test_project_block_cannot_be_overridden(self) -> None:
        result = analyze_decision(
            decision(
                title="Relax merge requirements",
                files_touched=[".merge-gate/policy.toml"],
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.BLOCK)
        self.assertEqual(result.matched_project_rules, ("GOV-01",))
        self.assertEqual(
            [step.name for step in result.trace[:2]],
            ["evaluate_project_policy", "project_policy_matches"],
        )


class ClearLedgerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_project_policy(
            CLEARLEDGER / ".merge-gate/policy.toml"
        )

    def _patch(self, name: str) -> str:
        return (CLEARLEDGER / "scenarios" / name).read_text(encoding="utf-8")

    def test_documentation_pr_is_a_candidate(self) -> None:
        result = analyze_decision(
            decision(
                title="Add reconciliation runbook",
                files_touched=["docs/reconciliation.md"],
                diff_excerpt=self._patch("docs-operations-runbook.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(
            result.final.action,
            GateAction.AUTO_MERGE_CANDIDATE,
        )
        self.assertEqual(result.matched_project_rules, ("DOC-01",))

    def test_payout_limit_pr_requires_finance_review(self) -> None:
        result = analyze_decision(
            decision(
                title="Raise automatic payout limit",
                files_touched=[
                    "src/clearledger/authorization.py",
                    "src/clearledger/payouts.py",
                    "tests/test_authorization.py",
                    "tests/test_payouts.py",
                ],
                diff_excerpt=self._patch("raise-payout-limit.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("PAY-01", result.matched_project_rules)
        self.assertIn("SEC-01", result.matched_project_rules)

    def test_failed_precision_pr_is_blocked(self) -> None:
        result = analyze_decision(
            decision(
                title="Round settlement to whole units",
                files_touched=["src/clearledger/payouts.py"],
                ci_passed=False,
                diff_excerpt=self._patch("break-currency-precision.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.BLOCK)

    def test_weakened_authorization_test_requires_review(self) -> None:
        result = analyze_decision(
            decision(
                title="Simplify authorization tests",
                files_touched=["tests/test_authorization.py"],
                diff_excerpt=self._patch("weaken-authorization-test.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("SEC-01", result.matched_project_rules)

    def test_env_example_is_waived_to_the_default_not_blocked(self) -> None:
        # SECRET-01's ".env.*" glob also matches the harmless ".env.example"
        # template. EXC-01 waives it, so the result is the project default
        # (human_review), not a block and not an auto-merge candidate.
        result = analyze_decision(
            decision(
                title="Document required environment variables",
                files_touched=[".env.example"],
                diff_excerpt=self._patch("secret-glob-false-positive.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("SECRET-01", result.matched_project_rules)

    def test_committed_secret_key_is_still_blocked(self) -> None:
        # Contrast with the exception above: this path isn't covered by
        # EXC-01, so the real SECRET-01 block still applies in full.
        result = analyze_decision(
            decision(
                title="Add payout signing key for the new HSM integration",
                files_touched=["secrets/payout_signing_key.pem"],
                diff_excerpt=self._patch("committed-secret-key.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.BLOCK)
        self.assertIn("SECRET-01", result.matched_project_rules)

    def test_title_alone_can_escalate_a_docs_only_change(self) -> None:
        # PAY-01 matches on the title term "payout limit" independent of any
        # path match, so it outranks DOC-01's path-only match even though
        # the diff itself only touches docs/.
        result = analyze_decision(
            decision(
                title="Document payout limit escalation steps for support",
                files_touched=["docs/operations.md"],
                diff_excerpt=self._patch("title-triggers-review.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("PAY-01", result.matched_project_rules)
        self.assertIn("DOC-01", result.matched_project_rules)

    def test_injected_instruction_in_diff_does_not_relax_the_result(self) -> None:
        result = analyze_decision(
            decision(
                title="Clarify settlement rounding documentation",
                files_touched=["src/clearledger/payouts.py"],
                diff_excerpt=self._patch("prompt-injection-in-docstring.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("PAY-01", result.matched_project_rules)

    def test_dependency_change_requires_platform_review(self) -> None:
        result = analyze_decision(
            decision(
                title="Add an HTTP client dependency for the outbound webhook integration",
                files_touched=["pyproject.toml"],
                diff_excerpt=self._patch("add-dependency.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertEqual(result.matched_project_rules, ("OPS-01",))

    def test_skipped_test_is_detected_like_a_deleted_one(self) -> None:
        result = analyze_decision(
            decision(
                title="Skip flaky authorization test",
                files_touched=["tests/test_authorization.py"],
                diff_excerpt=self._patch("skip-authorization-test.patch"),
            ),
            project_policy=self.policy,
        )
        self.assertEqual(result.final.action, GateAction.HUMAN_REVIEW)
        self.assertIn("SEC-01", result.matched_project_rules)


if __name__ == "__main__":
    unittest.main()
