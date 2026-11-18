"""Controlled self-adjustment using recommendation review history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class AdjustmentProposal:
    weight_deltas: dict[str, float]
    feature_family_changes: dict[str, bool]
    rationale: list[str]
    applied: bool


def propose_adjustments_from_reviews(
    review_df: pd.DataFrame,
    current_weights: dict[str, float],
    current_families: dict[str, bool],
    cfg: dict[str, Any],
) -> AdjustmentProposal:
    if review_df.empty:
        return AdjustmentProposal({}, {}, ["no_review_data"], False)
    min_samples = int(cfg.get("min_samples_per_tag", 20))
    max_step = float(cfg.get("max_weight_step", 0.03))
    max_toggle = int(cfg.get("max_family_toggle", 1))
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return AdjustmentProposal({}, {}, ["self_adjustment_disabled"], False)

    df = review_df.copy()
    if "success" not in df.columns:
        if "excess_ret" in df.columns:
            df["success"] = df["excess_ret"] > 0
        else:
            return AdjustmentProposal({}, {}, ["missing_success_label"], False)

    deltas: dict[str, float] = {}
    rationale: list[str] = []
    for key in ["momentum", "stability", "liquidity", "quality"]:
        tag_col = f"tag_{key}"
        if tag_col not in df.columns:
            continue
        enough = int(df[tag_col].sum()) >= min_samples
        if not enough:
            continue
        hit_with_tag = float(df[df[tag_col]]["success"].mean())
        hit_without = float(df[~df[tag_col]]["success"].mean()) if (~df[tag_col]).any() else hit_with_tag
        gap = hit_with_tag - hit_without
        if abs(gap) < 0.01:
            continue
        direction = 1.0 if gap > 0 else -1.0
        step = min(max_step, abs(gap) / 5.0)
        deltas[f"{key}_weight"] = round(direction * step, 4)
        rationale.append(f"{key}:success_gap={gap:.3f}")

    changes: dict[str, bool] = {}
    if max_toggle > 0 and "feature_family" in df.columns:
        grp = df.groupby("feature_family")["success"].agg(["mean", "count"]).reset_index()
        grp = grp[grp["count"] >= min_samples].sort_values("mean", ascending=False)
        if len(grp) >= 2:
            best = str(grp.iloc[0]["feature_family"])
            worst = str(grp.iloc[-1]["feature_family"])
            if best in current_families and not current_families.get(best, True):
                changes[best] = True
                rationale.append(f"enable_family:{best}")
            if worst in current_families and current_families.get(worst, True):
                changes[worst] = False
                rationale.append(f"disable_family:{worst}")
            if len(changes) > max_toggle:
                changes = dict(list(changes.items())[:max_toggle])

    applied = bool(deltas or changes)
    return AdjustmentProposal(weight_deltas=deltas, feature_family_changes=changes, rationale=rationale, applied=applied)
