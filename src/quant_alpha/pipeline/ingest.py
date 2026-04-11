"""Daily ingestion pipeline for A/H market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from glob import glob

import pandas as pd

from quant_alpha.config import ProjectPaths
from quant_alpha.data.akshare_adapter import AkshareAdapter


@dataclass
class DailyIngestor:
    adapter: AkshareAdapter
    paths: ProjectPaths

    def _latest_cached_bars(self, market: str) -> pd.DataFrame:
        pattern = self.paths.data_raw / f"market={market}" / "date=*" / "bars.parquet"
        files = sorted(glob(str(pattern)))
        if not files:
            return pd.DataFrame()
        return pd.read_parquet(files[-1])

    def run(
        self,
        end: date | None = None,
        lookback_days: int = 365,
        max_symbols_per_market: int | None = None,
    ) -> dict[str, object]:
        self.paths.ensure()
        end = end or date.today()
        start = end - timedelta(days=lookback_days)

        stats: dict[str, object] = {"rows": {}, "errors": {}}
        for market in ("A", "HK"):
            try:
                spot = self.adapter.fetch_spot(market)
                symbols = spot["symbol"].dropna().astype(str).unique().tolist()
            except Exception as exc:
                cached = self._latest_cached_bars(market)
                if not cached.empty:
                    out_file = self.paths.data_raw / f"market={market}" / f"date={end.isoformat()}" / "bars.parquet"
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    cached.to_parquet(out_file, index=False)
                    stats["rows"][market] = int(len(cached))
                    stats["errors"][market] = f"spot failed, fallback to cache: {exc}"
                else:
                    stats["rows"][market] = 0
                    stats["errors"][market] = f"spot failed and no cache available: {exc}"
                continue

            if max_symbols_per_market:
                symbols = symbols[:max_symbols_per_market]

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
            stats["rows"][market] = int(len(raw))

        return stats
