"""Score neutralization helpers."""

from __future__ import annotations

import pandas as pd


def neutralize_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "score" not in out.columns:
        return out

    if "industry" in out.columns:
        out["score"] = out["score"] - out.groupby(["date", "industry"])["score"].transform("mean")

    if "market_cap" in out.columns:
        cap_rank = out.groupby("date")["market_cap"].rank(pct=True)
        bucket = (cap_rank * 5).fillna(0).astype(int)
        out["score"] = out["score"] - out.groupby(["date", bucket])["score"].transform("mean")

    return out
