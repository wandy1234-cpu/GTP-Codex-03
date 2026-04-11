"""Walk-forward helper utilities used by the daily pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd


def build_walk_forward_windows(
    dates: Iterable[pd.Timestamp | datetime],
    train_days: int = 120,
    valid_days: int = 20,
    step_days: int = 5,
) -> list[dict[str, pd.Timestamp]]:
    """Build rolling walk-forward window boundaries from sorted trading dates."""
    uniq_dates = sorted(pd.to_datetime(pd.Index(dates)).dropna().unique())
    windows: list[dict[str, pd.Timestamp]] = []
    if len(uniq_dates) < train_days + valid_days:
        return windows

    for idx in range(train_days, len(uniq_dates) - valid_days + 1, step_days):
        train_start = pd.Timestamp(uniq_dates[0])
        train_end = pd.Timestamp(uniq_dates[idx - 1])
        valid_start = pd.Timestamp(uniq_dates[idx])
        valid_end = pd.Timestamp(uniq_dates[idx + valid_days - 1])
        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "valid_start": valid_start,
                "valid_end": valid_end,
            }
        )
    return windows


def fold_metrics(scored: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    Calculate per-date diagnostics from scored panel.

    Returns columns:
    - date
    - rank_ic: Spearman correlation(score, target_5d)
    - avg_topn_ret: average target_5d for top-N by score
    - coverage: number of symbols on that date
    """
    if scored.empty:
        return pd.DataFrame(columns=["date", "rank_ic", "avg_topn_ret", "coverage"])

    req = {"date", "score", "target_5d"}
    if not req.issubset(set(scored.columns)):
        return pd.DataFrame(columns=["date", "rank_ic", "avg_topn_ret", "coverage"])

    df = scored.copy()
    df["date"] = pd.to_datetime(df["date"])
    rows: list[dict[str, float | int | pd.Timestamp]] = []
    for dt, grp in df.groupby("date", dropna=True):
        g = grp.dropna(subset=["score", "target_5d"]).copy()
        if g.empty:
            continue
        rank_ic = g["score"].corr(g["target_5d"], method="spearman")
        top_pick = g.sort_values("score", ascending=False).head(top_n)
        rows.append(
            {
                "date": pd.Timestamp(dt),
                "rank_ic": float(rank_ic) if pd.notna(rank_ic) else float("nan"),
                "avg_topn_ret": float(top_pick["target_5d"].mean()) if not top_pick.empty else float("nan"),
                "coverage": int(g["symbol"].nunique()) if "symbol" in g.columns else int(len(g)),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["date", "rank_ic", "avg_topn_ret", "coverage"])
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
