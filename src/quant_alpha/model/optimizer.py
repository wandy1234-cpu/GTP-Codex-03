"""Bounded optimization framework for challenger search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import optuna
import pandas as pd

from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.features.basic import build_features
from quant_alpha.model.governance import composite_score
from quant_alpha.model.ranker import walk_forward_score
from quant_alpha.model.walk_forward import fold_metrics


@dataclass
class OptimizationResult:
    best_params: dict[str, Any]
    best_metrics: dict[str, float]
    best_composite: float
    study_file: str
    trials_file: str


def _candidate_metrics(scored: pd.DataFrame, top_n: int) -> dict[str, float]:
    fold_df = fold_metrics(scored, top_n=top_n)
    if fold_df.empty:
        return {
            "avg_topn_excess_ret": 0.0,
            "winner_precision": 0.0,
            "benchmark_hit_rate": 0.0,
            "rank_ic": 0.0,
            "stability_score": 0.0,
            "drawdown_penalty": 1.0,
            "turnover_penalty": 1.0,
            "fold_win_rate": 0.0,
            "recent_window_excess_ret": 0.0,
            "instability_score": 1.0,
        }
    avg_topn = float(fold_df["avg_topn_ret"].mean())
    rank_ic = float(fold_df["rank_ic"].mean())
    win_rate = float((fold_df["avg_topn_ret"] > 0).mean())
    stability = float(1.0 / (1.0 + fold_df["avg_topn_ret"].std(ddof=0)))
    dd_pen = float(max(0.0, -float(fold_df["avg_topn_ret"].min())))
    recent = float(fold_df.tail(min(6, len(fold_df)))["avg_topn_ret"].mean())
    return {
        "avg_topn_excess_ret": avg_topn,
        "winner_precision": win_rate,
        "benchmark_hit_rate": win_rate,
        "rank_ic": rank_ic,
        "stability_score": stability,
        "drawdown_penalty": dd_pen,
        "turnover_penalty": 1.0 / max(1, 5),
        "fold_win_rate": win_rate,
        "recent_window_excess_ret": recent,
        "instability_score": float(fold_df["avg_topn_ret"].std(ddof=0)),
    }


def run_optimization(bars: pd.DataFrame, cfg: SystemConfig, paths: ProjectPaths) -> OptimizationResult:
    opt_cfg = cfg.optimization
    weights = cfg.composite_weights
    features = build_features(bars)
    if features.empty:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        paths.experiment_dir.mkdir(parents=True, exist_ok=True)
        trials_file = paths.experiment_dir / f"optimization_trials_{now}.json"
        study_file = paths.experiment_dir / f"optimization_study_{now}.json"
        trials_file.write_text("[]", encoding="utf-8")
        study_file.write_text(
            json.dumps({"best_params": {}, "best_value": None, "n_trials": 0, "timestamp": now, "note": "empty_features"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return OptimizationResult(best_params={}, best_metrics={}, best_composite=float("nan"), study_file=str(study_file), trials_file=str(trials_file))
    study = optuna.create_study(direction="maximize")
    records: list[dict[str, Any]] = []

    def objective(trial: optuna.Trial) -> float:
        top_n = trial.suggest_categorical("top_n", list(opt_cfg.get("top_n_choices", [8, 10, 12, 15])))
        train_window_days = trial.suggest_int("train_window_days", 90, 180, step=15)
        step_days = trial.suggest_int("step_days", 5, 10, step=1)
        try:
            candidate = walk_forward_score(features, min_train_days=train_window_days, step_days=step_days)
            metrics = _candidate_metrics(candidate.scored, top_n=top_n)
            score = composite_score(metrics, weights)
            rec = {"trial_number": trial.number, "params": trial.params, "metrics": metrics, "composite_score": score}
        except Exception as exc:
            metrics = _candidate_metrics(pd.DataFrame(), top_n=top_n)
            score = -1.0
            rec = {"trial_number": trial.number, "params": trial.params, "metrics": metrics, "composite_score": score, "error": str(exc)}
        records.append(rec)
        return score

    study.optimize(
        objective,
        n_trials=int(opt_cfg.get("n_trials", 20)),
        timeout=int(opt_cfg.get("timeout_sec", 180)),
        show_progress_bar=False,
    )
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    paths.experiment_dir.mkdir(parents=True, exist_ok=True)
    trials_file = paths.experiment_dir / f"optimization_trials_{now}.json"
    study_file = paths.experiment_dir / f"optimization_study_{now}.json"
    trials_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    study_payload = {
        "best_params": study.best_params,
        "best_value": float(study.best_value),
        "n_trials": len(study.trials),
        "timestamp": now,
    }
    study_file.write_text(json.dumps(study_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    best = max(records, key=lambda x: x["composite_score"]) if records else {"params": {}, "metrics": {}, "composite_score": float("nan")}
    return OptimizationResult(
        best_params=dict(best["params"]),
        best_metrics=dict(best["metrics"]),
        best_composite=float(best["composite_score"]),
        study_file=str(study_file),
        trials_file=str(trials_file),
    )
