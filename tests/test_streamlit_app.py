from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class StreamlitSmokeTests(unittest.TestCase):
    def test_app_renders_without_exceptions(self) -> None:
        app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([title.value for title in app.title], ["Merge Gate"])
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["Overview", "Evidence", "Evaluation"],
        )
        trace = app.dataframe[0].value
        self.assertEqual(
            trace["Tool / function"].tolist(),
            [
                "retrieve_policies",
                "get_judgment",
                "verify_judgment",
                "raw_evidence_result",
                "compose_final_action",
            ],
        )


if __name__ == "__main__":
    unittest.main()
