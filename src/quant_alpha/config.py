"""Runtime configuration for Quant Alpha."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency fallback
    yaml = None

DEFAULT_AKSHARE_TOKEN = None


@dataclass(frozen=True)
class AkshareConfig:
    """Configuration used by the AkShare adapter."""

    token: str | None = None

    @classmethod
    def from_env(cls) -> "AkshareConfig":
        token = (
            os.getenv("AKSHARE_TOKEN")
            or os.getenv("AKSHARE_API_KEY")
            or DEFAULT_AKSHARE_TOKEN
        )
        if token:
            token = token.strip().strip('"').strip("'")
        return cls(token=token or None)


@dataclass(frozen=True)
class ProjectPaths:
    """Filesystem layout for data and model artifacts."""

    root: Path = Path.cwd()

    @property
    def data_raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def data_feature(self) -> Path:
        return self.root / "data" / "feature"

    @property
    def model_dir(self) -> Path:
        return self.root / "models"

    @property
    def report_dir(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> None:
        for path in [self.data_raw, self.data_feature, self.model_dir, self.report_dir, self.experiment_dir, self.governance_dir]:
            path.mkdir(parents=True, exist_ok=True)

    @property
    def experiment_dir(self) -> Path:
        return self.report_dir / "experiments"

    @property
    def governance_dir(self) -> Path:
        return self.report_dir / "governance"


@dataclass(frozen=True)
class SystemConfig:
    """Runtime knobs for filters, walk-forward and weekly recommendation settings."""

    filters: dict[str, Any]
    walk_forward: dict[str, Any]
    weekly: dict[str, Any]
    optimization: dict[str, Any]
    governance: dict[str, Any]
    composite_weights: dict[str, Any]
    drift: dict[str, Any]
    self_adjustment: dict[str, Any]

    @classmethod
    def default(cls) -> "SystemConfig":
        return cls(
            filters={
                "min_avg_amount": 5_000_000,
                "hk_min_avg_amount": 0,
                "min_listing_days": 60,
                "min_price": 1.0,
                "hk_min_price": 0,
            },
            walk_forward={
                "train_window_days": 120,
                "valid_window_days": 20,
                "step_days": 5,
            },
            weekly={"holding_horizon_days": 5},
            optimization={
                "run_on_daily": False,
                "n_trials": 20,
                "timeout_sec": 180,
                "top_n_choices": [8, 10, 12, 15],
                "holding_horizon_choices": [5, 10],
            },
            governance={
                "min_composite_improvement": 0.01,
                "min_fold_win_rate": 0.55,
                "max_drawdown_deterioration": 0.03,
                "max_turnover_deterioration": 0.20,
                "max_instability_deterioration": 0.10,
                "rollback_underperformance_threshold": -0.01,
            },
            composite_weights={
                "avg_topn_excess_ret": 0.30,
                "winner_precision": 0.20,
                "benchmark_hit_rate": 0.15,
                "rank_ic": 0.15,
                "stability_score": 0.10,
                "drawdown_penalty": 0.05,
                "turnover_penalty": 0.05,
            },
            drift={
                "psi_warn": 0.2,
                "psi_alert": 0.35,
                "mean_shift_sigma": 2.0,
                "perf_drop_threshold": 0.03,
                "recent_window_days": 20,
            },
            self_adjustment={
                "enabled": True,
                "lookback_reviews": 8,
                "min_samples_per_tag": 20,
                "max_weight_step": 0.03,
                "max_family_toggle": 1,
            },
        )

    @classmethod
    def load(cls, root: Path) -> "SystemConfig":
        """
        Load optional config file from project root and merge onto defaults.

        Supported files:
        - config/system.yaml or config/system.yml (preferred)
        - config/system.json
        """

        cfg = cls.default()
        config_dir = root / "config"
        candidates = [config_dir / "system.yaml", config_dir / "system.yml", config_dir / "system.json"]
        payload: dict[str, Any] = {}

        for path in candidates:
            if not path.exists():
                continue
            if path.suffix in {".yaml", ".yml"}:
                if yaml is None:
                    # PyYAML not installed; skip yaml and continue checking json fallback.
                    continue
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            else:
                loaded = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                payload = loaded
                break

        def _merge(defaults: dict[str, Any], custom: Any) -> dict[str, Any]:
            if not isinstance(custom, dict):
                return defaults
            merged = defaults.copy()
            merged.update(custom)
            return merged

        return cls(
            filters=_merge(cfg.filters, payload.get("filters")),
            walk_forward=_merge(cfg.walk_forward, payload.get("walk_forward")),
            weekly=_merge(cfg.weekly, payload.get("weekly")),
            optimization=_merge(cfg.optimization, payload.get("optimization")),
            governance=_merge(cfg.governance, payload.get("governance")),
            composite_weights=_merge(cfg.composite_weights, payload.get("composite_weights")),
            drift=_merge(cfg.drift, payload.get("drift")),
            self_adjustment=_merge(cfg.self_adjustment, payload.get("self_adjustment")),
        )
