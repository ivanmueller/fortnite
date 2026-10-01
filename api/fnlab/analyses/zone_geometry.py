from __future__ import annotations

from ..result import Result
from ..store import df
from . import Context, Param, register

METRICS = {
    "offset_ratio": ("Distance moved ÷ current radius", "How far the circle travels relative to its size"),
    "shrink_ratio": ("Next radius ÷ current radius", "How much the circle shrinks"),
    "u": ("u (0 = center, 1 = edge)", "Where in the allowed area the next center lands"),
}


@register("zone_geometry", "Storm pull geometry",
          "How far, how much and where each phase's circle moves, phase by phase, plus a map of next-circle centers.",
          params=[Param("metric", "Measure", "select", "offset_ratio",
                        [{"value": k, "label": v[0]} for k, v in METRICS.items()])])
def run(ctx: Context) -> Result:
    metric = ctx.params.get("metric", "offset_ratio")
    label, desc = METRICS.get(metric, METRICS["offset_ratio"])
    r = Result()
    z = df(ctx.con, "SELECT z.* FROM zone_offsets z JOIN sel USING (match_id)")
    if z.empty:
        r.headline = "No storm pulls in this selection."
        return r
    z["u"] = z["u"].clip(0, 1)
    phases = sorted(z["phase"].unique())
    r.chart("box", f"{label} by phase",
            [dict(name=f"Phase {int(p)}", values=z.loc[z.phase == p, metric].tolist()) for p in phases],
            y_label=label)
    sample = z.sample(min(len(z), 4000), random_state=0)
    r.chart("map_points", "Where next circles land",
            [dict(name=f"Phase {int(p)}", x=sample.loc[sample.phase == p, "next_x"].tolist(),
                  y=sample.loc[sample.phase == p, "next_y"].tolist()) for p in phases],
            x_label="X (Unreal units)", y_label="Y (Unreal units)")
    tbl = (z.groupby("phase").agg(pulls=("match_id", "size"), radius=("cur_r", "median"),
                                  next_radius=("next_r", "median"), moved=("dist", "median"),
                                  offset_ratio=("offset_ratio", "median"), shrink_ratio=("shrink_ratio", "median"),
                                  mean_u=("u", "mean"))
           .round(3).reset_index().rename(columns={"phase": "Phase", "pulls": "Pulls", "radius": "Median radius",
                                                   "next_radius": "Median next radius", "moved": "Median distance moved",
                                                   "offset_ratio": "Median moved ÷ radius",
                                                   "shrink_ratio": "Median shrink", "mean_u": "Mean u"}))
    r.table("Medians by phase", tbl)
    med = z.groupby("phase")[metric].median()
    r.headline = f"{desc}: median {med.min():.2f} to {med.max():.2f} across phases."
    r.metric("Storm pulls", f"{len(z):,}")
    r.metric("Phases", len(phases))
    r.metric(f"Overall median", f"{z[metric].median():.2f}", label)
    r.notes.append("Distances are in Unreal units (100 = 1 metre). Ratios are comparable across seasons; raw map "
                   "positions are only comparable within a season.")
    return r
