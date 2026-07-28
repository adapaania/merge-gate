"""GitHub Actions entrypoint for automatic Merge Gate PR checks."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Iterable

from engine import AnalysisResult, analyze_decision
from execution_trace import TraceStep
from github_pr import (
    GitHubPRSnapshot,
    build_decision_from_github,
    fetch_github_pr,
    fetch_github_text_file,
)
from model import Decision
from policies import GateAction
from project_policy import parse_project_policy


CI_RESULT = {
    "success": True,
    "failure": False,
    "cancelled": False,
    "timed_out": False,
    "skipped": None,
    "unknown": None,
}


def _markdown(value: object) -> str:
    return html.escape(str(value), quote=False).replace("|", "\\|").replace("\n", " ")


def _command_value(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def load_pull_request_url(event_path: str | Path) -> str:
    """Read the PR URL from GitHub's trusted workflow event file."""

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("Merge Gate must run from a pull_request workflow event.")
    url = pull_request.get("html_url")
    if not isinstance(url, str):
        raise ValueError("The workflow event does not contain a pull-request URL.")
    return url


def apply_workflow_ci_result(decision: Decision, ci_result: str) -> tuple[Decision, TraceStep]:
    """Use the completed prerequisite job instead of Merge Gate's in-progress check."""

    if ci_result not in CI_RESULT:
        raise ValueError(f"Unknown prerequisite CI result: {ci_result}")
    value = CI_RESULT[ci_result]
    return (
        decision.model_copy(update={"ci_passed": value}),
        TraceStep(
            kind="function",
            phase="CI",
            name="apply_workflow_ci_result",
            status="ok" if value is True else "warning",
            summary=f"Used prerequisite GitHub Actions result: {ci_result}.",
            duration_ms=0.0,
            details={
                "workflow_result": ci_result,
                "ci_passed": value,
            },
        ),
    )


def build_job_summary(
    snapshot: GitHubPRSnapshot,
    result: AnalysisResult,
    trace: Iterable[TraceStep],
) -> str:
    """Create a bounded GitHub-flavored Markdown decision and trace."""

    action_label = {
        GateAction.AUTO_MERGE_CANDIDATE: "Auto-merge candidate",
        GateAction.HUMAN_REVIEW: "Human review required",
        GateAction.BLOCK: "Blocked",
    }[result.final.action]
    action_icon = {
        GateAction.AUTO_MERGE_CANDIDATE: "✅",
        GateAction.HUMAN_REVIEW: "⚠️",
        GateAction.BLOCK: "⛔",
    }[result.final.action]
    matched_rules = ", ".join(result.matched_project_rules) or "project default"
    changed_files = "\n".join(
        f"- `{_markdown(file.filename)}`"
        for file in snapshot.files[:20]
    )
    if len(snapshot.files) > 20:
        changed_files += f"\n- …and {len(snapshot.files) - 20} more"

    trace_rows = "\n".join(
        "| {step} | {kind} | `{name}` | {status} | {summary} | {duration:.2f} |".format(
            step=index,
            kind=_markdown(item.kind),
            name=_markdown(item.name),
            status=_markdown(item.status),
            summary=_markdown(item.summary),
            duration=item.duration_ms,
        )
        for index, item in enumerate(trace, start=1)
    )
    return f"""# {action_icon} Merge Gate: {action_label}

- **PR:** [{_markdown(snapshot.repository)} #{snapshot.pr_number}]({_markdown(snapshot.html_url)})
- **Head SHA:** `{_markdown(snapshot.head_sha)}`
- **Project requirements:** {_markdown(matched_rules)}
- **Judge:** {_markdown(result.judgment.source)}

## Decision

{_markdown(result.final.reason)}

| Evidence | Value |
|---|---|
| Changed files | {len(snapshot.files)} |
| Changed lines | {result.decision.diff_lines} |
| CI evidence | {_markdown(snapshot.ci_status)}; prerequisite workflow applied |
| Diff complete | {_markdown(result.decision.diff_complete)} |
| Reversibility | {_markdown(result.decision.reversible)} |
| Incident linkage | {_markdown(result.decision.touches_incident_code)} |
| Evidence citations verified | {result.verification.checked_claims} |

<details>
<summary>Changed files</summary>

{changed_files}

</details>

## Tools and functions

| Step | Type | Tool / function | Status | What happened | Time (ms) |
|---:|---|---|---|---|---:|
{trace_rows}

> Merge Gate is advisory. It did not approve, comment on, or merge this PR.
"""


def _append_environment_file(variable: str, value: str) -> None:
    target = os.getenv(variable)
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as stream:
        stream.write(value)
        if not value.endswith("\n"):
            stream.write("\n")


def _publish_result(result: AnalysisResult, summary: str) -> int:
    _append_environment_file("GITHUB_STEP_SUMMARY", summary)
    _append_environment_file("GITHUB_OUTPUT", f"action={result.final.action.value}")
    _append_environment_file(
        "GITHUB_OUTPUT",
        f"reason={result.final.reason.replace(chr(10), ' ')}",
    )

    annotation = _command_value(result.final.reason)
    if result.final.action == GateAction.BLOCK:
        print(f"::error title=Merge Gate blocked this PR::{annotation}")
        return 1
    if result.final.action == GateAction.HUMAN_REVIEW:
        print(f"::warning title=Merge Gate requires human review::{annotation}")
        return 0
    print(f"::notice title=Merge Gate candidate::{annotation}")
    return 0


def run(args: argparse.Namespace) -> int:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is required for the automatic PR check.")
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH is unavailable.")

    snapshot = fetch_github_pr(
        load_pull_request_url(event_path),
        token=token,
    )
    decision, ci_trace = apply_workflow_ci_result(
        build_decision_from_github(snapshot),
        args.ci_result,
    )
    policy_text, policy_trace = fetch_github_text_file(
        snapshot.repository,
        ref=snapshot.base_sha,
        path=args.policy_path,
        token=token,
    )
    project_policy = parse_project_policy(
        policy_text,
        source=(
            f"{snapshot.repository}:{args.policy_path}@"
            f"{snapshot.base_sha[:12]}"
        ),
    )
    result = analyze_decision(
        decision,
        judge_mode="live",
        project_policy=project_policy,
        allow_offline_fallback=False,
    )
    trace = snapshot.trace + (ci_trace, policy_trace) + result.trace
    return _publish_result(
        result,
        build_job_summary(snapshot, result, trace),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Merge Gate for a GitHub PR event.")
    parser.add_argument(
        "--policy-path",
        default=".merge-gate/policy.toml",
        help="Project policy path fetched from the PR base commit.",
    )
    parser.add_argument(
        "--ci-result",
        choices=sorted(CI_RESULT),
        default="unknown",
        help="Result of the target repository's prerequisite test job.",
    )
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except Exception as exc:
        message = _command_value(f"{type(exc).__name__}: {exc}")
        print(f"::error title=Merge Gate could not evaluate this PR::{message}")
        _append_environment_file(
            "GITHUB_STEP_SUMMARY",
            "# ⛔ Merge Gate could not evaluate this PR\n\n"
            "The gate failed closed. Inspect the workflow log for the sanitized error type.",
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
