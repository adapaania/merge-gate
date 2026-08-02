"""Opt-in GitHub auto-merge execution for verified Merge Gate candidates."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Literal

import httpx

from execution_trace import TraceStep
from github_pr import API_ROOT, API_VERSION, GitHubPRSnapshot
from policies import GateAction
from policy_schema import PolicyDocument

AutoMergeStatus = Literal[
    "disabled",
    "not_candidate",
    "draft",
    "fork",
    "stale",
    "already_enabled",
    "already_merged",
    "enabled",
    "failed",
]


@dataclass(frozen=True)
class AutoMergeExecution:
    """Sanitized result of the optional execution phase."""

    status: AutoMergeStatus
    reason: str
    merge_method: str | None = None
    trace: tuple[TraceStep, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status in {"stale", "failed"}


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1_000, 2)


def _step(
    *,
    name: str,
    summary: str,
    started_at: float,
    status: Literal["ok", "warning", "error"] = "ok",
    details: dict[str, str | int | float | bool | None] | None = None,
) -> TraceStep:
    return TraceStep(
        kind="tool",
        phase="GitHub execution",
        name=name,
        status=status,
        summary=summary,
        duration_ms=_duration_ms(started_at),
        details=details or {},
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "merge-gate-auto-merge",
    }


def _result(
    status: AutoMergeStatus,
    reason: str,
    *,
    merge_method: str | None = None,
    trace: tuple[TraceStep, ...] = (),
) -> AutoMergeExecution:
    return AutoMergeExecution(
        status=status,
        reason=reason,
        merge_method=merge_method,
        trace=trace,
    )


def _effective_merge_method(
    project_policy: PolicyDocument,
    organization_policy: PolicyDocument | None,
) -> tuple[str | None, str]:
    """Require every configured policy layer to opt into execution.

    An organization baseline, when present, owns the merge method. This lets
    the shared layer tighten repository execution settings instead of allowing
    a repository overlay to silently grant itself write authority.
    """

    if not project_policy.execution.enabled:
        return None, "Repository policy has not opted into auto-merge execution."
    if organization_policy is not None:
        if not organization_policy.execution.enabled:
            return None, "Organization policy has not opted into auto-merge execution."
        return organization_policy.execution.merge_method, "Organization policy enabled auto-merge."
    return project_policy.execution.merge_method, "Repository policy enabled auto-merge."


def maybe_enable_auto_merge(
    *,
    snapshot: GitHubPRSnapshot,
    action: GateAction,
    ci_passed: bool | None,
    project_policy: PolicyDocument,
    organization_policy: PolicyDocument | None,
    token: str | None,
    client: httpx.Client | None = None,
) -> AutoMergeExecution:
    """Enable GitHub-native auto-merge only after every safety precondition.

    The mutation is intentionally idempotent. It re-reads the PR immediately
    before requesting auto-merge and compares both immutable evidence SHAs so
    a push or base-branch update cannot race the evaluated decision.
    """

    if action != GateAction.AUTO_MERGE_CANDIDATE:
        return _result(
            "not_candidate",
            f"Final action was {action.value}; no merge write was attempted.",
        )

    merge_method, policy_reason = _effective_merge_method(
        project_policy,
        organization_policy,
    )
    if merge_method is None:
        return _result("disabled", policy_reason)
    if snapshot.draft:
        return _result(
            "draft",
            "The pull request is a draft; auto-merge was not enabled.",
            merge_method=merge_method,
        )
    if snapshot.head_repository != snapshot.repository:
        return _result(
            "fork",
            "Cross-repository pull requests are never eligible for auto-merge execution.",
            merge_method=merge_method,
        )
    if ci_passed is not True:
        return _result(
            "failed",
            "Prerequisite CI was not verified as passing; auto-merge was not enabled.",
            merge_method=merge_method,
        )
    if not snapshot.diff_complete:
        return _result(
            "failed",
            "The evaluated diff was incomplete; auto-merge was not enabled.",
            merge_method=merge_method,
        )
    if not token:
        return _result(
            "failed",
            "Auto-merge is enabled by policy but no execution token was configured.",
            merge_method=merge_method,
        )

    owned_client = client is None
    http = client or httpx.Client(
        base_url=API_ROOT,
        headers=_headers(token),
        timeout=10.0,
    )
    trace: tuple[TraceStep, ...] = ()
    try:
        started_at = perf_counter()
        try:
            response = http.get(f"/repos/{snapshot.repository}/pulls/{snapshot.pr_number}")
        except httpx.HTTPError:
            return _result(
                "failed",
                "GitHub could not be reached while revalidating the pull request.",
                merge_method=merge_method,
                trace=(
                    _step(
                        name="github.rest.revalidate_pull_request",
                        summary="The final pre-merge GitHub read failed.",
                        started_at=started_at,
                        status="error",
                    ),
                ),
            )
        if not response.is_success:
            return _result(
                "failed",
                "GitHub rejected the final pull-request revalidation. Check execution-token permissions.",
                merge_method=merge_method,
                trace=(
                    _step(
                        name="github.rest.revalidate_pull_request",
                        summary="GitHub rejected the final pre-merge read.",
                        started_at=started_at,
                        status="error",
                        details={"http_status": response.status_code},
                    ),
                ),
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return _result(
                "failed",
                "GitHub returned malformed pull-request data during final revalidation.",
                merge_method=merge_method,
                trace=(
                    _step(
                        name="github.rest.revalidate_pull_request",
                        summary="GitHub returned malformed final evidence.",
                        started_at=started_at,
                        status="error",
                    ),
                ),
            )

        current_head = payload.get("head")
        current_base = payload.get("base")
        current_head_sha = current_head.get("sha") if isinstance(current_head, dict) else None
        current_base_sha = current_base.get("sha") if isinstance(current_base, dict) else None
        trace = (
            _step(
                name="github.rest.revalidate_pull_request",
                summary="Re-read the PR immediately before requesting auto-merge.",
                started_at=started_at,
                details={
                    "pr_number": snapshot.pr_number,
                    "head_matches": current_head_sha == snapshot.head_sha,
                    "base_matches": current_base_sha == snapshot.base_sha,
                },
            ),
        )

        if payload.get("merged") is True:
            return _result(
                "already_merged",
                "The pull request was already merged before execution completed.",
                merge_method=merge_method,
                trace=trace,
            )
        if payload.get("state") != "open":
            return _result(
                "failed",
                "The pull request is no longer open; auto-merge was not enabled.",
                merge_method=merge_method,
                trace=trace,
            )
        if payload.get("draft") is True:
            return _result(
                "draft",
                "The pull request became a draft; auto-merge was not enabled.",
                merge_method=merge_method,
                trace=trace,
            )
        if current_head_sha != snapshot.head_sha or current_base_sha != snapshot.base_sha:
            return _result(
                "stale",
                "The PR head or base changed after evaluation; a fresh Merge Gate run is required.",
                merge_method=merge_method,
                trace=trace,
            )
        if payload.get("auto_merge") is not None:
            return _result(
                "already_enabled",
                "GitHub auto-merge was already enabled for this exact pull request.",
                merge_method=merge_method,
                trace=trace,
            )

        node_id = payload.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            return _result(
                "failed",
                "GitHub did not return the pull-request node identifier required for auto-merge.",
                merge_method=merge_method,
                trace=trace,
            )

        mutation = """
mutation EnableMergeGateAutoMerge($input: EnablePullRequestAutoMergeInput!) {
  enablePullRequestAutoMerge(input: $input) {
    pullRequest {
      number
      autoMergeRequest { enabledAt mergeMethod }
    }
  }
}
""".strip()
        started_at = perf_counter()
        try:
            response = http.post(
                "https://api.github.com/graphql",
                json={
                    "query": mutation,
                    "variables": {
                        "input": {
                            "pullRequestId": node_id,
                            "mergeMethod": merge_method.upper(),
                        }
                    },
                },
            )
        except httpx.HTTPError:
            return _result(
                "failed",
                "GitHub could not be reached while enabling auto-merge.",
                merge_method=merge_method,
                trace=trace
                + (
                    _step(
                        name="github.graphql.enable_pull_request_auto_merge",
                        summary="The auto-merge mutation could not reach GitHub.",
                        started_at=started_at,
                        status="error",
                    ),
                ),
            )

        try:
            body = response.json()
        except ValueError:
            body = None
        mutation_step = _step(
            name="github.graphql.enable_pull_request_auto_merge",
            summary=(
                "Requested GitHub-native auto-merge for the verified PR."
                if response.is_success and isinstance(body, dict) and not body.get("errors")
                else "GitHub rejected the auto-merge request."
            ),
            started_at=started_at,
            status=(
                "ok"
                if response.is_success and isinstance(body, dict) and not body.get("errors")
                else "error"
            ),
            details={
                "http_status": response.status_code,
                "merge_method": merge_method,
            },
        )
        trace = trace + (mutation_step,)
        if (
            not response.is_success
            or not isinstance(body, dict)
            or body.get("errors")
            or not isinstance(body.get("data"), dict)
            or not isinstance(body["data"].get("enablePullRequestAutoMerge"), dict)
        ):
            return _result(
                "failed",
                "GitHub did not enable auto-merge. Check repository auto-merge settings, branch protection, and execution-token permissions.",
                merge_method=merge_method,
                trace=trace,
            )

        return _result(
            "enabled",
            "GitHub auto-merge is enabled and will merge after repository requirements pass.",
            merge_method=merge_method,
            trace=trace,
        )
    finally:
        if owned_client:
            http.close()
