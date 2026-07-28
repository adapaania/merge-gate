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
    load_pull_request_url,
)
from github_pr import GitHubChangedFile, GitHubPRSnapshot
from project_policy import parse_project_policy
from tests.helpers import decision


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


if __name__ == "__main__":
    unittest.main()
