"""Merge Gate's demo-day advisory interface.

Run directly with:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import os

import streamlit as st

st.set_page_config(
    page_title="Merge Gate",
    page_icon=":material/security:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

pages = [
    st.Page(
        "app_pages/live_decision.py",
        title="Live decision",
        icon=":material/gavel:",
        default=True,
    ),
    st.Page(
        "app_pages/system_evaluation.py",
        title="System evaluation",
        icon=":material/monitoring:",
    ),
]

if os.getenv("MERGE_GATE_DEV_MODE") == "1":
    pages.append(
        st.Page(
            "app_pages/replay_pr.py",
            title="Replay PR (dev)",
            icon=":material/bug_report:",
        )
    )

navigation = st.navigation(pages)
navigation.run()
