"""Barra-like risk controls using available local factors.

This is not MSCI Barra data. It implements a transparent risk-control layer
with common style exposures: size, momentum, volatility, and liquidity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


STYLE_FACTORS = ["style_size", "style_momentum", "style_volatility", "style_liquidity"]


def _zscore_by_date(s: pd.Series, dates: pd.Series) -> pd.Series:
    def _one(x: pd.Series) -> pd.Series:
        std = x.std(ddof=0)
        if not std or pd.isna(std):
            return pd.Series(0.0, index=x.index)
        return (x - x.mean()) / std

    return s.groupby(dates, group_keys=False).apply(_one)


def add_barra_style_exposures(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    if "date" not in out.columns:
        out["date"] = pd.Timestamp.today().normalize()
    dates = pd.to_datetime(out["date"], errors="coerce")

    size_base = None
    for col in ["market_cap", "float_market_cap", "amount", "amt_20d_mean"]:
        if col in out.columns:
            size_base = pd.to_numeric(out[col], errors="coerce")
            break
    if size_base is None:
        size_base = pd.Series(1.0, index=out.index)
    out["style_size"] = _zscore_by_date(np.log1p(size_base.clip(lower=0).fillna(0)), dates)

    momentum = pd.Series(0.0, index=out.index)
    if "ret_20d" in out.columns:
        momentum = momentum.add(pd.to_numeric(out["ret_20d"], errors="coerce").fillna(0), fill_value=0)
    if "ret_5d" in out.columns:
        momentum = momentum.add(pd.to_numeric(out["ret_5d"], errors="coerce").fillna(0), fill_value=0)
    out["style_momentum"] = _zscore_by_date(momentum, dates)

    vol = pd.to_numeric(out.get("vol_20d", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0)
    out["style_volatility"] = _zscore_by_date(vol, dates)

    liq_base = pd.to_numeric(out.get("amt_20d_mean", out.get("amount", pd.Series(0.0, index=out.index))), errors="coerce")
    out["style_liquidity"] = _zscore_by_date(np.log1p(liq_base.clip(lower=0).fillna(0)), dates)
    return out


def neutralize_scores_barra(df: pd.DataFrame, score_col: str = "score") -> pd.DataFrame:
    out = add_barra_style_exposures(df)
    if out.empty or score_col not in out.columns:
        return out
    out[score_col] = pd.to_numeric(out[score_col], errors="coerce").fillna(0.0)

    def _neutralize_day(g: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in STYLE_FACTORS if c in g.columns and g[c].notna().any()]
        if "industry" in g.columns:
            industry_dummies = pd.get_dummies(g["industry"].fillna("UNKNOWN"), prefix="ind", dtype=float)
        else:
            industry_dummies = pd.DataFrame(index=g.index)
        x = pd.concat([g[cols].fillna(0.0), industry_dummies], axis=1)
        if x.empty or len(g) <= x.shape[1] + 2:
            g["barra_score"] = g[score_col]
            return g
        x = np.column_stack([np.ones(len(x)), x.to_numpy(dtype=float)])
        y = g[score_col].to_numpy(dtype=float)
        try:
            beta = np.linalg.lstsq(x, y, rcond=None)[0]
            resid = y - x @ beta
            g["barra_score"] = resid
        except Exception:
            g["barra_score"] = g[score_col]
        return g

    out = out.groupby(pd.to_datetime(out["date"], errors="coerce"), group_keys=False).apply(_neutralize_day)
    out[score_col] = out["barra_score"].rank(pct=True)
    return out


def barra_risk_report(df: pd.DataFrame, score_col: str = "score", top_n: int = 10) -> dict:
    if df.empty:
        return {"status": "empty"}
    work = add_barra_style_exposures(df)
    if "date" in work.columns:
        latest = pd.to_datetime(work["date"], errors="coerce").max()
        work = work[pd.to_datetime(work["date"], errors="coerce") == latest].copy()
    work[score_col] = pd.to_numeric(work.get(score_col), errors="coerce").fillna(0.0)
    pick = work.sort_values(score_col, ascending=False).head(top_n)
    exposures = {}
    for col in STYLE_FACTORS:
        if col in pick.columns:
            exposures[col] = float(pd.to_numeric(pick[col], errors="coerce").mean())
    industry_exposure = {}
    if "industry" in pick.columns and not pick.empty:
        industry_exposure = {str(k): float(v) for k, v in pick["industry"].value_counts(normalize=True).items()}
    max_abs_style = max([abs(v) for v in exposures.values()] or [0.0])
    return {
        "status": "ok",
        "top_n": int(len(pick)),
        "style_exposure": exposures,
        "max_abs_style_exposure": float(max_abs_style),
        "industry_exposure": industry_exposure,
        "risk_flag": bool(max_abs_style > 1.0),
    }
