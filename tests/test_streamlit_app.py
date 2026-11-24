from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from execution_trace import TraceStep
from github_pr import GitHubChangedFile, GitHubPRSnapshot
from github_pr import GitHubFetchError
from judgment import offline_demo_judge

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STREAMLIT_APP = PROJECT_ROOT / "streamlit_app.py"
LIVE_DECISION_PAGE = PROJECT_ROOT / "app_pages" / "live_decision.py"
SYSTEM_EVALUATION_PAGE = PROJECT_ROOT / "app_pages" / "system_evaluation.py"

POLICY = """
[project]
name = "ClearLedger"
version = 1
default_action = "human_review"
default_reason = "Unknown application changes require owner review."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**"]
path_match = "all"
"""


def snapshot() -> GitHubPRSnapshot:
    return GitHubPRSnapshot(
        repository="acme/clearledger",
        pr_number=7,
        html_url="https://github.com/acme/clearledger/pull/7",
        title="Clarify operations guide",
        author="ada",
        base_ref="main",
        base_sha="1111111111111111",
        head_ref="docs/operations",
        head_sha="2222222222222222",
        head_repository="acme/clearledger",
        draft=False,
        additions=2,
        deletions=0,
        files=(
            GitHubChangedFile(
                filename="docs/operations.md",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
                patch="+ safer wording",
            ),
        ),
        checks=(),
        ci_status="passed",
        diff_excerpt="+ safer wording",
        diff_complete=True,
        fetched_at=datetime.now(UTC),
        trace=(),
    )


def _offline_judgment(decision, policies, mode="offline"):
    return offline_demo_judge(decision, policies)


class StreamlitSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_data.clear()

    def _mocked_live_pr(self):
        return (
            patch("github_pr.fetch_github_pr", return_value=snapshot()),
            patch(
                "github_pr.fetch_github_text_file",
                return_value=(
                    POLICY,
                    TraceStep(
                        kind="tool",
                        phase="GitHub",
                        name="get_file",
                        summary="Read .merge-gate/policy.toml",
                        duration_ms=1.0,
                    ),
                ),
            ),
            patch("engine.get_judgment", side_effect=_offline_judgment),
        )

    def test_entry_point_renders_the_live_decision_page_by_default(self) -> None:
        fetch_pr, fetch_text, get_judgment = self._mocked_live_pr()
        with fetch_pr, fetch_text, get_judgment:
            app = AppTest.from_file(STREAMLIT_APP, default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([title.value for title in app.title], ["Merge Gate"])
        self.assertIn("Run Merge Gate", [button.label for button in app.button])

    def test_live_decision_page_runs_the_full_pipeline(self) -> None:
        fetch_pr, fetch_text, get_judgment = self._mocked_live_pr()
        with fetch_pr, fetch_text, get_judgment:
            app = AppTest.from_file(LIVE_DECISION_PAGE, default_timeout=20).run()
            app.button(key="run_live_demo_gate").click().run()
        self.assertEqual(len(app.exception), 0)
        state_key = "live::acme/clearledger::7::2222222222222222"
        result = app.session_state[state_key]
        self.assertEqual(
            [step.name for step in result.trace],
            [
                "evaluate_project_policy",
                "project_policy_matches",
                "get_judgment",
                "verify_judgment",
                "raw_evidence_result",
                "compose_final_action",
            ],
        )

    def test_pr_number_field_switches_which_pr_is_fetched(self) -> None:
        calls: list[str] = []

        def fetch_pr_by_number(url: str, token=None):
            calls.append(url)
            pr_number = int(url.rsplit("/", 1)[-1])
            return snapshot().model_copy(update={"pr_number": pr_number, "html_url": url})

        with (
            patch("github_pr.fetch_github_pr", side_effect=fetch_pr_by_number),
            patch(
                "github_pr.fetch_github_text_file",
                return_value=(
                    POLICY,
                    TraceStep(
                        kind="tool",
                        phase="GitHub",
                        name="get_file",
                        summary="Read .merge-gate/policy.toml",
                        duration_ms=1.0,
                    ),
                ),
            ),
            patch("engine.get_judgment", side_effect=_offline_judgment),
        ):
            app = AppTest.from_file(LIVE_DECISION_PAGE, default_timeout=20).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(calls[-1].endswith("/pull/1"))

            app.number_input(key="live_pr_number").set_value(5).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(calls[-1].endswith("/pull/5"))

    def test_invalid_streamlit_github_token_retries_public_demo_anonymously(self) -> None:
        tokens: list[str | None] = []

        def fetch_with_stale_token_fallback(url: str, token=None):
            tokens.append(token)
            if token:
                raise GitHubFetchError("GitHub rejected the configured credentials.")
            return snapshot()

        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "expired-token"}),
            patch("github_pr.fetch_github_pr", side_effect=fetch_with_stale_token_fallback),
            patch(
                "github_pr.fetch_github_text_file",
                return_value=(
                    POLICY,
                    TraceStep(
                        kind="tool",
                        phase="GitHub",
                        name="get_file",
                        summary="Read .merge-gate/policy.toml",
                        duration_ms=1.0,
                    ),
                ),
            ),
            patch("engine.get_judgment", side_effect=_offline_judgment),
        ):
            app = AppTest.from_file(LIVE_DECISION_PAGE, default_timeout=20).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(tokens, ["expired-token", None])

    def test_system_evaluation_page_renders_without_exceptions(self) -> None:
        app = AppTest.from_file(SYSTEM_EVALUATION_PAGE, default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([title.value for title in app.title], ["System evaluation"])


if __name__ == "__main__":
    unittest.main()
