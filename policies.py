"""Deterministic escalation-policy baselines.

The Boolean functions are retained for the original evaluation harness.
``raw_evidence_rule`` is the policy used by the new advisory pipeline because
it derives risk from raw paths and checks instead of reading the label-leaking
``path_risk`` field.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from model import Decision


def always(d: Decision) -> bool:
    return True


def never(d: Decision) -> bool:
    return False


def confidence_threshold(d: Decision, t: float = 0.8) -> bool:
    return d.agent_confidence is None or d.agent_confidence < t


def diff_size_threshold(d: Decision, n: int = 10) -> bool:
    return d.diff_lines > n


def path_risk_rule(d: Decision) -> bool:
    """Legacy reference ceiling that reads the synthetic ``path_risk`` field."""

    return (
        d.path_risk == "high"
        or not d.reversible
        or not d.ci_passed
        or d.touches_incident_code
    )


def incident_code_rule(d: Decision) -> bool:
    return d.touches_incident_code is True


class GateAction(StrEnum):
    AUTO_MERGE_CANDIDATE = "auto_merge_candidate"
    HUMAN_REVIEW = "human_review"
    BLOCK = "block"


@dataclass(frozen=True)
class PolicyResult:
    policy: str
    action: GateAction
    reason: str

    @property
    def escalates(self) -> bool:
        return self.action != GateAction.AUTO_MERGE_CANDIDATE


SENSITIVE_PATH_TOKENS: dict[str, tuple[str, ...]] = {
    "authentication": ("auth", "oauth", "session", "token", "jwt", "crypto", "keys"),
    "payments": ("payment", "billing", "invoice", "ledger", "refund", "capture"),
    "database": ("migration", "migrations", "schema", "database", "db"),
    "infrastructure": (
        "infra",
        "terraform",
        "kubernetes",
        "deploy",
        "deployment",
        "workflow",
        "network",
    ),
    "permissions": ("permission", "permissions", "role", "roles", "access", "policy"),
}


def infer_sensitive_domains(d: Decision) -> list[str]:
    """Infer coarse risk domains from raw paths and the PR title.

    This is intentionally inspectable. It avoids reading ``path_risk`` or the
    expected label.
    """

    evidence = " ".join([d.title, *d.files_touched]).lower()
    return [
        domain
        for domain, tokens in SENSITIVE_PATH_TOKENS.items()
        if any(token in evidence for token in tokens)
    ]


REMOVED_ASSERTION = re.compile(
    r"\b(assert|self\.assert[A-Z]\w*|pytest\.raises|expect\s*\()"
)
ADDED_TEST_BYPASS = re.compile(
    r"(@pytest\.mark\.(skip|xfail)|unittest\.skip|skipTest\s*\()"
)


def weakens_tests(d: Decision) -> bool:
    """Detect a small, inspectable set of test-weakening signals.

    This is not semantic code review. It closes the unsafe shortcut where any
    test-only pull request was automatically treated as low risk.
    """

    evidence = " ".join([d.title, *d.files_touched]).lower()
    sensitive_tokens = {
        token
        for tokens in SENSITIVE_PATH_TOKENS.values()
        for token in tokens
    }
    if any(token in evidence for token in sensitive_tokens):
        return True

    removed_lines = [
        line[1:]
        for line in d.diff_excerpt.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    added_lines = [
        line[1:]
        for line in d.diff_excerpt.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    return (
        "# status: removed" in d.diff_excerpt.lower()
        or any("def test_" in line or REMOVED_ASSERTION.search(line) for line in removed_lines)
        or any(ADDED_TEST_BYPASS.search(line) for line in added_lines)
    )


def is_low_risk_only(d: Decision) -> bool:
    suffixes = {PurePosixPath(path).suffix.lower() for path in d.files_touched}
    normalized = [path.lower() for path in d.files_touched]
    docs_only = all(
        path.startswith(("docs/", "documentation/"))
        or PurePosixPath(path).name.lower() in {"readme.md", "changelog.md"}
        or PurePosixPath(path).suffix.lower() in {".md", ".rst", ".txt"}
        for path in normalized
    )
    tests_only = all(
        path.startswith(("tests/", "test/"))
        or "/tests/" in path
        or PurePosixPath(path).name.startswith("test_")
        for path in normalized
    )
    formatting_only = suffixes <= {".md", ".rst", ".txt"} and d.diff_lines <= 80
    return docs_only or (tests_only and not weakens_tests(d)) or formatting_only


def raw_evidence_result(d: Decision) -> PolicyResult:
    """Return the deterministic advisory decision from raw, observable facts."""

    if d.ci_passed is False:
        return PolicyResult("Deterministic gate", GateAction.BLOCK, "Observed CI did not pass.")
    if d.ci_passed is None:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            "CI evidence is pending or unavailable.",
        )
    if not d.diff_complete:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            "The available diff is incomplete, so the risk classification cannot be verified.",
        )

    if d.touches_incident_code is True:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            "The change touches a component linked to a previous incident.",
        )
    if d.reversible is False:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            "The change is not documented as reversible.",
        )
    # Classify the actual artifact before looking for sensitive words in its
    # directory name. ``docs/auth/guide.md`` describes authentication but does
    # not modify an authentication boundary.
    if is_low_risk_only(d):
        return PolicyResult(
            "Deterministic gate",
            GateAction.AUTO_MERGE_CANDIDATE,
            "Only low-risk documentation or test paths changed and CI passed.",
        )

    domains = infer_sensitive_domains(d)
    if domains:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            f"Sensitive domain detected: {', '.join(domains)}.",
        )
    if d.reversible is None or d.touches_incident_code is None:
        return PolicyResult(
            "Deterministic gate",
            GateAction.HUMAN_REVIEW,
            "Safety evidence is incomplete: reversibility or incident linkage is unknown.",
        )
    return PolicyResult(
        "Deterministic gate",
        GateAction.HUMAN_REVIEW,
        "No hard violation was found, but the change is not in a proven low-risk category.",
    )


def baseline_policy_results(d: Decision) -> list[PolicyResult]:
    confidence_escalates = confidence_threshold(d)
    diff_escalates = diff_size_threshold(d)
    if d.agent_confidence is None:
        confidence_reason = "Agent confidence is unavailable, so this threshold cannot approve."
    elif confidence_escalates:
        confidence_reason = (
            f"Agent confidence {d.agent_confidence:.0%} is below the 80% threshold."
        )
    else:
        confidence_reason = (
            f"Agent confidence {d.agent_confidence:.0%} meets the 80% threshold."
        )
    return [
        PolicyResult(
            "Confidence threshold",
            GateAction.HUMAN_REVIEW if confidence_escalates else GateAction.AUTO_MERGE_CANDIDATE,
            confidence_reason,
        ),
        PolicyResult(
            "Diff-size threshold",
            GateAction.HUMAN_REVIEW if diff_escalates else GateAction.AUTO_MERGE_CANDIDATE,
            (
                f"The {d.diff_lines}-line diff exceeds the 10-line threshold."
                if diff_escalates
                else f"The {d.diff_lines}-line diff is within the 10-line threshold."
            ),
        ),
        raw_evidence_result(d),
    ]


def raw_evidence_rule(d: Decision) -> bool:
    return raw_evidence_result(d).escalates
