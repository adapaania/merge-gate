from __future__ import annotations

import unittest
from datetime import date

from policies import GateAction
from policy_schema import (
    AutoMergePolicy,
    PolicyDocument,
    PolicyException,
    PolicyRule,
    combine_document_results,
    content_hash,
    evaluate_policy_document,
    parse_policy_document,
)
from tests.helpers import decision

ORG_TOML = """
[organization]
name = "Example Corp"
version = 3
default_action = "human_review"
default_reason = "Unclassified changes require a person until a rule clears them."

[[rules]]
id = "PAY-01"
title = "Payment behavior"
action = "human_review"
reason = "Payment changes require finance review."
paths = ["payments/**"]
required_teams = ["finance"]
requires_ci = true

[[rules]]
id = "SECRET-01"
title = "Committed secret material"
action = "block"
reason = "Secret material must never be committed."
paths = [".env", ".env.*", "secrets/**"]

[[rules]]
id = "DOC-01"
title = "Documentation-only changes"
action = "auto_merge_candidate"
reason = "Docs changes may proceed after CI."
paths = ["docs/**", "README.md"]
path_match = "all"
requires_ci = true
"""


def _org_policy() -> PolicyDocument:
    return parse_policy_document(ORG_TOML, section="organization").document


class PolicyRuleMatchingTests(unittest.TestCase):
    """Positive and negative matching, one rule at a time."""

    def setUp(self) -> None:
        self.policy = _org_policy()

    def test_positive_match_selects_the_matching_rule(self) -> None:
        evaluation = evaluate_policy_document(
            self.policy,
            decision(files_touched=["docs/guide.md"]),
            policy_label="Organization baseline",
        )
        self.assertEqual(evaluation.result.action, GateAction.AUTO_MERGE_CANDIDATE)
        self.assertEqual(evaluation.matched_rule_ids, ("DOC-01",))
        self.assertTrue(evaluation.requires_ci)

    def test_negative_match_falls_back_to_default(self) -> None:
        evaluation = evaluate_policy_document(
            self.policy,
            decision(files_touched=["src/app/unrelated.py"]),
            policy_label="Organization baseline",
        )
        self.assertEqual(evaluation.result.action, GateAction.HUMAN_REVIEW)
        self.assertEqual(evaluation.matched_rule_ids, ())
        self.assertEqual(evaluation.required_teams, ())

    def test_conflicting_matches_use_most_restrictive_precedence(self) -> None:
        # Touches both a payments path (human_review) and a secrets path (block).
        evaluation = evaluate_policy_document(
            self.policy,
            decision(files_touched=["payments/capture.py", "secrets/keys.pem"]),
            policy_label="Organization baseline",
        )
        self.assertEqual(evaluation.result.action, GateAction.BLOCK)
        self.assertEqual(set(evaluation.matched_rule_ids), {"PAY-01", "SECRET-01"})

    def test_required_teams_surface_from_the_winning_rule(self) -> None:
        evaluation = evaluate_policy_document(
            self.policy,
            decision(files_touched=["payments/capture.py"]),
            policy_label="Organization baseline",
        )
        self.assertEqual(evaluation.required_teams, ("finance",))


class MissingOrInvalidPolicyTests(unittest.TestCase):
    """Every failure mode must fail safely as ValueError, not leak a raw pydantic error."""

    def test_missing_section_is_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_policy_document("[not_organization]\nname = 'x'\n", section="organization")

    def test_invalid_toml_is_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_policy_document("not = [valid toml", section="organization")

    def test_schema_violation_is_a_value_error_not_a_bare_validation_error(self) -> None:
        # default_reason shorter than the required minimum length.
        bad = """
[organization]
name = "Example Corp"
version = 1
default_action = "human_review"
default_reason = "short"

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]
"""
        with self.assertRaises(ValueError):
            parse_policy_document(bad, section="organization")

    def test_duplicate_rule_ids_are_rejected(self) -> None:
        bad = """
[organization]
name = "Example Corp"
version = 1
default_action = "human_review"
default_reason = "Unclassified changes require a person."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]

[[rules]]
id = "DOC-01"
title = "Duplicate id"
action = "block"
reason = "This id collides with the rule above."
paths = ["other/**"]
"""
        with self.assertRaises(ValueError):
            parse_policy_document(bad, section="organization")

    def test_exception_referencing_an_unknown_rule_is_rejected(self) -> None:
        bad = """
[organization]
name = "Example Corp"
version = 1
default_action = "human_review"
default_reason = "Unclassified changes require a person."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]

[[exceptions]]
id = "EXC-01"
rule_id = "NOPE-99"
owner = "platform-team"
expires_on = 2099-01-01
reason = "This references a rule that does not exist."
"""
        with self.assertRaises(ValueError):
            parse_policy_document(bad, section="organization")

    def test_execution_defaults_to_disabled(self) -> None:
        self.assertEqual(_org_policy().execution, AutoMergePolicy())

    def test_document_can_explicitly_enable_squash_auto_merge(self) -> None:
        enabled = ORG_TOML.replace(
            'default_reason = "Unclassified changes require a person until a rule clears them."',
            'default_reason = "Unclassified changes require a person until a rule clears them."\n'
            "\n[organization.execution]\n"
            "enabled = true\n"
            'merge_method = "squash"',
        )
        policy = parse_policy_document(enabled, section="organization").document
        self.assertTrue(policy.execution.enabled)
        self.assertEqual(policy.execution.merge_method, "squash")

    def test_unknown_execution_merge_method_is_rejected(self) -> None:
        bad = ORG_TOML.replace(
            'default_reason = "Unclassified changes require a person until a rule clears them."',
            'default_reason = "Unclassified changes require a person until a rule clears them."\n'
            "\n[organization.execution]\n"
            "enabled = true\n"
            'merge_method = "fast_forward"',
        )
        with self.assertRaises(ValueError):
            parse_policy_document(bad, section="organization")


class PolicyExceptionTests(unittest.TestCase):
    """Exceptions waive one matched rule; expiry and scope are enforced."""

    def _policy_with_exception(
        self, *, expires_on: date, paths: tuple[str, ...] = ()
    ) -> PolicyDocument:
        return PolicyDocument(
            name="ClearLedger",
            version=1,
            default_action=GateAction.HUMAN_REVIEW,
            default_reason="Unclassified changes require a person until a rule clears them.",
            rules=(
                PolicyRule(
                    id="PAY-01",
                    title="Payment behavior",
                    action=GateAction.HUMAN_REVIEW,
                    reason="Payment changes require finance review.",
                    paths=("payments/**",),
                ),
            ),
            exceptions=(
                PolicyException(
                    id="EXC-01",
                    rule_id="PAY-01",
                    owner="finance-lead",
                    expires_on=expires_on,
                    reason="Approved temporary waiver for the Q1 migration window.",
                    paths=paths,
                ),
            ),
        )

    def test_active_exception_waives_the_matched_rule(self) -> None:
        policy = self._policy_with_exception(expires_on=date(2999, 1, 1))
        evaluation = evaluate_policy_document(
            policy,
            decision(files_touched=["payments/capture.py"]),
            policy_label="Project requirements",
            today=date(2026, 1, 1),
        )
        self.assertEqual(evaluation.result.action, GateAction.HUMAN_REVIEW)
        self.assertEqual(evaluation.result.reason, policy.default_reason)
        self.assertEqual(evaluation.waived_rule_ids, ("PAY-01",))
        self.assertEqual(evaluation.matched_rule_ids, ("PAY-01",))

    def test_expired_exception_does_not_waive_the_rule(self) -> None:
        policy = self._policy_with_exception(expires_on=date(2020, 1, 1))
        evaluation = evaluate_policy_document(
            policy,
            decision(files_touched=["payments/capture.py"]),
            policy_label="Project requirements",
            today=date(2026, 1, 1),
        )
        self.assertEqual(evaluation.result.action, GateAction.HUMAN_REVIEW)
        self.assertEqual(evaluation.waived_rule_ids, ())
        self.assertIn("PAY-01", evaluation.result.reason)

    def test_active_exception_on_a_block_rule_changes_the_outcome(self) -> None:
        # This is the discriminating case: without the waiver the result
        # would be BLOCK; with it, the next-most-restrictive matched rule
        # (human_review) must win instead of falling straight to default.
        policy = PolicyDocument(
            name="ClearLedger",
            version=1,
            default_action=GateAction.AUTO_MERGE_CANDIDATE,
            default_reason="Unclassified changes may proceed after CI.",
            rules=(
                PolicyRule(
                    id="SECRET-01",
                    title="Committed secret material",
                    action=GateAction.BLOCK,
                    reason="Secret material must never be committed.",
                    paths=("config/**",),
                ),
                PolicyRule(
                    id="CFG-01",
                    title="Configuration change",
                    action=GateAction.HUMAN_REVIEW,
                    reason="Configuration changes require owner review.",
                    paths=("config/**",),
                ),
            ),
            exceptions=(
                PolicyException(
                    id="EXC-01",
                    rule_id="SECRET-01",
                    owner="security-lead",
                    expires_on=date(2999, 1, 1),
                    reason="This path is a documented false positive, tracked in SEC-4021.",
                    paths=("config/**",),
                ),
            ),
        )
        evaluation = evaluate_policy_document(
            policy,
            decision(files_touched=["config/settings.yaml"]),
            policy_label="Project requirements",
            today=date(2026, 1, 1),
        )
        self.assertEqual(evaluation.result.action, GateAction.HUMAN_REVIEW)
        self.assertEqual(evaluation.waived_rule_ids, ("SECRET-01",))
        self.assertEqual(set(evaluation.matched_rule_ids), {"SECRET-01", "CFG-01"})

    def test_exception_scoped_to_an_unrelated_path_does_not_apply(self) -> None:
        policy = self._policy_with_exception(
            expires_on=date(2999, 1, 1),
            paths=("payments/legacy_batch.py",),
        )
        evaluation = evaluate_policy_document(
            policy,
            decision(files_touched=["payments/capture.py"]),
            policy_label="Project requirements",
            today=date(2026, 1, 1),
        )
        self.assertEqual(evaluation.waived_rule_ids, ())
        self.assertIn("PAY-01", evaluation.result.reason)


class CombineDocumentResultsTests(unittest.TestCase):
    def test_most_restrictive_of_two_layers_wins(self) -> None:
        from policies import PolicyResult

        combined = combine_document_results(
            [
                PolicyResult("Organization baseline", GateAction.HUMAN_REVIEW, "org default"),
                PolicyResult("Project requirements", GateAction.AUTO_MERGE_CANDIDATE, "project ok"),
            ]
        )
        self.assertEqual(combined.action, GateAction.HUMAN_REVIEW)

    def test_a_project_cannot_weaken_an_organization_block(self) -> None:
        from policies import PolicyResult

        combined = combine_document_results(
            [
                PolicyResult("Organization baseline", GateAction.BLOCK, "org blocks secrets"),
                PolicyResult(
                    "Project requirements", GateAction.AUTO_MERGE_CANDIDATE, "project sees nothing"
                ),
            ]
        )
        self.assertEqual(combined.action, GateAction.BLOCK)
        self.assertIn("org blocks secrets", combined.reason)


class ContentHashTests(unittest.TestCase):
    def test_identical_text_hashes_identically(self) -> None:
        self.assertEqual(content_hash(ORG_TOML), content_hash(ORG_TOML))

    def test_different_text_hashes_differently(self) -> None:
        self.assertNotEqual(content_hash(ORG_TOML), content_hash(ORG_TOML + "\n"))


if __name__ == "__main__":
    unittest.main()
