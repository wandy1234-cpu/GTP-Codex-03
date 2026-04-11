"""Stock universe filtering rules."""

from __future__ import annotations

import pandas as pd


def apply_stock_pool_filters(
    df: pd.DataFrame,
    *,
    min_liquidity_amount: float = 5_000_000,
    min_listing_days: int = 60,
) -> pd.DataFrame:
    out = df.copy()

    if "name" in out.columns:
        out = out[~out["name"].astype(str).str.contains("ST", case=False, na=False)]

    if "volume" in out.columns:
        out = out[out["volume"].fillna(0) > 0]

    if "amount" in out.columns:
        out = out[out["amount"].fillna(0) >= min_liquidity_amount]

    if "list_date" in out.columns and "date" in out.columns:
        out["list_date"] = pd.to_datetime(out["list_date"], errors="coerce")
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        listing_days = (out["date"] - out["list_date"]).dt.days
        out = out[listing_days >= min_listing_days]

    return out
