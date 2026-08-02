from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

import httpx

from github_automerge import maybe_enable_auto_merge
from github_pr import API_ROOT, GitHubChangedFile, GitHubPRSnapshot
from policies import GateAction
from policy_schema import parse_policy_document


def _policy(*, enabled: bool, section: str = "project", merge_method: str = "squash"):
    return parse_policy_document(
        f"""
[{section}]
name = "Example policy"
version = 1
default_action = "human_review"
default_reason = "Unclassified changes require human review."

[{section}.execution]
enabled = {str(enabled).lower()}
merge_method = "{merge_method}"

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]
path_match = "all"
""",
        section=section,
    ).document


def _snapshot(
    *,
    draft: bool = False,
    diff_complete: bool = True,
    head_repository: str = "acme/clearledger",
) -> GitHubPRSnapshot:
    return GitHubPRSnapshot(
        repository="acme/clearledger",
        pr_number=7,
        html_url="https://github.com/acme/clearledger/pull/7",
        title="Clarify operations guide",
        author="ada",
        base_ref="main",
        base_sha="1" * 40,
        head_ref="docs/operations",
        head_sha="2" * 40,
        head_repository=head_repository,
        draft=draft,
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
        ci_status="passed",
        diff_excerpt="+ safer wording",
        diff_complete=diff_complete,
        fetched_at=datetime.now(UTC),
        trace=(),
    )


def _rest_payload(**updates):
    payload = {
        "node_id": "PR_kwDO_example",
        "number": 7,
        "state": "open",
        "merged": False,
        "draft": False,
        "head": {"sha": "2" * 40},
        "base": {"sha": "1" * 40},
        "auto_merge": None,
    }
    payload.update(updates)
    return payload


class GitHubAutoMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = _policy(enabled=True)

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(
            base_url=API_ROOT,
            transport=httpx.MockTransport(handler),
        )

    def test_non_candidate_never_calls_github(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request: {request.url}")

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.HUMAN_REVIEW,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "not_candidate")

    def test_repository_policy_must_explicitly_enable_execution(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=_policy(enabled=False),
            organization_policy=None,
            token=None,
        )
        self.assertEqual(result.status, "disabled")

    def test_configured_organization_policy_must_also_opt_in(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=self.project,
            organization_policy=_policy(enabled=False, section="organization"),
            token="write-token",
        )
        self.assertEqual(result.status, "disabled")
        self.assertIn("Organization", result.reason)

    def test_organization_policy_controls_merge_method(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=_rest_payload())
            return httpx.Response(
                200,
                json={
                    "data": {
                        "enablePullRequestAutoMerge": {
                            "pullRequest": {"number": 7, "autoMergeRequest": {}}
                        }
                    }
                },
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=_policy(
                    enabled=True,
                    section="organization",
                    merge_method="rebase",
                ),
                token="write-token",
                client=client,
            )
        body = json.loads(requests[1].content)
        self.assertEqual(body["variables"]["input"]["mergeMethod"], "REBASE")
        self.assertEqual(result.merge_method, "rebase")

    def test_draft_candidate_does_not_need_or_use_write_token(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(draft=True),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=self.project,
            organization_policy=None,
            token=None,
        )
        self.assertEqual(result.status, "draft")

    def test_cross_repository_candidate_never_receives_write_authority(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(head_repository="external/fork"),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=self.project,
            organization_policy=None,
            token="write-token",
        )
        self.assertEqual(result.status, "fork")

    def test_enabled_policy_without_execution_token_fails_closed(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=self.project,
            organization_policy=None,
            token=None,
        )
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.failed)

    def test_incomplete_diff_fails_before_github_write(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(diff_complete=False),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=True,
            project_policy=self.project,
            organization_policy=None,
            token="write-token",
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("incomplete", result.reason)

    def test_unverified_ci_fails_before_github_write(self) -> None:
        result = maybe_enable_auto_merge(
            snapshot=_snapshot(),
            action=GateAction.AUTO_MERGE_CANDIDATE,
            ci_passed=None,
            project_policy=self.project,
            organization_policy=None,
            token="write-token",
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("CI", result.reason)

    def test_changed_head_sha_fails_stale_without_mutation(self) -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return httpx.Response(
                200,
                json=_rest_payload(head={"sha": "3" * 40}),
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "stale")
        self.assertEqual(methods, ["GET"])

    def test_changed_base_sha_fails_stale_without_mutation(self) -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return httpx.Response(
                200,
                json=_rest_payload(base={"sha": "4" * 40}),
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "stale")
        self.assertEqual(methods, ["GET"])

    def test_existing_auto_merge_request_is_idempotent(self) -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return httpx.Response(
                200,
                json=_rest_payload(auto_merge={"merge_method": "squash"}),
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "already_enabled")
        self.assertEqual(methods, ["GET"])

    def test_verified_candidate_enables_native_auto_merge(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=_rest_payload())
            return httpx.Response(
                200,
                json={
                    "data": {
                        "enablePullRequestAutoMerge": {
                            "pullRequest": {
                                "number": 7,
                                "autoMergeRequest": {
                                    "enabledAt": "2026-08-02T10:00:00Z",
                                    "mergeMethod": "SQUASH",
                                },
                            }
                        }
                    }
                },
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "enabled")
        self.assertEqual([request.method for request in requests], ["GET", "POST"])
        self.assertEqual(result.trace[-1].name, "github.graphql.enable_pull_request_auto_merge")

    def test_graphql_rejection_fails_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=_rest_payload())
            return httpx.Response(
                200,
                json={"errors": [{"message": "Auto merge is not allowed"}]},
            )

        with self._client(handler) as client:
            result = maybe_enable_auto_merge(
                snapshot=_snapshot(),
                action=GateAction.AUTO_MERGE_CANDIDATE,
                ci_passed=True,
                project_policy=self.project,
                organization_policy=None,
                token="write-token",
                client=client,
            )
        self.assertEqual(result.status, "failed")
        self.assertIn("settings", result.reason)


if __name__ == "__main__":
    unittest.main()
