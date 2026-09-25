"""Run Quant Alpha daily pipeline."""

import argparse
from pathlib import Path
import sys

try:
    from quant_alpha.pipeline.run_daily import run_daily
except ModuleNotFoundError:
    src_root = Path(__file__).resolve().parents[1] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.pipeline.run_daily import run_daily


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Quant Alpha daily pipeline.")
    parser.add_argument("--top-n", type=int, default=10, help="Number of recommendations to export.")
    parser.add_argument("--mode", choices=["weekly", "daily"], default="weekly", help="Recommendation mode.")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a bounded quick regression path with fewer symbols and a shorter lookback.",
    )
    parser.add_argument(
        "--enable-optimization",
        action="store_true",
        help="Run Optuna optimization even if config disables daily optimization.",
    )
    args = parser.parse_args()

    result = run_daily(
        top_n=args.top_n,
        mode=args.mode,
        smoke=args.smoke,
        enable_optimization=True if args.enable_optimization else None,
    )
    for k, v in result.items():
        print(f"{k}: {v}")
