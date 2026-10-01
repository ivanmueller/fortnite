"""
FastAPI app. Run from the repo root with `npm run dev` (API + dashboard together),
or alone with:  python -m uvicorn fnlab.main:app --reload --app-dir api
"""
from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import store
from .analyses import REGISTRY
from .analyses import Context
from .config import DATASET_LABELS, DATASETS
from .filters import Filters
from .result import clean

BOOT_ID = uuid.uuid4().hex[:8]  # changes on every reload; the dashboard watches it to refresh


class _HideHealthChecks(logging.Filter):
    """The dashboard polls /api/health every 2 s to spot code reloads; keep those out of the terminal."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HideHealthChecks())

app = FastAPI(title="Fortnite zone lab API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    datasets = {}
    for key in DATASETS:
        info = dict(label=DATASET_LABELS[key], available=store.available(key), matches=0, path=str(DATASETS[key]))
        if info["available"]:
            with store.connect(key) as con:
                info["matches"] = con.execute("SELECT count(*) FROM matches").fetchone()[0]
        datasets[key] = info
    return dict(ok=True, boot_id=BOOT_ID, datasets=datasets)


@app.get("/api/facets")
def facets(dataset: str = "demo"):
    """Values available for filtering: seasons, regions, event windows, date range."""
    _require(dataset)
    with store.connect(dataset) as con:
        def distinct(col):
            rows = con.execute(f"SELECT {col}, count(*) n FROM matches WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
            return [dict(value=str(v), count=n) for v, n in rows]
        lo, hi = con.execute("SELECT min(TRY_CAST(match_date AS DATE)), max(TRY_CAST(match_date AS DATE)) FROM matches").fetchone()
        return clean(dict(seasons=distinct("season"), regions=distinct("region"),
                          event_windows=distinct("event_window_id"), playlists=distinct("playlist"),
                          date_min=lo, date_max=hi))


class MatchQuery(BaseModel):
    filters: Filters = Field(default_factory=Filters)
    limit: int = 200


@app.post("/api/matches")
def matches(q: MatchQuery):
    _require(q.filters.dataset)
    with store.connect(q.filters.dataset) as con:
        n = store.select(con, q.filters)
        rows = store.df(con, f"""
            SELECT m.match_id, m.match_date, m.season, m.region, m.event_window_id, m.playlist,
                   m.n_humans AS players, m.n_teams AS teams, m.is_server_replay, round(m.length_s / 60, 1) AS minutes
            FROM matches m JOIN sel USING (match_id)
            ORDER BY TRY_CAST(m.match_date AS DATE) DESC NULLS LAST, m.match_id LIMIT {int(q.limit)}
        """)
    return clean(dict(total=n, columns=list(rows.columns), rows=rows.astype(object).where(rows.notna(), None).values.tolist()))


@app.get("/api/analyses")
def analyses():
    return [dict(id=a.id, title=a.title, summary=a.summary, needs_compare=a.needs_compare,
                 params=[asdict(p) for p in a.params]) for a in REGISTRY.values()]


class RunRequest(BaseModel):
    filters: Filters = Field(default_factory=Filters)
    compare: Filters | None = None
    params: dict = Field(default_factory=dict)


@app.post("/api/analyses/{analysis_id}/run")
def run_analysis(analysis_id: str, req: RunRequest):
    a = REGISTRY.get(analysis_id)
    if not a:
        raise HTTPException(404, f"Unknown analysis '{analysis_id}'")
    _require(req.filters.dataset)
    params = {p.name: p.default for p in a.params} | (req.params or {})
    t0 = time.perf_counter()
    with store.connect(req.filters.dataset) as con:
        n = store.select(con, req.filters, "sel")
        n_b = 0
        compare = None
        if a.needs_compare:
            compare = req.compare or Filters(dataset=req.filters.dataset)
            compare = compare.model_copy(update={"dataset": req.filters.dataset})
            n_b = store.select(con, compare, "sel_b")
        if n < a.min_matches or (a.needs_compare and n_b < a.min_matches):
            return dict(status="empty", message=f"This analysis needs at least {a.min_matches} matches in each selection. "
                        f"Selection A has {n}" + (f", B has {n_b}." if a.needs_compare else "."))
        try:
            result = a.run(Context(con=con, filters=req.filters, n_matches=n, params=params,
                                   compare=compare, n_compare=n_b))
        except Exception as e:  # surface analysis bugs in the dashboard instead of a bare 500
            traceback.print_exc()
            raise HTTPException(500, f"{type(e).__name__}: {e}")
    out = result.to_dict()
    out.update(status="ok", analysis=a.id, n_matches=n, n_compare=n_b, params=clean(params),
               elapsed_ms=round((time.perf_counter() - t0) * 1000))
    return out


def _require(dataset: str):
    if dataset not in DATASETS:
        raise HTTPException(400, f"Unknown dataset '{dataset}'")
    if not store.available(dataset):
        hint = {"demo": "npm run demo-data", "local": "pipeline/run.ps1 local"}.get(dataset, "the pipeline (pipeline/README.md)")
        raise HTTPException(404, f"No tables for '{dataset}' yet in {store.tables_dir(dataset)}. Create them with {hint}.")
