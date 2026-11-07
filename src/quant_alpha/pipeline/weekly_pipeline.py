"""Weekly prediction mode."""

from __future__ import annotations

import pandas as pd


def build_weekly_recommendations(scored: pd.DataFrame, top_n: int, model_version: str, horizon_days: int = 5) -> pd.DataFrame:
    if scored.empty:
        return scored
    latest = pd.to_datetime(scored["date"]).max()
    day = scored[pd.to_datetime(scored["date"]) == latest].copy()
    score_col = "final_score" if "final_score" in day.columns else "score"
    day = day.sort_values(score_col, ascending=False).drop_duplicates(subset=["market", "symbol"]).head(top_n)
    day = day.rename(columns={"date": "prediction_date", "name": "stock_name"})
    day["holding_horizon_days"] = horizon_days
    day["model_version"] = model_version
    if "final_score" not in day.columns:
        day["final_score"] = day[score_col]
    day["rank_position"] = range(1, len(day) + 1)
    cols = ["prediction_date", "holding_horizon_days", "market", "symbol", "stock_name", "model_version", "final_score", "rank_position"]
    for c in cols:
        if c not in day.columns:
            day[c] = None
    return day[cols]
