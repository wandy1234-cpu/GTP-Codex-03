"""Run Quant Alpha daily pipeline."""

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
    result = run_daily(top_n=10)
    for k, v in result.items():
        print(f"{k}: {v}")
