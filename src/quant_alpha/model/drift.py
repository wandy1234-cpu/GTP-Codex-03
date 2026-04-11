"""Simple data drift monitoring."""

from __future__ import annotations

import pandas as pd

from quant_alpha.model.ranker import FEATURE_COLS


def drift_report(feature_df: pd.DataFrame) -> dict:
    df = feature_df.dropna(subset=["date"] + FEATURE_COLS).copy()
    if df.empty:
        return {"status": "empty"}
    df["date"] = pd.to_datetime(df["date"])
    latest = df["date"].max()
    hist = df[df["date"] < latest]
    cur = df[df["date"] == latest]
    if hist.empty or cur.empty:
        return {"status": "insufficient_history"}

    deltas = {}
    for c in FEATURE_COLS:
        deltas[c] = float(cur[c].mean() - hist[c].mean())
    return {"status": "ok", "latest_date": str(latest), "feature_mean_shift": deltas}
