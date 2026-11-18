"""DuckDB helpers."""

from __future__ import annotations

from pathlib import Path
from glob import glob

import duckdb
import pandas as pd


def load_latest_raw(data_root: Path) -> pd.DataFrame:
    master_pattern = (data_root / "market=*" / "master_bars.parquet").as_posix()
    if glob(master_pattern):
        sql = f"SELECT * FROM read_parquet('{master_pattern}', union_by_name=true)"
        with duckdb.connect() as con:
            return con.execute(sql).df()

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
