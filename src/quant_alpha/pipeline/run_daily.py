"""Round-1 foundation pipeline: walk-forward, weekly mode, tradable filter, realistic backtest."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable

import joblib
import pandas as pd

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.data.akshare_adapter import AkshareAdapter
from quant_alpha.features.basic import build_features
from quant_alpha.model.drift import drift_report
from quant_alpha.model.experiment_tracker import ExperimentRun, ExperimentTracker
from quant_alpha.model.governance import ChampionChallengerRegistry, composite_score
from quant_alpha.model.optimizer import run_optimization
from quant_alpha.model.ranker import RankerResult, fallback_score, top_n_latest, walk_forward_score
from quant_alpha.model.review_adjustment import propose_adjustments_from_reviews
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


def _ensure_scored_schema(scored: pd.DataFrame, features: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    out = scored.copy()
    if "date" not in out.columns:
        if "date" in features.columns and len(features) == len(out):
            out["date"] = features["date"].values
            warnings.append("scored_missing_date_filled_from_features")
        else:
            out["date"] = pd.Timestamp.today().normalize()
            warnings.append("scored_missing_date_filled_with_today")
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if out["date"].notna().sum() == 0:
        if "date" in features.columns:
            feat_date = pd.to_datetime(features["date"], errors="coerce")
            if len(feat_date) == len(out) and feat_date.notna().sum() > 0:
                out["date"] = feat_date.values
                warnings.append("scored_all_nan_date_filled_from_features")
            else:
                out["date"] = pd.Timestamp.today().normalize()
                warnings.append("scored_all_nan_date_filled_with_today")
        else:
            out["date"] = pd.Timestamp.today().normalize()
            warnings.append("scored_all_nan_date_filled_with_today")
    if "market" not in out.columns:
        out["market"] = features["market"].values if ("market" in features.columns and len(features) == len(out)) else ""
        warnings.append("scored_missing_market_filled")
    if "symbol" not in out.columns:
        out["symbol"] = features["symbol"].values if ("symbol" in features.columns and len(features) == len(out)) else ""
        warnings.append("scored_missing_symbol_filled")
    if "target_5d" not in out.columns:
        out["target_5d"] = 0.0
        warnings.append("scored_missing_target_filled_zero")
    return out


def _emit(progress_cb: Callable[[float, str], None] | None, pct: float, message: str) -> None:
    if progress_cb is not None:
        progress_cb(max(0.0, min(1.0, float(pct))), message)


def run_daily(
    top_n: int = 10,
    mode: str = "weekly",
    progress_cb: Callable[[float, str], None] | None = None,
    enable_optimization: bool | None = None,
) -> dict:
    _emit(progress_cb, 0.02, "初始化配置")
    paths = ProjectPaths(Path.cwd())
    paths.ensure()
    cfg = SystemConfig.load(paths.root)
    tracker = ExperimentTracker(paths)
    governance = ChampionChallengerRegistry(paths)
    run_id = tracker.new_run_id()

    _emit(progress_cb, 0.08, "拉取市场数据")
    adapter = AkshareAdapter.from_env()
    def _ingest_progress(p: float, msg: str) -> None:
        # map ingest internal [0,1] to global [0.08,0.20]
        _emit(progress_cb, 0.08 + 0.12 * p, f"拉取市场数据 {msg}")

    ingest_stats = DailyIngestor(adapter, paths).run(progress_cb=_ingest_progress)
    coverage = ingest_stats.get("coverage", {}) if isinstance(ingest_stats, dict) else {}

    _emit(progress_cb, 0.20, "加载并校验数据")
    bars = load_latest_raw(paths.data_raw)
    warnings = _validate_dataset(bars)
    for market, cov in (coverage.items() if isinstance(coverage, dict) else []):
        miss = int((cov or {}).get("missing_symbol_count", 0))
        total = int((cov or {}).get("spot_symbol_count", 0))
        if total > 0 and miss / total > 0.30:
            warnings.append(f"coverage_warning:{market}:missing={miss}/{total}")
    if bars.empty:
        return {"status": "failed", "reason": "no market data", "warnings": warnings, "ingest": ingest_stats}

    bars, universe_diag = apply_stock_pool_filters_with_diagnostics(
        bars,
        min_liquidity_amount=float(cfg.filters.get("min_avg_amount", 5_000_000)),
        min_listing_days=int(cfg.filters.get("min_listing_days", 60)),
        min_price=float(cfg.filters.get("min_price", 1.0)),
    )

    _emit(progress_cb, 0.32, "构建特征")
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
    _emit(progress_cb, 0.45, "训练并打分（walk-forward）")
    wf_cfg = cfg.walk_forward
    try:
        ranker_result = walk_forward_score(
            features,
            min_train_days=int(wf_cfg.get("train_window_days", 120)),
            step_days=int(wf_cfg.get("step_days", 5)),
        )
    except Exception as exc:
        warnings.append(f"model_fallback:{exc}")
        fb = fallback_score(features)
        if fb.empty or "score" not in fb.columns:
            return {
                "status": "failed",
                "reason": f"model stage failed: {exc}",
                "warnings": warnings,
                "universe": universe_diag,
                "ingest": ingest_stats,
            }
        ranker_result = RankerResult(model={"type": "fallback_score", "reason": str(exc)}, scored=fb)

    scored = ranker_result.scored.copy()
    scored["score"] = scored["score"].astype(float)
    scored = _ensure_scored_schema(scored, features, warnings)

    # weekly output
    _emit(progress_cb, 0.60, "生成周频推荐")
    horizon = int(cfg.weekly.get("holding_horizon_days", 5))
    if mode == "weekly":
        recs = build_weekly_recommendations(scored, top_n=top_n, model_version=f"wf_{date.today().isoformat()}", horizon_days=horizon)
        recs = recs.rename(columns={"final_score": "score"}) if "final_score" in recs.columns else recs
    else:
        recs = top_n_latest(scored, n=top_n).rename(columns={"date": "prediction_date", "name": "stock_name"})
        recs["holding_horizon_days"] = horizon
        recs["model_version"] = f"wf_{date.today().isoformat()}"

    # walk-forward artifacts
    _emit(progress_cb, 0.68, "计算分层评估指标")
    try:
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
    except Exception as exc:
        warnings.append(f"fold_metrics_fallback:{exc}")
        dts = []
        wf_windows = []
        fold_df = pd.DataFrame(columns=["date", "rank_ic", "avg_topn_ret", "coverage"])

    # backtest realistic
    _emit(progress_cb, 0.74, "运行回测")
    try:
        backtest = run_topn_backtest(
            scored,
            n=top_n,
            fee_rate=0.0005,
            slippage_bps=5,
            rebalance_days=int(wf_cfg.get("step_days", 5)),
        )
    except Exception as exc:
        warnings.append(f"backtest_fallback:{exc}")
        backtest = pd.DataFrame(columns=["date", "strategy_ret", "cum_ret", "max_drawdown", "turnover"])

    snap = date.today().isoformat()
    feature_file = paths.data_feature / f"features_{snap}.parquet"
    model_file = paths.model_dir / f"ranker_{snap}.joblib"
    topn_file = paths.report_dir / f"topn_{snap}.parquet"
    bt_file = paths.report_dir / f"backtest_{snap}.parquet"
    wf_file = paths.report_dir / f"walkforward_{snap}.parquet"
    wf_sum_file = paths.report_dir / f"walkforward_summary_{snap}.json"
    drift_file = paths.report_dir / f"drift_{snap}.json"
    review_file = paths.report_dir / f"review_{snap}.parquet"
    adjust_file = paths.report_dir / f"self_adjustment_{snap}.json"

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
    _emit(progress_cb, 0.80, "执行漂移检测")
    drift = drift_report(features, scored_df=scored, config=cfg.drift)
    pd.Series(drift).to_json(drift_file, force_ascii=False)

    # lightweight recommendation review: realized outcomes for historic scored panel
    review_cols = ["date", "market", "symbol", "score", "target_5d"]
    review_df = scored[[c for c in review_cols if c in scored.columns]].copy()
    if not review_df.empty:
        review_df["success"] = review_df["target_5d"] > 0
        review_df["excess_ret"] = review_df["target_5d"] - review_df.groupby("date")["target_5d"].transform("mean")
        review_df.to_parquet(review_file, index=False)

    # controlled self-adjustment proposal (logged only; no silent apply)
    _emit(progress_cb, 0.86, "生成自调整建议（仅记录）")
    proposal = propose_adjustments_from_reviews(
        review_df=review_df if not review_df.empty else pd.DataFrame(),
        current_weights=cfg.composite_weights,
        current_families={"price_momentum": True, "volatility": True, "liquidity": True, "relative_strength": True},
        cfg=cfg.self_adjustment,
    )
    pd.Series(
        {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal": {
                "weight_deltas": proposal.weight_deltas,
                "feature_family_changes": proposal.feature_family_changes,
                "rationale": proposal.rationale,
                "applied": False,
            },
        }
    ).to_json(adjust_file, force_ascii=False)

    rec_metrics = {
        "winner_precision": float((review_df["success"].mean()) if not review_df.empty else 0.0),
        "benchmark_hit_rate": float((review_df["excess_ret"] > 0).mean()) if not review_df.empty else 0.0,
    }
    eval_metrics = {
        "rank_ic": float(fold_df["rank_ic"].mean()) if not fold_df.empty else 0.0,
        "avg_topn_excess_ret": float(fold_df["avg_topn_ret"].mean()) if not fold_df.empty else 0.0,
        "stability_score": float(1.0 / (1.0 + (fold_df["avg_topn_ret"].std(ddof=0) if not fold_df.empty else 0.0))),
        "drawdown_penalty": float(abs(backtest["max_drawdown"].min())) if not backtest.empty else 0.0,
        "turnover_penalty": float(backtest["turnover"].mean()) if not backtest.empty else 0.0,
        "fold_win_rate": float((fold_df["avg_topn_ret"] > 0).mean()) if not fold_df.empty else 0.0,
        "recent_window_excess_ret": float(fold_df.tail(min(6, len(fold_df)))["avg_topn_ret"].mean()) if not fold_df.empty else 0.0,
        "instability_score": float(fold_df["avg_topn_ret"].std(ddof=0)) if not fold_df.empty else 0.0,
    }
    full_metrics = {**eval_metrics, **rec_metrics}
    full_metrics["composite_score"] = composite_score(full_metrics, cfg.composite_weights)

    challenger = {"run_id": run_id, "model_version": f"wf_{snap}", "metrics": full_metrics}
    decision = governance.evaluate_challenger(challenger, cfg.governance, cfg.composite_weights)
    gov_event = governance.register_challenger(challenger, decision)
    rollback = governance.rollback_if_needed(
        recent_realized_excess_ret=float(full_metrics.get("recent_window_excess_ret", 0.0)),
        thresholds=cfg.governance,
    )

    # optimization (bounded) summary
    do_opt = bool(cfg.optimization.get("run_on_daily", False)) if enable_optimization is None else bool(enable_optimization)
    if do_opt:
        _emit(progress_cb, 0.90, "执行优化搜索（Optuna）")
        optimization_result = run_optimization(bars, cfg, paths)
    else:
        optimization_result = None

    _emit(progress_cb, 0.96, "写入实验追踪与治理记录")
    tracker.log_run(
        ExperimentRun(
            run_id=run_id,
            model_version=f"wf_{snap}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            config_snapshot={
                "filters": cfg.filters,
                "walk_forward": cfg.walk_forward,
                "weekly": cfg.weekly,
                "governance": cfg.governance,
                "drift": cfg.drift,
            },
            feature_families={"price_momentum": True, "volatility": True, "liquidity": True, "relative_strength": True},
            model_params={"engine": "lgbm_ranker", "notes": "defaults + walk-forward"},
            train_period={"start": str(min(dts)) if dts else None, "end": str(max(dts)) if dts else None},
            validation_period={"fold_count": int(len(fold_df))},
            evaluation_metrics=eval_metrics,
            recommendation_metrics=rec_metrics,
            promotion_decision=gov_event,
            notes=["bounded_self_adjustment_logged_only"],
            warnings=warnings + drift.get("alerts", []),
            artifacts={
                "model_file": str(model_file),
                "feature_file": str(feature_file),
                "topn_file": str(topn_file),
                "review_file": str(review_file),
                "drift_file": str(drift_file),
                "optimization_study_file": (optimization_result.study_file if optimization_result is not None else ""),
                "optimization_trials_file": (optimization_result.trials_file if optimization_result is not None else ""),
            },
        )
    )

    latest_date = pd.to_datetime(scored["date"]).max() if not scored.empty else None
    latest_count = int(scored[pd.to_datetime(scored["date"]) == latest_date]["symbol"].nunique()) if latest_date is not None else 0
    if latest_count < top_n:
        warnings.append(f"insufficient_latest_universe:{latest_count}<{top_n}")

    status = "ok_with_warnings" if warnings else "ok"
    _emit(progress_cb, 1.0, "流程完成")
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
        "drift_file": str(drift_file),
        "review_file": str(review_file),
        "self_adjustment_file": str(adjust_file),
        "governance_event": gov_event,
        "rollback": rollback,
        "optimization": {
            "enabled": do_opt,
            "best_params": (optimization_result.best_params if optimization_result is not None else {}),
            "best_metrics": (optimization_result.best_metrics if optimization_result is not None else {}),
            "best_composite": (optimization_result.best_composite if optimization_result is not None else None),
            "study_file": (optimization_result.study_file if optimization_result is not None else ""),
            "trials_file": (optimization_result.trials_file if optimization_result is not None else ""),
        },
        "run_id": run_id,
    }
