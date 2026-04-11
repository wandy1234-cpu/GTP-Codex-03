"""Promote latest optimization challenger if governance thresholds pass."""

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
    trials = sorted(paths.experiment_dir.glob("optimization_trials_*.json"))
    if not trials:
        print({"status": "no_trials_found"})
        return
    rows = json.loads(trials[-1].read_text(encoding="utf-8"))
    best = max(rows, key=lambda x: x.get("composite_score", float("-inf")))
    challenger = {"run_id": f"manual_promote_{trials[-1].stem}", "model_version": "challenger_optuna", "metrics": best.get("metrics", {})}
    decision = reg.evaluate_challenger(challenger, cfg.governance, cfg.composite_weights)
    event = reg.register_challenger(challenger, decision)
    print({"decision": decision.__dict__, "event": event})


if __name__ == "__main__":
    main()
