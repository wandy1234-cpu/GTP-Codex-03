"""Run drift checks from latest feature/scored artifacts."""

from __future__ import annotations

from pathlib import Path
import sys
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quant_alpha.config import ProjectPaths, SystemConfig
from quant_alpha.model.drift import drift_report


def main() -> None:
    paths = ProjectPaths(ROOT)
    cfg = SystemConfig.load(ROOT)
    feat_files = sorted(paths.data_feature.glob("features_*.parquet"))
    topn_files = sorted(paths.report_dir.glob("topn_*.parquet"))
    if not feat_files:
        print({"status": "no_feature_files"})
        return
    feat = pd.read_parquet(feat_files[-1])
    scored = pd.read_parquet(topn_files[-1]) if topn_files else None
    report = drift_report(feat, scored_df=scored, config=cfg.drift)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
