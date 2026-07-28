from __future__ import annotations

import unittest

from policy_retrieval import retrieve_policies
from tests.helpers import decision


class PolicyRetrievalTests(unittest.TestCase):
    def test_auth_change_retrieves_security_policy(self) -> None:
        matches = retrieve_policies(
            decision(
                title="Rotate JWT token",
                files_touched=["src/auth/token.py"],
            )
        )
        self.assertEqual(matches[0].policy_id, "SEC-04")

    def test_docs_retrieve_low_risk_policy(self) -> None:
        matches = retrieve_policies(decision())
        self.assertEqual(matches[0].policy_id, "LOW-01")

    def test_irrelevant_policies_are_not_used_as_padding(self) -> None:
        matches = retrieve_policies(
            decision(
                title="Improve empty-state wording",
                files_touched=["src/ui/copy.py"],
            )
        )
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
