"""Merge Gate's demo-day advisory interface.

Run directly with:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from engine import AnalysisResult, analyze_decision
from evaluation import bucket_table, evaluation_table
from execution_trace import TraceStep
from feedback import append_feedback
from github_pr import (
    GitHubFetchError,
    GitHubPRSnapshot,
    build_decision_from_github,
    fetch_github_pr,
    fetch_github_text_file,
)
from llm_judge import JudgeUnavailable, get_cached_live_judgment
from model import Decision, load_decisions
from policies import GateAction, PolicyResult
from policy_retrieval import retrieve_policies
from project_policy import (
    ProjectPolicy,
    evaluate_project_policy,
    parse_project_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parent
LIVE_DEMO_PR_URL = os.getenv(
    "MERGE_GATE_DEMO_PR_URL",
    "https://github.com/adapaania/clearledger-demo/pull/1",
)
DATASETS = {
    "Challenge set": PROJECT_ROOT / "data/heldout_decisions.json",
    "Original stress test": PROJECT_ROOT / "data/decisions.json",
}
PR_SOURCES = {
    "Live PR": "live_demo",
    "Evaluation fixtures": "fixture",
    "Replay another PR": "github",
}
DEFAULT_PR_SOURCE = os.getenv("MERGE_GATE_DEFAULT_SOURCE", "Live PR")


st.set_page_config(
    page_title="Merge Gate",
    page_icon=":material/security:",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data
def cached_decisions(path: str) -> list[Decision]:
    return load_decisions(path)


@st.cache_data
def cached_evaluation(path: str) -> pd.DataFrame:
    return evaluation_table(load_decisions(path))


@st.cache_data(ttl="3m", max_entries=8, show_spinner=False)
def cached_github_project(
    url: str,
    refresh_version: int,
) -> tuple[GitHubPRSnapshot, ProjectPolicy, TraceStep]:
    """Read a live PR and immutable project policy with bounded API caching."""

    _ = refresh_version
    token = os.getenv("GITHUB_TOKEN")
    snapshot = fetch_github_pr(url, token=token)
    policy_text, policy_trace = fetch_github_text_file(
        snapshot.repository,
        ref=snapshot.base_sha,
        path=".merge-gate/policy.toml",
        token=token,
    )
    project_policy = parse_project_policy(
        policy_text,
        source=(
            f"{snapshot.repository}:.merge-gate/policy.toml@"
            f"{snapshot.base_sha[:12]}"
        ),
    )
    return snapshot, project_policy, policy_trace


def action_label(action: GateAction) -> str:
    return {
        GateAction.AUTO_MERGE_CANDIDATE: "Auto-merge candidate",
        GateAction.HUMAN_REVIEW: "Human review",
        GateAction.BLOCK: "Blocked",
    }[action]


def judgment_source_label(source: str) -> str:
    return {
        "offline_demo_fixture": "Evaluation fixture",
        "live_claude": "Live Claude",
    }.get(source, source.replace("_", " ").capitalize())


def render_final_call(result: AnalysisResult) -> None:
    action = result.final.action
    message = f"{action_label(action)}: {result.final.reason}"
    if action == GateAction.AUTO_MERGE_CANDIDATE:
        st.success(message, icon=":material/check_circle:")
    elif action == GateAction.BLOCK:
        st.error(message, icon=":material/block:")
    else:
        st.warning(message, icon=":material/person_alert:")


def comparison_table(result: AnalysisResult) -> pd.DataFrame:
    rows = [
        {
            "Layer": baseline.policy,
            "Recommendation": action_label(baseline.action),
            "Why": baseline.reason,
        }
        for baseline in result.baselines
    ]
    rows.extend(
        [
            {
                "Layer": f"Independent judge ({judgment_source_label(result.judgment.source)})",
                "Recommendation": action_label(result.judgment.action),
                "Why": result.judgment.reasons[0],
            },
            {
                "Layer": result.final.policy,
                "Recommendation": action_label(result.final.action),
                "Why": result.final.reason,
            },
        ]
    )
    return pd.DataFrame(rows)


def render_evidence_summary(
    decision: Decision,
    *,
    judge_confidence: float | None,
) -> None:
    ci_label = {
        True: "CI passed",
        False: "CI failed",
        None: "CI pending or unknown",
    }[decision.ci_passed]
    ci_icon = {
        True: ":material/check_circle:",
        False: ":material/error:",
        None: ":material/pending:",
    }[decision.ci_passed]
    ci_color = {True: "green", False: "red", None: "orange"}[decision.ci_passed]
    with st.container(horizontal=True, gap="small"):
        st.badge(
            ci_label,
            icon=ci_icon,
            color=ci_color,
        )
        st.badge(
            f"{decision.diff_lines} changed lines",
            icon=":material/difference:",
            color="gray",
        )
        st.badge(
            f"{len(decision.files_touched)} changed "
            f"{'file' if len(decision.files_touched) == 1 else 'files'}",
            icon=":material/description:",
            color="gray",
        )
        if decision.agent_confidence is None:
            st.badge(
                "Authoring-agent confidence not supplied",
                icon=":material/smart_toy:",
                color="gray",
            )
        else:
            st.badge(
                f"Authoring-agent confidence {decision.agent_confidence:.0%}",
                icon=":material/smart_toy:",
                color="blue",
            )
        if judge_confidence is None:
            st.badge(
                "Claude analysis not run",
                icon=":material/model_training:",
                color="gray",
            )
        else:
            st.badge(
                f"Judge confidence {judge_confidence:.0%}",
                icon=":material/model_training:",
                color="gray",
            )
        if not decision.diff_complete:
            st.badge(
                "Diff incomplete",
                icon=":material/warning:",
                color="orange",
            )


def render_execution_trace(
    steps: tuple[TraceStep, ...],
    *,
    label: str = "Show tools and functions",
) -> None:
    """Render a sanitized, compact trace without exposing raw PR content."""

    with st.expander(label, icon=":material/terminal:"):
        st.caption(
            "This trace shows the read-only tools and internal functions that ran. "
            "Credentials, raw prompts, provider responses, and diff contents are omitted."
        )
        rows = [
            {
                "Step": index,
                "Type": step.kind.capitalize(),
                "Phase": step.phase,
                "Tool / function": step.name,
                "Status": step.status.capitalize(),
                "What happened": step.summary,
                "Time (ms)": step.duration_ms,
            }
            for index, step in enumerate(steps, start=1)
        ]
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "Step": st.column_config.NumberColumn(width="small"),
                "Type": st.column_config.TextColumn(width="small"),
                "Phase": st.column_config.TextColumn(width="small"),
                "Tool / function": st.column_config.TextColumn(width="medium"),
                "Status": st.column_config.TextColumn(width="small"),
                "What happened": st.column_config.TextColumn(width="large"),
                "Time (ms)": st.column_config.NumberColumn(format="%.2f"),
            },
        )


def _known_unknown(value: bool | None) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"


def _check_color(state: str) -> str:
    normalized = state.lower()
    if normalized in {"success", "neutral", "skipped"}:
        return "green"
    if normalized in {
        "failure",
        "error",
        "cancelled",
        "timed_out",
        "action_required",
    }:
        return "red"
    if normalized in {"queued", "in_progress", "pending", "requested", "waiting"}:
        return "orange"
    return "gray"


def render_live_pr_summary(snapshot: GitHubPRSnapshot) -> None:
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader(
                f"{snapshot.repository} #{snapshot.pr_number}",
                anchor=False,
            )
            st.link_button(
                "Open on GitHub",
                snapshot.html_url,
                icon=":material/open_in_new:",
                type="tertiary",
            )
        st.caption(
            f"@{snapshot.author} · {snapshot.head_ref} → {snapshot.base_ref} · "
            f"head {snapshot.head_sha[:12]} · "
            f"fetched {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        with st.container(horizontal=True, gap="small"):
            ci_color = {
                "passed": "green",
                "failed": "red",
                "pending": "orange",
                "unknown": "gray",
            }[snapshot.ci_status]
            st.badge(
                f"CI {snapshot.ci_status}",
                icon=":material/checklist:",
                color=ci_color,
            )
            st.badge(
                f"{len(snapshot.files)} changed files",
                icon=":material/description:",
                color="gray",
            )
            st.badge(
                f"+{snapshot.additions} / −{snapshot.deletions}",
                icon=":material/difference:",
                color="gray",
            )
            if snapshot.draft:
                st.badge("Draft", icon=":material/edit_note:", color="gray")

        visible_checks = [
            check
            for check in snapshot.checks
            if check.name in {"ClearLedger tests", "Merge Gate"}
        ]
        if visible_checks:
            st.markdown("**Observed GitHub checks**")
            with st.container(horizontal=True, gap="small"):
                for check in visible_checks:
                    st.badge(
                        f"{check.name}: {check.state.replace('_', ' ')}",
                        icon=":material/check_circle:",
                        color=_check_color(check.state),
                    )
            merge_gate_check = next(
                (
                    check
                    for check in visible_checks
                    if check.name == "Merge Gate"
                    and getattr(check, "details_url", None)
                ),
                None,
            )
            if merge_gate_check is not None:
                st.link_button(
                    "Open the actual Merge Gate check",
                    getattr(merge_gate_check, "details_url"),
                    icon=":material/open_in_new:",
                    type="tertiary",
                )


def render_project_requirements(
    project_result: PolicyResult | None,
    matched_rule_ids: tuple[str, ...],
) -> None:
    if project_result is None:
        return
    with st.container(border=True):
        st.subheader("Matched project requirements", anchor=False)
        if matched_rule_ids:
            with st.container(horizontal=True, gap="small"):
                for rule_id in matched_rule_ids:
                    st.badge(
                        rule_id,
                        icon=":material/policy:",
                        color="blue",
                    )
        else:
            st.badge("Project default", icon=":material/policy:", color="gray")
        st.write(project_result.reason)
        st.caption(
            "Fetched from `.merge-gate/policy.toml` at the pull request's "
            "immutable base commit."
        )


def render_evaluation(dataset_name: str, dataset_path: Path) -> None:
    table = cached_evaluation(str(dataset_path))
    decisions = cached_decisions(str(dataset_path))
    raw_row = table.loc[table["Policy"] == "Raw-evidence gate"].iloc[0]

    st.subheader(f"Policy evaluation · {dataset_name}", anchor=False)
    st.caption(
        "The target is high critical recall without sending every pull request "
        "to a person."
    )
    with st.container(horizontal=True, gap="small"):
        st.metric(
            "Critical recall",
            f"{raw_row['Critical recall']:.0%}",
            border=True,
            help="Share of labeled review-required changes escalated by the raw-evidence gate.",
        )
        st.metric(
            "Autonomous coverage",
            f"{raw_row['Autonomous coverage']:.0%}",
            border=True,
            help="Share of examples allowed to proceed without human review.",
        )
        st.metric(
            "False escalations",
            int(raw_row["False escalations"]),
            border=True,
            help="Safe changes unnecessarily sent to a human.",
        )
    st.caption(f"Missed escalations: {int(raw_row['Missed escalations'])}")

    points = (
        alt.Chart(table)
        .mark_circle(size=180)
        .encode(
            x=alt.X(
                "False escalations:Q",
                title="False escalations · unnecessary review",
                scale=alt.Scale(zero=True),
            ),
            y=alt.Y(
                "Missed escalations:Q",
                title="Missed escalations · unsafe autonomy",
                scale=alt.Scale(zero=True),
            ),
            color=alt.Color("Policy:N", legend=None),
            tooltip=[
                "Policy:N",
                "Missed escalations:Q",
                "False escalations:Q",
                alt.Tooltip("Critical recall:Q", format=".0%"),
                alt.Tooltip("Autonomous coverage:Q", format=".0%"),
            ],
        )
    )
    labels = points.mark_text(align="left", baseline="middle", dx=9).encode(
        text="Policy:N"
    )
    st.altair_chart(
        (points + labels).properties(
            title="Policy frontier · the lower-left corner is better"
        ),
        width="stretch",
    )

    if dataset_name == "Original stress test":
        st.warning(
            "The legacy path-risk ceiling is perfect here because `path_risk` was "
            "used to create these labels. Treat it as label leakage, not model performance.",
            icon=":material/science:",
        )
    else:
        st.caption(
            "This challenge set deliberately breaks path-risk and diff-size shortcuts. "
            "It is hand-authored and small, so it demonstrates evaluation design—not "
            "production readiness."
        )

    cached_judgments = sum(
        get_cached_live_judgment(decision, retrieve_policies(decision)) is not None
        for decision in decisions
    )
    if cached_judgments == len(decisions):
        st.success(
            f"Structured live-judge eval is complete: {cached_judgments}/{len(decisions)} "
            "predictions are cached and included in the table.",
            icon=":material/model_training:",
        )
    else:
        st.caption(
            f"Structured live-judge eval cache: {cached_judgments}/{len(decisions)}. "
            "Run `python evaluate_judge.py` to evaluate the model over the full "
            "challenge set; this intentionally requires an explicit API call."
        )

    with st.expander(
        "Compare all policies",
        icon=":material/table_chart:",
    ):
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                "Critical recall": st.column_config.ProgressColumn(
                    "Critical recall",
                    min_value=0.0,
                    max_value=1.0,
                    format="percent",
                ),
                "Autonomous coverage": st.column_config.ProgressColumn(
                    "Autonomous coverage",
                    min_value=0.0,
                    max_value=1.0,
                    format="percent",
                ),
                "Escalation precision": st.column_config.ProgressColumn(
                    "Escalation precision",
                    min_value=0.0,
                    max_value=1.0,
                    format="percent",
                ),
            },
        )

    with st.expander(
        "Inspect scenarios",
        icon=":material/search:",
    ):
        policy_name = st.selectbox(
            "Policy",
            table["Policy"].tolist(),
            index=table["Policy"].tolist().index("Raw-evidence gate"),
            key=f"bucket_policy::{dataset_name}",
        )
        st.dataframe(
            bucket_table(decisions, policy_name),
            hide_index=True,
            width="stretch",
        )


st.title("Merge Gate", anchor=False)
st.write("Live pull-request control and evaluation dashboard.")
st.caption(
    "The GitHub workflow is the product · this dashboard exposes its evidence, "
    "requirements, decision pipeline, and evaluation"
)

with st.sidebar:
    st.header("Demo settings")
    default_source = (
        DEFAULT_PR_SOURCE
        if DEFAULT_PR_SOURCE in PR_SOURCES
        else "Live PR"
    )
    selected_source_label = st.segmented_control(
        "PR source",
        list(PR_SOURCES),
        default=default_source,
        selection_mode="single",
        width="stretch",
    )
    selected_dataset = st.selectbox("Evaluation set", list(DATASETS))
    st.caption(
        "Live PR and replay call Claude only after you select "
        "**Analyze with Claude**. Evaluation fixtures remain test data."
    )

selected_source_label = selected_source_label or default_source
selected_source = PR_SOURCES[selected_source_label]
live_snapshot: GitHubPRSnapshot | None = None
project_policy: ProjectPolicy | None = None
source_trace: tuple[TraceStep, ...] = ()
analysis: AnalysisResult | None = None

if selected_source == "live_demo":
    st.session_state.setdefault("live_pr_refresh_version", 0)
    with st.container(border=True):
        st.subheader("Live PR", anchor=False)
        st.caption(
            "Public ClearLedger pull request · fetched directly from GitHub · "
            "no pasted URL"
        )
        with st.container(horizontal=True, vertical_alignment="center"):
            refresh_live = st.button(
                "Refresh from GitHub",
                icon=":material/refresh:",
                key="refresh_live_demo",
            )
            st.link_button(
                "Open proof PR",
                LIVE_DEMO_PR_URL,
                icon=":material/open_in_new:",
                type="tertiary",
            )
        if refresh_live:
            st.session_state["live_pr_refresh_version"] += 1

    live_slot = st.container()
    try:
        with live_slot.skeleton(height=180):
            live_snapshot, project_policy, policy_trace = cached_github_project(
                LIVE_DEMO_PR_URL,
                st.session_state["live_pr_refresh_version"],
            )
    except (GitHubFetchError, ValueError) as exc:
        live_slot.error(
            f"Live GitHub evidence is temporarily unavailable: {exc}",
            icon=":material/cloud_off:",
        )
        live_slot.link_button(
            "Open the live PR on GitHub",
            LIVE_DEMO_PR_URL,
            icon=":material/open_in_new:",
        )
        st.stop()

    render_live_pr_summary(live_snapshot)
    source_trace = live_snapshot.trace + (policy_trace,)
    decision = build_decision_from_github(live_snapshot)
    selected_id = decision.id
    feedback_scope = "Live GitHub PR"
    decision_context = (
        f"Live GitHub evidence · head {live_snapshot.head_sha[:12]}"
    )
    state_key = (
        f"live::{live_snapshot.repository}::{live_snapshot.pr_number}::"
        f"{live_snapshot.head_sha}::claude"
    )

    with st.container(horizontal=True, vertical_alignment="center"):
        run_gate = st.button(
            "Analyze with Claude",
            type="primary",
            icon=":material/model_training:",
            key="run_live_demo_gate",
        )
        st.caption(
            "The GitHub status above is observed. The detailed decision below "
            "is recomputed from the same PR evidence and base policy."
        )

    if run_gate:
        with st.status("Running Merge Gate…", expanded=True) as status:
            st.write("Using the target repository policy from the base commit")
            st.write("Calling Claude for a structured risk judgment")
            try:
                live_result = analyze_decision(
                    decision,
                    judge_mode="live",
                    project_policy=project_policy,
                    allow_offline_fallback=False,
                )
            except JudgeUnavailable as exc:
                status.update(
                    label="Claude analysis unavailable",
                    state="error",
                    expanded=False,
                )
                st.error(
                    f"Claude could not analyze this pull request: {exc}",
                    icon=":material/cloud_off:",
                )
            else:
                st.session_state[state_key] = live_result
                st.write("Verifying evidence and composing the advisory action")
                status.update(
                    label="Claude analysis complete",
                    state="complete",
                    expanded=False,
                )
    analysis = st.session_state.get(state_key)

elif selected_source == "fixture":
    decisions = cached_decisions(str(DATASETS[selected_dataset]))
    decision_by_id = {decision.id: decision for decision in decisions}

    with st.container(border=True):
        selected_id = st.selectbox(
            "Pull request",
            list(decision_by_id),
            format_func=lambda item: f"{item} · {decision_by_id[item].title}",
            key="fixture_pr",
        )
        with st.container(horizontal=True, vertical_alignment="center"):
            run_gate = st.button(
                "Recompute evaluation fixture",
                type="primary",
                icon=":material/science:",
                key="run_fixture_gate",
            )
            st.caption(
                f"{selected_dataset} · deterministic test fixture, not a live model"
            )

        state_key = f"fixture::{selected_dataset}::{selected_id}"
        if state_key not in st.session_state:
            st.session_state[state_key] = analyze_decision(
                decision_by_id[selected_id],
                judge_mode="offline",
            )

        if run_gate:
            with st.status("Running Merge Gate…", expanded=True) as status:
                st.write("Retrieving repository policy")
                st.write("Loading the deterministic evaluation judgment")
                st.session_state[state_key] = analyze_decision(
                    decision_by_id[selected_id],
                    judge_mode="offline",
                )
                st.write("Verifying evidence and composing the advisory action")
                status.update(label="Gate complete", state="complete", expanded=False)

    analysis = st.session_state[state_key]
    decision = analysis.decision
    decision_context = (
        f"{decision.id} · scenario: {decision.bucket.replace('_', ' ')}"
    )
    feedback_scope = selected_dataset
else:
    with st.container(border=True):
        st.subheader("Replay a GitHub PR", anchor=False)
        st.caption(
            "Debug utility: replay an existing PR without changing GitHub. "
            "The production-shaped demo runs automatically from GitHub Actions."
        )
        with st.form("github_pr_fetch", border=False):
            github_url = st.text_input(
                "GitHub pull-request URL",
                placeholder="https://github.com/owner/repository/pull/42",
                key="github_pr_url",
            )
            fetch_pr = st.form_submit_button(
                "Fetch PR",
                icon=":material/cloud_download:",
            )

        if fetch_pr:
            st.session_state["replay_pr_snapshot"] = None
            st.session_state["replay_project_policy"] = None
            st.session_state["replay_policy_trace"] = None
            with st.status("Reading GitHub evidence…", expanded=True) as status:
                st.write("Reading pull-request metadata and head commit")
                st.write("Reading changed files and available patches")
                st.write("Reading check runs and commit statuses")
                st.write("Reading project policy from the immutable base commit")
                try:
                    fetched_snapshot = fetch_github_pr(
                        github_url,
                        token=os.getenv("GITHUB_TOKEN"),
                    )
                    fetched_policy_text, fetched_policy_trace = (
                        fetch_github_text_file(
                            fetched_snapshot.repository,
                            ref=fetched_snapshot.base_sha,
                            path=".merge-gate/policy.toml",
                            token=os.getenv("GITHUB_TOKEN"),
                        )
                    )
                    fetched_project_policy = parse_project_policy(
                        fetched_policy_text,
                        source=(
                            f"{fetched_snapshot.repository}:"
                            ".merge-gate/policy.toml@"
                            f"{fetched_snapshot.base_sha[:12]}"
                        ),
                    )
                except (GitHubFetchError, ValueError) as exc:
                    status.update(
                        label="GitHub fetch failed",
                        state="error",
                        expanded=False,
                    )
                    st.error(str(exc), icon=":material/error:")
                else:
                    st.session_state["replay_pr_snapshot"] = fetched_snapshot
                    st.session_state["replay_project_policy"] = (
                        fetched_project_policy
                    )
                    st.session_state["replay_policy_trace"] = fetched_policy_trace
                    status.update(
                        label="GitHub evidence ready",
                        state="complete",
                        expanded=False,
                    )

    live_snapshot = st.session_state.get("replay_pr_snapshot")
    project_policy = st.session_state.get("replay_project_policy")
    policy_trace = st.session_state.get("replay_policy_trace")
    if live_snapshot is None:
        st.info(
            "Paste a connected repository's PR URL to replay it. The primary "
            "live demonstration is available under **Live PR**.",
            icon=":material/link:",
        )
        st.stop()

    render_live_pr_summary(live_snapshot)
    source_trace = live_snapshot.trace + (
        (policy_trace,) if policy_trace is not None else ()
    )
    decision = build_decision_from_github(live_snapshot)
    selected_id = decision.id
    feedback_scope = "GitHub PR"
    decision_context = (
        f"Live GitHub evidence · head {live_snapshot.head_sha[:12]}"
    )
    state_key = (
        f"github::{live_snapshot.repository}::{live_snapshot.pr_number}::"
        f"{live_snapshot.head_sha}::claude"
    )

    with st.container(horizontal=True, vertical_alignment="center"):
        run_gate = st.button(
            "Analyze with Claude",
            type="primary",
            icon=":material/model_training:",
            key="run_github_gate",
        )
        st.caption(
            "The result is advisory. Merge Gate will not comment, approve, or merge."
        )

    if run_gate:
        with st.status("Running Merge Gate…", expanded=True) as status:
            st.write("Retrieving repository policy")
            st.write("Calling Claude for a structured risk judgment")
            try:
                live_result = analyze_decision(
                    decision,
                    judge_mode="live",
                    project_policy=project_policy,
                    allow_offline_fallback=False,
                )
            except JudgeUnavailable as exc:
                status.update(
                    label="Claude analysis unavailable",
                    state="error",
                    expanded=False,
                )
                st.error(
                    f"Claude could not analyze this pull request: {exc}",
                    icon=":material/cloud_off:",
                )
            else:
                st.session_state[state_key] = live_result
                st.write("Verifying evidence and composing the advisory action")
                status.update(
                    label="Claude analysis complete",
                    state="complete",
                    expanded=False,
                )

    analysis = st.session_state.get(state_key)

st.subheader(decision.title, anchor=False)
st.caption(decision_context)
if analysis is None:
    render_evidence_summary(
        decision,
        judge_confidence=None,
    )
    if project_policy is not None:
        project_preview = evaluate_project_policy(project_policy, decision)
        render_project_requirements(
            project_preview.result,
            project_preview.matched_rule_ids,
        )
    st.info(
        "GitHub evidence and project requirements are ready. Select "
        "**Analyze with Claude** to produce the risk judgment and final action.",
        icon=":material/model_training:",
    )
    render_execution_trace(
        source_trace,
        label="Show GitHub reads",
    )
    st.stop()

render_final_call(analysis)
render_evidence_summary(
    decision,
    judge_confidence=analysis.judgment.confidence,
)
render_project_requirements(
    analysis.project_policy,
    analysis.matched_project_rules,
)

decision_tab, evidence_tab, evaluation_tab, methodology_tab = st.tabs(
    ["Live decision", "Evidence & policy", "Evaluation", "Methodology"]
)

with decision_tab:
    with st.container(border=True):
        st.subheader("Why this decision", anchor=False)
        st.write(analysis.final.reason)
        st.caption(
            f"Independent risk judge: {action_label(analysis.judgment.action)} · "
            f"judge confidence {analysis.judgment.confidence:.0%} · "
            f"{analysis.verification.checked_claims} evidence "
            f"{'citation' if analysis.verification.checked_claims == 1 else 'citations'} "
            "checked"
        )
        if analysis.verification.valid:
            st.badge(
                "Evidence verified",
                icon=":material/fact_check:",
                color="green",
            )
        else:
            st.badge(
                "Evidence verification failed",
                icon=":material/report:",
                color="red",
            )

    render_execution_trace(source_trace + analysis.trace)

    with st.expander(
        "Compare gate layers",
        icon=":material/account_tree:",
    ):
        st.dataframe(
            comparison_table(analysis),
            hide_index=True,
            width="stretch",
            column_config={
                "Layer": st.column_config.TextColumn(width="medium"),
                "Recommendation": st.column_config.TextColumn(width="medium"),
                "Why": st.column_config.TextColumn(width="large"),
            },
        )

    with st.expander(
        "Record human feedback",
        icon=":material/rate_review:",
    ):
        with st.form(f"feedback::{feedback_scope}::{selected_id}"):
            human_action = st.segmented_control(
                "Your assessment",
                ["Agree", "Override to review", "Override to approve"],
                default="Agree",
                selection_mode="single",
            )
            feedback_reason = st.text_area(
                "Why?",
                placeholder="Optional context for the next evaluation run",
            )
            save_feedback = st.form_submit_button(
                "Save feedback",
                icon=":material/save:",
            )
        if save_feedback:
            append_feedback(
                decision_id=decision.id,
                gate_action=analysis.final.action.value,
                human_action=human_action or "Agree",
                reason=feedback_reason,
            )
            st.success(
                "Feedback saved in this app instance for later evaluation. "
                "Hosted demo storage is not durable."
            )

with evidence_tab:
    evidence_column, policy_column = st.columns([1, 1])
    with evidence_column:
        st.subheader("Pull-request evidence", anchor=False)
        st.markdown("**Changed files**")
        st.code("\n".join(decision.files_touched), language=None)
        st.markdown("**Diff excerpt**")
        st.code(
            decision.diff_excerpt or "No diff excerpt supplied.",
            language="diff",
            wrap_lines=True,
        )
        confidence_label = (
            f"{decision.agent_confidence:.0%}"
            if decision.agent_confidence is not None
            else "not supplied"
        )
        st.caption(
            "Authoring-agent confidence: "
            f"{confidence_label}"
            f" · reversible: {_known_unknown(decision.reversible)}"
            f" · incident-linked: {_known_unknown(decision.touches_incident_code)}"
        )
        if live_snapshot is not None:
            with st.expander(
                "Observed GitHub checks",
                icon=":material/checklist:",
            ):
                if live_snapshot.checks:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Check": check.name,
                                    "Source": check.source.replace("_", " "),
                                    "State": check.state,
                                }
                                for check in live_snapshot.checks
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.caption("No check runs or commit statuses were observable.")

    with policy_column:
        st.subheader("Applicable project policy", anchor=False)
        if analysis.project_policy is not None:
            st.caption(
                "Fetched from the target repository at the pull request's "
                "immutable base commit. The judge may cite only these matched "
                "rule IDs and changed files."
            )
        else:
            st.caption(
                "The judge may cite only these policy IDs and the changed files."
            )
        if not analysis.policies:
            st.info(
                "No repository policy crossed the retrieval threshold. "
                "The gate therefore requires review.",
                icon=":material/search_off:",
            )
        for policy in analysis.policies:
            with st.expander(
                f"{policy.policy_id} · {policy.title}",
                expanded=policy is analysis.policies[0],
            ):
                st.write(policy.text)
                terms = ", ".join(policy.matched_terms) or "domain boost only"
                st.caption(
                    f"Relevance {policy.score:.2f} · matched terms: {terms}"
                )

    st.subheader("Independent judgment", anchor=False)
    st.caption("One input to the final action below, not the decision itself.")
    with st.container(border=True):
        with st.container(horizontal=True, gap="small"):
            st.badge(action_label(analysis.judgment.action), color="gray")
            st.badge(judgment_source_label(analysis.judgment.source), color="gray")
            st.badge(
                f"{analysis.judgment.confidence:.0%} confidence",
                color="gray",
            )
        st.write(" ".join(analysis.judgment.reasons))
        if analysis.judgment.uncertainties:
            st.markdown("**Uncertainties**")
            for uncertainty in analysis.judgment.uncertainties:
                st.write(f"- {uncertainty}")

    if analysis.verification.valid:
        st.caption(
            ":material/fact_check: "
            f"Verified {analysis.verification.checked_claims} evidence citation(s).",
        )
    else:
        st.error("Evidence verification failed.", icon=":material/report:")
        for error in analysis.verification.errors:
            st.write(f"- {error}")

    with st.expander(
        "View raw structured output",
        icon=":material/data_object:",
    ):
        st.json(analysis.judgment.model_dump(mode="json"), expanded=True)

with evaluation_tab:
    render_evaluation(selected_dataset, DATASETS[selected_dataset])

with methodology_tab:
    st.subheader("How the gate works", anchor=False)
    st.write(
        "The product is an automatic escalation-policy check. A connected "
        "repository invokes it from a pull-request workflow; this dashboard "
        "makes one live run inspectable and supports evaluation and replay."
    )
    st.markdown(
        """
1. **Collect evidence** — changed files, diff size, CI, reversibility, incident link.
2. **Apply hard controls** — failed CI blocks; sensitive or unknown changes require review.
3. **Retrieve policy** — select versioned repository rules relevant to the change.
4. **Judge independently** — use a structured model output, not the author's confidence.
5. **Verify claims** — reject invented file paths or policy IDs.
6. **Compose an advisory action** — the most restrictive valid control wins.
7. **Measure and learn** — compare misses, false escalations, coverage, and overrides.
"""
    )
    st.caption(
        "This prototype demonstrates the control and evaluation loop. It does "
        "not claim production safety, and it never performs a merge."
    )
