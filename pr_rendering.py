"""Shared rendering for a single pull-request decision.

Both the live decision page and the developer-only replay page show the same
GitHub evidence, project policy, and advisory decision for one PR, so this
module holds the rendering functions they both call.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import AnalysisResult
from execution_trace import TraceStep
from github_pr import GitHubPRSnapshot
from model import Decision
from policies import GateAction, PolicyResult


def action_label(action: GateAction) -> str:
    return {
        GateAction.AUTO_MERGE_CANDIDATE: "Auto-merge candidate",
        GateAction.HUMAN_REVIEW: "Human review",
        GateAction.BLOCK: "Blocked",
    }[action]


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
                "Layer": f"Independent judge ({result.judgment.model})",
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
                "Merge Gate not run yet",
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
    *,
    matched_organization_rules: tuple[str, ...] = (),
    required_teams: tuple[str, ...] = (),
) -> None:
    if project_result is None:
        return
    with st.container(border=True):
        st.subheader("Matched policy", anchor=False)
        tagged_rules = [f"org:{rule_id}" for rule_id in matched_organization_rules] + [
            f"repo:{rule_id}" for rule_id in matched_rule_ids
        ]
        if tagged_rules:
            with st.container(horizontal=True, gap="small"):
                for rule_id in tagged_rules:
                    st.badge(rule_id, icon=":material/policy:", color="blue")
        else:
            st.badge("Policy default", icon=":material/policy:", color="gray")
        st.write(project_result.reason)
        if required_teams:
            st.caption(
                "Required reviewer teams: " + ", ".join(required_teams)
            )
        st.caption(
            "Fetched from `.merge-gate/policy.toml` (and the organization "
            "baseline, when configured) at the pull request's immutable base "
            "commit."
        )


def _known_unknown(value: bool | None) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"


def render_evidence_detail(
    decision: Decision,
    *,
    live_snapshot: GitHubPRSnapshot | None,
) -> None:
    """Render changed files, diff excerpt, and all observed GitHub checks."""

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
            "All observed GitHub checks",
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


def render_policy_detail(analysis: AnalysisResult) -> None:
    """Render the policy sections the judge was allowed to cite."""

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
            st.caption(f"Relevance {policy.score:.2f} · matched terms: {terms}")


def render_judgment_detail(analysis: AnalysisResult) -> None:
    """Render the independent judge's structured output and verification."""

    st.subheader("Independent judgment", anchor=False)
    st.caption("One input to the final action below, not the decision itself.")
    with st.container(border=True):
        with st.container(horizontal=True, gap="small"):
            st.badge(action_label(analysis.judgment.action), color="gray")
            st.badge(f"Model: {analysis.judgment.model}", color="gray")
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


def render_decision_flow(
    *,
    analysis: AnalysisResult,
    decision: Decision,
    source_trace: tuple[TraceStep, ...],
    live_snapshot: GitHubPRSnapshot | None,
    feedback_scope: str,
    selected_id: str,
) -> None:
    """Render the full post-run decision story as one vertical flow."""

    from feedback import append_feedback  # local import avoids a page-load cycle

    render_final_call(analysis)
    render_evidence_summary(decision, judge_confidence=analysis.judgment.confidence)
    render_project_requirements(
        analysis.project_policy,
        analysis.matched_project_rules,
        matched_organization_rules=analysis.matched_organization_rules,
        required_teams=analysis.required_teams,
    )

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
            st.badge("Evidence verified", icon=":material/fact_check:", color="green")
        else:
            st.badge(
                "Evidence verification failed",
                icon=":material/report:",
                color="red",
            )

    st.subheader("Pull-request evidence", anchor=False)
    render_evidence_detail(decision, live_snapshot=live_snapshot)

    st.subheader("Applicable project policy", anchor=False)
    render_policy_detail(analysis)

    render_judgment_detail(analysis)

    render_execution_trace(source_trace + analysis.trace)

    with st.expander("Compare gate layers", icon=":material/account_tree:"):
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

    with st.expander("Record human feedback", icon=":material/rate_review:"):
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
            save_feedback = st.form_submit_button("Save feedback", icon=":material/save:")
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
