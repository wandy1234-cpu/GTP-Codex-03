"""AkShare adapter for A-share and H-share market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import akshare as ak
import pandas as pd

from quant_alpha.config import AkshareConfig
from quant_alpha.data.normalize import A_SPOT_RENAME, H_SPOT_RENAME, normalize_history, normalize_spot

Market = Literal["A", "HK"]


@dataclass
class AkshareAdapter:
    """Thin wrapper around AkShare APIs with normalized output schema."""

    config: AkshareConfig

    def __post_init__(self) -> None:
        # AkShare的大多数接口不强制token; 如果未来需要可在此扩展统一鉴权。
        if self.config.token:
            # 保留token字段，避免误报“未使用”。
            _ = self.config.token

    @classmethod
    def from_env(cls) -> "AkshareAdapter":
        return cls(config=AkshareConfig.from_env())

    def fetch_spot(self, market: Market) -> pd.DataFrame:
        """Fetch latest spot quotes.

        Args:
            market: "A" for mainland A-share, "HK" for Hong Kong stocks.
        """
        if market == "A":
            raw = ak.stock_zh_a_spot_em()
            return normalize_spot(raw, A_SPOT_RENAME, market="A")
        if market == "HK":
            raw = ak.stock_hk_spot_em()
            return normalize_spot(raw, H_SPOT_RENAME, market="HK")
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
        """Fetch normalized historical bars."""
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        if market == "A":
            raw = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
            )
            return normalize_history(raw, symbol=symbol, market="A")

        if market == "HK":
            raw = ak.stock_hk_hist(
                symbol=symbol,
                period=period,
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
            )
            return normalize_history(raw, symbol=symbol, market="HK")

        raise ValueError(f"Unsupported market: {market}")
