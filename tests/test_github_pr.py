from __future__ import annotations

import unittest

import httpx

from github_pr import (
    build_decision_from_github,
    fetch_github_pr,
    fetch_github_text_file,
    parse_github_pr_url,
    summarize_ci_state,
)


class GitHubPRTests(unittest.TestCase):
    def test_parse_pull_request_url(self) -> None:
        self.assertEqual(
            parse_github_pr_url("https://github.com/acme/widget/pull/12"),
            ("acme", "widget", 12),
        )

    def test_reject_non_pull_request_urls(self) -> None:
        invalid = [
            "http://github.com/acme/widget/pull/12",
            "https://example.com/acme/widget/pull/12",
            "https://github.com/acme/widget/issues/12",
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_github_pr_url(url)

    def test_ci_summary_is_conservative(self) -> None:
        self.assertEqual(summarize_ci_state({"check_runs": []}, {"statuses": []}), "unknown")
        self.assertEqual(
            summarize_ci_state(
                {"check_runs": [{"name": "tests", "status": "in_progress"}]},
                {"statuses": []},
            ),
            "pending",
        )
        self.assertEqual(
            summarize_ci_state(
                {
                    "check_runs": [
                        {
                            "name": "tests",
                            "status": "completed",
                            "conclusion": "failure",
                        }
                    ]
                },
                {"statuses": []},
            ),
            "failed",
        )

    def test_fetches_and_normalizes_a_read_only_pr(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/repos/acme/widget/pulls/12":
                return httpx.Response(
                    200,
                    json={
                        "title": "Clarify setup guide",
                        "html_url": "https://github.com/acme/widget/pull/12",
                        "user": {"login": "ada"},
                        "base": {
                            "ref": "main",
                            "sha": "1111111111111111",
                        },
                        "head": {
                            "ref": "docs/setup",
                            "sha": "abcdef1234567890",
                        },
                        "draft": False,
                        "additions": 3,
                        "deletions": 1,
                        "changed_files": 1,
                    },
                )
            if path == "/repos/acme/widget/pulls/12/files":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "filename": "docs/setup.md",
                            "status": "modified",
                            "additions": 3,
                            "deletions": 1,
                            "changes": 4,
                            "patch": "@@ -1 +1 @@\n-old\n+new",
                        }
                    ],
                )
            if path == "/repos/acme/widget/commits/abcdef1234567890/check-runs":
                return httpx.Response(
                    200,
                    json={
                        "total_count": 1,
                        "check_runs": [
                            {
                                "name": "tests",
                                "status": "completed",
                                "conclusion": "success",
                            }
                        ],
                    },
                )
            if path == "/repos/acme/widget/commits/abcdef1234567890/status":
                return httpx.Response(
                    200,
                    json={"state": "pending", "total_count": 0, "statuses": []},
                )
            return httpx.Response(404)

        snapshot = fetch_github_pr(
            "https://github.com/acme/widget/pull/12",
            transport=httpx.MockTransport(handler),
        )
        decision = build_decision_from_github(snapshot)

        self.assertEqual(snapshot.repository, "acme/widget")
        self.assertEqual(snapshot.ci_status, "passed")
        self.assertTrue(snapshot.diff_complete)
        self.assertEqual(decision.files_touched, ["docs/setup.md"])
        self.assertIsNone(decision.agent_confidence)
        self.assertIsNone(decision.reversible)
        self.assertEqual(
            [step.name for step in snapshot.trace],
            [
                "github.rest.get_pull_request",
                "github.rest.list_pull_request_files",
                "github.rest.list_check_runs",
                "github.rest.get_combined_commit_status",
                "build_decision_from_github",
            ],
        )

    def test_fetches_project_policy_from_an_immutable_base_ref(self) -> None:
        encoded = "W3Byb2plY3RdCm5h\nbWUgPSAiQ2xlYXJMZWRnZXIiCg=="

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params["ref"], "base-sha")
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": encoded},
            )

        text, trace = fetch_github_text_file(
            "acme/widget",
            ref="base-sha",
            path=".merge-gate/policy.toml",
            transport=httpx.MockTransport(handler),
        )
        self.assertIn('name = "ClearLedger"', text)
        self.assertEqual(
            trace.name,
            "github.rest.get_project_policy_at_base",
        )


if __name__ == "__main__":
    unittest.main()
