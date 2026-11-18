"""Broad index and market breadth summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import akshare as ak
import pandas as pd

from quant_alpha.storage.duckdb_store import load_latest_raw


A_BROAD_INDEXES = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "000016": "上证50",
}

HK_BROAD_INDEXES = {
    "HSI": "恒生指数",
    "HSCEI": "恒生中国企业指数",
    "HSTECH": "恒生科技指数",
}


def _first_col(cols: Iterable[str], candidates: Iterable[str]) -> str | None:
    col_set = {str(c): c for c in cols}
    for cand in candidates:
        if cand in col_set:
            return str(col_set[cand])
    lower = {str(c).lower(): str(c) for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _normalize_index_spot(raw: pd.DataFrame, market: str, wanted: dict[str, str]) -> pd.DataFrame:
    if raw.empty:
        rows = [
            {"market": market, "symbol": code, "name": name, "close": pd.NA, "pct_change": pd.NA, "change": pd.NA, "amount": pd.NA, "source": "unavailable"}
            for code, name in wanted.items()
        ]
        return pd.DataFrame(rows)
    cols = raw.columns.tolist()
    symbol_col = _first_col(cols, ["代码", "code", "symbol", "指数代码"])
    name_col = _first_col(cols, ["名称", "name", "指数名称"])
    close_col = _first_col(cols, ["最新价", "最新", "close", "last", "现价"])
    pct_col = _first_col(cols, ["涨跌幅", "pct_change", "change_percent", "涨幅"])
    change_col = _first_col(cols, ["涨跌额", "change", "涨跌"])
    amount_col = _first_col(cols, ["成交额", "amount"])

    if symbol_col is None and name_col is None:
        return pd.DataFrame(columns=["market", "symbol", "name", "close", "pct_change", "change", "amount", "source"])

    out = pd.DataFrame(index=raw.index)
    out["market"] = market
    out["symbol"] = raw[symbol_col].astype(str).str.strip() if symbol_col else ""
    out["name"] = raw[name_col].astype(str).str.strip() if name_col else ""
    out["close"] = pd.to_numeric(raw[close_col], errors="coerce") if close_col else pd.NA
    out["pct_change"] = pd.to_numeric(raw[pct_col], errors="coerce") if pct_col else pd.NA
    out["change"] = pd.to_numeric(raw[change_col], errors="coerce") if change_col else pd.NA
    out["amount"] = pd.to_numeric(raw[amount_col], errors="coerce") if amount_col else pd.NA
    out["source"] = "akshare_index_spot"

    wanted_codes = set(wanted)
    wanted_names = set(wanted.values())
    code_match = out["symbol"].isin(wanted_codes)
    name_match = out["name"].isin(wanted_names)
    contains_match = out["name"].map(lambda x: any(name in str(x) or str(x) in name for name in wanted_names))
    picked = out[code_match | name_match | contains_match].copy()

    if picked.empty:
        rows = [{"market": market, "symbol": code, "name": name, "close": pd.NA, "pct_change": pd.NA, "change": pd.NA, "amount": pd.NA, "source": "not_found"} for code, name in wanted.items()]
        return pd.DataFrame(rows)

    picked["_order"] = picked["symbol"].map({code: i for i, code in enumerate(wanted)}).fillna(999)
    picked = picked.sort_values(["_order", "name"]).drop_duplicates(subset=["name"], keep="first")
    return picked.drop(columns=["_order"]).reset_index(drop=True)


def fetch_broad_index_quotes() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    try:
        frames.append(_normalize_index_spot(ak.stock_zh_index_spot_em(), "A", A_BROAD_INDEXES))
    except Exception as exc:
        errors.append({"market": "A", "error": repr(exc)})
        frames.append(_normalize_index_spot(pd.DataFrame(), "A", A_BROAD_INDEXES))

    hk_fns = [getattr(ak, "stock_hk_index_spot_em", None), getattr(ak, "stock_hk_index_spot_sina", None)]
    hk_frame = pd.DataFrame()
    hk_error = None
    for fn in hk_fns:
        if fn is None:
            continue
        try:
            hk_frame = _normalize_index_spot(fn(), "HK", HK_BROAD_INDEXES)
            if not hk_frame.empty:
                break
        except Exception as exc:
            hk_error = exc
            continue
    if hk_frame.empty:
        if hk_error is not None:
            errors.append({"market": "HK", "error": repr(hk_error)})
        hk_frame = _normalize_index_spot(pd.DataFrame(), "HK", HK_BROAD_INDEXES)
    frames.append(hk_frame)

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if errors:
        out.attrs["errors"] = errors
    return out


def _infer_market_from_path(data_root: Path, df: pd.DataFrame) -> pd.DataFrame:
    if "market" in df.columns and df["market"].notna().any():
        return df
    frames = []
    for market_dir in sorted(data_root.glob("market=*")):
        market = market_dir.name.split("=", 1)[-1]
        master = market_dir / "master_bars.parquet"
        if master.exists():
            part = pd.read_parquet(master)
            part["market"] = part.get("market", market).fillna(market) if "market" in part.columns else market
            frames.append(part)
    return pd.concat(frames, ignore_index=True) if frames else df


def market_median_change(data_root: Path) -> pd.DataFrame:
    raw = load_latest_raw(data_root)
    raw = _infer_market_from_path(data_root, raw)
    if raw.empty:
        return pd.DataFrame(columns=["market", "median_pct_change", "stock_count", "latest_date", "change_source"])

    df = raw.copy()
    df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
    if df["date"].notna().any():
        latest = df["date"].max()
        df = df[df["date"] == latest].copy()
    else:
        latest = None

    if "pct_change" in df.columns:
        change = pd.to_numeric(df["pct_change"], errors="coerce")
        source = "pct_change"
    elif {"close", "pre_close"}.issubset(df.columns):
        change = pd.to_numeric(df["close"], errors="coerce") / pd.to_numeric(df["pre_close"], errors="coerce") - 1.0
        change = change * 100
        source = "close/pre_close"
    elif {"close", "open"}.issubset(df.columns):
        change = pd.to_numeric(df["close"], errors="coerce") / pd.to_numeric(df["open"], errors="coerce") - 1.0
        change = change * 100
        source = "close/open_proxy"
    else:
        return pd.DataFrame(columns=["market", "median_pct_change", "stock_count", "latest_date", "change_source"])

    df["market"] = df.get("market", "").fillna("").astype(str)
    df.loc[df["market"].eq("H"), "market"] = "HK"
    df["pct_change_for_median"] = change
    df = df.dropna(subset=["pct_change_for_median"])
    rows = []
    for market, grp in df.groupby("market", dropna=False):
        label = market if str(market).strip() else "ALL"
        rows.append(
            {
                "market": label,
                "median_pct_change": float(grp["pct_change_for_median"].median()),
                "stock_count": int(grp["symbol"].nunique()) if "symbol" in grp.columns else int(len(grp)),
                "latest_date": str(latest.date()) if latest is not None else "latest_snapshot",
                "change_source": source,
            }
        )
    return pd.DataFrame(rows).sort_values("market").reset_index(drop=True)
