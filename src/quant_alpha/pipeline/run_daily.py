"""Round-1 foundation pipeline: walk-forward, weekly mode, tradable filter, realistic backtest."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import joblib
import pandas as pd

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.data.akshare_adapter import AkshareAdapter
from quant_alpha.features.basic import build_features
from quant_alpha.model.ranker import top_n_latest, walk_forward_score
from quant_alpha.model.walk_forward import build_walk_forward_windows, fold_metrics
from quant_alpha.pipeline.filters import apply_stock_pool_filters_with_diagnostics
from quant_alpha.pipeline.ingest import DailyIngestor
from quant_alpha.pipeline.weekly_pipeline import build_weekly_recommendations
from quant_alpha.storage.duckdb_store import load_latest_raw, save_feature_snapshot


def _validate_dataset(df: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if df.empty:
        warnings.append("dataset_empty")
        return warnings
    req = ["market", "symbol", "date", "close", "amount"]
    miss = [c for c in req if c not in df.columns]
    if miss:
        warnings.append(f"missing_columns:{','.join(miss)}")
    dup = df.duplicated(subset=[c for c in ["market", "symbol", "date"] if c in df.columns]).sum()
    if dup:
        warnings.append(f"duplicate_rows:{int(dup)}")
    return warnings


def run_daily(top_n: int = 10, mode: str = "weekly") -> dict:
    paths = ProjectPaths(Path.cwd())
    paths.ensure()
    cfg = SystemConfig.load(paths.root)

    adapter = AkshareAdapter.from_env()
    ingest_stats = DailyIngestor(adapter, paths).run()

    bars = load_latest_raw(paths.data_raw)
    warnings = _validate_dataset(bars)
    if bars.empty:
        return {"status": "failed", "reason": "no market data", "warnings": warnings, "ingest": ingest_stats}

    bars, universe_diag = apply_stock_pool_filters_with_diagnostics(
        bars,
        min_liquidity_amount=float(cfg.filters.get("min_avg_amount", 5_000_000)),
        min_listing_days=int(cfg.filters.get("min_listing_days", 60)),
        min_price=float(cfg.filters.get("min_price", 1.0)),
    )

    features = build_features(bars)
    if features.empty:
        return {
            "status": "failed",
            "reason": "feature dataframe empty after filtering",
            "warnings": warnings,
            "universe": universe_diag,
            "ingest": ingest_stats,
        }

    # strict walk-forward scoring
    wf_cfg = cfg.walk_forward
    ranker_result = walk_forward_score(
        features,
        min_train_days=int(wf_cfg.get("train_window_days", 120)),
        step_days=int(wf_cfg.get("step_days", 5)),
    )

    scored = ranker_result.scored.copy()
    scored["score"] = scored["score"].astype(float)

    # weekly output
    horizon = int(cfg.weekly.get("holding_horizon_days", 5))
    if mode == "weekly":
        recs = build_weekly_recommendations(scored, top_n=top_n, model_version=f"wf_{date.today().isoformat()}", horizon_days=horizon)
        recs = recs.rename(columns={"final_score": "score"}) if "final_score" in recs.columns else recs
    else:
        recs = top_n_latest(scored, n=top_n).rename(columns={"date": "prediction_date", "name": "stock_name"})
        recs["holding_horizon_days"] = horizon
        recs["model_version"] = f"wf_{date.today().isoformat()}"

    # walk-forward artifacts
    dts = sorted(pd.to_datetime(scored["date"]).dropna().unique())
    wf_windows = build_walk_forward_windows(
        dts,
        train_days=int(wf_cfg.get("train_window_days", 120)),
        valid_days=int(wf_cfg.get("valid_window_days", 20)),
        step_days=int(wf_cfg.get("step_days", 5)),
    )
    fold_df = fold_metrics(scored)
    if not wf_windows:
        warnings.append("insufficient_fold_count")

    # backtest realistic
    backtest = run_topn_backtest(
        scored,
        n=top_n,
        fee_rate=0.0005,
        slippage_bps=5,
        rebalance_days=int(wf_cfg.get("step_days", 5)),
    )

    snap = date.today().isoformat()
    feature_file = paths.data_feature / f"features_{snap}.parquet"
    model_file = paths.model_dir / f"ranker_{snap}.joblib"
    topn_file = paths.report_dir / f"topn_{snap}.parquet"
    bt_file = paths.report_dir / f"backtest_{snap}.parquet"
    wf_file = paths.report_dir / f"walkforward_{snap}.parquet"
    wf_sum_file = paths.report_dir / f"walkforward_summary_{snap}.json"

    save_feature_snapshot(features, feature_file)
    joblib.dump(ranker_result.model, model_file)
    recs.to_parquet(topn_file, index=False)
    backtest.to_parquet(bt_file, index=False)
    fold_df.to_parquet(wf_file, index=False)

    summary = {
        "fold_count": int(len(fold_df)),
        "rank_ic_mean": float(fold_df["rank_ic"].mean()) if not fold_df.empty else None,
        "avg_topn_ret_mean": float(fold_df["avg_topn_ret"].mean()) if not fold_df.empty else None,
    }
    wf_sum_file.write_text(pd.Series(summary).to_json(force_ascii=False), encoding="utf-8")

    latest_date = pd.to_datetime(scored["date"]).max() if not scored.empty else None
    latest_count = int(scored[pd.to_datetime(scored["date"]) == latest_date]["symbol"].nunique()) if latest_date is not None else 0
    if latest_count < top_n:
        warnings.append(f"insufficient_latest_universe:{latest_count}<{top_n}")

    status = "ok_with_warnings" if warnings else "ok"
    return {
        "status": status,
        "mode": mode,
        "warnings": warnings,
        "ingest": ingest_stats,
        "universe": universe_diag,
        "data_quality": {
            "latest_date": str(latest_date) if latest_date is not None else None,
            "latest_symbol_count": latest_count,
            "requested_top_n": top_n,
            "actual_top_n_count": int(len(recs)),
        },
        "feature_file": str(feature_file),
        "model_file": str(model_file),
        "topn_file": str(topn_file),
        "backtest_file": str(bt_file),
        "walkforward_file": str(wf_file),
        "walkforward_summary_file": str(wf_sum_file),
    }
