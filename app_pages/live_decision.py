"""Merge Gate's primary page: one live pull request, one decision.

This is the default view. It fetches a public ClearLedger pull request
directly from GitHub, then runs Merge Gate on demand. A PR-number field lets
you switch between the several scenario PRs on the one connected demo
repository — it is not a generic "paste any URL" control; that stays on the
dev-only replay page.
"""

from __future__ import annotations

import os

import streamlit as st

from engine import analyze_decision
from execution_trace import TraceStep
from github_pr import (
    GitHubFetchError,
    GitHubPRSnapshot,
    build_decision_from_github,
    fetch_github_pr,
    fetch_github_text_file,
)
from llm_judge import JudgeUnavailable
from policy_schema import LoadedPolicy, parse_policy_document
from pr_rendering import (
    render_decision_flow,
    render_evidence_summary,
    render_execution_trace,
    render_live_pr_summary,
    render_project_requirements,
)
from project_policy import evaluate_project_policy

DEFAULT_DEMO_PR_URL = os.getenv(
    "MERGE_GATE_DEMO_PR_URL",
    "https://github.com/adapaania/clearledger-demo/pull/1",
)
DEMO_REPO_URL, _, _default_pr_number = DEFAULT_DEMO_PR_URL.rpartition("/pull/")
try:
    DEFAULT_PR_NUMBER = int(_default_pr_number)
except ValueError:
    DEFAULT_PR_NUMBER = 1
ORG_POLICY_REPO = os.getenv("MERGE_GATE_ORG_POLICY_REPO", "")
ORG_POLICY_REF = os.getenv("MERGE_GATE_ORG_POLICY_REF", "main")
ORG_POLICY_PATH = os.getenv("MERGE_GATE_ORG_POLICY_PATH", ".merge-gate/organization.toml")


@st.cache_data(ttl="3m", max_entries=8, show_spinner=False)
def cached_github_project(
    url: str,
    refresh_version: int,
) -> tuple[GitHubPRSnapshot, LoadedPolicy, LoadedPolicy | None, tuple[TraceStep, ...]]:
    """Read a live PR, its immutable project policy, and an optional shared
    organization baseline, with bounded API caching."""

    _ = refresh_version
    token = os.getenv("GITHUB_TOKEN")
    snapshot = fetch_github_pr(url, token=token)
    policy_text, policy_trace = fetch_github_text_file(
        snapshot.repository,
        ref=snapshot.base_sha,
        path=".merge-gate/policy.toml",
        token=token,
    )
    project_source = f"{snapshot.repository}:.merge-gate/policy.toml@{snapshot.base_sha[:12]}"
    project_policy = parse_policy_document(policy_text, section="project", source=project_source)

    organization_policy: LoadedPolicy | None = None
    trace_steps: tuple[TraceStep, ...] = (policy_trace,)
    if ORG_POLICY_REPO:
        org_text, org_trace = fetch_github_text_file(
            ORG_POLICY_REPO,
            ref=ORG_POLICY_REF,
            path=ORG_POLICY_PATH,
            token=token,
        )
        org_source = f"{ORG_POLICY_REPO}:{ORG_POLICY_PATH}@{ORG_POLICY_REF}"
        organization_policy = parse_policy_document(
            org_text, section="organization", source=org_source
        )
        trace_steps = trace_steps + (org_trace,)

    return snapshot, project_policy, organization_policy, trace_steps


st.title("Merge Gate", anchor=False)
st.write("Live pull-request control.")

st.session_state.setdefault("live_pr_refresh_version", 0)
with st.container(border=True):
    st.subheader("Live PR", anchor=False)
    st.caption(
        f"`{DEMO_REPO_URL.removeprefix('https://github.com/')}` · fetched "
        "directly from GitHub · no pasted URL"
    )
    with st.container(horizontal=True, vertical_alignment="center"):
        pr_number = st.number_input(
            "PR #",
            min_value=1,
            step=1,
            value=DEFAULT_PR_NUMBER,
            key="live_pr_number",
            width=100,
        )
        live_demo_pr_url = f"{DEMO_REPO_URL}/pull/{int(pr_number)}"
        refresh_live = st.button(
            "Refresh from GitHub",
            icon=":material/refresh:",
            key="refresh_live_demo",
        )
        st.link_button(
            "Open this PR",
            live_demo_pr_url,
            icon=":material/open_in_new:",
            type="tertiary",
        )
    if refresh_live:
        st.session_state["live_pr_refresh_version"] += 1

live_slot = st.container()
try:
    with live_slot.skeleton(height=180):
        live_snapshot, loaded_project_policy, loaded_org_policy, policy_trace_steps = (
            cached_github_project(
                live_demo_pr_url,
                st.session_state["live_pr_refresh_version"],
            )
        )
except (GitHubFetchError, ValueError) as exc:
    live_slot.error(
        f"Live GitHub evidence is temporarily unavailable: {exc}",
        icon=":material/cloud_off:",
    )
    live_slot.link_button(
        "Open this PR on GitHub",
        live_demo_pr_url,
        icon=":material/open_in_new:",
    )
    st.stop()

render_live_pr_summary(live_snapshot)
source_trace = live_snapshot.trace + policy_trace_steps
decision = build_decision_from_github(live_snapshot)
selected_id = decision.id
feedback_scope = "Live GitHub PR"
decision_context = f"Live GitHub evidence · head {live_snapshot.head_sha[:12]}"
state_key = (
    f"live::{live_snapshot.repository}::{live_snapshot.pr_number}::"
    f"{live_snapshot.head_sha}"
)

with st.container(horizontal=True, vertical_alignment="center"):
    run_gate = st.button(
        "Run Merge Gate",
        type="primary",
        icon=":material/gavel:",
        key="run_live_demo_gate",
    )
    st.caption(
        "The GitHub status above is observed. The detailed decision below "
        "is recomputed from the same PR evidence and base policy."
    )

if run_gate:
    with st.status("Running Merge Gate…", expanded=True) as status:
        st.write("Using the target repository policy from the base commit")
        st.write("Running the semantic risk judge")
        try:
            live_result = analyze_decision(
                decision,
                judge_mode="live",
                project_policy=loaded_project_policy.document,
                project_policy_source=loaded_project_policy.source,
                project_policy_hash=loaded_project_policy.content_hash,
                organization_policy=(
                    loaded_org_policy.document if loaded_org_policy else None
                ),
                organization_policy_source=(
                    loaded_org_policy.source if loaded_org_policy else None
                ),
                organization_policy_hash=(
                    loaded_org_policy.content_hash if loaded_org_policy else None
                ),
                allow_offline_fallback=False,
            )
        except JudgeUnavailable as exc:
            status.update(
                label="Semantic judge unavailable",
                state="error",
                expanded=False,
            )
            st.error(
                f"The semantic judge could not analyze this pull request: {exc}",
                icon=":material/cloud_off:",
            )
        else:
            st.session_state[state_key] = live_result
            st.write("Verifying evidence and composing the advisory action")
            status.update(
                label="Merge Gate analysis complete",
                state="complete",
                expanded=False,
            )

analysis = st.session_state.get(state_key)

st.subheader(decision.title, anchor=False)
st.caption(decision_context)

if analysis is None:
    render_evidence_summary(decision, judge_confidence=None)
    project_preview = evaluate_project_policy(loaded_project_policy.document, decision)
    render_project_requirements(project_preview.result, project_preview.matched_rule_ids)
    st.info(
        "GitHub evidence and project requirements are ready. Select "
        "**Run Merge Gate** to produce the risk judgment and final action.",
        icon=":material/gavel:",
    )
    render_execution_trace(source_trace, label="Show GitHub reads")
    st.stop()

render_decision_flow(
    analysis=analysis,
    decision=decision,
    source_trace=source_trace,
    live_snapshot=live_snapshot,
    feedback_scope=feedback_scope,
    selected_id=selected_id,
)
