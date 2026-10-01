"""Normalization helpers for AkShare outputs."""

from __future__ import annotations

import pandas as pd

A_SPOT_RENAME = {
    "代码": "symbol",
    "名称": "name",
    "最新价": "close",
    "涨跌幅": "pct_change",
    "涨跌额": "change",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "最高": "high",
    "最低": "low",
    "今开": "open",
    "昨收": "pre_close",
    "量比": "volume_ratio",
    "换手率": "turnover_rate",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "market_cap",
    "流通市值": "float_market_cap",
}

H_SPOT_RENAME = {
    "代码": "symbol",
    "名称": "name",
    "最新价": "close",
    "涨跌额": "change",
    "涨跌幅": "pct_change",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "昨收": "pre_close",
    "成交量": "volume",
    "成交额": "amount",
}


def normalize_spot(df: pd.DataFrame, rename_map: dict[str, str], market: str) -> pd.DataFrame:
    """Normalize spot quote dataframe to unified schema."""
    out = df.rename(columns=rename_map).copy()
    out["market"] = market
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str)
    return out


def normalize_history(df: pd.DataFrame, symbol: str, market: str) -> pd.DataFrame:
    """Normalize historical bars into standard OHLCV schema."""
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover_rate",
    }
    out = df.rename(columns=rename_map).copy()
    out["symbol"] = symbol
    out["market"] = market
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"])
    return out
