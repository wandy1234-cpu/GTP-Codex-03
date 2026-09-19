"""Stock universe filtering rules."""

from __future__ import annotations

import pandas as pd


def apply_stock_pool_filters(
    df: pd.DataFrame,
    *,
    min_liquidity_amount: float = 5_000_000,
    hk_min_liquidity_amount: float = 0,
    min_listing_days: int = 60,
) -> pd.DataFrame:
    out = df.copy()

    if "name" in out.columns:
        out = out[~out["name"].astype(str).str.contains("ST", case=False, na=False)]

    if "volume" in out.columns:
        out = out[out["volume"].fillna(0) > 0]

    if "amount" in out.columns:
        if "market" in out.columns:
            threshold = out["market"].astype(str).map({"HK": hk_min_liquidity_amount}).fillna(min_liquidity_amount)
            out = out[out["amount"].fillna(0) >= threshold]
        else:
            out = out[out["amount"].fillna(0) >= min_liquidity_amount]

    if "list_date" in out.columns and "date" in out.columns:
        out["list_date"] = pd.to_datetime(out["list_date"], errors="coerce")
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        listing_days = (out["date"] - out["list_date"]).dt.days
        out = out[listing_days >= min_listing_days]

    return out


def _market_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "market" not in df.columns or "symbol" not in df.columns:
        return {}
    return {str(k): int(v) for k, v in df.groupby("market")["symbol"].nunique(dropna=True).items()}


def apply_stock_pool_filters_with_diagnostics(
    df: pd.DataFrame,
    *,
    min_liquidity_amount: float = 5_000_000,
    hk_min_liquidity_amount: float = 0,
    min_listing_days: int = 60,
    min_price: float = 1.0,
    hk_min_price: float = 0,
) -> tuple[pd.DataFrame, dict]:
    diag = {
        "before_count": int(len(df)),
        "before_symbol_count": int(df[["market", "symbol"]].drop_duplicates().shape[0]) if {"market", "symbol"}.issubset(df.columns) else int(len(df)),
        "before_by_market": _market_counts(df),
        "removed_by_rule": {},
        "removed_by_rule_by_market": {},
        "missing_fields": [],
    }
    out = df.copy()

    def _record(rule: str, mask: pd.Series) -> None:
        diag["removed_by_rule"][rule] = int(mask.sum())
        if "market" in out.columns:
            diag["removed_by_rule_by_market"][rule] = {
                str(k): int(v) for k, v in out.loc[mask].groupby("market").size().items()
            }

    if "name" in out.columns:
        m = out["name"].astype(str).str.contains("ST", case=False, na=False)
        _record("st", m)
        out = out[~m]
    else:
        diag["missing_fields"].append("name")

    if "volume" in out.columns:
        m = out["volume"].fillna(0) <= 0
        _record("suspended", m)
        out = out[~m]
    elif "suspend_flag" in out.columns:
        m = out["suspend_flag"].fillna(False).astype(bool)
        _record("suspended", m)
        out = out[~m]
    else:
        diag["missing_fields"].append("volume/suspend_flag")

    if "amount" in out.columns:
        if "market" in out.columns:
            threshold = out["market"].astype(str).map({"HK": hk_min_liquidity_amount}).fillna(min_liquidity_amount)
            m = out["amount"].fillna(0) < threshold
        else:
            m = out["amount"].fillna(0) < min_liquidity_amount
        _record("amount", m)
        out = out[~m]
    else:
        diag["missing_fields"].append("amount")

    if "close" in out.columns:
        if "market" in out.columns:
            threshold = out["market"].astype(str).map({"HK": hk_min_price}).fillna(min_price)
            m = out["close"].fillna(0) < threshold
        else:
            m = out["close"].fillna(0) < min_price
        _record("min_price", m)
        out = out[~m]
    else:
        diag["missing_fields"].append("close")

    if "list_date" in out.columns and "date" in out.columns:
        out["list_date"] = pd.to_datetime(out["list_date"], errors="coerce")
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        m = (out["date"] - out["list_date"]).dt.days < min_listing_days
        m = m.fillna(False)
        _record("listing_days", m)
        out = out[~m]
    else:
        diag["missing_fields"].append("list_date")

    diag["after_count"] = int(len(out))
    diag["after_symbol_count"] = int(out[["market", "symbol"]].drop_duplicates().shape[0]) if {"market", "symbol"}.issubset(out.columns) else int(len(out))
    diag["after_by_market"] = _market_counts(out)
    return out, diag
