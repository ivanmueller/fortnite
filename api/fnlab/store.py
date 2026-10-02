"""
DuckDB over the pipeline's Parquet tables.

A fresh in-memory connection is opened per request with one view per table, so
re-running the pipeline is picked up immediately: no import step, no cache to bust.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

from .config import DATASETS
from .filters import Filters

TABLES = ["matches", "players", "teams", "zones", "zone_offsets", "bus", "positions", "kills", "eliminations",
          "landings", "pois", "health", "damage", "chests", "pickups", "weapons_held", "builds"]


def tables_dir(dataset: str) -> Path:
    return DATASETS[dataset] / "tables"


def available(dataset: str) -> bool:
    return (tables_dir(dataset) / "matches.parquet").exists()


@contextmanager
def connect(dataset: str):
    con = duckdb.connect()
    try:
        d = tables_dir(dataset)
        for name in TABLES:
            p = d / f"{name}.parquet"
            if not p.exists():
                continue
            try:
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{p.as_posix()}')")
            except duckdb.Error:
                pass  # empty table with no columns, e.g. eliminations in synthetic data
        yield con
    finally:
        con.close()


def has_table(con, name: str) -> bool:
    return con.execute("SELECT count(*) FROM duckdb_views() WHERE view_name = ?", [name]).fetchone()[0] > 0


def columns(con, table: str) -> set[str]:
    return set(con.execute(f"SELECT * FROM {table} LIMIT 0").df().columns)


def select(con, f: Filters, name: str = "sel") -> int:
    """Create temp table <name>(match_id) for the filtered matches. Returns how many."""
    where, params = f.where(columns(con, "matches"))
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT match_id FROM matches WHERE {where}", params)
    return con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]


def df(con, sql: str, params: list | None = None) -> pd.DataFrame:
    return plain(con.execute(sql, params or []).df())


def plain(out: pd.DataFrame) -> pd.DataFrame:
    """Standard numpy types for every query result. DuckDB returns whole-number and true/false columns with gaps as
    pandas' nullable types, whose missing value (pd.NA) can't be used in yes/no checks; analyses expect NaN / None."""
    for c in out.columns:
        dt = out[c].dtype
        if not isinstance(dt, pd.api.extensions.ExtensionDtype):
            continue
        s = out[c]
        if pd.api.types.is_bool_dtype(dt):
            out[c] = s.astype(bool) if not s.isna().any() else s.astype(object).where(s.notna(), None)
        elif pd.api.types.is_integer_dtype(dt):
            out[c] = s.astype("int64") if not s.isna().any() else s.astype("float64")
        elif pd.api.types.is_float_dtype(dt):
            out[c] = s.astype("float64")
        elif pd.api.types.is_string_dtype(dt) and getattr(dt, "na_value", None) is pd.NA:
            out[c] = s.astype(object).where(s.notna(), None)        # only the NA-based text type; the default one uses NaN
    return out
