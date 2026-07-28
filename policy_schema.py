"""Shared rule, exception, and document schema for org and repo policy layers.

An organization baseline and a repository overlay are both a `PolicyDocument`:
a versioned, named set of `PolicyRule`s plus optional `PolicyException`
waivers. `project_policy.py` and `organization_policy.py` are thin,
section-specific wrappers around this module so the two layers share one
matching, exception, and hashing implementation.
"""

from __future__ import annotations

import fnmatch
import hashlib
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from model import Decision
from policies import GateAction, PolicyResult

RULE_ID_PATTERN = r"^[A-Z][A-Z0-9_-]*-\d+$"


class PolicyRule(BaseModel):
    """One inspectable rule matched against PR paths or title terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=RULE_ID_PATTERN)
    title: str = Field(min_length=3)
    action: GateAction
    reason: str = Field(min_length=8)
    paths: tuple[str, ...] = ()
    title_terms: tuple[str, ...] = ()
    path_match: Literal["any", "all"] = "any"
    required_teams: tuple[str, ...] = ()
    requires_ci: bool = False
    category: str | None = None

    @model_validator(mode="after")
    def require_matcher(self) -> PolicyRule:
        if not self.paths and not self.title_terms:
            raise ValueError("a policy rule needs at least one path or title matcher")
        return self


class PolicyException(BaseModel):
    """A time-boxed, owner-attributed waiver for one rule.

    An exception never lowers the *default* action, and it only ever removes
    one specific matched rule from consideration for a decision — the winning
    action still falls back to the next-most-restrictive matched rule, or to
    the policy default, so a waiver can only reduce restriction as far as the
    document's other applicable rules allow.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=RULE_ID_PATTERN)
    rule_id: str
    owner: str = Field(min_length=2)
    expires_on: date
    reason: str = Field(min_length=8)
    paths: tuple[str, ...] = ()


class PolicyDocument(BaseModel):
    """A versioned set of rules: an organization baseline or a repo overlay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=2)
    version: int = Field(ge=1)
    default_action: GateAction = GateAction.HUMAN_REVIEW
    default_reason: str = Field(min_length=8)
    rules: tuple[PolicyRule, ...] = Field(min_length=1)
    exceptions: tuple[PolicyException, ...] = ()

    @field_validator("rules")
    @classmethod
    def require_unique_rule_ids(cls, rules: tuple[PolicyRule, ...]) -> tuple[PolicyRule, ...]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        return rules

    @model_validator(mode="after")
    def require_exceptions_reference_real_rules(self) -> PolicyDocument:
        exception_ids = [exception.id for exception in self.exceptions]
        if len(exception_ids) != len(set(exception_ids)):
            raise ValueError("policy exception IDs must be unique")
        rule_ids = {rule.id for rule in self.rules}
        unknown = sorted({
            exception.rule_id for exception in self.exceptions if exception.rule_id not in rule_ids
        })
        if unknown:
            raise ValueError(f"exceptions reference unknown rule IDs: {', '.join(unknown)}")
        return self


@dataclass(frozen=True)
class LoadedPolicy:
    """A parsed policy document plus the provenance needed to audit a decision."""

    document: PolicyDocument
    source: str
    content_hash: str


@dataclass(frozen=True)
class PolicySourceRef:
    """Provenance for one policy layer that contributed to a decision."""

    scope: Literal["organization", "project"]
    name: str
    version: int
    content_hash: str | None
    source: str


@dataclass(frozen=True)
class PolicyDocumentEvaluation:
    """Deterministic result of one policy document, and why it fired."""

    result: PolicyResult
    matched_rule_ids: tuple[str, ...]
    waived_rule_ids: tuple[str, ...]
    required_teams: tuple[str, ...]
    requires_ci: bool


def content_hash(text: str) -> str:
    """A short, stable fingerprint of a policy file's exact text."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_policy_document(
    text: str,
    *,
    section: Literal["project", "organization"],
    source: str = "policy",
) -> LoadedPolicy:
    """Parse a `[project]` or `[organization]` TOML policy contract.

    Raises ``ValueError`` for every failure mode — bad TOML, a missing
    section, or a rule/exception that fails schema validation — so every
    caller can fail closed with one exception type instead of needing to
    know that pydantic errors don't subclass it.
    """

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{source} is not valid TOML") from exc
    header = raw.get(section)
    rules = raw.get("rules")
    if not isinstance(header, dict) or not isinstance(rules, list):
        raise ValueError(f"{source} needs [{section}] and at least one [[rules]] section")
    exceptions = raw.get("exceptions", [])
    if not isinstance(exceptions, list):
        raise ValueError(f"{source} exceptions must be a list of [[exceptions]] tables")
    try:
        document = PolicyDocument.model_validate(
            {**header, "rules": rules, "exceptions": exceptions}
        )
    except ValidationError as exc:
        raise ValueError(f"{source} is not a valid policy document: {exc}") from exc
    return LoadedPolicy(document=document, source=source, content_hash=content_hash(text))


def _path_matches(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.strip().lstrip("/")
    normalized_path = path.strip().lstrip("/")
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def match_rule(rule: PolicyRule, decision: Decision) -> tuple[str, ...]:
    """Return the observable paths/title terms that make a rule applicable."""

    evidence: list[str] = []
    if rule.paths:
        path_hits = {
            path: any(_path_matches(pattern, path) for pattern in rule.paths)
            for path in decision.files_touched
        }
        path_match = (
            all(path_hits.values()) if rule.path_match == "all" else any(path_hits.values())
        )
        if path_match:
            evidence.extend(path for path, matched in path_hits.items() if matched)

    title = decision.title.lower()
    matched_terms = [
        term for term in rule.title_terms if term.strip() and term.lower() in title
    ]
    evidence.extend(f"title:{term}" for term in matched_terms)
    return tuple(dict.fromkeys(evidence))


def _exception_is_active(
    exception: PolicyException,
    *,
    today: date,
    evidence: tuple[str, ...],
) -> bool:
    if exception.expires_on < today:
        return False
    if not exception.paths:
        return True
    return any(
        _path_matches(pattern, path)
        for pattern in exception.paths
        for path in evidence
    )


_PRIORITY = {
    GateAction.AUTO_MERGE_CANDIDATE: 1,
    GateAction.HUMAN_REVIEW: 2,
    GateAction.BLOCK: 3,
}


def evaluate_policy_document(
    document: PolicyDocument,
    decision: Decision,
    *,
    policy_label: str,
    today: date | None = None,
) -> PolicyDocumentEvaluation:
    """Apply one policy document: active exceptions waive rules, then the
    most restrictive remaining matched rule wins; unmatched falls to default.
    """

    as_of = today or datetime.now(UTC).date()
    matches = [(rule, match_rule(rule, decision)) for rule in document.rules]
    matched = [(rule, evidence) for rule, evidence in matches if evidence]

    exceptions_by_rule: dict[str, list[PolicyException]] = {}
    for exception in document.exceptions:
        exceptions_by_rule.setdefault(exception.rule_id, []).append(exception)

    waived: list[str] = []
    active: list[tuple[PolicyRule, tuple[str, ...]]] = []
    for rule, evidence in matched:
        covering = [
            exception
            for exception in exceptions_by_rule.get(rule.id, ())
            if _exception_is_active(exception, today=as_of, evidence=evidence)
        ]
        if covering:
            waived.append(rule.id)
        else:
            active.append((rule, evidence))

    if not active:
        return PolicyDocumentEvaluation(
            result=PolicyResult(policy_label, document.default_action, document.default_reason),
            matched_rule_ids=tuple(rule.id for rule, _ in matched),
            waived_rule_ids=tuple(waived),
            required_teams=(),
            requires_ci=False,
        )

    winning_priority = max(_PRIORITY[rule.action] for rule, _ in active)
    winners = [rule for rule, _ in active if _PRIORITY[rule.action] == winning_priority]
    winner_ids = ", ".join(rule.id for rule in winners)
    reasons = " ".join(rule.reason for rule in winners)
    return PolicyDocumentEvaluation(
        result=PolicyResult(policy_label, winners[0].action, f"{winner_ids}: {reasons}"),
        matched_rule_ids=tuple(rule.id for rule, _ in matched),
        waived_rule_ids=tuple(waived),
        required_teams=tuple(
            dict.fromkeys(team for rule in winners for team in rule.required_teams)
        ),
        requires_ci=any(rule.requires_ci for rule in winners),
    )


def combine_document_results(results: list[PolicyResult]) -> PolicyResult:
    """Pick the most restrictive of several policy-document results.

    Used to combine an organization baseline with a repository overlay so a
    repository policy can only tighten the baseline, never weaken it: the
    combined action is always at least as restrictive as every input.
    """

    winning_priority = max(_PRIORITY[result.action] for result in results)
    winners = [result for result in results if _PRIORITY[result.action] == winning_priority]
    return PolicyResult(
        " + ".join(dict.fromkeys(result.policy for result in winners)),
        winners[0].action,
        " ".join(dict.fromkeys(result.reason for result in winners)),
    )
