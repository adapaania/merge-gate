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
from feedback import append_feedback
from github_pr import (
    GitHubFetchError,
    GitHubPRSnapshot,
    build_decision_from_github,
    fetch_github_pr,
)
from llm_judge import get_cached_live_judgment
from model import Decision, load_decisions
from policies import GateAction
from policy_retrieval import retrieve_policies
from execution_trace import TraceStep


PROJECT_ROOT = Path(__file__).resolve().parent
DATASETS = {
    "Challenge set": PROJECT_ROOT / "data/heldout_decisions.json",
    "Original stress test": PROJECT_ROOT / "data/decisions.json",
}
JUDGE_MODES = {
    "Offline fixture": "offline",
    "Live Claude": "live",
}
PR_SOURCES = {
    "Demo fixtures": "fixture",
    "Replay GitHub PR": "github",
}


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


def action_label(action: GateAction) -> str:
    return {
        GateAction.AUTO_MERGE_CANDIDATE: "Auto-merge candidate",
        GateAction.HUMAN_REVIEW: "Human review",
        GateAction.BLOCK: "Blocked",
    }[action]


def judgment_source_label(source: str) -> str:
    return {
        "offline_demo_fixture": "Offline fixture",
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


def render_evidence_summary(decision: Decision) -> None:
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
st.write("Evaluation and replay dashboard for an automatic GitHub merge check.")
st.caption(
    "Live gating starts from the target repository's PR workflow · no automatic merges"
)

with st.sidebar:
    st.header("Demo settings")
    selected_source_label = st.segmented_control(
        "PR source",
        list(PR_SOURCES),
        default="Demo fixtures",
        selection_mode="single",
        width="stretch",
    )
    selected_dataset = st.selectbox("Evaluation set", list(DATASETS))
    selected_mode_label = st.segmented_control(
        "Independent judge",
        list(JUDGE_MODES),
        default="Offline fixture",
        selection_mode="single",
        width="stretch",
    )
    st.caption(
        "The offline fixture is reproducible. Live mode calls the configured "
        "Claude model only when you run the gate."
    )

selected_source_label = selected_source_label or "Demo fixtures"
selected_source = PR_SOURCES[selected_source_label]
selected_mode_label = selected_mode_label or "Offline fixture"
selected_mode = JUDGE_MODES[selected_mode_label]
live_snapshot: GitHubPRSnapshot | None = None
source_trace: tuple[TraceStep, ...] = ()

if selected_source == "fixture":
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
                "Run live gate" if selected_mode == "live" else "Run gate",
                type="primary",
                icon=":material/play_arrow:",
                key="run_fixture_gate",
            )
            st.caption(f"{selected_dataset} · {selected_mode_label}")

        state_key = f"fixture::{selected_dataset}::{selected_id}::{selected_mode}"
        if state_key not in st.session_state:
            # Live mode starts with a clearly labeled offline preview. The API
            # is called only after an explicit click, so reruns cannot spend tokens.
            st.session_state[state_key] = analyze_decision(
                decision_by_id[selected_id],
                judge_mode="offline",
            )

        if run_gate:
            with st.status("Running Merge Gate…", expanded=True) as status:
                st.write("Retrieving repository policy")
                st.write(
                    "Calling the independent judge"
                    if selected_mode == "live"
                    else "Loading the deterministic demo judgment"
                )
                st.session_state[state_key] = analyze_decision(
                    decision_by_id[selected_id],
                    judge_mode=selected_mode,
                )
                st.write("Verifying evidence and composing the advisory action")
                status.update(label="Gate complete", state="complete", expanded=False)

    analysis: AnalysisResult = st.session_state[state_key]
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
            st.session_state["live_pr_snapshot"] = None
            with st.status("Reading GitHub evidence…", expanded=True) as status:
                st.write("Reading pull-request metadata and head commit")
                st.write("Reading changed files and available patches")
                st.write("Reading check runs and commit statuses")
                try:
                    fetched_snapshot = fetch_github_pr(
                        github_url,
                        token=os.getenv("GITHUB_TOKEN"),
                    )
                except (GitHubFetchError, ValueError) as exc:
                    status.update(
                        label="GitHub fetch failed",
                        state="error",
                        expanded=False,
                    )
                    st.error(str(exc), icon=":material/error:")
                else:
                    st.session_state["live_pr_snapshot"] = fetched_snapshot
                    status.update(
                        label="GitHub evidence ready",
                        state="complete",
                        expanded=False,
                    )

    live_snapshot = st.session_state.get("live_pr_snapshot")
    if live_snapshot is None:
        st.info(
            "Paste a public PR URL, or configure `GITHUB_TOKEN` for a private "
            "repository, then fetch the PR.",
            icon=":material/link:",
        )
        st.stop()

    render_live_pr_summary(live_snapshot)
    source_trace = live_snapshot.trace
    decision = build_decision_from_github(live_snapshot)
    selected_id = decision.id
    feedback_scope = "GitHub PR"
    decision_context = (
        f"Live GitHub evidence · head {live_snapshot.head_sha[:12]} · "
        f"{selected_mode_label}"
    )
    state_key = (
        f"github::{live_snapshot.repository}::{live_snapshot.pr_number}::"
        f"{live_snapshot.head_sha}::{selected_mode}"
    )

    with st.container(horizontal=True, vertical_alignment="center"):
        run_gate = st.button(
            "Run live gate" if selected_mode == "live" else "Run gate",
            type="primary",
            icon=":material/play_arrow:",
            key="run_github_gate",
        )
        st.caption(
            "The result is advisory. Merge Gate will not comment, approve, or merge."
        )

    if run_gate:
        with st.status("Running Merge Gate…", expanded=True) as status:
            st.write("Retrieving repository policy")
            st.write(
                "Calling the independent judge"
                if selected_mode == "live"
                else "Loading the deterministic demo judgment"
            )
            st.session_state[state_key] = analyze_decision(
                decision,
                judge_mode=selected_mode,
            )
            st.write("Verifying evidence and composing the advisory action")
            status.update(label="Gate complete", state="complete", expanded=False)

    if state_key not in st.session_state:
        render_evidence_summary(decision)
        render_execution_trace(
            source_trace,
            label="Show GitHub fetch tools",
        )
        st.info(
            "The PR evidence is ready. Select Run gate to produce an advisory decision.",
            icon=":material/play_circle:",
        )
        st.stop()
    analysis = st.session_state[state_key]

if (
    selected_source == "fixture"
    and selected_mode == "live"
    and analysis.judgment.source != "live_claude"
):
    st.caption(
        ":material/cloud_off: Live mode is selected, but this is the offline "
        "preview. Run the gate to call Claude."
    )
if analysis.judge_warning:
    st.warning(
        f"Live judge unavailable: {analysis.judge_warning} "
        "The recommendation uses the offline fixture and remains advisory.",
        icon=":material/warning:",
    )

st.subheader(decision.title, anchor=False)
st.caption(decision_context)
render_final_call(analysis)
render_evidence_summary(decision)

overview_tab, evidence_tab, evaluation_tab = st.tabs(
    ["Overview", "Evidence", "Evaluation"]
)

with overview_tab:
    with st.container(border=True):
        st.subheader("Why this decision", anchor=False)
        st.write(analysis.final.reason)
        st.caption(
            f"Independent judge: {action_label(analysis.judgment.action)} · "
            f"{analysis.judgment.confidence:.0%} confidence · "
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
            st.success("Feedback saved locally for later evaluation.")

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
            else "unknown"
        )
        st.caption(
            "Agent confidence: "
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
        st.subheader("Retrieved policy", anchor=False)
        if live_snapshot is None:
            st.caption(
                "The judge may cite only these policy IDs and the changed files."
            )
        else:
            st.caption(
                "Prototype limitation: retrieval uses Merge Gate's local policy "
                "fixture, not a policy file fetched from the target repository. "
                "The judge may cite only these policy IDs and changed files."
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

with st.expander(
    "How the gate works",
    icon=":material/schema:",
):
    st.write(
        "The product is an automatic escalation-policy check. A connected "
        "repository invokes it from a pull-request workflow; this dashboard "
        "exists for evaluation, inspection, and historical replay."
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
