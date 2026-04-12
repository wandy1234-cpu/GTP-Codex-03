"""AkShare adapter for A-share and H-share market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import time
from typing import Literal
import urllib.request

import akshare as ak
import pandas as pd
import requests

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

    def fetch_symbol_name_map(self, market: Market) -> dict[str, str]:
        """Best-effort symbol->name mapping for A/HK."""
        mapping: dict[str, str] = {}
        def _has_cjk(text: str) -> bool:
            return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))

        def _set_pref(key: str, name: str) -> None:
            old = str(mapping.get(key, "")).strip()
            new = str(name).strip()
            if not new:
                return
            if not old:
                mapping[key] = new
                return
            if _has_cjk(new) and not _has_cjk(old):
                mapping[key] = new
                return
            if _has_cjk(new) == _has_cjk(old) and len(new) > len(old):
                mapping[key] = new

        def _norm_symbol(s: str) -> str:
            x = str(s).strip()
            x = x.removesuffix(".0")
            digits = "".join(ch for ch in x.lower().replace("hk", "") if ch.isdigit())
            if market == "HK" and digits:
                return digits.zfill(5) if len(digits) <= 5 else digits
            return digits if digits else x

        candidates: list = []
        if market == "A":
            candidates = [getattr(ak, "stock_zh_a_spot_em", None), getattr(ak, "stock_zh_a_spot", None), getattr(ak, "stock_info_a_code_name", None)]
        elif market == "HK":
            candidates = [getattr(ak, "stock_hk_spot_em", None), getattr(ak, "stock_hk_spot", None), getattr(ak, "stock_hk_name_code", None)]
        for fn in candidates:
            if fn is None:
                continue
            try:
                df = self._with_retry(fn)
            except Exception:
                continue
            cols = df.columns.tolist()
            sym_col = next((c for c in ["symbol", "代码", "code", "证券代码", "股票代码"] if c in cols), None)
            name_col = next((c for c in ["name", "名称", "证券简称", "股票简称"] if c in cols), None)
            if sym_col and name_col:
                for s, n in zip(df[sym_col].astype(str), df[name_col].astype(str)):
                    name = str(n).strip()
                    if name:
                        raw = str(s).strip()
                        _set_pref(raw, name)
                        _set_pref(_norm_symbol(raw), name)
        return mapping

    def fetch_hk_name_by_symbol(self, symbol: str) -> str:
        """Best-effort HK single symbol name fetch via eastmoney quote api."""
        code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(5)
        def _has_cjk(text: str) -> bool:
            return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))

        best = ""
        # try akshare code-name mapping first (usually faster and more stable than quote api)
        fn = getattr(ak, "stock_hk_name_code", None)
        if fn is not None:
            try:
                df = self._with_retry(fn)
                cols = df.columns.tolist()
                sym_col = next((c for c in ["symbol", "代码", "code", "证券代码", "股票代码"] if c in cols), None)
                name_col = next((c for c in ["name", "名称", "证券简称", "股票简称"] if c in cols), None)
                if sym_col and name_col:
                    s = (
                        df[sym_col]
                        .astype(str)
                        .str.strip()
                        .str.replace(r"\.0+$", "", regex=True)
                        .str.replace(r"[^0-9]", "", regex=True)
                        .str.zfill(5)
                    )
                    m = s == code
                    if m.any():
                        name = str(df.loc[m, name_col].iloc[0]).strip()
                        if name and _has_cjk(name):
                            return name
                        if name and not best:
                            best = name
            except Exception:
                pass
        secid = f"116.{code}"
        params = {"secid": secid, "fields": "f57,f58"}
        for url in ["https://push2delay.eastmoney.com/api/qt/stock/get", "https://push2.eastmoney.com/api/qt/stock/get"]:
            try:
                resp = requests.get(url, params=params, timeout=8)
                resp.raise_for_status()
                data = resp.json()
                name = ((data or {}).get("data") or {}).get("f58")
                if name:
                    name_s = str(name).strip()
                    if _has_cjk(name_s):
                        return name_s
                    if not best:
                        best = name_s
            except Exception:
                continue
        # fallback: sina HK quote endpoint
        try:
            url2 = f"https://hq.sinajs.cn/list=rt_hk{code}"
            req = urllib.request.Request(url2, headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                txt = resp.read().decode("gbk", errors="ignore")
            # example: var hq_str_rt_hk00700="腾讯控股,...."
            if '"' in txt:
                payload = txt.split('"', 1)[1].rsplit('"', 1)[0]
                first = payload.split(",", 1)[0].strip()
                if first and first != code and _has_cjk(first):
                    return first
                if first and first != code and not best:
                    best = first
        except Exception:
            pass
        return best

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
