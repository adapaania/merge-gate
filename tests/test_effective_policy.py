from __future__ import annotations

import unittest

from engine import analyze_decision
from organization_policy import parse_organization_policy
from policies import GateAction
from project_policy import parse_project_policy
from tests.helpers import decision

STRICT_ORG = """
[organization]
name = "Example Corp"
version = 3
default_action = "human_review"
default_reason = "Unclassified changes require a person until a rule clears them."

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
paths = ["docs/**"]
"""

LENIENT_ORG = """
[organization]
name = "Startup Inc"
version = 1
default_action = "auto_merge_candidate"
default_reason = "This organization has not opted any category into review."

[[rules]]
id = "DOC-01"
title = "Documentation-only changes"
action = "auto_merge_candidate"
reason = "Docs changes may proceed after CI."
paths = ["docs/**"]
"""

REPO_POLICY = """
[project]
name = "ClearLedger"
version = 1
default_action = "human_review"
default_reason = "Unclassified application changes require owner review."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]

[[rules]]
id = "GOV-01"
title = "Merge policy governance"
action = "block"
reason = "Merge requirements cannot change without a separate governance process."
paths = [".merge-gate/**"]
"""


class OrganizationAndProjectCombinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.org = parse_organization_policy(STRICT_ORG)
        self.repo = parse_project_policy(REPO_POLICY)

    def test_organization_block_cannot_be_weakened_by_a_silent_repo_policy(self) -> None:
        result = analyze_decision(
            decision(files_touched=["secrets/keys.pem"]),
            project_policy=self.repo,
            organization_policy=self.org,
        )
        self.assertEqual(result.final.action, GateAction.BLOCK)
        self.assertIn("SECRET-01", result.matched_organization_rules)
        self.assertEqual(result.matched_project_rules, ())

    def test_repository_policy_can_tighten_beyond_the_organization_baseline(self) -> None:
        # The org baseline has no opinion on `.merge-gate/**`, so it falls to
        # its default (human_review); the repo's GOV-01 rule tightens that to
        # block. The combined effective result must reflect the tightening.
        result = analyze_decision(
            decision(
                title="Relax merge requirements",
                files_touched=[".merge-gate/policy.toml"],
            ),
            project_policy=self.repo,
            organization_policy=self.org,
        )
        self.assertEqual(result.final.action, GateAction.BLOCK)
        self.assertIn("GOV-01", result.matched_project_rules)

    def test_both_layers_recorded_in_policy_sources_with_hashes(self) -> None:
        result = analyze_decision(
            decision(files_touched=["docs/guide.md"]),
            project_policy=self.repo,
            organization_policy=self.org,
            project_policy_source="acme/clearledger:.merge-gate/policy.toml@abc123",
            project_policy_hash="repo-hash",
            organization_policy_source="acme/org-policy:baseline.toml@def456",
            organization_policy_hash="org-hash",
        )
        scopes = {source.scope: source for source in result.policy_sources}
        self.assertEqual(scopes["organization"].content_hash, "org-hash")
        self.assertEqual(scopes["organization"].version, 3)
        self.assertEqual(scopes["project"].content_hash, "repo-hash")
        self.assertEqual(scopes["project"].version, 1)

    def test_required_teams_surface_on_the_analysis_result(self) -> None:
        org = parse_organization_policy(
            """
[organization]
name = "Example Corp"
version = 1
default_action = "human_review"
default_reason = "Unclassified changes require a person until a rule clears them."

[[rules]]
id = "PAY-01"
title = "Payment behavior"
action = "human_review"
reason = "Payment changes require finance review."
paths = ["payments/**"]
required_teams = ["finance"]
"""
        )
        result = analyze_decision(
            decision(files_touched=["payments/capture.py"]),
            project_policy=self.repo,
            organization_policy=org,
        )
        self.assertIn("finance", result.required_teams)

    def test_a_repository_with_no_organization_policy_is_unaffected(self) -> None:
        # Backward compatibility: omitting organization_policy must reproduce
        # exactly today's project-only behavior.
        with_org = analyze_decision(
            decision(files_touched=["docs/guide.md"]),
            project_policy=self.repo,
            organization_policy=parse_organization_policy(LENIENT_ORG),
        )
        without_org = analyze_decision(
            decision(files_touched=["docs/guide.md"]),
            project_policy=self.repo,
        )
        self.assertEqual(without_org.matched_organization_rules, ())
        self.assertEqual(with_org.final.action, without_org.final.action)


class CounterfactualPolicyTests(unittest.TestCase):
    """The same PR must be free to land differently under different org policies."""

    def test_same_pr_different_organizations_different_outcomes(self) -> None:
        # Neither organization's rules touch this repo's own paths, so the
        # only thing that differs is whether the org itself blocks secrets.
        pr = decision(files_touched=["secrets/keys.pem"])
        repo = parse_project_policy(REPO_POLICY)

        under_strict_org = analyze_decision(
            pr,
            project_policy=repo,
            organization_policy=parse_organization_policy(STRICT_ORG),
        )
        under_lenient_org = analyze_decision(
            pr,
            project_policy=repo,
            organization_policy=parse_organization_policy(LENIENT_ORG),
        )
        self.assertEqual(under_strict_org.final.action, GateAction.BLOCK)
        self.assertIn("SECRET-01", under_strict_org.matched_organization_rules)
        self.assertEqual(under_lenient_org.final.action, GateAction.HUMAN_REVIEW)

    def test_two_repositories_can_use_different_policies_under_one_organization(self) -> None:
        pr = decision(files_touched=[".merge-gate/policy.toml"])
        org = parse_organization_policy(LENIENT_ORG)

        strict_repo = parse_project_policy(REPO_POLICY)  # has a GOV-01 block rule
        lenient_repo = parse_project_policy(
            """
[project]
name = "SandboxRepo"
version = 1
default_action = "auto_merge_candidate"
default_reason = "This sandbox repository has not defined any restrictions."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]
"""
        )
        under_strict_repo = analyze_decision(pr, project_policy=strict_repo, organization_policy=org)
        under_lenient_repo = analyze_decision(
            pr, project_policy=lenient_repo, organization_policy=org
        )
        self.assertEqual(under_strict_repo.final.action, GateAction.BLOCK)
        # Neither policy layer objects here, but "policy.toml" itself still
        # trips the deterministic permissions-domain check — a real repo
        # policy is a floor under any org/repo leniency, not something either
        # layer can waive by staying silent.
        self.assertEqual(under_lenient_repo.final.action, GateAction.HUMAN_REVIEW)


if __name__ == "__main__":
    unittest.main()
