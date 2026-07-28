"""End-to-end advisory decision engine."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from judgment import JudgeResult
from llm_judge import JudgeUnavailable, get_judgment
from model import Decision
from organization_policy import OrganizationPolicy, evaluate_organization_policy
from policies import GateAction, PolicyResult, baseline_policy_results, raw_evidence_result
from policy_retrieval import PolicyMatch, retrieve_policies
from policy_schema import PolicySourceRef, combine_document_results
from project_policy import (
    ProjectPolicy,
    evaluate_project_policy,
    project_policy_matches,
)
from execution_trace import TraceStep
from verifier import VerificationResult, verify_judgment


@dataclass(frozen=True)
class AnalysisResult:
    """Full output: baselines, retrieved policies, judge, verification, and final action."""

    decision: Decision
    baselines: tuple[PolicyResult, ...]
    policies: tuple[PolicyMatch, ...]
    judgment: JudgeResult
    verification: VerificationResult
    final: PolicyResult
    project_policy: PolicyResult | None = None
    matched_project_rules: tuple[str, ...] = ()
    matched_organization_rules: tuple[str, ...] = ()
    required_teams: tuple[str, ...] = ()
    policy_requires_ci: bool = False
    policy_sources: tuple[PolicySourceRef, ...] = ()
    judge_warning: str | None = None
    trace: tuple[TraceStep, ...] = ()


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1_000, 2)


def compose_final_action(
    deterministic: PolicyResult,
    judgment: JudgeResult,
    verification: VerificationResult,
    project_policy: PolicyResult | None = None,
) -> PolicyResult:
    """Compose a conservative advisory action from verified pipeline outputs."""

    if deterministic.action == GateAction.BLOCK:
        return deterministic
    if project_policy is not None and project_policy.action == GateAction.BLOCK:
        return project_policy
    # Unsupported judge output → force human review.
    if not verification.valid:
        return PolicyResult(
            "Hybrid Merge Gate",
            GateAction.HUMAN_REVIEW,
            "The model recommendation contained unsupported evidence.",
        )
    # Deterministic already wants a human.
    if deterministic.action == GateAction.HUMAN_REVIEW:
        return PolicyResult(
            "Hybrid Merge Gate",
            GateAction.HUMAN_REVIEW,
            deterministic.reason,
        )
    if project_policy is not None and project_policy.action == GateAction.HUMAN_REVIEW:
        return PolicyResult(
            "Hybrid Merge Gate",
            GateAction.HUMAN_REVIEW,
            project_policy.reason,
        )
    # Judge says block, but only a human can confirm → escalate to review.
    if judgment.action == GateAction.BLOCK:
        return PolicyResult(
            "Hybrid Merge Gate",
            GateAction.HUMAN_REVIEW,
            "The model found a possible blocking risk; a human must confirm it.",
        )

    # Judge asks for review.
    if judgment.action == GateAction.HUMAN_REVIEW:
        return PolicyResult(
            "Hybrid Merge Gate",
            GateAction.HUMAN_REVIEW,
            judgment.reasons[0],
        )

    # Hard checks + judge agree low risk → auto-merge candidate.
    return PolicyResult(
        "Hybrid Merge Gate",
        GateAction.AUTO_MERGE_CANDIDATE,
        "Hard checks passed and both policy and judge found low verified risk.",
    )


def analyze_decision(
    decision: Decision,
    *,
    judge_mode: str = "offline",
    project_policy: ProjectPolicy | None = None,
    organization_policy: OrganizationPolicy | None = None,
    project_policy_source: str | None = None,
    project_policy_hash: str | None = None,
    organization_policy_source: str | None = None,
    organization_policy_hash: str | None = None,
    allow_offline_fallback: bool = True,
) -> AnalysisResult:
    """Run retrieval → judge → verify → hard checks → conservative composition.

    ``project_policy`` and ``organization_policy`` are independent: either,
    both, or neither may be supplied. When both are present, the repository
    overlay can only tighten the organization baseline, never weaken it — the
    effective result is always at least as restrictive as each layer alone.
    The optional ``*_source``/``*_hash`` arguments are provenance only; they
    do not affect the decision, only what gets recorded about it.
    """

    trace: list[TraceStep] = []

    project_result: PolicyResult | None = None
    matched_project_rules: tuple[str, ...] = ()
    matched_organization_rules: tuple[str, ...] = ()
    required_teams: tuple[str, ...] = ()
    policy_requires_ci = False
    policy_sources: list[PolicySourceRef] = []
    if project_policy is None:
        started_at = perf_counter()
        policies = retrieve_policies(decision)
        trace.append(
            TraceStep(
                kind="function",
                phase="Policy",
                name="retrieve_policies",
                summary=f"Retrieved {len(policies)} relevant repository policy sections.",
                duration_ms=_duration_ms(started_at),
                details={
                    "matches": len(policies),
                    "policy_ids": ", ".join(policy.policy_id for policy in policies) or "none",
                },
            )
        )
    else:
        organization_result: PolicyResult | None = None
        if organization_policy is not None:
            started_at = perf_counter()
            organization_evaluation = evaluate_organization_policy(organization_policy, decision)
            organization_result = organization_evaluation.result
            matched_organization_rules = organization_evaluation.matched_rule_ids
            required_teams = tuple(
                dict.fromkeys(required_teams + organization_evaluation.required_teams)
            )
            policy_requires_ci = policy_requires_ci or organization_evaluation.requires_ci
            policy_sources.append(
                PolicySourceRef(
                    scope="organization",
                    name=organization_policy.name,
                    version=organization_policy.version,
                    content_hash=organization_policy_hash,
                    source=organization_policy_source or "organization policy",
                )
            )
            trace.append(
                TraceStep(
                    kind="function",
                    phase="Policy",
                    name="evaluate_organization_policy",
                    summary=(
                        f"Applied {organization_policy.name} baseline; "
                        f"result was {organization_result.action.value}."
                    ),
                    duration_ms=_duration_ms(started_at),
                    details={
                        "organization": organization_policy.name,
                        "version": organization_policy.version,
                        "matched_rules": ", ".join(matched_organization_rules) or "default",
                        "action": organization_result.action.value,
                    },
                )
            )

        started_at = perf_counter()
        project_evaluation = evaluate_project_policy(project_policy, decision)
        project_result = project_evaluation.result
        matched_project_rules = project_evaluation.matched_rule_ids
        required_teams = tuple(dict.fromkeys(required_teams + project_evaluation.required_teams))
        policy_requires_ci = policy_requires_ci or project_evaluation.requires_ci
        policy_sources.append(
            PolicySourceRef(
                scope="project",
                name=project_policy.name,
                version=project_policy.version,
                content_hash=project_policy_hash,
                source=project_policy_source or "project policy",
            )
        )
        trace.append(
            TraceStep(
                kind="function",
                phase="Policy",
                name="evaluate_project_policy",
                summary=(
                    f"Applied {project_policy.name} requirements; "
                    f"result was {project_result.action.value}."
                ),
                duration_ms=_duration_ms(started_at),
                details={
                    "project": project_policy.name,
                    "version": project_policy.version,
                    "matched_rules": ", ".join(matched_project_rules) or "default",
                    "action": project_result.action.value,
                },
            )
        )

        if organization_result is not None:
            started_at = perf_counter()
            project_result = combine_document_results([organization_result, project_result])
            trace.append(
                TraceStep(
                    kind="function",
                    phase="Policy",
                    name="combine_policy_layers",
                    summary=(
                        "Combined organization and project policy; effective result was "
                        f"{project_result.action.value}."
                    ),
                    duration_ms=_duration_ms(started_at),
                    details={"action": project_result.action.value},
                )
            )

        started_at = perf_counter()
        policies = project_policy_matches(project_policy, decision)
        trace.append(
            TraceStep(
                kind="function",
                phase="Policy",
                name="project_policy_matches",
                summary=f"Supplied {len(policies)} applicable project rules to the judge.",
                duration_ms=_duration_ms(started_at),
                details={
                    "matches": len(policies),
                    "policy_ids": ", ".join(policy.policy_id for policy in policies) or "none",
                },
            )
        )

    warning: str | None = None
    started_at = perf_counter()
    try:
        judgment = get_judgment(decision, policies, mode=judge_mode)
    except JudgeUnavailable as exc:
        if not allow_offline_fallback:
            raise
        warning = str(exc)
        judgment = get_judgment(decision, policies, mode="offline")
    trace.append(
        TraceStep(
            kind="function",
            phase="Judge",
            name="get_judgment",
            status="warning" if warning else "ok",
            summary=(
                "The requested live judge was unavailable; used the offline fixture."
                if warning
                else f"Produced a structured {judgment.source.replace('_', ' ')} judgment."
            ),
            duration_ms=_duration_ms(started_at),
            details={
                "requested_mode": judge_mode,
                "result_source": judgment.source,
                "action": judgment.action.value,
                "fallback_used": warning is not None,
            },
        )
    )

    started_at = perf_counter()
    verification = verify_judgment(decision, policies, judgment)
    trace.append(
        TraceStep(
            kind="function",
            phase="Verify",
            name="verify_judgment",
            status="ok" if verification.valid else "warning",
            summary=(
                f"Verified {verification.checked_claims} evidence citations."
                if verification.valid
                else "Rejected one or more unsupported evidence claims."
            ),
            duration_ms=_duration_ms(started_at),
            details={
                "valid": verification.valid,
                "checked_claims": verification.checked_claims,
                "errors": len(verification.errors),
            },
        )
    )

    started_at = perf_counter()
    deterministic = raw_evidence_result(decision)
    trace.append(
        TraceStep(
            kind="function",
            phase="Control",
            name="raw_evidence_result",
            summary=f"Deterministic controls returned {deterministic.action.value}.",
            duration_ms=_duration_ms(started_at),
            details={"action": deterministic.action.value},
        )
    )

    started_at = perf_counter()
    final = compose_final_action(
        deterministic,
        judgment,
        verification,
        project_result,
    )
    trace.append(
        TraceStep(
            kind="function",
            phase="Compose",
            name="compose_final_action",
            summary=f"Composed final advisory action: {final.action.value}.",
            duration_ms=_duration_ms(started_at),
            details={"action": final.action.value},
        )
    )

    return AnalysisResult(
        decision=decision,
        baselines=tuple(baseline_policy_results(decision)),
        policies=tuple(policies),
        judgment=judgment,
        verification=verification,
        final=final,
        project_policy=project_result,
        matched_project_rules=matched_project_rules,
        matched_organization_rules=matched_organization_rules,
        required_teams=required_teams,
        policy_requires_ci=policy_requires_ci,
        policy_sources=tuple(policy_sources),
        judge_warning=warning,
        trace=tuple(trace),
    )
