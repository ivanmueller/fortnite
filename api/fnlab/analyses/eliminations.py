from __future__ import annotations

import numpy as np

from ..result import Result
from ..store import df, has_table
from . import Context, Param, register


@register("eliminations", "Where eliminations happen",
          "Each elimination's distance from the circle the storm was closing to at that moment, by phase, plus a map.",
          params=[Param("include_downs", "Include knocks", "boolean", False)])
def run(ctx: Context) -> Result:
    r = Result()
    if not has_table(ctx.con, "kills"):
        r.headline = "No kill feed in this dataset."
        return r
    downs = "" if ctx.params.get("include_downs") else "AND NOT coalesce(k.downed, FALSE)"
    k = df(ctx.con, f"""
        WITH k AS (
            SELECT k.match_id, k.t, k.x, k.y FROM kills k JOIN sel USING (match_id)
            WHERE k.t IS NOT NULL AND k.t > 0 AND k.x IS NOT NULL {downs}
        ),
        z AS (SELECT z.* FROM zones z JOIN sel USING (match_id) WHERE z.start_shrink_t IS NOT NULL)
        SELECT k.*, z.phase, sqrt(power(k.x - z.next_x, 2) + power(k.y - z.next_y, 2)) / z.next_r AS d
        FROM k ASOF JOIN z ON k.match_id = z.match_id AND k.t >= z.start_shrink_t
    """)
    if k.empty:
        r.headline = "No eliminations with locations after the first storm phase."
        return r
    outside = (k["d"] > 1).mean()
    r.headline = f"{outside:.0%} of {len(k):,} eliminations happened outside the circle the storm was closing to."
    r.metric("Eliminations", f"{len(k):,}")
    r.metric("Outside closing circle", f"{outside:.0%}")
    r.metric("Median distance", f"{k['d'].median():.2f}", "In closing-circle radii; ≤ 1 is inside")
    ph = k.groupby("phase").agg(n=("d", "size"), out=("d", lambda s: (s > 1).mean()))
    r.chart("bar", "Share of eliminations outside the closing circle, by phase",
            [dict(name="% outside", x=[f"Phase {int(p)}" for p in ph.index], y=(ph["out"] * 100).round(1).tolist())],
            y_label="% outside")
    r.chart("histogram", "Distance from the closing circle's center",
            [dict(name="Eliminations", values=np.clip(k["d"], 0, 4).tolist())], bins=40, range=[0, 4],
            x_label="Next-circle radii (values above 4 shown at 4)", y_label="Eliminations",
            reference_lines=[dict(axis="x", value=1, label="Circle edge")])
    s = k.sample(min(len(k), 5000), random_state=0)
    r.chart("map_points", "Elimination locations",
            [dict(name=f"Phase {int(p)}", x=s.loc[s.phase == p, "x"].tolist(), y=s.loc[s.phase == p, "y"].tolist())
             for p in sorted(s["phase"].unique())], x_label="X", y_label="Y")
    r.notes.append("Eliminations before the first storm starts closing are excluded, because there's no circle to measure against yet.")
    return r
