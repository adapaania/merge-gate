"""Machine-readable, repository-owned merge requirements.

A repository's `.merge-gate/policy.toml` is a `[project]`-flavored
`PolicyDocument` (see `policy_schema.py`). This module is a thin,
backward-compatible wrapper: it keeps the original function names and return
shapes used by the engine, the GitHub Action, and the dashboard, while
delegating parsing, matching, and exception handling to the schema module
shared with `organization_policy.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from model import Decision
from policies import PolicyResult
from policy_retrieval import PolicyMatch
from policy_schema import (
    LoadedPolicy,
    PolicyDocument,
    PolicyRule,
    evaluate_policy_document,
    match_rule,
    parse_policy_document,
)

ProjectPolicy = PolicyDocument


@dataclass(frozen=True)
class ProjectPolicyEvaluation:
    """Deterministic result and the project rules that caused it."""

    result: PolicyResult
    matched_rule_ids: tuple[str, ...]
    waived_rule_ids: tuple[str, ...] = ()
    required_teams: tuple[str, ...] = ()
    requires_ci: bool = False


def parse_project_policy(text: str, *, source: str = "project policy") -> ProjectPolicy:
    """Parse the target repository's TOML policy contract."""

    return parse_policy_document(text, section="project", source=source).document


def load_project_policy(path: str | Path) -> ProjectPolicy:
    source = Path(path)
    return parse_project_policy(source.read_text(encoding="utf-8"), source=str(source))


def load_project_policy_with_provenance(path: str | Path) -> LoadedPolicy:
    """Like ``load_project_policy``, but also returns source and content hash."""

    source = Path(path)
    return parse_policy_document(
        source.read_text(encoding="utf-8"),
        section="project",
        source=str(source),
    )


def match_project_rule(rule: PolicyRule, decision: Decision) -> tuple[str, ...]:
    """Return the observable paths/title terms that make a rule applicable."""

    return match_rule(rule, decision)


def evaluate_project_policy(
    policy: ProjectPolicy,
    decision: Decision,
) -> ProjectPolicyEvaluation:
    """Apply project-owned requirements with the most restrictive match winning."""

    evaluation = evaluate_policy_document(
        policy,
        decision,
        policy_label=f"Project requirements · {policy.name}",
    )
    return ProjectPolicyEvaluation(
        result=evaluation.result,
        matched_rule_ids=evaluation.matched_rule_ids,
        waived_rule_ids=evaluation.waived_rule_ids,
        required_teams=evaluation.required_teams,
        requires_ci=evaluation.requires_ci,
    )


def project_policy_matches(
    policy: ProjectPolicy,
    decision: Decision,
) -> list[PolicyMatch]:
    """Expose applicable project requirements to the structured judge."""

    matches: list[PolicyMatch] = []
    for rule in policy.rules:
        evidence = match_rule(rule, decision)
        if not evidence:
            continue
        text = (
            f"Required action: {rule.action.value}. {rule.reason} "
            f"Configured paths: {', '.join(rule.paths) or 'none'}."
        )
        matches.append(
            PolicyMatch(
                policy_id=rule.id,
                title=rule.title,
                text=text,
                score=10.0,
                matched_terms=evidence,
            )
        )
    return matches
