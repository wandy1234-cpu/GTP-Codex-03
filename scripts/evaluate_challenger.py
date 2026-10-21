"""Evaluate latest optimization candidate against current champion."""

from __future__ import annotations

from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.model.governance import ChampionChallengerRegistry


def main() -> None:
    paths = ProjectPaths(ROOT)
    cfg = SystemConfig.load(ROOT)
    reg = ChampionChallengerRegistry(paths)
    exp_dir = paths.experiment_dir
    trials = sorted(exp_dir.glob("optimization_trials_*.json"))
    if not trials:
        print({"status": "no_trials_found"})
        return
    rows = json.loads(trials[-1].read_text(encoding="utf-8"))
    best = max(rows, key=lambda x: x.get("composite_score", float("-inf")))
    challenger = {"run_id": f"manual_eval_{trials[-1].stem}", "model_version": "challenger_optuna", "metrics": best.get("metrics", {})}
    decision = reg.evaluate_challenger(challenger, cfg.governance, cfg.composite_weights)
    print({"decision": decision.__dict__, "challenger": challenger["run_id"]})


if __name__ == "__main__":
    main()
