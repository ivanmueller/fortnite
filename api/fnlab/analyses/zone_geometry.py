from __future__ import annotations

import numpy as np

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register

METRICS = {
    "offset_ratio": ("Distance moved ÷ current radius", "How far the circle travels relative to its size"),
    "shrink_ratio": ("Next radius ÷ current radius", "How much the circle shrinks"),
    "u": ("u (0 = center, 1 = edge)", "Where in the allowed area the next center lands"),
}


@register("zone_geometry", "Storm pull geometry",
          "How far, how much and where each phase's circle moves, phase by zone, plus a map of next-circle centers.",
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
    r.chart("box", "Distribution by zone",
            [dict(name=f"Zone {int(p)}", values=z.loc[z.phase == p, metric].tolist()) for p in phases],
            y_label=label)
    sample = z.sample(min(len(z), 4000), random_state=0)
    r.chart("map_points", "Where next circles land",
            [dict(name=f"Zone {int(p)}", x=sample.loc[sample.phase == p, "next_x"].tolist(),
                  y=sample.loc[sample.phase == p, "next_y"].tolist()) for p in phases],
            x_label="X (Unreal units)", y_label="Y (Unreal units)")
    has_kind = "kind" in z.columns
    by = z.groupby("phase")
    spread = (by["dist"].std() / by["dist"].mean()).fillna(0)
    tbl = (z.groupby("phase").agg(pulls=("match_id", "size"), radius=("cur_r", "median"),
                                  next_radius=("next_r", "median"), moved=("dist", "median"),
                                  offset_ratio=("offset_ratio", "median"), shrink_ratio=("shrink_ratio", "median"),
                                  mean_u=("u", "mean"))
           .round(3).reset_index().rename(columns={"phase": "Zone", "pulls": "Pulls", "radius": "Median radius",
                                                   "next_radius": "Median next radius", "moved": "Median distance moved",
                                                   "offset_ratio": "Median moved ÷ radius",
                                                   "shrink_ratio": "Median shrink", "mean_u": "Mean u"}))
    if "zone_type" in z.columns:
        # Most common type for each zone, and how consistently that zone has it across matches.
        mode = z.groupby("phase")["zone_type"].agg(lambda t: t.mode().iat[0] if t.notna().any() else "–")
        share = z.groupby("phase")["zone_type"].agg(lambda t: (t == t.mode().iat[0]).mean() if t.notna().any() else np.nan)
        tbl.insert(1, "Type", mode.reindex(tbl["Zone"]).to_numpy())
        tbl.insert(2, "Type in % of matches", [f"{v:.0%}" if v == v else "–" for v in share.reindex(tbl["Zone"])])
        tbl.insert(3, "Next zone inside current", [f"{v:.0%}" for v in z.groupby("phase")["overlap"].median().reindex(tbl["Zone"])])
    else:
        tbl.insert(1, "Type", [("moving" if (z.loc[z.phase == p, "kind"] == "moving").mean() > 0.5 else "shrinking")
                               if has_kind else "–" for p in tbl["Zone"]])
    if "wait_s" in z.columns:
        tbl.insert(4 if "zone_type" in z.columns else 2, "Wait before (s)", by["wait_s"].median().round(0).reindex(tbl["Zone"]).to_numpy())
    tbl["Distance spread"] = [f"{spread.get(p, 0):.1%}" for p in tbl["Zone"]]
    tbl["Distance fixed?"] = ["yes" if (spread.get(p, 1) < 0.02 and by.size().get(p, 0) >= 5) else "no"
                              for p in tbl["Zone"]]
    r.table("Zone by zone", tbl)
    fixed = [int(p) for p in tbl["Zone"] if tbl.loc[tbl["Zone"] == p, "Distance fixed?"].iat[0] == "yes"]
    if fixed:
        r.notes.insert(0, f"Zones {', '.join(map(str, fixed))} move the same distance in every match (spread under 2%). "
                          "The game sets how far those circles move; only the direction can vary, so test direction, not distance.")
    med = z.groupby("phase")[metric].median()
    r.headline = f"{desc}: median {med.min():.2f} to {med.max():.2f} across zones."
    r.metric("Storm pulls", f"{len(z):,}")
    r.metric("Zones", len(phases))
    r.metric(f"Overall median", f"{z[metric].median():.2f}", label)
    iqr = z.groupby("phase")[metric].quantile(0.75) - z.groupby("phase")[metric].quantile(0.25)
    steady = int(iqr.idxmin()) if len(iqr) else None
    conclude(r, ctx, primary=[], alpha=0.005, recommended=100,
             descriptive=(f"Median {label.lower()} runs from {med.min():.2f} (phase {int(med.idxmin())}) to "
                          f"{med.max():.2f} (zone {int(med.idxmax())}). Zone {steady} is the most consistent from "
                          "match to match." if steady is not None else "Not enough pulls to describe."),
             next_none=["Switch the measure to see distance, shrink and edge position in turn.",
                        "Test whether these pulls are random on Is the storm random?"])
    r.notes.append("Distances are in Unreal units (100 = 1 metre). Ratios are comparable across seasons; raw map "
                   "positions are only comparable within a season.")
    return r
