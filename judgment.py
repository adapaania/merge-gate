"""Structured judgment models and the transparent offline demo judge."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model import Decision
from policies import GateAction, infer_sensitive_domains, raw_evidence_result
from policy_retrieval import PolicyMatch


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=3)
    source_ids: list[str] = Field(min_length=1)

    @field_validator("source_ids")
    @classmethod
    def require_unique_nonempty_sources(cls, source_ids: list[str]) -> list[str]:
        if any(not source_id.strip() for source_id in source_ids):
            raise ValueError("evidence source IDs must not be empty")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("evidence source IDs must be unique per claim")
        return source_ids


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
                claim=(
                    "Changed file is part of the observable pull-request evidence."
                ),
                source_ids=[f"file:{decision.files_touched[0]}"],
            )
        )
    for policy_id in triggered[:2]:
        evidence.append(
            EvidenceCitation(
                claim=f"Retrieved repository policy {policy_id} applies to the detected domain.",
                source_ids=[f"policy:{policy_id}"],
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
