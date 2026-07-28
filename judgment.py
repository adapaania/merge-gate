"""Structured judgment models and the transparent offline demo judge."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from model import Decision
from policies import GateAction, infer_sensitive_domains, raw_evidence_result
from policy_retrieval import PolicyMatch


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=3)
    file: str | None = None
    policy_id: str | None = None


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: GateAction
    risk_level: str
    reasons: list[str] = Field(min_length=1)
    risk_categories: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    triggered_policies: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    model: str


def offline_demo_judge(decision: Decision, policies: list[PolicyMatch]) -> JudgeResult:
    """Return a deterministic, explicitly labeled fixture judgment.

    This keeps the demo reliable when no model key is available. The UI must
    label it as an offline fixture, never as a live model result.
    """

    gate = raw_evidence_result(decision)
    domains = infer_sensitive_domains(decision)
    triggered = [policy.policy_id for policy in policies if policy.score >= 4.0]
    evidence: list[EvidenceCitation] = []

    if decision.files_touched:
        evidence.append(
            EvidenceCitation(
                file=decision.files_touched[0],
                claim=(
                    "Changed file is part of the observable pull-request evidence."
                ),
            )
        )
    for policy_id in triggered[:2]:
        evidence.append(
            EvidenceCitation(
                policy_id=policy_id,
                claim=f"Retrieved repository policy {policy_id} applies to the detected domain.",
            )
        )

    reasons = [gate.reason]
    uncertainties: list[str] = []
    if not decision.diff_excerpt:
        uncertainties.append("No diff excerpt is available for semantic inspection.")

    risk_level = {
        GateAction.BLOCK: "critical",
        GateAction.HUMAN_REVIEW: "elevated",
        GateAction.AUTO_MERGE_CANDIDATE: "low",
    }[gate.action]
    confidence = {
        GateAction.BLOCK: 0.99,
        GateAction.HUMAN_REVIEW: 0.86,
        GateAction.AUTO_MERGE_CANDIDATE: 0.82,
    }[gate.action]

    return JudgeResult(
        action=gate.action,
        risk_level=risk_level,
        reasons=reasons,
        risk_categories=domains,
        evidence=evidence,
        triggered_policies=triggered,
        uncertainties=uncertainties,
        confidence=confidence,
        source="offline_demo_fixture",
        model="deterministic-fixture",
    )

