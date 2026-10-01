"""Practical drift monitoring with PSI and recent-vs-history checks."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_alpha.model.ranker import FEATURE_COLS


def _psi(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    expected = expected.dropna()
    actual = actual.dropna()
    if expected.empty or actual.empty:
        return 0.0
    q = pd.qcut(expected.rank(method="first"), q=min(bins, expected.nunique()), duplicates="drop")
    edges = sorted(set([x.left for x in q.cat.categories] + [q.cat.categories[-1].right]))
    if len(edges) < 2:
        return 0.0
    e = pd.cut(expected.rank(method="first"), bins=edges, include_lowest=True).value_counts(normalize=True, sort=False)
    a = pd.cut(actual.rank(method="first"), bins=edges, include_lowest=True).value_counts(normalize=True, sort=False)
    e = e.replace(0, 1e-6)
    a = a.replace(0, 1e-6)
    return float(((a - e) * np.log(a / e)).sum())


def drift_report(feature_df: pd.DataFrame, scored_df: pd.DataFrame | None = None, config: dict | None = None) -> dict:
    cfg = config or {}
    psi_warn = float(cfg.get("psi_warn", 0.2))
    psi_alert = float(cfg.get("psi_alert", 0.35))
    sigma_limit = float(cfg.get("mean_shift_sigma", 2.0))
    perf_drop_threshold = float(cfg.get("perf_drop_threshold", 0.03))
    recent_window = int(cfg.get("recent_window_days", 20))

    df = feature_df.dropna(subset=["date"] + FEATURE_COLS).copy()
    if df.empty:
        return {"status": "empty"}
    df["date"] = pd.to_datetime(df["date"])
    latest = df["date"].max()
    hist = df[df["date"] < latest]
    cur = df[df["date"] == latest]
    if hist.empty or cur.empty:
        return {"status": "insufficient_history"}

    deltas = {}
    psi_map = {}
    alerts: list[str] = []
    for c in FEATURE_COLS:
        h_mean = float(hist[c].mean())
        h_std = float(hist[c].std(ddof=0) or 1.0)
        c_mean = float(cur[c].mean())
        shift = c_mean - h_mean
        sigma_shift = shift / h_std if h_std else 0.0
        deltas[c] = {"mean_shift": shift, "sigma_shift": sigma_shift}
        psi_val = _psi(hist[c], cur[c])
        psi_map[c] = psi_val
        if abs(sigma_shift) >= sigma_limit:
            alerts.append(f"feature_mean_shift:{c}:{sigma_shift:.2f}sigma")
        if psi_val >= psi_alert:
            alerts.append(f"feature_psi_alert:{c}:{psi_val:.3f}")
        elif psi_val >= psi_warn:
            alerts.append(f"feature_psi_warn:{c}:{psi_val:.3f}")

    score_drift = {}
    perf_drift = {}
    if scored_df is not None and not scored_df.empty and "score" in scored_df.columns:
        s = scored_df.copy()
        s["date"] = pd.to_datetime(s["date"])
        s_hist = s[s["date"] < s["date"].max()]
        s_cur = s[s["date"] == s["date"].max()]
        if not s_hist.empty and not s_cur.empty:
            score_drift = {
                "mean_shift": float(s_cur["score"].mean() - s_hist["score"].mean()),
                "std_shift": float((s_cur["score"].std(ddof=0) or 0.0) - (s_hist["score"].std(ddof=0) or 0.0)),
                "psi": _psi(s_hist["score"], s_cur["score"]),
            }
            if score_drift["psi"] >= psi_alert:
                alerts.append(f"score_psi_alert:{score_drift['psi']:.3f}")

        if "target_5d" in s.columns:
            by_day = s.groupby("date", as_index=False)["target_5d"].mean()
            recent = by_day.tail(min(recent_window, len(by_day)))
            base = by_day.iloc[: max(0, len(by_day) - len(recent))]
            if not recent.empty and not base.empty:
                perf_drift = {
                    "recent_mean": float(recent["target_5d"].mean()),
                    "base_mean": float(base["target_5d"].mean()),
                }
                perf_drift["drop"] = perf_drift["recent_mean"] - perf_drift["base_mean"]
                if perf_drift["drop"] <= -abs(perf_drop_threshold):
                    alerts.append(f"realized_perf_drop:{perf_drift['drop']:.4f}")

    return {
        "status": "ok" if not alerts else "warning",
        "latest_date": str(latest),
        "feature_shift": deltas,
        "feature_psi": psi_map,
        "score_drift": score_drift,
        "performance_drift": perf_drift,
        "alerts": alerts,
        "recommend_retrain": bool(alerts),
    }
