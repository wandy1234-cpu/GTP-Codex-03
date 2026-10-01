"""Round-1 foundation pipeline: walk-forward, weekly mode, tradable filter, realistic backtest."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Callable

import joblib
import pandas as pd

from quant_alpha.backtest.simple import run_topn_backtest
from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.data.akshare_adapter import AkshareAdapter
from quant_alpha.features.basic import build_features
from quant_alpha.model.drift import drift_report
from quant_alpha.model.barra import barra_risk_report, neutralize_scores_barra
from quant_alpha.model.evaluation_gates import evaluate_release_gates
from quant_alpha.model.experiment_tracker import ExperimentRun, ExperimentTracker
from quant_alpha.model.governance import ChampionChallengerRegistry, composite_score
from quant_alpha.model.optimizer import run_optimization
from quant_alpha.model.neutralize import neutralize_scores
from quant_alpha.model.ranker import RankerResult, fallback_score, top_n_latest, walk_forward_score
from quant_alpha.model.review_adjustment import propose_adjustments_from_reviews
from quant_alpha.model.walk_forward import build_walk_forward_windows, fold_metrics
from quant_alpha.pipeline.filters import apply_stock_pool_filters_with_diagnostics
from quant_alpha.pipeline.ingest import DailyIngestor
from quant_alpha.pipeline.weekly_pipeline import build_weekly_recommendations
from quant_alpha.storage.duckdb_store import load_latest_raw, save_feature_snapshot

HK_FALLBACK_NAMES = {
    "00005": "汇丰控股",
    "00700": "腾讯控股",
    "00883": "中国海洋石油",
    "00941": "中国移动",
    "01299": "友邦保险",
    "01788": "国泰君安国际",
    "03690": "美团-W",
}
HK_FALLBACK_NAMES.update(
    {
        "00005": "\u6c47\u4e30\u63a7\u80a1",
        "00700": "\u817e\u8baf\u63a7\u80a1",
        "00883": "\u4e2d\u56fd\u6d77\u6d0b\u77f3\u6cb9",
        "00941": "\u4e2d\u56fd\u79fb\u52a8",
        "01299": "\u53cb\u90a6\u4fdd\u9669",
        "01788": "\u56fd\u6cf0\u541b\u5b89\u56fd\u9645",
        "03690": "\u7f8e\u56e2-W",
    }
)


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


def _fill_recommendation_names(recs: pd.DataFrame, paths: ProjectPaths, allow_live_lookup: bool = True) -> pd.DataFrame:
    if recs.empty:
        return recs
    out = recs.copy()
    name_col = "stock_name" if "stock_name" in out.columns else ("name" if "name" in out.columns else None)
    if name_col is None:
        return out
    def _has_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))

    mask = out[name_col].isna() | (out[name_col].astype(str).str.strip() == "")
    if "market" in out.columns:
        hk_non_cjk = (
            out["market"].astype(str).eq("HK")
            & out[name_col].astype(str).str.strip().ne("")
            & ~out[name_col].astype(str).map(_has_cjk)
        )
        mask = mask | hk_non_cjk
    if not mask.any():
        return out
    name_file = paths.data_raw / "symbol_names.parquet"
    if not name_file.exists():
        return out
    ndf = pd.read_parquet(name_file)
    if not {"symbol", "name"}.issubset(ndf.columns):
        return out
    def _norm_sym(x: str) -> str:
        s = str(x).strip()
        if s.endswith(".0"):
            # parquet/csv round-trip may cast symbols like 700 -> 700.0
            try:
                s = str(int(float(s)))
            except Exception:
                pass
        low = s.lower().replace("hk", "")
        digits = "".join(ch for ch in low if ch.isdigit())
        if digits:
            return digits.zfill(5) if len(digits) <= 5 else digits
        return s
    mp = dict(zip(ndf["symbol"].astype(str), ndf["name"].astype(str)))
    mp.update({k: v for k, v in HK_FALLBACK_NAMES.items() if k not in mp or not str(mp.get(k, "")).strip()})
    # HK symbols often appear as 1/00001 across different endpoints; normalize both keys.
    for k, v in list(mp.items()):
        ks = str(k)
        if ks.isdigit() and len(ks) <= 5:
            mp.setdefault(ks.zfill(5), v)
        mp.setdefault(_norm_sym(ks), v)
    sym = out.loc[mask, "symbol"].astype(str)
    name_from_map = sym.map(mp)
    if "market" in out.columns:
        hk_mask = out.loc[mask, "market"].astype(str).eq("HK")
        norm_hk = sym.loc[hk_mask].map(_norm_sym)
        name_from_map.loc[hk_mask] = norm_hk.map(mp).fillna(name_from_map.loc[hk_mask])
        # if HK names still missing, try live spot name map once
        unresolved_hk = hk_mask & (name_from_map.isna() | (name_from_map.astype(str).str.strip() == ""))
        if unresolved_hk.any() and allow_live_lookup:
            try:
                adapter = AkshareAdapter.from_env()
                for idx, s in sym.loc[unresolved_hk].items():
                    n = adapter.fetch_hk_name_by_symbol(s)
                    if n:
                        name_from_map.loc[idx] = n
                unresolved_hk = hk_mask & (name_from_map.isna() | (name_from_map.astype(str).str.strip() == ""))
                if not unresolved_hk.any():
                    out.loc[mask, name_col] = name_from_map.fillna(out.loc[mask, name_col])
                    return out
                hk_name_map = adapter.fetch_symbol_name_map("HK")
                if hk_name_map:
                    nm = {(_norm_sym(k)): str(v) for k, v in hk_name_map.items() if str(v).strip() and _has_cjk(v)}
                    if not nm:
                        nm = {(_norm_sym(k)): str(v) for k, v in hk_name_map.items() if str(v).strip()}
                    name_from_map.loc[unresolved_hk] = (
                        sym.loc[unresolved_hk].map(_norm_sym).map(nm).fillna(name_from_map.loc[unresolved_hk])
                    )
                    unresolved_hk = hk_mask & (name_from_map.isna() | (name_from_map.astype(str).str.strip() == ""))
                if not unresolved_hk.any():
                    out.loc[mask, name_col] = name_from_map.fillna(out.loc[mask, name_col])
                    return out
                # spot fallback may fail; do not block single-symbol fallback on that failure
                try:
                    hk_spot = adapter.fetch_spot("HK")
                    hk_names = hk_spot["name"].astype(str) if "name" in hk_spot.columns else pd.Series("", index=hk_spot.index)
                    hk_map = dict(zip(hk_spot["symbol"].astype(str).map(_norm_sym), hk_names))
                    name_from_map.loc[unresolved_hk] = sym.loc[unresolved_hk].map(_norm_sym).map(hk_map).fillna(name_from_map.loc[unresolved_hk])
                except Exception:
                    pass
                still = hk_mask & (name_from_map.isna() | (name_from_map.astype(str).str.strip() == ""))
                if still.any():
                    for idx, s in sym.loc[still].items():
                        n = adapter.fetch_hk_name_by_symbol(s)
                        if n:
                            name_from_map.loc[idx] = n
            except Exception:
                pass
    out.loc[mask, name_col] = name_from_map.fillna(out.loc[mask, name_col])
    return out


def _emit(progress_cb: Callable[[float, str], None] | None, pct: float, message: str) -> None:
    if progress_cb is not None:
        progress_cb(max(0.0, min(1.0, float(pct))), message)


def _clean_metric_map(metrics: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and pd.isna(value):
            out[key] = 0.0
        else:
            out[key] = value
    return out


def run_daily(
    top_n: int = 10,
    mode: str = "weekly",
    progress_cb: Callable[[float, str], None] | None = None,
    enable_optimization: bool | None = None,
    smoke: bool = False,
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

    cached_smoke_bars = load_latest_raw(paths.data_raw) if smoke else pd.DataFrame()
    if smoke and not cached_smoke_bars.empty:
        ingest_stats = {
            "rows": {"cached": int(len(cached_smoke_bars))},
            "errors": {},
            "coverage": {},
            "notes": {"smoke": "used_cached_raw_data; live_ingest_skipped"},
        }
    else:
        ingest_stats = DailyIngestor(adapter, paths).run(
            progress_cb=_ingest_progress,
            lookback_days=90 if smoke else 365,
            max_symbols_per_market=30 if smoke else None,
            history_backfill_batch=30 if smoke else 1000,
            history_workers=2 if smoke else 8,
        )
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
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce") if "date" in bars.columns else pd.Timestamp.today().normalize()
    raw_latest_date = pd.to_datetime(bars["date"], errors="coerce").max() if "date" in bars.columns else None
    raw_latest = bars[pd.to_datetime(bars["date"], errors="coerce") == raw_latest_date] if raw_latest_date is not None else bars
    raw_symbol_count = int(raw_latest[["market", "symbol"]].drop_duplicates().shape[0]) if {"market", "symbol"}.issubset(raw_latest.columns) else int(len(raw_latest))
    raw_by_market = (
        {str(k): int(v) for k, v in raw_latest.groupby("market")["symbol"].nunique(dropna=True).items()}
        if {"market", "symbol"}.issubset(raw_latest.columns)
        else {}
    )

    bars, universe_diag = apply_stock_pool_filters_with_diagnostics(
        bars,
        min_liquidity_amount=float(cfg.filters.get("min_avg_amount", 5_000_000)),
        hk_min_liquidity_amount=float(cfg.filters.get("hk_min_avg_amount", 0)),
        min_listing_days=int(cfg.filters.get("min_listing_days", 60)),
        min_price=float(cfg.filters.get("min_price", 1.0)),
        hk_min_price=float(cfg.filters.get("hk_min_price", 0)),
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
    feature_dates = pd.to_datetime(features["date"], errors="coerce") if "date" in features.columns else pd.Series(dtype="datetime64[ns]")
    latest_feature_date = feature_dates.max() if not feature_dates.empty else pd.NaT
    if not pd.isna(latest_feature_date):
        scored_dates = pd.to_datetime(scored["date"], errors="coerce") if "date" in scored.columns else pd.Series(dtype="datetime64[ns]")
        latest_scored_count = int(scored.loc[scored_dates == latest_feature_date, "symbol"].nunique()) if "symbol" in scored.columns else 0
        latest_feature_count = int(features.loc[feature_dates == latest_feature_date, "symbol"].nunique()) if "symbol" in features.columns else 0
        if latest_feature_count > 0 and latest_scored_count < min(top_n, latest_feature_count):
            latest_features = features.loc[feature_dates == latest_feature_date].copy()
            latest_fallback = fallback_score(latest_features)
            if not latest_fallback.empty and "score" in latest_fallback.columns:
                old = scored.loc[scored_dates != latest_feature_date].copy()
                scored = pd.concat([old, latest_fallback], ignore_index=True)
                warnings.append(
                    f"latest_cross_section_score_fallback:{latest_scored_count}/{latest_feature_count}"
                )
    scored = neutralize_scores(scored)
    scored = neutralize_scores_barra(scored)
    barra_report = barra_risk_report(scored, top_n=top_n)

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
    recs = _fill_recommendation_names(recs, paths, allow_live_lookup=True)
    if not recs.empty:
        recs = recs.reset_index(drop=True)
        recs["rank_position"] = range(1, len(recs) + 1)

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
    if len(backtest) < 2:
        # fallback historical backtest using heuristic score on full feature history
        hist = fallback_score(features.copy())
        if "date" in hist.columns:
            hist["date"] = pd.to_datetime(hist["date"], errors="coerce")
            hist = hist.dropna(subset=["date", "score"])
        if "target_5d" in hist.columns:
            hist["target_5d"] = pd.to_numeric(hist["target_5d"], errors="coerce").fillna(0.0)
        hist_bt = run_topn_backtest(
            hist,
            n=top_n,
            fee_rate=0.0005,
            slippage_bps=5,
            rebalance_days=int(wf_cfg.get("step_days", 5)),
        )
        if len(hist_bt) >= 2:
            backtest = hist_bt
            warnings.append("backtest_rebuilt_from_feature_history")

    snap = date.today().isoformat()
    feature_file = paths.data_feature / f"features_{snap}.parquet"
    model_file = paths.model_dir / f"ranker_{snap}.joblib"
    topn_file = paths.report_dir / f"topn_{snap}.parquet"
    bt_file = paths.report_dir / f"backtest_{snap}.parquet"
    wf_file = paths.report_dir / f"walkforward_{snap}.parquet"
    wf_sum_file = paths.report_dir / f"walkforward_summary_{snap}.json"
    drift_file = paths.report_dir / f"drift_{snap}.json"
    barra_file = paths.report_dir / f"barra_risk_{snap}.json"
    gate_file = paths.report_dir / f"quality_gate_{snap}.json"
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
    barra_file.write_text(json.dumps(barra_report, ensure_ascii=False, indent=2), encoding="utf-8")

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
        "fold_count": int(len(fold_df)),
        "rank_ic": float(fold_df["rank_ic"].mean()) if not fold_df.empty else 0.0,
        "avg_topn_excess_ret": float(fold_df["avg_topn_ret"].mean()) if not fold_df.empty else 0.0,
        "stability_score": float(1.0 / (1.0 + (fold_df["avg_topn_ret"].std(ddof=0) if not fold_df.empty else 0.0))),
        "drawdown_penalty": float(abs(backtest["max_drawdown"].min())) if not backtest.empty else 0.0,
        "turnover_penalty": float(backtest["turnover"].mean()) if not backtest.empty else 0.0,
        "fold_win_rate": float((fold_df["avg_topn_ret"] > 0).mean()) if not fold_df.empty else 0.0,
        "recent_window_excess_ret": float(fold_df.tail(min(6, len(fold_df)))["avg_topn_ret"].mean()) if not fold_df.empty else 0.0,
        "instability_score": float(fold_df["avg_topn_ret"].std(ddof=0)) if not fold_df.empty else 0.0,
    }
    eval_metrics = _clean_metric_map(eval_metrics)
    rec_metrics = _clean_metric_map(rec_metrics)
    full_metrics = {**eval_metrics, **rec_metrics}
    full_metrics["composite_score"] = composite_score(full_metrics, cfg.composite_weights)
    release_gate = evaluate_release_gates(
        full_metrics,
        drift,
        warnings,
        min_fold_count=1 if smoke else 3,
        min_fold_win_rate=float(cfg.governance.get("min_fold_win_rate", 0.55)) - (0.20 if smoke else 0.0),
        max_drawdown=float(cfg.governance.get("max_drawdown_gate", 0.35)),
        max_turnover=float(cfg.governance.get("max_turnover_gate", 2.0)),
    )
    gate_file.write_text(json.dumps(release_gate, ensure_ascii=False, indent=2), encoding="utf-8")

    challenger = {"run_id": run_id, "model_version": f"wf_{snap}", "metrics": full_metrics}
    decision = governance.evaluate_challenger(challenger, cfg.governance, cfg.composite_weights)
    gov_event = governance.register_challenger(challenger, decision)
    rollback = governance.rollback_if_needed(
        recent_realized_excess_ret=float(full_metrics.get("recent_window_excess_ret", 0.0)),
        thresholds=cfg.governance,
    )

    # optimization (bounded) summary
    do_opt = False if smoke else (bool(cfg.optimization.get("run_on_daily", False)) if enable_optimization is None else bool(enable_optimization))
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
                "smoke": smoke,
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
            notes=["bounded_self_adjustment_logged_only", f"release_gate={release_gate['status']}"],
            warnings=warnings + drift.get("alerts", []),
            artifacts={
                "model_file": str(model_file),
                "feature_file": str(feature_file),
                "topn_file": str(topn_file),
                "review_file": str(review_file),
                "drift_file": str(drift_file),
                "barra_risk_file": str(barra_file),
                "quality_gate_file": str(gate_file),
                "optimization_study_file": (optimization_result.study_file if optimization_result is not None else ""),
                "optimization_trials_file": (optimization_result.trials_file if optimization_result is not None else ""),
            },
        )
    )

    latest_date = pd.to_datetime(scored["date"]).max() if not scored.empty else None
    latest_count = int(scored[pd.to_datetime(scored["date"]) == latest_date]["symbol"].nunique()) if latest_date is not None else 0
    filtered_latest = bars[pd.to_datetime(bars["date"], errors="coerce") == pd.to_datetime(bars["date"], errors="coerce").max()] if (not bars.empty and "date" in bars.columns) else bars
    filtered_by_market = (
        {str(k): int(v) for k, v in filtered_latest.groupby("market")["symbol"].nunique(dropna=True).items()}
        if {"market", "symbol"}.issubset(filtered_latest.columns)
        else {}
    )
    if latest_count < top_n:
        warnings.append(f"insufficient_latest_universe:{latest_count}<{top_n}")

    status = "ok_with_warnings" if warnings else "ok"
    _emit(progress_cb, 1.0, "流程完成")
    return {
        "status": status,
        "mode": mode,
        "smoke": smoke,
        "warnings": warnings,
        "ingest": ingest_stats,
        "universe": universe_diag,
        "data_quality": {
            "latest_date": str(raw_latest_date) if raw_latest_date is not None else (str(latest_date) if latest_date is not None else None),
            "raw_latest_symbol_count": raw_symbol_count,
            "raw_latest_by_market": raw_by_market,
            "filtered_latest_symbol_count": int(sum(filtered_by_market.values())) if filtered_by_market else int(len(filtered_latest)),
            "filtered_latest_by_market": filtered_by_market,
            "scored_latest_symbol_count": latest_count,
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
        "barra_risk_file": str(barra_file),
        "barra_risk": barra_report,
        "quality_gate_file": str(gate_file),
        "quality_gate": release_gate,
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
