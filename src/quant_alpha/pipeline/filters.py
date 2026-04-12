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


def apply_stock_pool_filters_with_diagnostics(
    df: pd.DataFrame,
    *,
    min_liquidity_amount: float = 5_000_000,
    min_listing_days: int = 60,
    min_price: float = 1.0,
) -> tuple[pd.DataFrame, dict]:
    diag = {"before_count": int(len(df)), "removed_by_rule": {}, "missing_fields": []}
    out = df.copy()

    if "name" in out.columns:
        m = out["name"].astype(str).str.contains("ST", case=False, na=False)
        diag["removed_by_rule"]["st"] = int(m.sum())
        out = out[~m]
    else:
        diag["missing_fields"].append("name")

    if "volume" in out.columns:
        m = out["volume"].fillna(0) <= 0
        diag["removed_by_rule"]["suspended"] = int(m.sum())
        out = out[~m]
    elif "suspend_flag" in out.columns:
        m = out["suspend_flag"].fillna(False).astype(bool)
        diag["removed_by_rule"]["suspended"] = int(m.sum())
        out = out[~m]
    else:
        diag["missing_fields"].append("volume/suspend_flag")

    if "amount" in out.columns:
        m = out["amount"].fillna(0) < min_liquidity_amount
        diag["removed_by_rule"]["amount"] = int(m.sum())
        out = out[~m]
    else:
        diag["missing_fields"].append("amount")

    if "close" in out.columns:
        m = out["close"].fillna(0) < min_price
        diag["removed_by_rule"]["min_price"] = int(m.sum())
        out = out[~m]
    else:
        diag["missing_fields"].append("close")

    if "list_date" in out.columns and "date" in out.columns:
        out["list_date"] = pd.to_datetime(out["list_date"], errors="coerce")
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        m = (out["date"] - out["list_date"]).dt.days < min_listing_days
        diag["removed_by_rule"]["listing_days"] = int(m.fillna(False).sum())
        out = out[~m.fillna(False)]
    else:
        diag["missing_fields"].append("list_date")

    diag["after_count"] = int(len(out))
    return out, diag
