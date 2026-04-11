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

DEFAULT_AKSHARE_TOKEN = "4de5bc6ef18cbd032999b72d3245c4566c0be59b00d70839db24bc23"


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
        for path in [self.data_raw, self.data_feature, self.model_dir, self.report_dir]:
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class SystemConfig:
    """Runtime knobs for filters, walk-forward and weekly recommendation settings."""

    filters: dict[str, Any]
    walk_forward: dict[str, Any]
    weekly: dict[str, Any]

    @classmethod
    def default(cls) -> "SystemConfig":
        return cls(
            filters={
                "min_avg_amount": 5_000_000,
                "min_listing_days": 60,
                "min_price": 1.0,
            },
            walk_forward={
                "train_window_days": 120,
                "valid_window_days": 20,
                "step_days": 5,
            },
            weekly={"holding_horizon_days": 5},
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
        )
