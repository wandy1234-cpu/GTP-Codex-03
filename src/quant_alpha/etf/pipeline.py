"""ETF recommendation pipeline using the same factor/ranking primitives."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import akshare as ak
import numpy as np
import pandas as pd

from quant_alpha.features.basic import build_features
from quant_alpha.model.ranker import fallback_score


SEED_ETFS = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "510050": "上证50ETF",
    "588000": "科创50ETF",
    "159915": "创业板ETF",
    "512100": "中证1000ETF",
    "512880": "证券ETF",
    "512170": "医疗ETF",
    "515790": "光伏ETF",
    "515030": "新能源车ETF",
    "512760": "芯片ETF",
    "512690": "酒ETF",
    "159928": "消费ETF",
    "513050": "中概互联网ETF",
    "513330": "恒生互联网ETF",
}


@dataclass(frozen=True)
class EtfPipelineResult:
    status: str
    topn_file: str
    data_file: str
    rows: int
    etf_count: int
    warnings: list[str]


def _first_col(df: pd.DataFrame, names: list[str]) -> str | None:
    cols = {str(c): str(c) for c in df.columns}
    for name in names:
        if name in cols:
            return cols[name]
    return None


def _normalize_spot(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["symbol", "name", "close", "pct_change", "amount", "volume"])
    symbol_col = _first_col(raw, ["代码", "基金代码", "symbol", "code"])
    name_col = _first_col(raw, ["名称", "基金简称", "name"])
    close_col = _first_col(raw, ["最新价", "现价", "close"])
    pct_col = _first_col(raw, ["涨跌幅", "pct_change"])
    amount_col = _first_col(raw, ["成交额", "amount"])
    volume_col = _first_col(raw, ["成交量", "volume"])
    out = pd.DataFrame(index=raw.index)
    out["symbol"] = raw[symbol_col].astype(str).str.replace(r"\.0+$", "", regex=True).str.strip() if symbol_col else ""
    out["name"] = raw[name_col].astype(str).str.strip() if name_col else ""
    out["close"] = pd.to_numeric(raw[close_col], errors="coerce") if close_col else pd.NA
    out["pct_change"] = pd.to_numeric(raw[pct_col], errors="coerce") if pct_col else pd.NA
    out["amount"] = pd.to_numeric(raw[amount_col], errors="coerce") if amount_col else pd.NA
    out["volume"] = pd.to_numeric(raw[volume_col], errors="coerce") if volume_col else pd.NA
    return out.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"]).reset_index(drop=True)


def _normalize_hist(raw: pd.DataFrame, symbol: str, name: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "振幅": "amplitude",
    }
    out = raw.rename(columns=rename).copy()
    if "date" not in out.columns:
        return pd.DataFrame()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["symbol"] = str(symbol)
    out["name"] = str(name)
    out["market"] = "ETF"
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_change"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    keep = ["date", "market", "symbol", "name", "open", "high", "low", "close", "volume", "amount", "pct_change"]
    for col in keep:
        if col not in out.columns:
            out[col] = pd.NA
    return out[keep].dropna(subset=["date", "close"]).reset_index(drop=True)


def _seed_spot() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": list(SEED_ETFS.keys()),
            "name": list(SEED_ETFS.values()),
            "close": pd.NA,
            "pct_change": pd.NA,
            "amount": pd.NA,
            "volume": pd.NA,
        }
    )


def _synthetic_bars(lookback_days: int = 220) -> pd.DataFrame:
    idx = pd.bdate_range(end=date.today(), periods=max(80, lookback_days))
    rows: list[dict[str, Any]] = []
    for pos, (symbol, name) in enumerate(SEED_ETFS.items()):
        rng = np.random.default_rng(10_000 + pos)
        drift = 0.00005 * ((pos % 5) - 2)
        shocks = rng.normal(drift, 0.009 + 0.001 * (pos % 4), size=len(idx))
        close = 1.0 + np.cumsum(shocks)
        close = np.maximum(close, 0.1)
        for dt, px, ret in zip(idx, close, shocks):
            rows.append(
                {
                    "date": pd.Timestamp(dt),
                    "market": "ETF",
                    "symbol": symbol,
                    "name": name,
                    "open": float(max(px * (1 - ret / 2), 0.1)),
                    "high": float(max(px * 1.006, 0.1)),
                    "low": float(max(px * 0.994, 0.1)),
                    "close": float(px),
                    "volume": float(rng.integers(1_000_000, 20_000_000)),
                    "amount": float(rng.integers(20_000_000, 800_000_000)),
                    "pct_change": float(ret * 100),
                }
            )
    return pd.DataFrame(rows)


def _cache_paths(root: Path) -> tuple[Path, Path, Path]:
    etf_dir = root / "data" / "etf"
    report_dir = root / "reports"
    etf_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return etf_dir / "etf_spot.parquet", etf_dir / "etf_bars.parquet", report_dir / f"etf_topn_{date.today().isoformat()}.parquet"


def _load_cache(root: Path) -> pd.DataFrame:
    _, bars_file, _ = _cache_paths(root)
    if not bars_file.exists():
        return pd.DataFrame()
    out = pd.read_parquet(bars_file)
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out


def fetch_all_etf_bars(root: Path, lookback_days: int = 220, workers: int = 8, force_refresh: bool = False) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    spot_file, bars_file, _ = _cache_paths(root)
    if bars_file.exists() and not force_refresh:
        return _load_cache(root), ["used_cached_etf_bars"]

    try:
        spot = _normalize_spot(ak.fund_etf_spot_em())
    except Exception as exc:
        cached = _load_cache(root)
        if not cached.empty:
            return cached, [f"spot_failed_used_cache:{exc!r}"]
        spot = _seed_spot()
        warnings.append(f"spot_failed_used_seed_etfs:{exc!r}")

    if spot.empty:
        cached = _load_cache(root)
        if not cached.empty:
            return cached, ["etf_spot_empty_used_cache"]
        spot = _seed_spot()
        warnings.append("etf_spot_empty_used_seed_etfs")
    spot.to_parquet(spot_file, index=False)
    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []

    def _one(row: dict[str, Any]) -> pd.DataFrame:
        symbol = str(row["symbol"])
        name = str(row.get("name", ""))
        try:
            raw = ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")
            hist = _normalize_hist(raw, symbol=symbol, name=name)
            if not hist.empty:
                return hist
        except Exception:
            pass
        raw = ak.fund_etf_hist_sina(symbol=symbol)
        return _normalize_hist(raw, symbol=symbol, name=name)

    rows = spot.to_dict("records")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_one, row): row for row in rows}
        for fut in as_completed(futs):
            row = futs[fut]
            try:
                hist = fut.result()
                if not hist.empty:
                    frames.append(hist)
            except Exception as exc:
                warnings.append(f"hist_failed:{row.get('symbol')}:{exc!r}")

    if not frames:
        cached = _load_cache(root)
        if not cached.empty:
            return cached, warnings + ["hist_all_failed_used_cache"]
        bars = _synthetic_bars(lookback_days=lookback_days)
        bars.to_parquet(bars_file, index=False)
        return bars, warnings + ["hist_all_failed_used_synthetic_seed_data", "synthetic_etf_data_not_for_trading"]
    bars = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
    bars = bars.drop_duplicates(subset=["symbol", "date"], keep="last").reset_index(drop=True)
    bars.to_parquet(bars_file, index=False)
    return bars, warnings


def recommend_etfs(root: Path, top_n: int = 5, force_refresh: bool = False, workers: int = 8) -> EtfPipelineResult:
    bars, warnings = fetch_all_etf_bars(root, force_refresh=force_refresh, workers=workers)
    _, bars_file, topn_file = _cache_paths(root)
    if bars.empty:
        topn = pd.DataFrame(columns=["prediction_date", "holding_horizon_days", "market", "symbol", "etf_name", "score", "rank_position"])
        topn.to_parquet(topn_file, index=False)
        return EtfPipelineResult("failed", str(topn_file), str(bars_file), 0, 0, warnings + ["etf_bars_empty"])

    features = build_features(bars)
    scored = fallback_score(features)
    scored["date"] = pd.to_datetime(scored["date"], errors="coerce")
    latest = scored["date"].max()
    day = scored[scored["date"] == latest].copy()
    if day.empty:
        day = scored.copy()
    day["score"] = pd.to_numeric(day.get("score"), errors="coerce").fillna(0.0)
    day = day.sort_values("score", ascending=False).drop_duplicates(subset=["symbol"]).head(top_n)
    out = day.rename(columns={"date": "prediction_date", "name": "etf_name"})[
        ["date" if "date" in day.columns else "prediction_date"]
    ] if False else day.copy()
    out = out.rename(columns={"date": "prediction_date", "name": "etf_name"})
    out["holding_horizon_days"] = 5
    out["model_version"] = f"etf_quant_alpha_{date.today().isoformat()}"
    out["rank_position"] = range(1, len(out) + 1)
    cols = ["prediction_date", "holding_horizon_days", "market", "symbol", "etf_name", "close", "score", "model_version", "rank_position"]
    for col in cols:
        if col not in out.columns:
            out[col] = None
    out[cols].to_parquet(topn_file, index=False)
    summary = {
        "status": "ok_with_warnings" if warnings else "ok",
        "topn_file": str(topn_file),
        "data_file": str(bars_file),
        "rows": int(len(bars)),
        "etf_count": int(bars["symbol"].nunique()),
        "warnings": warnings,
    }
    topn_file.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return EtfPipelineResult(**summary)
