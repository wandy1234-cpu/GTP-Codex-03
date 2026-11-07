"""Self-iteration utilities for model upgrade."""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import optuna
import pandas as pd

from quant_alpha.model.ranker import FEATURE_COLS


@dataclass
class IterationReport:
    best_params: dict
    best_score: float


def optimize_once(feature_df: pd.DataFrame) -> IterationReport:
    df = feature_df.dropna(subset=FEATURE_COLS + ["target_5d", "date"]).copy()
    if df.empty:
        return IterationReport(best_params={}, best_score=float("nan"))

    x = df[FEATURE_COLS]
    y = df["target_5d"]

    def objective(trial: optuna.Trial) -> float:
        params = {
            "num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
        }
        model = lgb.LGBMRegressor(random_state=42, **params)
        model.fit(x, y)
        return float(model.score(x, y))

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=20, show_progress_bar=False)
    best_params = dict(study.best_params)
    best_score = float(study.best_value)

    return IterationReport(best_params=best_params, best_score=best_score)
