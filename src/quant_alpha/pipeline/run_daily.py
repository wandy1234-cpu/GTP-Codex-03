"""End-to-end daily pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import joblib

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.config import ProjectPaths
from quant_alpha.data.akshare_adapter import AkshareAdapter
from quant_alpha.features.basic import build_features
from quant_alpha.model.ranker import top_n_latest, train_ranker
from quant_alpha.model.self_improve import optimize_once
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

    try:
        ranker_result = train_ranker(features)
        topn = top_n_latest(ranker_result.scored, n=top_n)
        backtest = run_topn_backtest(ranker_result.scored, n=top_n)
        improve = optimize_once(features)
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"model stage failed: {exc}",
            "ingest": ingest_stats,
        }

    model_file = paths.model_dir / f"ranker_{snap_date}.joblib"
    joblib.dump(ranker_result.model, model_file)

    topn_file = paths.report_dir / f"topn_{snap_date}.parquet"
    bt_file = paths.report_dir / f"backtest_{snap_date}.parquet"
    topn.to_parquet(topn_file, index=False)
    backtest.to_parquet(bt_file, index=False)

    has_warnings = any(bool(v) for v in (ingest_stats.get("errors") or {}).values())
    return {
        "status": "ok_with_warnings" if has_warnings else "ok",
        "ingest": ingest_stats,
        "feature_file": str(feature_file),
        "model_file": str(model_file),
        "topn_file": str(topn_file),
        "backtest_file": str(bt_file),
        "best_params": improve.best_params,
        "best_score": improve.best_score,
    }
