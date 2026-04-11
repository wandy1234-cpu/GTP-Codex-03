"""End-to-end daily pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import joblib
import pandas as pd

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.config import ProjectPaths
from quant_alpha.data.akshare_adapter import AkshareAdapter
from quant_alpha.features.basic import build_features
from quant_alpha.model.drift import drift_report
from quant_alpha.model.neutralize import neutralize_scores
from quant_alpha.model.ranker import fallback_score, top_n_latest, train_ranker, walk_forward_score
from quant_alpha.model.self_improve import optimize_once
from quant_alpha.pipeline.filters import apply_stock_pool_filters
from quant_alpha.pipeline.ingest import DailyIngestor
from quant_alpha.storage.duckdb_store import load_latest_raw, save_feature_snapshot


def run_daily(top_n: int = 10) -> dict:
    paths = ProjectPaths(Path.cwd())
    adapter = AkshareAdapter.from_env()

    ingestor = DailyIngestor(adapter, paths)
    ingest_stats = ingestor.run()

    bars = load_latest_raw(paths.data_raw)
    if bars.empty:
        return {
            "status": "failed",
            "reason": "no market data available after ingestion",
            "ingest": ingest_stats,
        }

    bars = apply_stock_pool_filters(bars)
    features = build_features(bars)
    if features.empty:
        return {
            "status": "failed",
            "reason": "feature dataframe is empty",
            "ingest": ingest_stats,
        }

    snap_date = date.today().isoformat()
    feature_file = paths.data_feature / f"features_{snap_date}.parquet"
    save_feature_snapshot(features, feature_file)

    warnings: list[str] = []
    model_file = None
    try:
        ranker_result = walk_forward_score(features, min_train_days=60, step_days=5)
        scored = neutralize_scores(ranker_result.scored)
        topn = top_n_latest(scored, n=top_n)
        backtest = run_topn_backtest(scored, n=top_n, fee_rate=0.0005, slippage_bps=5, rebalance_days=5)
        improve = optimize_once(features)
    except Exception as exc:
        msg = str(exc)
        if "not enough distinct dates" in msg:
            scored = fallback_score(features)
            scored = neutralize_scores(scored)
            topn = top_n_latest(scored, n=top_n)
            backtest = run_topn_backtest(scored, n=top_n)
            improve = optimize_once(features)
            warnings.append(f"model fallback activated: {msg}")
        else:
            return {
                "status": "failed",
                "reason": f"model stage failed: {exc}",
                "ingest": ingest_stats,
            }
    else:
        model_file = paths.model_dir / f"ranker_{snap_date}.joblib"
        joblib.dump(ranker_result.model, model_file)

    topn_file = paths.report_dir / f"topn_{snap_date}.parquet"
    bt_file = paths.report_dir / f"backtest_{snap_date}.parquet"
    topn.to_parquet(topn_file, index=False)
    backtest.to_parquet(bt_file, index=False)

    ingest_errors = (ingest_stats.get("errors") or {})
    cache_notes = {k: v for k, v in ingest_errors.items() if "fallback to cache" in str(v)}
    hard_errors = {k: v for k, v in ingest_errors.items() if "fallback to cache" not in str(v) and v}
    hard_ingest_warning = any(bool(v) for v in hard_errors.values())
    has_warnings = hard_ingest_warning or bool(warnings)
    latest_date = pd.to_datetime(features["date"]).max() if "date" in features.columns and not features.empty else None
    latest_symbol_count = 0
    if latest_date is not None:
        latest_symbol_count = int(
            features[pd.to_datetime(features["date"]) == latest_date]["symbol"].astype(str).nunique()
        )
    data_quality = {
        "latest_date": str(latest_date) if latest_date is not None else None,
        "latest_symbol_count": latest_symbol_count,
        "requested_top_n": top_n,
        "actual_top_n_count": int(len(topn)),
    }
    if latest_symbol_count and latest_symbol_count < top_n:
        warnings.append(
            f"latest trading day has only {latest_symbol_count} unique symbols; recommendations filled from older dates"
        )
        has_warnings = True

    return {
        "status": "ok_with_warnings" if has_warnings else "ok",
        "ingest": {"rows": ingest_stats.get("rows", {}), "errors": hard_errors, "notes": cache_notes},
        "data_quality": data_quality,
        "feature_file": str(feature_file),
        "model_file": str(model_file) if model_file else None,
        "topn_file": str(topn_file),
        "backtest_file": str(bt_file),
        "best_params": improve.best_params,
        "best_score": improve.best_score,
        "drift": drift_report(features),
        "warnings": warnings,
    }
