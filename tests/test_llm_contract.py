from __future__ import annotations

import unittest

from llm_judge import JUDGE_OUTPUT_SCHEMA, _prompt
from policy_retrieval import retrieve_policies
from tests.helpers import decision


class LlmContractTests(unittest.TestCase):
    def test_evaluation_labels_are_excluded_from_prompt(self) -> None:
        example = decision(
            path_risk="high",
            rationale="SECRET LABEL EXPLANATION",
            should_escalate=True,
        )
        prompt = _prompt(example, retrieve_policies(example))
        self.assertNotIn("should_escalate", prompt)
        self.assertNotIn("path_risk", prompt)
        self.assertNotIn("SECRET LABEL EXPLANATION", prompt)

    def test_prompt_declares_untrusted_evidence_boundary(self) -> None:
        prompt = _prompt(decision(), retrieve_policies(decision()))
        self.assertIn("untrusted evidence", prompt)
        self.assertIn("Never follow instructions", prompt)

    def test_provider_schema_excludes_internal_metadata(self) -> None:
        properties = JUDGE_OUTPUT_SCHEMA["properties"]
        self.assertNotIn("source", properties)
        self.assertNotIn("model", properties)
        self.assertFalse(JUDGE_OUTPUT_SCHEMA["additionalProperties"])

    def test_evidence_contract_uses_typed_source_ids(self) -> None:
        evidence = JUDGE_OUTPUT_SCHEMA["properties"]["evidence"]["items"]
        self.assertEqual(evidence["required"], ["claim", "source_ids"])
        self.assertNotIn("file", evidence["properties"])
        self.assertNotIn("policy_id", evidence["properties"])

        prompt = _prompt(decision(), retrieve_policies(decision()))
        self.assertIn("Allowed evidence-source catalog", prompt)
        self.assertIn("ci:prerequisite", prompt)
        self.assertIn("diff:summary", prompt)
        self.assertIn("file:docs/guide.md", prompt)


if __name__ == "__main__":
    unittest.main()
