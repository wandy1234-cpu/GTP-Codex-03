"""Daily ingestion pipeline for A/H market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from glob import glob

import numpy as np
import pandas as pd

from quant_alpha.config import ProjectPaths
from quant_alpha.data.akshare_adapter import AkshareAdapter


@dataclass
class DailyIngestor:
    adapter: AkshareAdapter
    paths: ProjectPaths

    def _seed_symbols(self, market: str) -> list[str]:
        if market == "A":
            return ["000001", "000002", "600519", "600036", "300750"]
        return ["00700", "00941", "00005", "01299", "03690"]

    def _builtin_name_map(self) -> dict[str, str]:
        return {
            "000001": "平安银行",
            "000002": "万科A",
            "600519": "贵州茅台",
            "600036": "招商银行",
            "300750": "宁德时代",
            "00700": "腾讯控股",
            "00941": "中国移动",
            "00005": "汇丰控股",
            "01299": "友邦保险",
            "03690": "美团-W",
        }

    def _name_store_file(self) -> str:
        return str(self.paths.data_raw / "symbol_names.parquet")

    def _load_name_map(self) -> dict[str, str]:
        store = self._name_store_file()
        if glob(store):
            df = pd.read_parquet(store)
            mapping = dict(zip(df["symbol"].astype(str), df["name"].astype(str)))
        else:
            mapping = {}
        mapping.update({k: v for k, v in self._builtin_name_map().items() if k not in mapping})
        return mapping

    def _save_name_map(self, mapping: dict[str, str]) -> None:
        if not mapping:
            return
        out = pd.DataFrame({"symbol": list(mapping.keys()), "name": list(mapping.values())})
        out.to_parquet(self._name_store_file(), index=False)

    def _synthetic_bars(self, market: str, end: date, lookback_days: int) -> pd.DataFrame:
        days = max(lookback_days, 120)
        idx = pd.bdate_range(end=end, periods=days)
        rows: list[dict] = []
        rng = np.random.default_rng(42 if market == "A" else 84)
        for symbol in self._seed_symbols(market):
            px = 20 + np.cumsum(rng.normal(0, 0.2, size=len(idx)))
            for d, p in zip(idx, px):
                rows.append(
                    {
                        "date": d,
                        "open": float(max(p * 0.995, 0.1)),
                        "high": float(max(p * 1.01, 0.1)),
                        "low": float(max(p * 0.99, 0.1)),
                        "close": float(max(p, 0.1)),
                        "volume": float(rng.integers(100000, 5000000)),
                        "amount": float(rng.integers(1_000_000, 500_000_000)),
                        "symbol": symbol,
                        "name": f"SYN_{symbol}",
                        "market": market,
                    }
                )
        return pd.DataFrame(rows)

    def _cached_bars_window(self, market: str, max_files: int = 180) -> pd.DataFrame:
        pattern = self.paths.data_raw / f"market={market}" / "date=*" / "bars.parquet"
        files = sorted(glob(str(pattern)))
        if not files:
            return pd.DataFrame()
        selected = files[-max_files:]
        frames = [pd.read_parquet(f) for f in selected]
        out = pd.concat(frames, ignore_index=True)
        name_map = self._load_name_map()
        if "name" not in out.columns:
            out["name"] = out["symbol"].astype(str).map(name_map).fillna("")
        else:
            missing = out["name"].isna() | (out["name"].astype(str).str.strip() == "")
            out.loc[missing, "name"] = out.loc[missing, "symbol"].astype(str).map(name_map).fillna("")
        return out

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
                name_map = (
                    spot[[c for c in ["symbol", "name"] if c in spot.columns]]
                    .drop_duplicates(subset=["symbol"])
                    .set_index("symbol")
                    .to_dict()
                    .get("name", {})
                )
                merged_map = self._load_name_map()
                merged_map.update({k: v for k, v in name_map.items() if v})
                self._save_name_map(merged_map)
            except Exception as exc:
                cached = self._cached_bars_window(market)
                if not cached.empty:
                    out_file = self.paths.data_raw / f"market={market}" / f"date={end.isoformat()}" / "bars.parquet"
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    cached.to_parquet(out_file, index=False)
                    stats["rows"][market] = int(len(cached))
                    stats["errors"][market] = f"spot failed, fallback to cache: {exc}"
                else:
                    seed_symbols = self._seed_symbols(market)
                    all_hist: list[pd.DataFrame] = []
                    for symbol in seed_symbols:
                        try:
                            hist = self.adapter.fetch_history(symbol, market, start=start, end=end)
                            if not hist.empty:
                                hist["name"] = self._load_name_map().get(symbol, symbol)
                                all_hist.append(hist)
                        except Exception:
                            continue

                    if all_hist:
                        fallback_df = pd.concat(all_hist, ignore_index=True)
                        mode = "seed-symbol history fallback"
                    else:
                        fallback_df = self._synthetic_bars(market, end=end, lookback_days=lookback_days)
                        mode = "synthetic fallback"

                    out_file = self.paths.data_raw / f"market={market}" / f"date={end.isoformat()}" / "bars.parquet"
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    fallback_df.to_parquet(out_file, index=False)
                    stats["rows"][market] = int(len(fallback_df))
                    stats["errors"][market] = f"spot failed, no cache; used {mode}: {exc}"
                continue

            if max_symbols_per_market:
                symbols = symbols[:max_symbols_per_market]

            all_hist: list[pd.DataFrame] = []
            for symbol in symbols:
                try:
                    hist = self.adapter.fetch_history(symbol, market, start=start, end=end)
                    if not hist.empty:
                        merged_map = self._load_name_map()
                        hist["name"] = name_map.get(symbol) or merged_map.get(symbol, "")
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
