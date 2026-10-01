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
    df = df.replace([np.inf, -np.inf], np.nan)
    return df
