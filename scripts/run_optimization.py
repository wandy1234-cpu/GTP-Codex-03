"""Run bounded Optuna optimization and persist study artifacts."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.model.optimizer import run_optimization
from quant_alpha.storage.duckdb_store import load_latest_raw


def main() -> None:
    paths = ProjectPaths(ROOT)
    cfg = SystemConfig.load(ROOT)
    bars = load_latest_raw(paths.data_raw)
    result = run_optimization(bars, cfg, paths)
    print({"best_params": result.best_params, "best_composite": result.best_composite, "study_file": result.study_file})


if __name__ == "__main__":
    main()
