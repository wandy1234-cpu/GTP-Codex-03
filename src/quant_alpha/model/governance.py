"""Champion/challenger governance with bounded promotion and rollback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from quant_alpha.config import ProjectPaths


@dataclass
class GovernanceDecision:
    action: str
    accepted: bool
    rationale: str
    composite_delta: float
    details: dict[str, Any]


def composite_score(metrics: dict[str, Any], weights: dict[str, float]) -> float:
    plus = (
        weights.get("avg_topn_excess_ret", 0.30) * float(metrics.get("avg_topn_excess_ret", 0.0))
        + weights.get("winner_precision", 0.20) * float(metrics.get("winner_precision", 0.0))
        + weights.get("benchmark_hit_rate", 0.15) * float(metrics.get("benchmark_hit_rate", 0.0))
        + weights.get("rank_ic", 0.15) * float(metrics.get("rank_ic", 0.0))
        + weights.get("stability_score", 0.10) * float(metrics.get("stability_score", 0.0))
    )
    minus = (
        weights.get("drawdown_penalty", 0.05) * float(metrics.get("drawdown_penalty", 0.0))
        + weights.get("turnover_penalty", 0.05) * float(metrics.get("turnover_penalty", 0.0))
    )
    return float(plus - minus)


class ChampionChallengerRegistry:
    def __init__(self, paths: ProjectPaths) -> None:
        self.root = paths.governance_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "champion_state.json"
        self.history_file = self.root / "governance_history.jsonl"

    def load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"champion": None, "previous_champion": None, "challengers": []}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_history(self, record: dict[str, Any]) -> None:
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def evaluate_challenger(
        self,
        challenger: dict[str, Any],
        thresholds: dict[str, Any],
        weights: dict[str, float],
    ) -> GovernanceDecision:
        state = self.load_state()
        champion = state.get("champion")
        if champion is None:
            return GovernanceDecision("promote", True, "no_existing_champion", 1.0, {"reason": "bootstrap"})

        c_score = composite_score(champion.get("metrics", {}), weights)
        n_score = composite_score(challenger.get("metrics", {}), weights)
        delta = n_score - c_score

        fold_win_rate = float(challenger.get("metrics", {}).get("fold_win_rate", 0.0))
        drawdown_det = float(challenger.get("metrics", {}).get("drawdown_penalty", 0.0)) - float(champion.get("metrics", {}).get("drawdown_penalty", 0.0))
        turnover_det = float(challenger.get("metrics", {}).get("turnover_penalty", 0.0)) - float(champion.get("metrics", {}).get("turnover_penalty", 0.0))
        instab_det = float(challenger.get("metrics", {}).get("instability_score", 0.0)) - float(champion.get("metrics", {}).get("instability_score", 0.0))
        recent_det = float(challenger.get("metrics", {}).get("recent_window_excess_ret", 0.0)) - float(champion.get("metrics", {}).get("recent_window_excess_ret", 0.0))

        accept = (
            delta >= float(thresholds.get("min_composite_improvement", 0.01))
            and fold_win_rate >= float(thresholds.get("min_fold_win_rate", 0.55))
            and drawdown_det <= float(thresholds.get("max_drawdown_deterioration", 0.03))
            and turnover_det <= float(thresholds.get("max_turnover_deterioration", 0.20))
            and instab_det <= float(thresholds.get("max_instability_deterioration", 0.10))
            and recent_det >= 0
        )
        rationale = "promotion_thresholds_met" if accept else "promotion_thresholds_not_met"
        return GovernanceDecision(
            action="promote" if accept else "reject",
            accepted=accept,
            rationale=rationale,
            composite_delta=float(delta),
            details={
                "champion_composite": c_score,
                "challenger_composite": n_score,
                "fold_win_rate": fold_win_rate,
                "drawdown_deterioration": drawdown_det,
                "turnover_deterioration": turnover_det,
                "instability_deterioration": instab_det,
                "recent_window_deterioration": recent_det,
            },
        )

    def register_challenger(self, challenger: dict[str, Any], decision: GovernanceDecision) -> dict[str, Any]:
        state = self.load_state()
        now = datetime.now(timezone.utc).isoformat()
        event = {"timestamp": now, "type": "challenger_eval", "challenger": challenger, "decision": decision.__dict__}
        if decision.accepted:
            state["previous_champion"] = state.get("champion")
            state["champion"] = {**challenger, "promoted_at": now, "promotion_rationale": decision.rationale}
            event["type"] = "promotion"
        else:
            q = list(state.get("challengers", []))
            q.insert(0, {**challenger, "evaluated_at": now, "rejection_rationale": decision.rationale})
            state["challengers"] = q[:20]

        self.save_state(state)
        self._append_history(event)
        return event

    def rollback_if_needed(self, recent_realized_excess_ret: float, thresholds: dict[str, Any]) -> dict[str, Any]:
        state = self.load_state()
        champion = state.get("champion")
        previous = state.get("previous_champion")
        now = datetime.now(timezone.utc).isoformat()
        if champion is None or previous is None:
            return {"rolled_back": False, "reason": "insufficient_history"}
        limit = float(thresholds.get("rollback_underperformance_threshold", -0.01))
        if recent_realized_excess_ret > limit:
            return {"rolled_back": False, "reason": "performance_within_threshold"}
        state["champion"] = previous
        state["previous_champion"] = champion
        state["champion"]["rollback_at"] = now
        rationale = f"recent_realized_excess_ret={recent_realized_excess_ret:.4f} <= threshold={limit:.4f}"
        self.save_state(state)
        event = {"timestamp": now, "type": "rollback", "rationale": rationale}
        self._append_history(event)
        return {"rolled_back": True, "reason": rationale, "event": event}

    def history(self, limit: int = 200) -> pd.DataFrame:
        if not self.history_file.exists():
            return pd.DataFrame()
        rows = [json.loads(x) for x in self.history_file.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).tail(limit).reset_index(drop=True)
