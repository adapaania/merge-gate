from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from engine import analyze_decision
from execution_trace import TraceStep
from github_action import (
    _publish_result,
    apply_workflow_ci_result,
    build_job_summary,
    build_parser,
    load_pull_request_url,
    main,
    run,
)
from github_pr import GitHubChangedFile, GitHubPRSnapshot
from judgment import offline_demo_judge
from project_policy import parse_project_policy
from tests.helpers import decision


def _offline_judgment(decision, policies, mode="offline"):
    del mode
    return offline_demo_judge(decision, policies)

ORGANIZATION_POLICY = """
[organization]
name = "Example Corp"
version = 2
default_action = "human_review"
default_reason = "This organization requires a person until a rule clears the change."

[[rules]]
id = "SECRET-01"
title = "Committed secret material"
action = "block"
reason = "Secret material must never be committed."
paths = [".env", ".env.*", "secrets/**"]
"""


POLICY = """
[project]
name = "ClearLedger"
version = 1
default_action = "human_review"
default_reason = "Unknown application changes require owner review."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]
path_match = "all"
"""


def snapshot() -> GitHubPRSnapshot:
    return GitHubPRSnapshot(
        repository="acme/clearledger",
        pr_number=7,
        html_url="https://github.com/acme/clearledger/pull/7",
        title="Clarify operations guide",
        author="ada",
        base_ref="main",
        base_sha="1111111111111111",
        head_ref="docs/operations",
        head_sha="2222222222222222",
        draft=False,
        additions=2,
        deletions=0,
        files=(
            GitHubChangedFile(
                filename="docs/operations.md",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
                patch="+ safer wording",
            ),
        ),
        checks=(),
        ci_status="unknown",
        diff_excerpt="+ safer wording",
        diff_complete=True,
        fetched_at=datetime.now(timezone.utc),
        trace=(),
    )


class GitHubActionTests(unittest.TestCase):
    def test_reads_pr_url_from_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "html_url": "https://github.com/acme/clearledger/pull/7"
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_pull_request_url(event),
                "https://github.com/acme/clearledger/pull/7",
            )

    def test_prerequisite_ci_overrides_in_progress_gate_check(self) -> None:
        updated, trace = apply_workflow_ci_result(
            decision(ci_passed=None),
            "success",
        )
        self.assertTrue(updated.ci_passed)
        self.assertEqual(trace.name, "apply_workflow_ci_result")

    def test_summary_contains_project_result_and_function_trace(self) -> None:
        project_policy = parse_project_policy(POLICY)
        result = analyze_decision(
            decision(
                files_touched=["docs/operations.md"],
                ci_passed=True,
            ),
            project_policy=project_policy,
        )
        trace = (
            TraceStep(
                kind="tool",
                phase="GitHub evidence",
                name="github.rest.get_pull_request",
                summary="Read PR metadata.",
                duration_ms=1.25,
            ),
        ) + result.trace
        summary = build_job_summary(snapshot(), result, trace)
        self.assertIn("Auto-merge candidate", summary)
        self.assertIn("DOC-01", summary)
        self.assertIn("github.rest.get_pull_request", summary)
        self.assertIn("evaluate_project_policy", summary)

    def test_block_result_writes_summary_outputs_and_fails_job(self) -> None:
        project_policy = parse_project_policy(POLICY)
        result = analyze_decision(
            decision(ci_passed=False),
            project_policy=project_policy,
        )
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.md"
            output_path = Path(directory) / "output.txt"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_STEP_SUMMARY": str(summary_path),
                    "GITHUB_OUTPUT": str(output_path),
                },
            ):
                exit_code = _publish_result(result, "# Result")
            self.assertEqual(exit_code, 1)
            self.assertIn("# Result", summary_path.read_text(encoding="utf-8"))
            self.assertIn(
                "action=block",
                output_path.read_text(encoding="utf-8"),
            )

    def test_reusable_action_exposes_only_the_live_judge(self) -> None:
        action = Path("action.yml").read_text(encoding="utf-8")
        self.assertNotIn("judge-mode", action)
        self.assertIn("anthropic-api-key", action)
        self.assertIn("required: true", action)


class OrganizationPolicyWiringTests(unittest.TestCase):
    """`run()` end to end: an optional org baseline can tighten the repo policy."""

    def _fetch_text(self, repository: str, *, ref: str, path: str, token=None):
        del ref, token
        if repository == "acme/org-policy":
            return ORGANIZATION_POLICY, TraceStep(
                kind="tool",
                phase="GitHub evidence",
                name="github.rest.get_organization_policy",
                summary="Read the organization baseline.",
                duration_ms=1.0,
            )
        return POLICY, TraceStep(
            kind="tool",
            phase="GitHub evidence",
            name="github.rest.get_project_policy_at_base",
            summary="Read the repository policy.",
            duration_ms=1.0,
        )

    def test_organization_default_can_elevate_a_repo_auto_merge_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(
                json.dumps({"pull_request": {"html_url": "https://github.com/acme/clearledger/pull/7"}}),
                encoding="utf-8",
            )
            summary_path = Path(directory) / "summary.md"
            output_path = Path(directory) / "output.txt"
            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_TOKEN": "test-token",
                        "GITHUB_EVENT_PATH": str(event_path),
                        "GITHUB_STEP_SUMMARY": str(summary_path),
                        "GITHUB_OUTPUT": str(output_path),
                    },
                ),
                patch("github_action.fetch_github_pr", return_value=snapshot()),
                patch("github_action.fetch_github_text_file", side_effect=self._fetch_text),
                patch("engine.get_judgment", side_effect=_offline_judgment),
            ):
                args = build_parser().parse_args(
                    ["--ci-result", "success", "--org-policy-repo", "acme/org-policy"]
                )
                exit_code = run(args)
            summary = summary_path.read_text(encoding="utf-8")
            # DOC-01 alone would auto-merge; the org's unmatched-default of
            # human_review must still win the combination.
            self.assertEqual(exit_code, 0)
            self.assertIn("Human review required", summary)
            self.assertIn("repo:DOC-01", summary)
            self.assertIn("organization", summary)
            self.assertIn("Example Corp", summary)

    def test_org_policy_flags_default_to_disabled(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.org_policy_repo, "")
        self.assertEqual(args.org_policy_ref, "main")
        self.assertEqual(args.org_policy_path, ".merge-gate/organization.toml")

    def test_invalid_organization_policy_fails_the_job_closed(self) -> None:
        # A malformed org policy must never fall back to "no organization
        # policy" or "repository policy only" — it must fail the run.
        def fetch_text(repository: str, *, ref: str, path: str, token=None):
            del ref, token
            if repository == "acme/org-policy":
                return "not = [valid toml", TraceStep(
                    kind="tool",
                    phase="GitHub evidence",
                    name="github.rest.get_organization_policy",
                    summary="Read the organization baseline.",
                    duration_ms=1.0,
                )
            return POLICY, TraceStep(
                kind="tool",
                phase="GitHub evidence",
                name="github.rest.get_project_policy_at_base",
                summary="Read the repository policy.",
                duration_ms=1.0,
            )

        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(
                json.dumps({"pull_request": {"html_url": "https://github.com/acme/clearledger/pull/7"}}),
                encoding="utf-8",
            )
            summary_path = Path(directory) / "summary.md"
            output_path = Path(directory) / "output.txt"
            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_TOKEN": "test-token",
                        "GITHUB_EVENT_PATH": str(event_path),
                        "GITHUB_STEP_SUMMARY": str(summary_path),
                        "GITHUB_OUTPUT": str(output_path),
                    },
                ),
                patch(
                    "sys.argv",
                    [
                        "github_action.py",
                        "--ci-result",
                        "success",
                        "--org-policy-repo",
                        "acme/org-policy",
                    ],
                ),
                patch("github_action.fetch_github_pr", return_value=snapshot()),
                patch("github_action.fetch_github_text_file", side_effect=fetch_text),
                patch("engine.get_judgment", side_effect=_offline_judgment),
            ):
                exit_code = main()
            self.assertEqual(exit_code, 2)
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("failed closed", summary)


if __name__ == "__main__":
    unittest.main()
