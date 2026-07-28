from __future__ import annotations

from model import Decision


def decision(**overrides) -> Decision:
    values = {
        "id": "test_pr",
        "title": "Update documentation",
        "files_touched": ["docs/guide.md"],
        "diff_lines": 4,
        "path_risk": "low",
        "ci_passed": True,
        "reversible": True,
        "touches_incident_code": False,
        "agent_confidence": 0.9,
        "should_escalate": False,
        "bucket": "test",
        "rationale": "test fixture",
        "diff_excerpt": "+ clearer words",
    }
    values.update(overrides)
    return Decision.model_validate(values)
