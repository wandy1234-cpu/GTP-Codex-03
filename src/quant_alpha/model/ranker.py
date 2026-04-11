"""LightGBM ranker and walk-forward utilities."""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import pandas as pd

FEATURE_COLS = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "amt_20d_mean"]


@dataclass
class RankerResult:
    model: lgb.LGBMRanker
    scored: pd.DataFrame


def train_ranker(feature_df: pd.DataFrame) -> RankerResult:
    df = feature_df.dropna(subset=FEATURE_COLS + ["target_5d", "date", "symbol", "market"]).copy()
    if df.empty:
        raise ValueError("feature dataframe is empty after dropna")
    df["date"] = pd.to_datetime(df["date"])

    uniq_dates = sorted(df["date"].unique())
    if len(uniq_dates) < 2:
        raise ValueError("not enough distinct dates for train/test split")
    split_idx = int(len(uniq_dates) * 0.8)
    split_idx = max(1, min(split_idx, len(uniq_dates) - 1))
    train_dates = set(uniq_dates[:split_idx])

    train = df[df["date"].isin(train_dates)].copy()
    test = df[~df["date"].isin(train_dates)].copy()
    if train.empty:
        raise ValueError("train set is empty")
    if test.empty:
        # 极端小样本时，回退为使用最后一个交易日作为测试集
        latest = df["date"].max()
        test = df[df["date"] == latest].copy()
        train = df[df["date"] < latest].copy()
        if train.empty or test.empty:
            raise ValueError("unable to build non-empty train/test sets")

    train["relevance"] = train.groupby("date")["target_5d"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop")
    ).astype(int)
    train_group = train.groupby("date").size().tolist()
    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        random_state=42,
    )
    model.fit(train[FEATURE_COLS], train["relevance"], group=train_group)

    test_x = test[FEATURE_COLS]
    if test_x.empty:
        raise ValueError("test feature matrix is empty")
    test["score"] = model.predict(test_x)
    return RankerResult(model=model, scored=test)


def top_n_latest(scored: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if scored.empty:
        return scored
    latest = scored["date"].max()
    pick = scored[scored["date"] == latest].sort_values("score", ascending=False).head(n)
    return pick[["date", "market", "symbol", "close", "score", "target_5d"]]
