"""Deterministic verification of model-supplied evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from judgment import JudgeResult
from model import Decision
from policies import GateAction
from policy_retrieval import PolicyMatch


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    checked_claims: int
    errors: list[str]


def verify_judgment(
    decision: Decision,
    policies: list[PolicyMatch],
    judgment: JudgeResult,
) -> VerificationResult:
    available_files = set(decision.files_touched)
    available_policies = {policy.policy_id for policy in policies}
    errors: list[str] = []

    for policy_id in judgment.triggered_policies:
        if policy_id not in available_policies:
            errors.append(f"Policy {policy_id} was not retrieved.")

    for citation in judgment.evidence:
        if citation.file is None and citation.policy_id is None:
            errors.append(f"Evidence claim has no source: {citation.claim}")
        if citation.file is not None and citation.file not in available_files:
            errors.append(f"Cited file was not changed: {citation.file}")
        if citation.policy_id is not None and citation.policy_id not in available_policies:
            errors.append(f"Cited policy was not retrieved: {citation.policy_id}")

    if judgment.action != GateAction.AUTO_MERGE_CANDIDATE and not judgment.evidence:
        errors.append("A restrictive decision must contain verifiable evidence.")

    return VerificationResult(
        valid=not errors,
        checked_claims=len(judgment.evidence),
        errors=errors,
    )

