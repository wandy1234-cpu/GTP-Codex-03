"""DuckDB helpers."""

from __future__ import annotations

from pathlib import Path
from glob import glob
from datetime import date

import duckdb
import pandas as pd


def load_latest_raw(data_root: Path) -> pd.DataFrame:
    master_pattern = (data_root / "market=*" / "master_bars.parquet").as_posix()
    master_files = glob(master_pattern)
    if master_files:
        frames = []
        for file in master_files:
            part = pd.read_parquet(file)
            market = Path(file).parent.name.split("=", 1)[-1]
            if "market" not in part.columns:
                part["market"] = market
            else:
                part["market"] = part["market"].fillna(market)
            if "date" in part.columns:
                part["date"] = pd.to_datetime(part["date"], errors="coerce")
                if not part.empty and part["date"].notna().sum() == 0:
                    part["date"] = pd.Timestamp(date.today())
            frames.append(part)
        dated_pattern = (data_root / "market=*" / "date=*" / "bars.parquet").as_posix()
        for file in glob(dated_pattern):
            part = pd.read_parquet(file)
            market = Path(file).parents[1].name.split("=", 1)[-1]
            if "market" not in part.columns:
                part["market"] = market
            else:
                part["market"] = part["market"].fillna(market)
            if "date" in part.columns:
                part["date"] = pd.to_datetime(part["date"], errors="coerce")
                part = part[part["date"].notna()]
            if not part.empty:
                frames.append(part)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        keys = [c for c in ["market", "symbol", "date"] if c in out.columns]
        return out.drop_duplicates(subset=keys, keep="last").reset_index(drop=True) if keys else out

    pattern = (data_root / "market=*" / "date=*" / "bars.parquet").as_posix()
    matched = glob(pattern)
    if not matched:
        return pd.DataFrame()
    sql = f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
    with duckdb.connect() as con:
        return con.execute(sql).df()


def save_feature_snapshot(feature_df: pd.DataFrame, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    feature_df.to_parquet(out_file, index=False)
