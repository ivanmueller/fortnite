"""
The result format every analysis returns.

Charts are a neutral spec (kind + series), not Plotly JSON, so the dashboard can
render them with Plotly today and another library later without touching analyses.
Chart kinds: bar, stacked_bar, line, histogram, polar_histogram, box, map_points.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd


def clean(v: Any) -> Any:
    """Make values JSON-safe: NaN/inf -> None, numpy -> Python, dates -> ISO strings."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, np.ndarray):
        return [clean(x) for x in v.tolist()]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
        return v.isoformat()
    if v is pd.NA or v is pd.NaT:
        return None
    return v


@dataclass
class Result:
    headline: str = ""
    metrics: list[dict] = field(default_factory=list)   # {label, value, detail?}
    tests: list[dict] = field(default_factory=list)     # {group, name, n, value, p, significant, reading}
    charts: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)    # {title, columns, rows}
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def metric(self, label: str, value, detail: str | None = None) -> None:
        self.metrics.append(dict(label=label, value=value, detail=detail))

    def test(self, group: str, name: str, n: int, value: str, p: float, alpha: float, reading: str = "") -> None:
        sig = p is not None and np.isfinite(p) and p < alpha
        self.tests.append(dict(group=group, name=name, n=int(n), value=value, p=p, significant=bool(sig), reading=reading))

    def chart(self, kind: str, title: str, series: list[dict], **options) -> None:
        self.charts.append(dict(kind=kind, title=title, series=series, options=options))

    def table(self, title: str, frame: pd.DataFrame) -> None:
        self.tables.append(dict(title=title, columns=list(map(str, frame.columns)),
                                rows=frame.astype(object).where(frame.notna(), None).values.tolist()))

    def to_dict(self) -> dict:
        return clean(asdict(self))
