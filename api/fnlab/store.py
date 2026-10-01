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

TABLES = ["matches", "players", "teams", "zones", "zone_offsets", "bus", "positions", "kills", "eliminations"]


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


def select(con, f: Filters, name: str = "sel") -> int:
    """Create temp table <name>(match_id) for the filtered matches. Returns how many."""
    where, params = f.where()
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT match_id FROM matches WHERE {where}", params)
    return con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]


def df(con, sql: str, params: list | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).df()
