"""LightGBM ranker and walk-forward utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import pandas as pd

FEATURE_COLS = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "amt_20d_mean"]


@dataclass
class RankerResult:
    model: Any
    scored: pd.DataFrame


def train_ranker(feature_df: pd.DataFrame) -> RankerResult:
    df = feature_df.dropna(subset=FEATURE_COLS + ["target_5d", "date", "symbol", "market"]).copy()
    if df.empty:
        raise ValueError("feature dataframe is empty after dropna")
    df["date"] = pd.to_datetime(df["date"])

    uniq_dates = sorted(df["date"].unique())
    if len(uniq_dates) < 2:
        # 单交易日场景：按股票维度切分 train/test，避免整体失败
        symbols = sorted(df["symbol"].astype(str).unique())
        split = max(1, int(len(symbols) * 0.8))
        split = min(split, len(symbols) - 1) if len(symbols) > 1 else 1
        train_symbols = set(symbols[:split])
        train = df[df["symbol"].astype(str).isin(train_symbols)].copy()
        test = df[~df["symbol"].astype(str).isin(train_symbols)].copy()
    else:
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
    scored = scored.copy()
    scored["date"] = pd.to_datetime(scored["date"])
    dates = sorted(scored["date"].dropna().unique(), reverse=True)
    buckets: list[pd.DataFrame] = []
    seen: set[tuple[str, str]] = set()
    for dt in dates:
        day = scored[scored["date"] == dt].sort_values("score", ascending=False)
        for _, row in day.iterrows():
            key = (str(row.get("market", "")), str(row.get("symbol", "")))
            if key in seen:
                continue
            seen.add(key)
            buckets.append(row.to_frame().T)
            if len(seen) >= n:
                break
        if len(seen) >= n:
            break
    if buckets:
        pick = pd.concat(buckets, ignore_index=True)
    else:
        pick = scored.sort_values("score", ascending=False).drop_duplicates(subset=["market", "symbol"]).head(n)
    if "name" not in pick.columns:
        pick = pick.assign(name="")
    return pick[["date", "market", "symbol", "name", "close", "score", "target_5d"]]


def fallback_score(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Heuristic scorer for small-sample fallback."""
    df = feature_df.copy()
    if df.empty:
        return df
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0.0
    score = (
        0.35 * df["ret_20d"].fillna(0)
        + 0.25 * df["ret_5d"].fillna(0)
        + 0.15 * df["ret_1d"].fillna(0)
        - 0.15 * df["vol_20d"].fillna(0)
        + 0.10 * (df["amt_20d_mean"].fillna(0).rank(pct=True))
    )
    df["score"] = score
    return df
