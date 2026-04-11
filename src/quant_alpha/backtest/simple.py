"""Simple top-N backtest."""

from __future__ import annotations

import pandas as pd


def run_topn_backtest(
    scored: pd.DataFrame,
    n: int = 10,
    fee_rate: float = 0.0005,
    slippage_bps: float = 5.0,
    rebalance_days: int = 5,
) -> pd.DataFrame:
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
    daily = daily.iloc[:: max(1, rebalance_days)].reset_index(drop=True)
    cost = fee_rate + slippage_bps / 10000.0
    daily["strategy_ret_net"] = daily["strategy_ret"] - cost
    daily["cum_ret"] = (1 + daily["strategy_ret_net"]).cumprod() - 1
    daily["cum_max"] = daily["cum_ret"].cummax()
    daily["drawdown"] = daily["cum_ret"] - daily["cum_max"]
    daily["max_drawdown"] = daily["drawdown"].cummin()
    return daily
