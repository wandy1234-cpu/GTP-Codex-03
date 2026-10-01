"""Experiment tracking and registry persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from typing import Any

import pandas as pd

from quant_alpha.config import ProjectPaths


def _json_safe(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@dataclass
class ExperimentRun:
    run_id: str
    model_version: str
    timestamp: str
    config_snapshot: dict[str, Any]
    feature_families: dict[str, bool]
    model_params: dict[str, Any]
    train_period: dict[str, Any]
    validation_period: dict[str, Any]
    evaluation_metrics: dict[str, Any]
    recommendation_metrics: dict[str, Any]
    promotion_decision: dict[str, Any]
    notes: list[str]
    warnings: list[str]
    artifacts: dict[str, str]


class ExperimentTracker:
    """Append-only experiment tracker with per-run folders and index file."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.root = paths.experiment_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root / "registry.jsonl"

    def new_run_id(self) -> str:
        return f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"

    def log_run(self, payload: ExperimentRun) -> Path:
        run_dir = self.root / payload.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        with self.index_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_json_safe(payload), ensure_ascii=False) + "\n")
        return run_json

    def list_runs(self, limit: int = 50) -> pd.DataFrame:
        if not self.index_file.exists():
            return pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for line in self.index_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        if not rows:
            return pd.DataFrame()
        out = pd.DataFrame(rows)
        out = out.sort_values("timestamp", ascending=False).head(limit).reset_index(drop=True)
        return out
