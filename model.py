"""Validated data models for pull-request escalation decisions."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Decision(BaseModel):
    """The evidence available to a gate when it assesses one pull request.

    ``path_risk`` is retained for compatibility with the original stress-test
    dataset, but the new hybrid gate deliberately does not use it. It is a
    derived feature that leaks the synthetic label in the original data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    files_touched: list[str]
    diff_lines: int = Field(ge=0)
    path_risk: str = "unknown"
    ci_passed: bool | None = None
    reversible: bool | None = None
    touches_incident_code: bool | None = None
    agent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    diff_complete: bool = True
    should_escalate: bool | None = None
    bucket: str = "unlabeled"
    rationale: str = ""
    diff_excerpt: str = ""

    @field_validator("files_touched")
    @classmethod
    def require_files(cls, files: list[str]) -> list[str]:
        if not files:
            raise ValueError("at least one changed file is required")
        if len(set(files)) != len(files):
            raise ValueError("changed file paths must be unique")
        return files


def load_decisions(path: str | Path) -> list[Decision]:
    """Load and validate a JSON array of decisions.

    Duplicate IDs are rejected because they would corrupt cache and evaluation
    results.
    """

    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{source} must contain a JSON array")

    decisions = [Decision.model_validate(item) for item in raw]
    ids = [decision.id for decision in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{source} contains duplicate decision IDs")
    return decisions
