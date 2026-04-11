"""Daily ingestion pipeline for A/H market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from quant_alpha.config import ProjectPaths
from quant_alpha.data.akshare_adapter import AkshareAdapter


@dataclass
class DailyIngestor:
    adapter: AkshareAdapter
    paths: ProjectPaths

    def run(self, end: date | None = None, lookback_days: int = 365) -> dict[str, int]:
        self.paths.ensure()
        end = end or date.today()
        start = end - timedelta(days=lookback_days)

        stats: dict[str, int] = {}
        for market in ("A", "HK"):
            spot = self.adapter.fetch_spot(market)
            symbols = spot["symbol"].dropna().astype(str).unique().tolist()

            all_hist: list[pd.DataFrame] = []
            for symbol in symbols:
                try:
                    hist = self.adapter.fetch_history(symbol, market, start=start, end=end)
                    if not hist.empty:
                        all_hist.append(hist)
                except Exception:
                    continue

            if all_hist:
                raw = pd.concat(all_hist, ignore_index=True)
            else:
                raw = pd.DataFrame()

            out_file = self.paths.data_raw / f"market={market}" / f"date={end.isoformat()}" / "bars.parquet"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            raw.to_parquet(out_file, index=False)
            stats[market] = len(raw)

        return stats
