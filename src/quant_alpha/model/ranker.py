"""LightGBM ranker and walk-forward utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
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

    # 对全样本打分，保证 TopN 与回测覆盖完整股票池/时间段
    full_x = df[FEATURE_COLS]
    if full_x.empty:
        raise ValueError("full feature matrix is empty")
    df["score"] = model.predict(full_x)
    return RankerResult(model=model, scored=df)


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
    # robust fallback: if history factors are sparse, blend cross-sectional spot proxies
    if "date" in df.columns:
        grp = df.groupby("date", group_keys=False)
        cs_amt = grp["amount"].rank(pct=True) if "amount" in df.columns else pd.Series(0.5, index=df.index)
        cs_vol = grp["volume"].rank(pct=True) if "volume" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret1 = grp["ret_1d"].rank(pct=True) if "ret_1d" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret5 = grp["ret_5d"].rank(pct=True) if "ret_5d" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret20 = grp["ret_20d"].rank(pct=True) if "ret_20d" in df.columns else pd.Series(0.5, index=df.index)
        cs_pct = grp["pct_change"].rank(pct=True) if "pct_change" in df.columns else pd.Series(0.5, index=df.index)
        cs_sym = grp["symbol"].transform(lambda s: pd.Series(pd.util.hash_pandas_object(s.astype(str), index=False), index=s.index).rank(pct=True))
    else:
        cs_amt = df["amount"].rank(pct=True) if "amount" in df.columns else pd.Series(0.5, index=df.index)
        cs_vol = df["volume"].rank(pct=True) if "volume" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret1 = df["ret_1d"].rank(pct=True) if "ret_1d" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret5 = df["ret_5d"].rank(pct=True) if "ret_5d" in df.columns else pd.Series(0.5, index=df.index)
        cs_ret20 = df["ret_20d"].rank(pct=True) if "ret_20d" in df.columns else pd.Series(0.5, index=df.index)
        cs_pct = df["pct_change"].rank(pct=True) if "pct_change" in df.columns else pd.Series(0.5, index=df.index)
        cs_sym = pd.Series(pd.util.hash_pandas_object(df.get("symbol", pd.Series(np.arange(len(df)))), index=False), index=df.index).rank(pct=True)

    score = (
        0.25 * cs_ret20.fillna(0.5)
        + 0.20 * cs_ret5.fillna(0.5)
        + 0.15 * cs_ret1.fillna(0.5)
        + 0.15 * cs_pct.fillna(0.5)
        + 0.15 * cs_amt.fillna(0.5)
        + 0.10 * cs_vol.fillna(0.5)
        + 0.05 * cs_sym.fillna(0.5)
    )
    df["score"] = score.astype(float)
    return df


def walk_forward_score(
    feature_df: pd.DataFrame,
    min_train_days: int = 60,
    step_days: int = 5,
) -> RankerResult:
    """Strict walk-forward scoring with expanding window."""
    df = feature_df.dropna(subset=FEATURE_COLS + ["target_5d", "date", "symbol", "market"]).copy()
    if df.empty:
        raise ValueError("feature dataframe is empty after dropna")
    df["date"] = pd.to_datetime(df["date"])
    uniq_dates = sorted(df["date"].unique())
    if len(uniq_dates) <= min_train_days:
        return train_ranker(df)

    scored_parts: list[pd.DataFrame] = []
    last_model: Any = None
    for idx in range(min_train_days, len(uniq_dates), step_days):
        train_dates = set(uniq_dates[:idx])
        test_dates = set(uniq_dates[idx : idx + step_days])
        if not test_dates:
            continue
        train = df[df["date"].isin(train_dates)].copy()
        test = df[df["date"].isin(test_dates)].copy()
        if train.empty or test.empty:
            continue
        train["relevance"] = train.groupby("date")["target_5d"].transform(
            lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop")
        ).astype(int)
        group = train.groupby("date").size().tolist()
        model = lgb.LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            n_estimators=250,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
        )
        model.fit(train[FEATURE_COLS], train["relevance"], group=group)
        test["score"] = model.predict(test[FEATURE_COLS])
        scored_parts.append(test)
        last_model = model

    if not scored_parts:
        return train_ranker(df)
    scored = pd.concat(scored_parts, ignore_index=True)
    return RankerResult(model=last_model, scored=scored)
