"""
Analysis registry.

To add an analysis: create a module in this folder, decorate a function with
@register(...), and import the module at the bottom of this file. It appears in
the dashboard automatically, with a form built from its params.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..filters import Filters
from ..result import Result


@dataclass
class Param:
    name: str
    label: str
    kind: str                      # "select" | "number" | "boolean"
    default: object = None
    options: list | None = None    # for select: [{"value", "label"}]
    help: str | None = None


@dataclass
class Context:
    con: object                    # duckdb connection with views + temp table `sel`
    filters: Filters
    n_matches: int
    params: dict
    compare: Filters | None = None # for analyses with needs_compare
    n_compare: int = 0             # temp table `sel_b` holds the comparison matches


@dataclass
class Analysis:
    id: str
    title: str
    summary: str
    run: Callable[[Context], Result]
    params: list[Param] = field(default_factory=list)
    needs_compare: bool = False
    min_matches: int = 1


REGISTRY: dict[str, Analysis] = {}


def register(id: str, title: str, summary: str, params: list[Param] | None = None,
             needs_compare: bool = False, min_matches: int = 1):
    def wrap(fn):
        REGISTRY[id] = Analysis(id, title, summary, fn, params or [], needs_compare, min_matches)
        return fn
    return wrap


ALPHA_PARAM = Param("alpha", "Significance threshold", "select", 0.005,
                    [{"value": v, "label": f"p < {v}"} for v in (0.05, 0.01, 0.005, 0.001)],
                    "Many tests run at once, so a strict threshold avoids chasing noise.")

from . import overview, zone_randomness, zone_geometry, compare_periods, positioning, eliminations, height  # noqa: E402,F401
