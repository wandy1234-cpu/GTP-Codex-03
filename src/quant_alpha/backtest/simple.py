"""Simple top-N backtest."""

from __future__ import annotations

import pandas as pd


def run_topn_backtest(scored: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame(columns=["date", "strategy_ret", "cum_ret"])

    df = scored.sort_values(["date", "score"], ascending=[True, False]).copy()
    daily = (
        df.groupby("date", group_keys=False)
        .head(n)
        .groupby("date", as_index=False)["target_5d"]
        .mean()
        .rename(columns={"target_5d": "strategy_ret"})
    )
    daily["cum_ret"] = (1 + daily["strategy_ret"]).cumprod() - 1
    return daily
