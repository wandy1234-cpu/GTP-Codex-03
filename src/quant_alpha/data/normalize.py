"""Normalization helpers for AkShare outputs."""

from __future__ import annotations

import pandas as pd


A_SPOT_RENAME = {
    "\u4ee3\u7801": "symbol",
    "\u540d\u79f0": "name",
    "\u6700\u65b0\u4ef7": "close",
    "\u6da8\u8dcc\u5e45": "pct_change",
    "\u6da8\u8dcc\u989d": "change",
    "\u6210\u4ea4\u91cf": "volume",
    "\u6210\u4ea4\u989d": "amount",
    "\u632f\u5e45": "amplitude",
    "\u6700\u9ad8": "high",
    "\u6700\u4f4e": "low",
    "\u4eca\u5f00": "open",
    "\u6628\u6536": "pre_close",
    "\u91cf\u6bd4": "volume_ratio",
    "\u6362\u624b\u7387": "turnover_rate",
    "\u5e02\u76c8\u7387-\u52a8\u6001": "pe_ttm",
    "\u5e02\u51c0\u7387": "pb",
    "\u603b\u5e02\u503c": "market_cap",
    "\u6d41\u901a\u5e02\u503c": "float_market_cap",
    "code": "symbol",
    "symbol": "symbol",
    "name": "name",
    "close": "close",
    "pct_change": "pct_change",
    "change": "change",
    "volume": "volume",
    "amount": "amount",
    "high": "high",
    "low": "low",
    "open": "open",
}

H_SPOT_RENAME = {
    "\u4ee3\u7801": "symbol",
    "\u540d\u79f0": "name",
    "\u6700\u65b0\u4ef7": "close",
    "\u6da8\u8dcc\u989d": "change",
    "\u6da8\u8dcc\u5e45": "pct_change",
    "\u4eca\u5f00": "open",
    "\u6700\u9ad8": "high",
    "\u6700\u4f4e": "low",
    "\u6628\u6536": "pre_close",
    "\u6210\u4ea4\u91cf": "volume",
    "\u6210\u4ea4\u989d": "amount",
    "code": "symbol",
    "symbol": "symbol",
    "name": "name",
    "close": "close",
    "pct_change": "pct_change",
    "change": "change",
    "volume": "volume",
    "amount": "amount",
    "high": "high",
    "low": "low",
    "open": "open",
}

HISTORY_RENAME = {
    "\u65e5\u671f": "date",
    "\u5f00\u76d8": "open",
    "\u6536\u76d8": "close",
    "\u6700\u9ad8": "high",
    "\u6700\u4f4e": "low",
    "\u6210\u4ea4\u91cf": "volume",
    "\u6210\u4ea4\u989d": "amount",
    "\u632f\u5e45": "amplitude",
    "\u6da8\u8dcc\u5e45": "pct_change",
    "\u6da8\u8dcc\u989d": "change",
    "\u6362\u624b\u7387": "turnover_rate",
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
    "pct_change": "pct_change",
    "change": "change",
}


def normalize_spot(df: pd.DataFrame, rename_map: dict[str, str], market: str) -> pd.DataFrame:
    """Normalize spot quote dataframe to unified schema."""
    out = df.rename(columns=rename_map).copy()
    out["market"] = market
    if "symbol" in out.columns:
        sym = out["symbol"].astype(str).str.strip()
        sym = sym.str.replace(r"\.0+$", "", regex=True)
        if market == "HK":
            digits = sym.str.replace(r"[^0-9]", "", regex=True)
            has_digits = digits.str.len() > 0
            sym = sym.where(~has_digits, digits.str.zfill(5))
        out["symbol"] = sym
    return out


def normalize_history(df: pd.DataFrame, symbol: str, market: str) -> pd.DataFrame:
    """Normalize historical bars into standard OHLCV schema."""
    out = df.rename(columns=HISTORY_RENAME).copy()
    out["symbol"] = symbol
    out["market"] = market
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"])
    return out
