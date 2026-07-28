"""Evaluation tables used by the CLI, tests, and Streamlit dashboard."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from llm_judge import get_cached_live_judgment
from model import Decision
from policies import (
    always,
    confidence_threshold,
    diff_size_threshold,
    never,
    path_risk_rule,
    raw_evidence_rule,
)
from scorer import detailed_score
from policy_retrieval import retrieve_policies


def load_legacy_llm_cache(path: str = "llm_cache.json") -> dict[str, bool]:
    source = Path(path)
    if not source.exists():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8"))
    return {str(key): bool(value) for key, value in raw.items()}


def policy_registry(decisions: list[Decision]) -> dict[str, Callable[[Decision], bool]]:
    cache = load_legacy_llm_cache()
    policies: dict[str, Callable[[Decision], bool]] = {
        "Always review": always,
        "Never review": never,
        "Confidence < 80%": confidence_threshold,
        "Diff > 10 lines": diff_size_threshold,
        "Legacy path-risk ceiling": path_risk_rule,
        "Raw-evidence gate": raw_evidence_rule,
    }
    if all(decision.id in cache for decision in decisions):
        policies["Legacy cached Claude"] = lambda decision: cache[decision.id]

    structured_results = {
        decision.id: get_cached_live_judgment(
            decision,
            retrieve_policies(decision),
        )
        for decision in decisions
    }
    if all(result is not None for result in structured_results.values()):
        policies["Cached structured Claude"] = lambda decision: (
            structured_results[decision.id].action
            != "auto_merge_candidate"  # type: ignore[union-attr]
        )
    return policies


def evaluation_table(decisions: list[Decision]) -> pd.DataFrame:
    rows: list[dict] = []
    for name, policy in policy_registry(decisions).items():
        metrics = detailed_score(policy, decisions)
        rows.append(
            {
                "Policy": name,
                "Missed escalations": metrics.missed_escalations,
                "False escalations": metrics.false_escalations,
                "Critical recall": metrics.critical_recall,
                "Autonomous coverage": metrics.autonomous_coverage,
                "Escalation precision": metrics.escalation_precision,
            }
        )
    return pd.DataFrame(rows)


def bucket_table(decisions: list[Decision], policy_name: str) -> pd.DataFrame:
    policy = policy_registry(decisions)[policy_name]
    rows: list[dict] = []
    for bucket in sorted({decision.bucket for decision in decisions}):
        subset = [decision for decision in decisions if decision.bucket == bucket]
        metrics = detailed_score(policy, subset)
        rows.append(
            {
                "Bucket": bucket.replace("_", " "),
                "Examples": metrics.total,
                "Missed escalations": metrics.missed_escalations,
                "False escalations": metrics.false_escalations,
            }
        )
    return pd.DataFrame(rows)
