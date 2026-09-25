"""Basic feature and label engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df = df.sort_values(["market", "symbol", "date"])

    grp = df.groupby(["market", "symbol"], group_keys=False)
    df["ret_1d"] = grp["close"].pct_change()
    df["ret_5d"] = grp["close"].pct_change(5)
    df["ret_20d"] = grp["close"].pct_change(20)
    df["vol_20d"] = grp["ret_1d"].rolling(20).std().reset_index(level=[0, 1], drop=True)
    df["amt_20d_mean"] = grp["amount"].rolling(20).mean().reset_index(level=[0, 1], drop=True)

    df["target_5d"] = grp["close"].shift(-5) / df["close"] - 1.0
    df["target_10d"] = grp["close"].shift(-10) / df["close"] - 1.0
    # benchmark-relative labels (market-level mean return proxy)
    mkt_5 = df.groupby(["market", "date"])["target_5d"].transform("mean")
    mkt_10 = df.groupby(["market", "date"])["target_10d"].transform("mean")
    df["future_excess_ret_5d"] = df["target_5d"] - mkt_5
    df["future_excess_ret_10d"] = df["target_10d"] - mkt_10
    df["future_rank_pct_5d"] = df.groupby(["market", "date"])["future_excess_ret_5d"].rank(pct=True)
    if "industry" in df.columns:
        df["future_rank_pct_5d_sector"] = df.groupby(["market", "date", "industry"])["future_excess_ret_5d"].rank(pct=True)
    else:
        df["future_rank_pct_5d_sector"] = np.nan
    df["beats_benchmark_5d"] = (df["future_excess_ret_5d"] > 0).astype(float)
    df["is_top_decile_winner_5d"] = (df["future_rank_pct_5d"] >= 0.9).astype(float)
    df = df.replace([np.inf, -np.inf], np.nan)
    return df
