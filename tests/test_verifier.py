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
                    file="docs/guide.md",
                )
            ),
        )
        self.assertTrue(result.valid)

    def test_invented_file_is_rejected(self) -> None:
        example = decision()
        result = verify_judgment(
            example,
            retrieve_policies(example),
            judgment_with(
                EvidenceCitation(
                    claim="A secret file changed.",
                    file="prod/secrets.env",
                )
            ),
        )
        self.assertFalse(result.valid)
        self.assertIn("Cited file was not changed", result.errors[0])

    def test_invented_policy_is_rejected(self) -> None:
        example = decision()
        result = verify_judgment(
            example,
            retrieve_policies(example),
            judgment_with(
                EvidenceCitation(
                    claim="An unavailable policy applies.",
                    policy_id="FAKE-99",
                )
            ),
        )
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
