"""One-year historical backtest against the Shanghai Composite."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.features.basic import build_features
from quant_alpha.model.barra import barra_risk_report, neutralize_scores_barra
from quant_alpha.model.neutralize import neutralize_scores
from quant_alpha.model.ranker import fallback_score, walk_forward_score
from quant_alpha.pipeline.filters import apply_stock_pool_filters_with_diagnostics
from quant_alpha.storage.duckdb_store import load_latest_raw


PROXY_ENV_KEYS = [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "GIT_HTTP_PROXY",
    "GIT_HTTPS_PROXY",
]


@dataclass(frozen=True)
class OneYearBacktestResult:
    status: str
    report_file: str
    curve_file: str
    rows: int
    symbol_count: int
    metrics: dict[str, Any]
    warnings: list[str]


@contextmanager
def _without_broken_proxy():
    old = {k: os.environ.get(k) for k in PROXY_ENV_KEYS}
    try:
        for k in PROXY_ENV_KEYS:
            os.environ.pop(k, None)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _norm_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol) if ch.isdigit()) or str(symbol).strip()


def _prefixed_symbol(symbol: str) -> str:
    s = str(symbol).strip().lower()
    digits = _norm_symbol(s)
    if s.startswith(("sh", "sz", "bj")):
        return s
    if digits.startswith(("6", "9")):
        return f"sh{digits}"
    if digits.startswith(("8", "4")):
        return f"bj{digits}"
    return f"sz{digits}"


def _normalize_hist(raw: pd.DataFrame, symbol: str, name: str = "") -> pd.DataFrame:
    rename = {
        "\u65e5\u671f": "date",
        "\u5f00\u76d8": "open",
        "\u6536\u76d8": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u6da8\u8dcc\u5e45": "pct_change",
        "date": "date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "amount": "amount",
        "pct_change": "pct_change",
    }
    out = raw.rename(columns=rename).copy()
    if "date" not in out.columns:
        return pd.DataFrame()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["market"] = "A"
    out["symbol"] = symbol
    out["name"] = name
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_change"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = pd.NA
    if out["volume"].isna().all() and out["amount"].notna().any():
        out["volume"] = out["amount"]
    return out[
        ["date", "market", "symbol", "name", "open", "high", "low", "close", "volume", "amount", "pct_change"]
    ].dropna(subset=["date", "close"])


def _cache_file(root: Path) -> Path:
    out = root / "data" / "backtest" / "a_share_1y_bars.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _load_or_fetch_history(root: Path, max_symbols: int, refresh: bool, workers: int) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    cache = _cache_file(root)
    if cache.exists() and not refresh:
        out = pd.read_parquet(cache)
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        cached_symbols = int(out["symbol"].nunique()) if "symbol" in out.columns else 0
        min_cached_symbols = max(1, int(max_symbols * 0.7))
        if cached_symbols >= min_cached_symbols:
            return out, [f"used_cached_one_year_history:symbols={cached_symbols}"]
        warnings.append(f"cached_one_year_history_too_small:{cached_symbols}<{min_cached_symbols}")

    latest = load_latest_raw(root / "data" / "raw")
    latest = latest[latest.get("market").astype(str).eq("A")] if "market" in latest.columns else latest
    if latest.empty:
        return pd.DataFrame(), ["no_a_share_universe_cache"]
    latest = latest.drop_duplicates(subset=["symbol"]).copy()
    latest["amount"] = pd.to_numeric(latest.get("amount"), errors="coerce").fillna(0)
    latest = latest.sort_values("amount", ascending=False).head(max_symbols)
    start = (date.today() - timedelta(days=390)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")

    def _one(row: dict[str, Any]) -> pd.DataFrame:
        raw_symbol = str(row.get("symbol", ""))
        symbol = _norm_symbol(raw_symbol)
        prefixed = _prefixed_symbol(raw_symbol)
        name = str(row.get("name", ""))
        errors = []
        with _without_broken_proxy():
            sources = [
                (
                    "stock_zh_a_hist",
                    {"symbol": symbol, "period": "daily", "start_date": start, "end_date": end, "adjust": "qfq"},
                ),
                ("stock_zh_a_daily", {"symbol": prefixed, "start_date": start, "end_date": end, "adjust": "qfq"}),
                (
                    "stock_zh_a_hist_tx",
                    {"symbol": prefixed, "start_date": start, "end_date": end, "adjust": "qfq", "timeout": 10},
                ),
            ]
            for fn_name, kwargs in sources:
                try:
                    raw = getattr(ak, fn_name)(**kwargs)
                    hist = _normalize_hist(raw, symbol=raw_symbol, name=name)
                    if not hist.empty:
                        return hist
                except Exception as exc:
                    errors.append(f"{fn_name}:{type(exc).__name__}")
        raise RuntimeError(";".join(errors) or "all_history_sources_failed")

    frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_one, row): row for row in latest.to_dict("records")}
        for fut in as_completed(futs):
            row = futs[fut]
            try:
                hist = fut.result()
                if not hist.empty:
                    frames.append(hist)
            except Exception as exc:
                warnings.append(f"history_failed:{row.get('symbol')}:{exc!r}")
    if not frames:
        return pd.DataFrame(), warnings + ["history_download_empty"]
    bars = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["symbol", "date"], keep="last")
    bars.to_parquet(cache, index=False)
    return bars, warnings


def _benchmark_returns() -> pd.DataFrame:
    start = (date.today() - timedelta(days=390)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    raw = pd.DataFrame()
    with _without_broken_proxy():
        for fn_name, kwargs in [
            ("stock_zh_index_daily_em", {"symbol": "sh000001"}),
            ("stock_zh_index_daily", {"symbol": "sh000001"}),
            ("stock_zh_index_daily_tx", {"symbol": "sh000001"}),
        ]:
            try:
                raw = getattr(ak, fn_name)(**kwargs)
                if not raw.empty:
                    break
            except Exception:
                raw = pd.DataFrame()
    if raw.empty:
        return pd.DataFrame(columns=["date", "benchmark_ret_sh"])
    raw = raw.rename(columns={"date": "date", "\u65e5\u671f": "date", "close": "close", "\u6536\u76d8": "close"}).copy()
    if "date" not in raw.columns or "close" not in raw.columns:
        return pd.DataFrame(columns=["date", "benchmark_ret_sh"])
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw[(raw["date"] >= pd.to_datetime(start)) & (raw["date"] <= pd.to_datetime(end))]
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["date", "close"]).sort_values("date")
    raw["benchmark_ret_sh"] = raw["close"].shift(-5) / raw["close"] - 1.0
    return raw[["date", "benchmark_ret_sh"]].dropna()


def run_one_year_backtest(
    root: Path,
    top_n: int = 10,
    refresh: bool = False,
    max_symbols: int = 800,
    workers: int = 8,
) -> OneYearBacktestResult:
    warnings: list[str] = []
    bars, hist_warnings = _load_or_fetch_history(root, max_symbols=max_symbols, refresh=refresh, workers=workers)
    warnings.extend(hist_warnings)
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    curve_file = report_dir / f"one_year_backtest_{date.today().isoformat()}.parquet"
    report_file = report_dir / f"one_year_backtest_{date.today().isoformat()}.json"
    if bars.empty:
        payload = {"status": "failed", "reason": "no one-year history", "warnings": warnings}
        report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        pd.DataFrame().to_parquet(curve_file, index=False)
        return OneYearBacktestResult("failed", str(report_file), str(curve_file), 0, 0, {}, warnings)

    bars, diag = apply_stock_pool_filters_with_diagnostics(
        bars,
        min_liquidity_amount=5_000_000,
        min_listing_days=60,
        min_price=1.0,
    )
    if bars.empty:
        warnings.append("filtered_history_empty")
        payload = {"status": "failed", "reason": "filtered one-year history empty", "warnings": warnings}
        report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        pd.DataFrame().to_parquet(curve_file, index=False)
        return OneYearBacktestResult("failed", str(report_file), str(curve_file), 0, 0, {"universe": diag}, warnings)

    features = build_features(bars)
    valid_days = pd.to_datetime(features["date"], errors="coerce").nunique() if "date" in features.columns else 0
    if valid_days < 80:
        warnings.append(f"insufficient_history_days:{valid_days}<80")
    try:
        scored = walk_forward_score(features, min_train_days=80, step_days=5).scored
    except Exception as exc:
        warnings.append(f"model_fallback:{exc}")
        scored = fallback_score(features)
    scored = neutralize_scores(scored)
    scored = neutralize_scores_barra(scored)
    bt = run_topn_backtest(scored, n=top_n, fee_rate=0.0005, slippage_bps=5, rebalance_days=5)
    bench = _benchmark_returns()
    if not bench.empty and not bt.empty:
        bt["date"] = pd.to_datetime(bt["date"], errors="coerce")
        bt = bt.drop(columns=[c for c in ["benchmark_ret"] if c in bt.columns]).merge(bench, on="date", how="left")
        bt["benchmark_ret"] = bt["benchmark_ret_sh"].fillna(0.0)
        bt["excess_ret"] = bt["strategy_ret_net"] - bt["benchmark_ret"]
        bt["cum_excess_ret"] = (1 + bt["excess_ret"]).cumprod() - 1
    else:
        warnings.append("benchmark_shanghai_unavailable_used_cross_section_mean")
    bt.to_parquet(curve_file, index=False)
    metrics = {
        "top_n": int(top_n),
        "start": str(pd.to_datetime(bt["date"]).min().date()) if not bt.empty else None,
        "end": str(pd.to_datetime(bt["date"]).max().date()) if not bt.empty else None,
        "strategy_cum_ret": float(bt["cum_ret"].iloc[-1]) if not bt.empty and "cum_ret" in bt.columns else 0.0,
        "cum_excess_vs_shanghai": float(bt["cum_excess_ret"].iloc[-1])
        if not bt.empty and "cum_excess_ret" in bt.columns
        else 0.0,
        "max_drawdown": float(bt["max_drawdown"].min()) if not bt.empty and "max_drawdown" in bt.columns else 0.0,
        "hit_rate": float((bt["excess_ret"] > 0).mean()) if not bt.empty and "excess_ret" in bt.columns else 0.0,
        "barra": barra_risk_report(scored, top_n=top_n),
        "universe": diag,
    }
    payload = {
        "status": "ok_with_warnings" if warnings else "ok",
        "rows": int(len(bars)),
        "symbol_count": int(bars["symbol"].nunique()),
        "metrics": metrics,
        "warnings": warnings,
        "curve_file": str(curve_file),
    }
    report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return OneYearBacktestResult(
        payload["status"],
        str(report_file),
        str(curve_file),
        int(len(bars)),
        int(bars["symbol"].nunique()),
        metrics,
        warnings,
    )
