"""Developer-only page: replay an arbitrary GitHub PR without changing GitHub.

Not linked from normal navigation. Enable with MERGE_GATE_DEV_MODE=1. This is
a troubleshooting and evaluation utility, not the product's normal trigger —
the production-shaped demo runs automatically from GitHub Actions and the
default proof PR lives on the Live decision page.
"""

from __future__ import annotations

import os

import streamlit as st

from engine import analyze_decision
from github_pr import (
    GitHubFetchError,
    build_decision_from_github,
    fetch_github_pr,
    fetch_github_text_file,
)
from llm_judge import JudgeUnavailable
from pr_rendering import render_decision_flow, render_live_pr_summary
from project_policy import parse_project_policy

st.title("Replay a GitHub PR", anchor=False)
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
    fetch_pr = st.form_submit_button("Fetch PR", icon=":material/cloud_download:")

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
            fetched_snapshot = fetch_github_pr(github_url, token=os.getenv("GITHUB_TOKEN"))
            fetched_policy_text, fetched_policy_trace = fetch_github_text_file(
                fetched_snapshot.repository,
                ref=fetched_snapshot.base_sha,
                path=".merge-gate/policy.toml",
                token=os.getenv("GITHUB_TOKEN"),
            )
            fetched_project_policy = parse_project_policy(
                fetched_policy_text,
                source=(
                    f"{fetched_snapshot.repository}:"
                    f".merge-gate/policy.toml@{fetched_snapshot.base_sha[:12]}"
                ),
            )
        except (GitHubFetchError, ValueError) as exc:
            status.update(label="GitHub fetch failed", state="error", expanded=False)
            st.error(str(exc), icon=":material/error:")
        else:
            st.session_state["replay_pr_snapshot"] = fetched_snapshot
            st.session_state["replay_project_policy"] = fetched_project_policy
            st.session_state["replay_policy_trace"] = fetched_policy_trace
            status.update(label="GitHub evidence ready", state="complete", expanded=False)

live_snapshot = st.session_state.get("replay_pr_snapshot")
project_policy = st.session_state.get("replay_project_policy")
policy_trace = st.session_state.get("replay_policy_trace")
if live_snapshot is None:
    st.info(
        "Paste a connected repository's PR URL to replay it. The primary "
        "live demonstration is available on the Live decision page.",
        icon=":material/link:",
    )
    st.stop()

render_live_pr_summary(live_snapshot)
source_trace = live_snapshot.trace + ((policy_trace,) if policy_trace is not None else ())
decision = build_decision_from_github(live_snapshot)
selected_id = decision.id
feedback_scope = "GitHub PR"
decision_context = f"Live GitHub evidence · head {live_snapshot.head_sha[:12]}"
state_key = f"github::{live_snapshot.repository}::{live_snapshot.pr_number}::{live_snapshot.head_sha}"

with st.container(horizontal=True, vertical_alignment="center"):
    run_gate = st.button(
        "Run Merge Gate",
        type="primary",
        icon=":material/gavel:",
        key="run_github_gate",
    )
    st.caption("The result is advisory. Merge Gate will not comment, approve, or merge.")

if run_gate:
    with st.status("Running Merge Gate…", expanded=True) as status:
        st.write("Retrieving repository policy")
        st.write("Running the semantic risk judge")
        try:
            live_result = analyze_decision(
                decision,
                judge_mode="live",
                project_policy=project_policy,
                allow_offline_fallback=False,
            )
        except JudgeUnavailable as exc:
            status.update(label="Semantic judge unavailable", state="error", expanded=False)
            st.error(
                f"The semantic judge could not analyze this pull request: {exc}",
                icon=":material/cloud_off:",
            )
        else:
            st.session_state[state_key] = live_result
            st.write("Verifying evidence and composing the advisory action")
            status.update(label="Merge Gate analysis complete", state="complete", expanded=False)

analysis = st.session_state.get(state_key)

st.subheader(decision.title, anchor=False)
st.caption(decision_context)

if analysis is None:
    st.info(
        "GitHub evidence is ready. Select **Run Merge Gate** to produce the "
        "risk judgment and final action.",
        icon=":material/gavel:",
    )
    st.stop()

render_decision_flow(
    analysis=analysis,
    decision=decision,
    source_trace=source_trace,
    live_snapshot=live_snapshot,
    feedback_scope=feedback_scope,
    selected_id=selected_id,
)
