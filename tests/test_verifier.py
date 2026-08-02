from __future__ import annotations

import unittest

from judgment import EvidenceCitation, JudgeResult
from policies import GateAction
from policy_retrieval import retrieve_policies
from tests.helpers import decision
from verifier import verify_judgment


def judgment_with(citation: EvidenceCitation) -> JudgeResult:
    return JudgeResult(
        action=GateAction.HUMAN_REVIEW,
        risk_level="elevated",
        reasons=["Review is required."],
        evidence=[citation],
        confidence=0.8,
        source="test",
        model="test",
    )


class EvidenceVerifierTests(unittest.TestCase):
    def test_changed_file_citation_is_valid(self) -> None:
        example = decision()
        result = verify_judgment(
            example,
            retrieve_policies(example),
            judgment_with(
                EvidenceCitation(
                    claim="The changed file is observable.",
                    source_ids=["file:docs/guide.md"],
                )
            ),
        )
        self.assertTrue(result.valid)

    def test_ci_and_diff_sources_are_valid(self) -> None:
        example = decision(ci_passed=True, diff_complete=True, diff_lines=4)
        policies = retrieve_policies(example)
        judgment = JudgeResult(
            action=GateAction.AUTO_MERGE_CANDIDATE,
            risk_level="low",
            reasons=["The supplied evidence supports a low-risk result."],
            evidence=[
                EvidenceCitation(
                    claim="Prerequisite CI passed.",
                    source_ids=["ci:prerequisite"],
                ),
                EvidenceCitation(
                    claim="The complete diff contains four changed lines.",
                    source_ids=["diff:summary"],
                ),
            ],
            confidence=0.9,
            source="test",
            model="test",
        )
        result = verify_judgment(example, policies, judgment)
        self.assertTrue(result.valid)

    def test_invented_file_is_rejected(self) -> None:
        example = decision()
        result = verify_judgment(
            example,
            retrieve_policies(example),
            judgment_with(
                EvidenceCitation(
                    claim="A secret file changed.",
                    source_ids=["file:prod/secrets.env"],
                )
            ),
        )
        self.assertFalse(result.valid)
        self.assertIn("Evidence source was not supplied", result.errors[0])

    def test_invented_policy_is_rejected(self) -> None:
        example = decision()
        result = verify_judgment(
            example,
            retrieve_policies(example),
            judgment_with(
                EvidenceCitation(
                    claim="An unavailable policy applies.",
                    source_ids=["policy:FAKE-99"],
                )
            ),
        )
        self.assertFalse(result.valid)

    def test_source_ids_must_be_unique(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            EvidenceCitation(
                claim="Duplicate source reference.",
                source_ids=["diff:summary", "diff:summary"],
            )


if __name__ == "__main__":
    unittest.main()
