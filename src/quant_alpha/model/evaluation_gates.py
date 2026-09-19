"""Quality gates for model and pipeline releases."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    value: Any
    threshold: Any
    severity: str = "error"


def _f(metrics: dict[str, Any], name: str, default: float = 0.0) -> float:
    try:
        value = float(metrics.get(name, default))
    except Exception:
        return default
    return default if math.isnan(value) or math.isinf(value) else value


def evaluate_release_gates(
    metrics: dict[str, Any],
    drift: dict[str, Any] | None,
    warnings: list[str] | None,
    *,
    min_fold_count: int = 3,
    min_fold_win_rate: float = 0.45,
    min_rank_ic: float = -0.05,
    max_drawdown: float = 0.35,
    max_turnover: float = 2.0,
    max_error_warnings: int = 0,
) -> dict[str, Any]:
    """Return a machine-readable gate report for daily model artifacts."""
    drift = drift or {}
    warnings = warnings or []
    alerts = list(drift.get("alerts", []) or [])
    error_warnings = [w for w in warnings if str(w).startswith(("model_fallback:", "backtest_fallback:", "coverage_warning:"))]

    checks = [
        GateCheck(name="fold_count", passed=int(_f(metrics, "fold_count")) >= min_fold_count, value=int(_f(metrics, "fold_count")), threshold=min_fold_count),
        GateCheck(name="fold_win_rate", passed=_f(metrics, "fold_win_rate") >= min_fold_win_rate, value=_f(metrics, "fold_win_rate"), threshold=min_fold_win_rate),
        GateCheck(name="rank_ic", passed=_f(metrics, "rank_ic") >= min_rank_ic, value=_f(metrics, "rank_ic"), threshold=min_rank_ic),
        GateCheck(name="max_drawdown", passed=_f(metrics, "drawdown_penalty") <= max_drawdown, value=_f(metrics, "drawdown_penalty"), threshold=max_drawdown),
        GateCheck(name="turnover", passed=_f(metrics, "turnover_penalty") <= max_turnover, value=_f(metrics, "turnover_penalty"), threshold=max_turnover),
        GateCheck(name="drift_alerts", passed=len(alerts) == 0, value=alerts, threshold="no alerts", severity="warning"),
        GateCheck(name="pipeline_error_warnings", passed=len(error_warnings) <= max_error_warnings, value=error_warnings, threshold=max_error_warnings),
    ]
    failed = [c for c in checks if not c.passed and c.severity == "error"]
    warned = [c for c in checks if not c.passed and c.severity == "warning"]
    return {
        "status": "pass" if not failed else "fail",
        "checks": [c.__dict__ for c in checks],
        "failed_checks": [c.name for c in failed],
        "warning_checks": [c.name for c in warned],
        "summary": {
            "error_count": len(failed),
            "warning_count": len(warned),
            "drift_alert_count": len(alerts),
            "pipeline_error_warning_count": len(error_warnings),
        },
    }
