"""Run one-year model backtest against the Shanghai Composite."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    from quant_alpha.backtest.yearly import run_one_year_backtest
except ModuleNotFoundError:
    src_root = Path(__file__).resolve().parents[1] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.backtest.yearly import run_one_year_backtest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-year TopN backtest vs Shanghai Composite.")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--refresh", action="store_true", help="Download one-year A-share history.")
    parser.add_argument("--max-symbols", type=int, default=800, help="Limit history download universe for runtime control.")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    result = run_one_year_backtest(
        Path.cwd(),
        top_n=args.top_n,
        refresh=args.refresh,
        max_symbols=args.max_symbols,
        workers=args.workers,
    )
    for k, v in result.__dict__.items():
        print(f"{k}: {v}")
