"""Daily ingestion pipeline for A/H market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from glob import glob
from pathlib import Path
from typing import Callable

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

    def _master_file(self, market: str) -> Path:
        return self.paths.data_raw / f"market={market}" / "master_bars.parquet"

    def _load_master(self, market: str) -> pd.DataFrame:
        fp = self._master_file(market)
        if not fp.exists():
            return pd.DataFrame()
        out = pd.read_parquet(fp)
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
        return self._dedupe_bars(out)

    def _save_master(self, market: str, df: pd.DataFrame) -> None:
        fp = self._master_file(market)
        fp.parent.mkdir(parents=True, exist_ok=True)
        self._dedupe_bars(df).to_parquet(fp, index=False)

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
        out = self._dedupe_bars(pd.concat(frames, ignore_index=True))
        name_map = self._load_name_map()
        if "name" not in out.columns:
            out["name"] = out["symbol"].astype(str).map(name_map).fillna("")
        else:
            missing = out["name"].isna() | (out["name"].astype(str).str.strip() == "")
            out.loc[missing, "name"] = out.loc[missing, "symbol"].astype(str).map(name_map).fillna("")
        return out

    def _spot_to_daily_bars(self, spot: pd.DataFrame, market: str, trade_date: date) -> pd.DataFrame:
        if spot.empty:
            return pd.DataFrame()
        out = pd.DataFrame()
        out["date"] = pd.to_datetime(trade_date)
        out["market"] = market
        out["symbol"] = spot["symbol"].astype(str) if "symbol" in spot.columns else ""
        out["name"] = spot["name"].astype(str) if "name" in spot.columns else ""
        out["open"] = pd.to_numeric(spot.get("open"), errors="coerce")
        out["high"] = pd.to_numeric(spot.get("high"), errors="coerce")
        out["low"] = pd.to_numeric(spot.get("low"), errors="coerce")
        out["close"] = pd.to_numeric(spot.get("close"), errors="coerce")
        out["volume"] = pd.to_numeric(spot.get("volume"), errors="coerce")
        out["amount"] = pd.to_numeric(spot.get("amount"), errors="coerce")
        return self._dedupe_bars(out)

    def _expand_with_universe(self, market: str, base: pd.DataFrame, start: date, end: date, max_symbols: int = 800) -> pd.DataFrame:
        universe = self.adapter.fetch_symbol_universe(market)
        if not universe:
            return base
        existing = set(base["symbol"].astype(str).unique()) if not base.empty and "symbol" in base.columns else set()
        targets = [s for s in universe if s not in existing][:max_symbols]
        frames: list[pd.DataFrame] = [base] if not base.empty else []
        name_map = self._load_name_map()
        for sym in targets:
            try:
                h = self.adapter.fetch_history(sym, market, start=start, end=end)
                if h.empty:
                    continue
                h["name"] = name_map.get(sym, "")
                frames.append(h)
            except Exception:
                continue
        if not frames:
            return base
        return self._dedupe_bars(pd.concat(frames, ignore_index=True))

    def run(
        self,
        end: date | None = None,
        lookback_days: int = 365,
        max_symbols_per_market: int | None = None,
        history_backfill_batch: int = 200,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> dict[str, object]:
        def _emit(pct: float, msg: str) -> None:
            if progress_cb is not None:
                progress_cb(max(0.0, min(1.0, float(pct))), msg)

        self.paths.ensure()
        end = end or date.today()
        start = end - timedelta(days=lookback_days)

        stats: dict[str, object] = {"rows": {}, "errors": {}, "coverage": {}, "notes": {}}
        markets = ("A", "HK")
        for mi, market in enumerate(markets):
            base = mi / len(markets)
            span = 1.0 / len(markets)
            _emit(base + 0.02 * span, f"[{market}] 初始化增量上下文")
            name_cache = self._load_name_map()
            master = self._load_master(market)
            last_dt = pd.to_datetime(master["date"]).max().date() if (not master.empty and "date" in master.columns) else None
            inc_start = start if last_dt is None else max(start, last_dt - timedelta(days=10))
            stats["notes"][market] = f"incremental_start={inc_start.isoformat()} last_cached_date={last_dt.isoformat() if last_dt else 'none'}"
            try:
                _emit(base + 0.10 * span, f"[{market}] 拉取 spot 列表")
                spot = self.adapter.fetch_spot(market)
                symbols = spot["symbol"].dropna().astype(str).unique().tolist()
                _emit(base + 0.16 * span, f"[{market}] spot 成功，symbol={len(symbols)}")
                name_map = (
                    spot[[c for c in ["symbol", "name"] if c in spot.columns]]
                    .drop_duplicates(subset=["symbol"])
                    .set_index("symbol")
                    .to_dict()
                    .get("name", {})
                )
                merged_map = name_cache
                merged_map.update({k: v for k, v in name_map.items() if v})
                self._save_name_map(merged_map)

                # fast path: use spot as latest-day bar snapshot for all symbols
                spot_daily = self._spot_to_daily_bars(spot, market=market, trade_date=end)
                if not spot_daily.empty:
                    master = pd.concat([master, spot_daily], ignore_index=True) if not master.empty else spot_daily
                    master = self._dedupe_bars(master)
                    _emit(base + 0.20 * span, f"[{market}] 已用 spot 快速更新最新交易日，rows={len(spot_daily)}")
            except Exception as exc:
                _emit(base + 0.18 * span, f"[{market}] spot 失败，尝试缓存回退")
                # 尝试退化到“仅股票代码列表”模式，避免因为 spot 接口异常导致整市场回退缓存
                symbols = self.adapter.fetch_symbol_universe(market)
                if max_symbols_per_market:
                    symbols = symbols[:max_symbols_per_market]
                if symbols:
                    _emit(base + 0.20 * span, f"[{market}] spot 失败，改用代码清单模式 symbol={len(symbols)}")
                    name_map = {}
                    all_hist: list[pd.DataFrame] = []
                    fetched_symbols = 0
                    total_symbols = max(1, len(symbols))
                    for symbol in symbols:
                        try:
                            hist = self.adapter.fetch_history(symbol, market, start=inc_start, end=end)
                            if not hist.empty:
                                hist["name"] = name_cache.get(symbol, "")
                                all_hist.append(hist)
                                fetched_symbols += 1
                        except Exception:
                            continue
                        if fetched_symbols % 50 == 0:
                            inner = fetched_symbols / total_symbols
                            _emit(base + (0.20 + 0.60 * inner) * span, f"[{market}] 代码清单模式进度 {fetched_symbols}/{total_symbols}")

                    if all_hist:
                        inc = pd.concat(all_hist, ignore_index=True)
                        raw = pd.concat([master, inc], ignore_index=True) if not master.empty else inc
                        raw = self._dedupe_bars(raw)
                        latest_dt = pd.to_datetime(raw["date"]).max() if (not raw.empty and "date" in raw.columns) else pd.Timestamp(end)
                        out_file = self.paths.data_raw / f"market={market}" / f"date={latest_dt.date().isoformat()}" / "bars.parquet"
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        raw.to_parquet(out_file, index=False)
                        self._save_master(market, raw)
                        stats["rows"][market] = int(len(raw))
                        spot_set = set(symbols)
                        got_set = set(raw["symbol"].astype(str).unique()) if not raw.empty else set()
                        miss = sorted(list(spot_set - got_set))
                        latest_day = raw[pd.to_datetime(raw["date"]) == latest_dt] if (not raw.empty and "date" in raw.columns) else pd.DataFrame()
                        stats["coverage"][market] = {
                            "spot_symbol_count": int(len(spot_set)),
                            "fetched_symbol_count": int(fetched_symbols),
                            "stored_symbol_count": int(len(got_set)),
                            "latest_trade_date": str(latest_dt.date()),
                            "latest_trade_symbol_count": int(latest_day["symbol"].astype(str).nunique()) if not latest_day.empty else 0,
                            "missing_symbol_count": int(len(miss)),
                            "missing_symbol_sample": miss[:20],
                        }
                        stats["notes"][market] += " | spot_failed_use_symbol_universe=true"
                        _emit(base + 0.95 * span, f"[{market}] 代码清单模式完成，rows={len(raw)}")
                        continue

                cached = self._cached_bars_window(market)
                if not cached.empty:
                    cached = self._expand_with_universe(market, cached, start=start, end=end)
                    out_file = self.paths.data_raw / f"market={market}" / f"date={end.isoformat()}" / "bars.parquet"
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    self._dedupe_bars(cached).to_parquet(out_file, index=False)
                    self._save_master(market, cached)
                    stats["rows"][market] = int(len(cached))
                    stats["errors"][market] = f"spot failed, fallback to cache: {exc!r}"
                    _emit(base + 0.95 * span, f"[{market}] 使用缓存回退完成，rows={len(cached)}")
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
                    fallback_df = self._dedupe_bars(fallback_df)
                    fallback_df.to_parquet(out_file, index=False)
                    self._save_master(market, fallback_df)
                    stats["rows"][market] = int(len(fallback_df))
                    stats["errors"][market] = f"spot failed, no cache; used {mode}: {exc!r}"
                    _emit(base + 0.95 * span, f"[{market}] 使用{mode}完成，rows={len(fallback_df)}")
                continue

            if max_symbols_per_market:
                symbols = symbols[:max_symbols_per_market]

            all_hist: list[pd.DataFrame] = []
            fetched_symbols = 0
            existing = set(master["symbol"].astype(str).unique()) if not master.empty else set()
            missing_symbols = [s for s in symbols if s not in existing]
            # avoid full-universe per-symbol history every run; bounded incremental backfill only
            if master.empty:
                targets = symbols[: min(len(symbols), max(1, history_backfill_batch))]
                _emit(base + 0.22 * span, f"[{market}] 首次建库，分批拉取 history {len(targets)}/{len(symbols)}")
            else:
                targets = missing_symbols[: min(len(missing_symbols), max(1, history_backfill_batch))]
                _emit(base + 0.22 * span, f"[{market}] 增量回补缺失 history {len(targets)}/{len(missing_symbols)}")

            total_symbols = max(1, len(targets))
            for symbol in targets:
                try:
                    hist = self.adapter.fetch_history(symbol, market, start=inc_start, end=end)
                    if not hist.empty:
                        hist["name"] = name_map.get(symbol) or name_cache.get(symbol, "")
                        all_hist.append(hist)
                        fetched_symbols += 1
                except Exception:
                    continue
                if fetched_symbols % 50 == 0:
                    inner = fetched_symbols / total_symbols
                    _emit(base + (0.22 + 0.56 * inner) * span, f"[{market}] history进度 {fetched_symbols}/{total_symbols}")

            if all_hist:
                inc = pd.concat(all_hist, ignore_index=True)
                raw = pd.concat([master, inc], ignore_index=True) if not master.empty else inc
            else:
                raw = master.copy() if not master.empty else pd.DataFrame()

            raw = self._dedupe_bars(raw)
            latest_dt = pd.to_datetime(raw["date"]).max() if (not raw.empty and "date" in raw.columns) else pd.Timestamp(end)
            out_file = self.paths.data_raw / f"market={market}" / f"date={latest_dt.date().isoformat()}" / "bars.parquet"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            raw.to_parquet(out_file, index=False)
            self._save_master(market, raw)
            stats["rows"][market] = int(len(raw))
            latest_day = raw[pd.to_datetime(raw["date"]) == latest_dt] if (not raw.empty and "date" in raw.columns) else pd.DataFrame()
            latest_symbols = latest_day["symbol"].astype(str).nunique() if not latest_day.empty else 0
            spot_set = set(symbols)
            got_set = set(raw["symbol"].astype(str).unique()) if not raw.empty else set()
            miss = sorted(list(spot_set - got_set))
            stats["coverage"][market] = {
                "spot_symbol_count": int(len(spot_set)),
                "fetched_symbol_count": int(fetched_symbols),
                "stored_symbol_count": int(len(got_set)),
                "latest_trade_date": str(latest_dt.date()),
                "latest_trade_symbol_count": int(latest_symbols),
                "missing_symbol_count": int(len(miss)),
                "missing_symbol_sample": miss[:20],
                "backfill_remaining_count": int(max(0, len(miss) - fetched_symbols)),
            }
            _emit(base + 0.98 * span, f"[{market}] 完成，latest={latest_dt.date()} symbols={latest_symbols}")

        return stats
    def _dedupe_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        keys = [c for c in ["market", "symbol", "date"] if c in df.columns]
        if not keys:
            return df
        out = df.copy()
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
        out = out.sort_values(keys).drop_duplicates(subset=keys, keep="last")
        return out.reset_index(drop=True)
