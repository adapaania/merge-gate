"""Merge Gate's evaluation page: aggregate policy metrics over a labeled set.

This is separate from the live decision page on purpose. It reads local
labeled fixtures, not GitHub, so it is deterministic and safe to explore
without triggering a live model call.
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from evaluation import bucket_table, evaluation_table
from llm_judge import get_cached_live_judgment
from model import Decision, load_decisions
from policy_retrieval import retrieve_policies

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS = {
    "Challenge set": PROJECT_ROOT / "data/heldout_decisions.json",
    "Original stress test": PROJECT_ROOT / "data/decisions.json",
}


@st.cache_data
def cached_decisions(path: str) -> list[Decision]:
    return load_decisions(path)


@st.cache_data
def cached_evaluation(path: str) -> pd.DataFrame:
    return evaluation_table(load_decisions(path))


st.title("System evaluation", anchor=False)
st.write(
    "How well does the deterministic policy separate safe changes from ones "
    "that need a human, on a small labeled set?"
)
st.caption(
    "These results are self-authored and synthetic. They demonstrate the "
    "evaluation mechanics, not real-world or cross-repository accuracy."
)

selected_dataset = st.selectbox("Evaluation set", list(DATASETS))
dataset_path = DATASETS[selected_dataset]
table = cached_evaluation(str(dataset_path))
decisions = cached_decisions(str(dataset_path))
raw_row = table.loc[table["Policy"] == "Raw-evidence gate"].iloc[0]

st.subheader(f"Policy evaluation · {selected_dataset}", anchor=False)
st.caption(
    "The target is high critical recall without sending every pull request "
    "to a person."
)
with st.container(horizontal=True, gap="small"):
    st.metric(
        "Critical recall",
        f"{raw_row['Critical recall']:.0%}",
        border=True,
        help="Share of labeled review-required changes escalated by the raw-evidence gate.",
    )
    st.metric(
        "Autonomous coverage",
        f"{raw_row['Autonomous coverage']:.0%}",
        border=True,
        help="Share of examples allowed to proceed without human review.",
    )
    st.metric(
        "False escalations",
        int(raw_row["False escalations"]),
        border=True,
        help="Safe changes unnecessarily sent to a human.",
    )
st.caption(f"Missed escalations: {int(raw_row['Missed escalations'])}")

points = (
    alt.Chart(table)
    .mark_circle(size=180)
    .encode(
        x=alt.X(
            "False escalations:Q",
            title="False escalations · unnecessary review",
            scale=alt.Scale(zero=True),
        ),
        y=alt.Y(
            "Missed escalations:Q",
            title="Missed escalations · unsafe autonomy",
            scale=alt.Scale(zero=True),
        ),
        color=alt.Color("Policy:N", legend=None),
        tooltip=[
            "Policy:N",
            "Missed escalations:Q",
            "False escalations:Q",
            alt.Tooltip("Critical recall:Q", format=".0%"),
            alt.Tooltip("Autonomous coverage:Q", format=".0%"),
        ],
    )
)
labels = points.mark_text(align="left", baseline="middle", dx=9).encode(text="Policy:N")
st.altair_chart(
    (points + labels).properties(title="Policy frontier · the lower-left corner is better"),
    width="stretch",
)

if selected_dataset == "Original stress test":
    st.warning(
        "The legacy path-risk ceiling is perfect here because `path_risk` was "
        "used to create these labels. Treat it as label leakage, not model performance.",
        icon=":material/science:",
    )
else:
    st.caption(
        "This challenge set deliberately breaks path-risk and diff-size shortcuts. "
        "It is hand-authored and small, so it demonstrates evaluation design—not "
        "production readiness."
    )

cached_judgments = sum(
    get_cached_live_judgment(decision, retrieve_policies(decision)) is not None
    for decision in decisions
)
if cached_judgments == len(decisions):
    st.success(
        f"Structured live-judge eval is complete: {cached_judgments}/{len(decisions)} "
        "predictions are cached and included in the table.",
        icon=":material/model_training:",
    )
else:
    st.caption(
        f"Structured live-judge eval cache: {cached_judgments}/{len(decisions)}. "
        "Run `python evaluate_judge.py` to evaluate the model over the full "
        "challenge set; this intentionally requires an explicit API call."
    )

with st.expander("Compare all policies", icon=":material/table_chart:"):
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Critical recall": st.column_config.ProgressColumn(
                "Critical recall",
                min_value=0.0,
                max_value=1.0,
                format="percent",
            ),
            "Autonomous coverage": st.column_config.ProgressColumn(
                "Autonomous coverage",
                min_value=0.0,
                max_value=1.0,
                format="percent",
            ),
            "Escalation precision": st.column_config.ProgressColumn(
                "Escalation precision",
                min_value=0.0,
                max_value=1.0,
                format="percent",
            ),
        },
    )

with st.expander("Inspect scenarios", icon=":material/search:"):
    policy_name = st.selectbox(
        "Policy",
        table["Policy"].tolist(),
        index=table["Policy"].tolist().index("Raw-evidence gate"),
        key=f"bucket_policy::{selected_dataset}",
    )
    st.dataframe(
        bucket_table(decisions, policy_name),
        hide_index=True,
        width="stretch",
    )

st.divider()
st.caption(
    "How the gate works, its rollout plan, and its full evaluation strategy are "
    "documented in `docs/product-and-evaluation-design.md` and `docs/live-demo.md`."
)
