"""
scoring.py - the one place points come from.

Every page that turns a placement into points (team audit, game review, expected points, the decision engine)
asks this module. The tables live in scoring.json next to this file, so a rule change is an edit there, not a
code change. The active scheme is, in order: FN_SCORING in the environment, SCORING in the project's .env,
then "active" in scoring.json.

The file is re-read when it changes, so editing it takes effect on the next query. Caches that depend on
points (the expected-points model, the engine) include Scheme.key, so they rebuild when the table changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import _env_file

TABLES = Path(__file__).with_name("scoring.json")
DEFAULT = "fncs_2026_duos_finals"


@dataclass(frozen=True)
class Scheme:
    id: str
    label: str
    placement: tuple[tuple[int, float], ...]   # (place, points), place 1 first; places not listed score 0
    elimination: float
    source: str

    @property
    def table(self) -> dict[int, float]:
        return dict(self.placement)

    @property
    def paid_places(self) -> int:
        """The last place that still scores placement points (25 in FNCS 2026 Duos)."""
        return max((p for p, v in self.placement if v > 0), default=0)

    @property
    def key(self) -> tuple:
        """Identifies the exact table, for caches."""
        return (self.id, self.placement, self.elimination)

    def points(self, place) -> float:
        """Placement points for one place (0 for unknown or unpaid places)."""
        try:
            return float(self.table.get(int(place), 0.0))
        except (TypeError, ValueError):
            return 0.0

    def placement_points(self, places) -> pd.Series:
        """Placement points for a column of places."""
        s = pd.Series(places)
        return pd.to_numeric(s, errors="coerce").map(self.table).fillna(0.0).astype(float)

    def total(self, places, eliminations) -> pd.Series:
        """Placement points plus elimination points, for columns of places and eliminations."""
        e = pd.to_numeric(pd.Series(eliminations), errors="coerce").fillna(0.0).to_numpy(float)
        return self.placement_points(places) + self.elimination * e

    def describe(self) -> str:
        t = self.table
        top = ", ".join(f"{t[p]:.0f}" for p in sorted(t)[:4])
        last = self.paid_places
        return (f"{self.label}: {top}… for 1st to 4th, down to {t.get(last, 0):.0f} for {_ordinal(last)}, 0 below; "
                f"{self.elimination:g} per elimination.")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def validate(placement: dict[int, float]) -> list[str]:
    """Problems with a placement table: gaps, points rising with a worse place, negative points."""
    problems = []
    places = sorted(placement)
    if not places or places[0] != 1:
        problems.append("the table must start at 1st place")
    if places and places != list(range(1, places[-1] + 1)):
        problems.append("places must be consecutive (no gaps)")
    pts = [placement[p] for p in places]
    if any(b > a for a, b in zip(pts, pts[1:])):
        problems.append("points must never rise for a worse place")
    if any(v < 0 for v in pts):
        problems.append("points can't be negative")
    return problems


_CACHE: dict = {}


def _read() -> dict:
    mtime = TABLES.stat().st_mtime if TABLES.exists() else None
    if _CACHE.get("mtime") != mtime:
        _CACHE["data"] = json.loads(TABLES.read_text(encoding="utf-8")) if mtime is not None else {}
        _CACHE["mtime"] = mtime
    return _CACHE["data"]


def schemes() -> dict[str, Scheme]:
    out = {}
    for sid, s in _read().get("schemes", {}).items():
        table = {int(k): float(v) for k, v in s.get("placement", {}).items()}
        problems = validate(table)
        if problems:
            raise ValueError(f"scoring.json, scheme '{sid}': " + "; ".join(problems))
        out[sid] = Scheme(id=sid, label=s.get("label", sid), placement=tuple(sorted(table.items())),
                          elimination=float(s.get("elimination", 0)), source=s.get("source", ""))
    return out


def active_id() -> str:
    return os.environ.get("FN_SCORING") or _env_file("SCORING") or _read().get("active") or DEFAULT


def get(scheme_id: str | None = None) -> Scheme:
    all_ = schemes()
    sid = scheme_id or active_id()
    if sid not in all_:
        raise ValueError(f"Unknown scoring scheme '{sid}'. Choose one of: {', '.join(all_)} (scoring.json).")
    return all_[sid]


def active() -> Scheme:
    return get()


def chance_in_points(placements, scheme: Scheme | None = None) -> float:
    """Share of placements that score placement points."""
    s = scheme or active()
    p = pd.to_numeric(pd.Series(placements), errors="coerce").dropna()
    return float((p <= s.paid_places).mean()) if len(p) else np.nan
