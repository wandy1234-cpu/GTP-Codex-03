"""Run Quant Alpha daily pipeline."""

from quant_alpha.pipeline.run_daily import run_daily


if __name__ == "__main__":
    result = run_daily(top_n=10)
    for k, v in result.items():
        print(f"{k}: {v}")
