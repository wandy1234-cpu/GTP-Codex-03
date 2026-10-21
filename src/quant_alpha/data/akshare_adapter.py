"""AkShare adapter for A-share and H-share market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import time
from typing import Literal

import akshare as ak
import pandas as pd

from quant_alpha.config import AkshareConfig
from quant_alpha.data.normalize import A_SPOT_RENAME, H_SPOT_RENAME, normalize_history, normalize_spot

Market = Literal["A", "HK"]


@dataclass
class AkshareAdapter:
    config: AkshareConfig
    max_retries: int = 3
    retry_sleep: float = 1.5

    def __post_init__(self) -> None:
        if self.config.token:
            _ = self.config.token

    @classmethod
    def from_env(cls) -> "AkshareAdapter":
        return cls(config=AkshareConfig.from_env())

    def fetch_spot(self, market: Market) -> pd.DataFrame:
        if market == "A":
            last_exc: Exception | None = None
            for fn in [getattr(ak, "stock_zh_a_spot_em", None), getattr(ak, "stock_zh_a_spot", None)]:
                if fn is None:
                    continue
                try:
                    raw = self._with_retry(fn)
                    return normalize_spot(raw, A_SPOT_RENAME, market="A")
                except Exception as exc:
                    last_exc = exc
                    continue
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("No available A-share spot endpoint in akshare runtime")
        if market == "HK":
            last_exc: Exception | None = None
            for fn in [getattr(ak, "stock_hk_spot_em", None), getattr(ak, "stock_hk_spot", None)]:
                if fn is None:
                    continue
                try:
                    raw = self._with_retry(fn)
                    return normalize_spot(raw, H_SPOT_RENAME, market="HK")
                except Exception as exc:
                    last_exc = exc
                    continue
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("No available HK spot endpoint in akshare runtime")
        raise ValueError(f"Unsupported market: {market}")

    def fetch_history(
        self,
        symbol: str,
        market: Market,
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "qfq",
        period: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> pd.DataFrame:
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        if market == "A":
            raw = self._with_retry(
                ak.stock_zh_a_hist,
                symbol=symbol,
                period=period,
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
            )
            return normalize_history(raw, symbol=symbol, market="A")

        if market == "HK":
            raw = self._with_retry(
                ak.stock_hk_hist,
                symbol=symbol,
                period=period,
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
            )
            return normalize_history(raw, symbol=symbol, market="HK")
        raise ValueError(f"Unsupported market: {market}")

    def fetch_symbol_universe(self, market: Market) -> list[str]:
        if market == "A":
            try:
                df = self._with_retry(ak.stock_info_a_code_name)
                for c in ["code", "代码", "symbol"]:
                    if c in df.columns:
                        return df[c].astype(str).tolist()
            except Exception:
                return []
            return []

        if market == "HK":
            for fn in [getattr(ak, "stock_hk_spot_em", None), getattr(ak, "stock_hk_spot", None)]:
                if fn is None:
                    continue
                try:
                    df = self._with_retry(fn)
                    for c in ["代码", "symbol", "code"]:
                        if c in df.columns:
                            return df[c].astype(str).tolist()
                except Exception:
                    continue
            return []
        return []

    def _with_retry(self, fn, *args, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_sleep * attempt)
        if last_exc is not None:
            raise last_exc
