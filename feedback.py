"""Local feedback persistence for the advisory demo."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def append_feedback(
    *,
    decision_id: str,
    gate_action: str,
    human_action: str,
    reason: str,
    path: str = "data/feedback.jsonl",
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "decision_id": decision_id,
        "gate_action": gate_action,
        "human_action": human_action,
        "reason": reason.strip(),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
