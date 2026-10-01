"""Run ETF Top-N recommendation pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    from quant_alpha.etf.pipeline import recommend_etfs
except ModuleNotFoundError:
    src_root = Path(__file__).resolve().parents[1] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.etf.pipeline import recommend_etfs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recommend future-week ETF Top-N.")
    parser.add_argument("--top-n", type=int, default=5, help="Number of ETFs to recommend.")
    parser.add_argument("--refresh", action="store_true", help="Download/refresh all ETF history before ranking.")
    parser.add_argument("--workers", type=int, default=8, help="Parallel ETF history workers.")
    args = parser.parse_args()
    result = recommend_etfs(Path.cwd(), top_n=args.top_n, force_refresh=args.refresh, workers=args.workers)
    for k, v in result.__dict__.items():
        print(f"{k}: {v}")
