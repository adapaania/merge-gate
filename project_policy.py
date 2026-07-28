"""Machine-readable, repository-owned merge requirements."""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from model import Decision
from policies import GateAction, PolicyResult
from policy_retrieval import PolicyMatch


class ProjectRule(BaseModel):
    """One inspectable project rule matched against PR paths or title terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_-]*-\d+$")
    title: str = Field(min_length=3)
    action: GateAction
    reason: str = Field(min_length=8)
    paths: tuple[str, ...] = ()
    title_terms: tuple[str, ...] = ()
    path_match: Literal["any", "all"] = "any"

    @model_validator(mode="after")
    def require_matcher(self) -> ProjectRule:
        if not self.paths and not self.title_terms:
            raise ValueError("a project rule needs at least one path or title matcher")
        return self


class ProjectPolicy(BaseModel):
    """Versioned merge requirements loaded from a target repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=2)
    version: int = Field(ge=1)
    default_action: GateAction = GateAction.HUMAN_REVIEW
    default_reason: str = Field(min_length=8)
    rules: tuple[ProjectRule, ...] = Field(min_length=1)

    @field_validator("rules")
    @classmethod
    def require_unique_rule_ids(
        cls,
        rules: tuple[ProjectRule, ...],
    ) -> tuple[ProjectRule, ...]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("project policy rule IDs must be unique")
        return rules


class ProjectPolicyEvaluation(BaseModel):
    """Deterministic result and the project rules that caused it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: PolicyResult
    matched_rule_ids: tuple[str, ...]


def parse_project_policy(text: str, *, source: str = "project policy") -> ProjectPolicy:
    """Parse the target repository's TOML policy contract."""

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{source} is not valid TOML") from exc
    project = raw.get("project")
    rules = raw.get("rules")
    if not isinstance(project, dict) or not isinstance(rules, list):
        raise ValueError(f"{source} needs [project] and at least one [[rules]] section")
    return ProjectPolicy.model_validate({**project, "rules": rules})


def load_project_policy(path: str | Path) -> ProjectPolicy:
    source = Path(path)
    return parse_project_policy(
        source.read_text(encoding="utf-8"),
        source=str(source),
    )


def _path_matches(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.strip().lstrip("/")
    normalized_path = path.strip().lstrip("/")
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def match_project_rule(rule: ProjectRule, decision: Decision) -> tuple[str, ...]:
    """Return the observable paths/title terms that make a rule applicable."""

    evidence: list[str] = []
    if rule.paths:
        path_hits = {
            path: any(_path_matches(pattern, path) for pattern in rule.paths)
            for path in decision.files_touched
        }
        path_match = (
            all(path_hits.values())
            if rule.path_match == "all"
            else any(path_hits.values())
        )
        if path_match:
            evidence.extend(path for path, matched in path_hits.items() if matched)

    title = decision.title.lower()
    matched_terms = [
        term
        for term in rule.title_terms
        if term.strip() and term.lower() in title
    ]
    evidence.extend(f"title:{term}" for term in matched_terms)
    return tuple(dict.fromkeys(evidence))


def evaluate_project_policy(
    policy: ProjectPolicy,
    decision: Decision,
) -> ProjectPolicyEvaluation:
    """Apply project-owned requirements with the most restrictive match winning."""

    matches = [
        (rule, match_project_rule(rule, decision))
        for rule in policy.rules
    ]
    matched = [(rule, evidence) for rule, evidence in matches if evidence]
    policy_name = f"Project requirements · {policy.name}"
    if not matched:
        return ProjectPolicyEvaluation(
            result=PolicyResult(
                policy_name,
                policy.default_action,
                policy.default_reason,
            ),
            matched_rule_ids=(),
        )

    priority = {
        GateAction.AUTO_MERGE_CANDIDATE: 1,
        GateAction.HUMAN_REVIEW: 2,
        GateAction.BLOCK: 3,
    }
    winning_priority = max(priority[rule.action] for rule, _ in matched)
    winners = [
        rule
        for rule, _ in matched
        if priority[rule.action] == winning_priority
    ]
    winner_ids = ", ".join(rule.id for rule in winners)
    reasons = " ".join(rule.reason for rule in winners)
    return ProjectPolicyEvaluation(
        result=PolicyResult(
            policy_name,
            winners[0].action,
            f"{winner_ids}: {reasons}",
        ),
        matched_rule_ids=tuple(rule.id for rule, _ in matched),
    )


def project_policy_matches(
    policy: ProjectPolicy,
    decision: Decision,
) -> list[PolicyMatch]:
    """Expose applicable project requirements to the structured judge."""

    matches: list[PolicyMatch] = []
    for rule in policy.rules:
        evidence = match_project_rule(rule, decision)
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
