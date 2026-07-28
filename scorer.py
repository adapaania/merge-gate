"""Evaluation metrics for escalation policies."""

from collections.abc import Callable
from dataclasses import dataclass

from model import Decision


@dataclass(frozen=True)
class PolicyMetrics:
    true_escalations: int
    false_escalations: int
    missed_escalations: int
    correct_approvals: int

    @property
    def total(self) -> int:
        return (
            self.true_escalations
            + self.false_escalations
            + self.missed_escalations
            + self.correct_approvals
        )

    @property
    def critical_recall(self) -> float:
        positives = self.true_escalations + self.missed_escalations
        return self.true_escalations / positives if positives else 0.0

    @property
    def escalation_precision(self) -> float:
        escalations = self.true_escalations + self.false_escalations
        return self.true_escalations / escalations if escalations else 0.0

    @property
    def autonomous_coverage(self) -> float:
        approvals = self.missed_escalations + self.correct_approvals
        return approvals / self.total if self.total else 0.0


def detailed_score(
    policy: Callable[[Decision], bool],
    decisions: list[Decision],
) -> PolicyMetrics:
    true_esc = 0
    false_esc = 0
    missed = 0
    correct_approval = 0
    for d in decisions:
        if d.should_escalate is None:
            continue
        answer = policy(d)
        truth = d.should_escalate
        if truth and answer:
            true_esc += 1
        elif truth and not answer:
            missed += 1
        elif not truth and answer:
            false_esc += 1
        else:
            correct_approval += 1
    return PolicyMetrics(true_esc, false_esc, missed, correct_approval)


def score_policy(
    policy: Callable[[Decision], bool],
    decisions: list[Decision],
) -> tuple[int, int]:
    metrics = detailed_score(policy, decisions)
    return metrics.missed_escalations, metrics.false_escalations
