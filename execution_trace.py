"""Sanitized execution-trace models for the demo interface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TraceValue = str | int | float | bool | None


class TraceStep(BaseModel):
    """One externally safe record of a tool call or pipeline function.

    Details are deliberately limited to scalar metadata. Raw diffs, model
    prompts, credentials, and provider response bodies do not belong here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tool", "function"]
    phase: str
    name: str
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    duration_ms: float = Field(ge=0.0)
    details: dict[str, TraceValue] = Field(default_factory=dict)
