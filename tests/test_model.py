from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from model import Decision, load_decisions
from tests.helpers import decision


class DecisionModelTests(unittest.TestCase):
    def test_confidence_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            decision(agent_confidence=1.1)

    def test_at_least_one_changed_file_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            decision(files_touched=[])

    def test_duplicate_ids_are_rejected(self) -> None:
        record = decision().model_dump(mode="json")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "duplicates.json"
            target.write_text(json.dumps([record, record]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_decisions(target)

    def test_unknown_fields_are_rejected(self) -> None:
        payload = decision().model_dump()
        payload["hidden_answer"] = True
        with self.assertRaises(ValidationError):
            Decision.model_validate(payload)

    def test_live_evidence_can_preserve_unknowns(self) -> None:
        value = decision(
            ci_passed=None,
            reversible=None,
            touches_incident_code=None,
            agent_confidence=None,
        )
        self.assertIsNone(value.ci_passed)
        self.assertIsNone(value.reversible)
        self.assertIsNone(value.touches_incident_code)
        self.assertIsNone(value.agent_confidence)


if __name__ == "__main__":
    unittest.main()
