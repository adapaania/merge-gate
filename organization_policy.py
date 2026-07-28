"""Machine-readable organization-wide baseline merge requirements.

An organization baseline is an `[organization]`-flavored `PolicyDocument`
(see `policy_schema.py`) that every connected repository inherits. It is
optional: a repository with no configured organization policy behaves exactly
as it did before this module existed. When both are configured,
`effective_policy.py` combines the two so a repository can only tighten the
baseline, never weaken it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from model import Decision
from policies import PolicyResult
from policy_schema import (
    LoadedPolicy,
    PolicyDocument,
    evaluate_policy_document,
    parse_policy_document,
)

OrganizationPolicy = PolicyDocument


@dataclass(frozen=True)
class OrganizationPolicyEvaluation:
    """Deterministic result and the organization rules that caused it."""

    result: PolicyResult
    matched_rule_ids: tuple[str, ...]
    waived_rule_ids: tuple[str, ...] = ()
    required_teams: tuple[str, ...] = ()
    requires_ci: bool = False


def parse_organization_policy(
    text: str,
    *,
    source: str = "organization policy",
) -> OrganizationPolicy:
    """Parse an organization's TOML baseline policy contract."""

    return parse_policy_document(text, section="organization", source=source).document


def load_organization_policy(path: str | Path) -> OrganizationPolicy:
    source = Path(path)
    return parse_organization_policy(source.read_text(encoding="utf-8"), source=str(source))


def load_organization_policy_with_provenance(path: str | Path) -> LoadedPolicy:
    """Like ``load_organization_policy``, but also returns source and content hash."""

    source = Path(path)
    return parse_policy_document(
        source.read_text(encoding="utf-8"),
        section="organization",
        source=str(source),
    )


def evaluate_organization_policy(
    policy: OrganizationPolicy,
    decision: Decision,
) -> OrganizationPolicyEvaluation:
    """Apply the organization baseline with the most restrictive match winning."""

    evaluation = evaluate_policy_document(
        policy,
        decision,
        policy_label=f"Organization baseline · {policy.name}",
    )
    return OrganizationPolicyEvaluation(
        result=evaluation.result,
        matched_rule_ids=evaluation.matched_rule_ids,
        waived_rule_ids=evaluation.waived_rule_ids,
        required_teams=evaluation.required_teams,
        requires_ci=evaluation.requires_ci,
    )
