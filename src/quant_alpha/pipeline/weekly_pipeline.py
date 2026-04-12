"""Weekly prediction mode."""

from __future__ import annotations

import pandas as pd
import numpy as np


def build_weekly_recommendations(scored: pd.DataFrame, top_n: int, model_version: str, horizon_days: int = 5) -> pd.DataFrame:
    if scored.empty:
        return scored
    dt = pd.to_datetime(scored.get("date"), errors="coerce")
    latest = dt.max()
    if pd.isna(latest):
        # fallback: no valid date, select by global score ranking
        day = scored.copy()
    else:
        day = scored[dt == latest].copy()
    if day.empty:
        day = scored.copy()
    score_col = "final_score" if "final_score" in day.columns else "score"
    if score_col not in day.columns:
        day[score_col] = pd.Series(pd.util.hash_pandas_object(day.get("symbol", pd.Series(np.arange(len(day)))), index=False), index=day.index).rank(pct=True)
    else:
        day[score_col] = pd.to_numeric(day[score_col], errors="coerce")
        if day[score_col].notna().sum() == 0:
            day[score_col] = pd.Series(pd.util.hash_pandas_object(day.get("symbol", pd.Series(np.arange(len(day)))), index=False), index=day.index).rank(pct=True)
        else:
            day[score_col] = day[score_col].fillna(day[score_col].median())
    # market-neutral score calibration: avoid persistent A/H scale mismatch
    if "market" in day.columns:
        day["score_calibrated"] = day.groupby("market")[score_col].rank(pct=True)
    else:
        day["score_calibrated"] = day[score_col].rank(pct=True)
    ranked = day.sort_values("score_calibrated", ascending=False).drop_duplicates(subset=["market", "symbol"])
    mk = set(ranked["market"].astype(str).unique()) if "market" in ranked.columns else set()
    if "A" in mk and "HK" in mk and top_n >= 2:
        hk_quota = min(int((ranked["market"] == "HK").sum()), max(1, top_n // 5))
        a_quota = min(int((ranked["market"] == "A").sum()), max(1, top_n - hk_quota))
        pick_a = ranked[ranked["market"] == "A"].head(a_quota)
        pick_hk = ranked[ranked["market"] == "HK"].head(hk_quota)
        picked = pd.concat([pick_a, pick_hk], ignore_index=True)
        need = max(0, top_n - len(picked))
        if need > 0:
            used = set(zip(picked["market"].astype(str), picked["symbol"].astype(str)))
            rest = ranked[~ranked.apply(lambda r: (str(r.get("market", "")), str(r.get("symbol", ""))) in used, axis=1)].head(need)
            picked = pd.concat([picked, rest], ignore_index=True)
        day = picked.sort_values("score_calibrated", ascending=False).head(top_n)
    else:
        day = ranked.head(top_n)
    day = day.rename(columns={"date": "prediction_date", "name": "stock_name"})
    day["holding_horizon_days"] = horizon_days
    day["model_version"] = model_version
    day["final_score"] = day["score_calibrated"].astype(float)
    day["rank_position"] = range(1, len(day) + 1)
    cols = ["prediction_date", "holding_horizon_days", "market", "symbol", "stock_name", "model_version", "final_score", "rank_position"]
    for c in cols:
        if c not in day.columns:
            day[c] = None
    return day[cols]
