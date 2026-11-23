"""Self-iteration utilities for model upgrade."""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
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

    params_grid = [
        {"num_leaves": 31, "learning_rate": 0.05},
        {"num_leaves": 63, "learning_rate": 0.03},
        {"num_leaves": 15, "learning_rate": 0.08},
    ]

    best_params: dict = {}
    best_score = float("-inf")

    for params in params_grid:
        model = lgb.LGBMRegressor(
            n_estimators=250,
            random_state=42,
            **params,
        )
        x = df[FEATURE_COLS]
        y = df["target_5d"]
        model.fit(x, y)
        score = float(model.score(x, y))
        if score > best_score:
            best_score = score
            best_params = params

    return IterationReport(best_params=best_params, best_score=best_score)
