from __future__ import annotations

import numpy as np
import pandas as pd

from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

BUCKETS = [0, 0.5, 1.0, 1.5, 2.0, 3.0, np.inf]
BUCKET_LABELS = ["< 0.5", "0.5–1", "1–1.5", "1.5–2", "2–3", "3+"]
TIERS = [(1, 5, "Top 5"), (6, 15, "6th–15th"), (16, 999, "16th or lower")]
MIN_BUCKET = 20  # buckets with fewer team snapshots are left off the chart (still in the table)
FRESH_S = 10.0  # a position sample older than this at shrink start means the player is dead or untracked


@register("positioning", "Position vs placement",
          "Where teams stand when each storm starts closing, measured against the circle it closes to, "
          "and how that relates to where they finish.",
          params=[ALPHA_PARAM,
                  Param("phase", "Phase", "select", "all",
                        [{"value": "all", "label": "All phases"}] + [{"value": str(i), "label": f"Phase {i}"} for i in range(1, 11)])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    phase = ctx.params.get("phase", "all")
    r = Result()
    phase_sql = "" if phase == "all" else f"AND z.phase = {int(phase)}"
    d = df(ctx.con, f"""
        WITH z AS (
            SELECT z.match_id, z.phase, z.next_x, z.next_y, z.next_r, z.start_shrink_t
            FROM zones z JOIN sel USING (match_id) WHERE z.start_shrink_t IS NOT NULL {phase_sql}
        ),
        pl AS (
            SELECT p.match_id, p.id, p.team_index FROM players p JOIN sel USING (match_id)
            WHERE NOT coalesce(p.is_bot, FALSE)
        ),
        zp AS (SELECT * FROM z JOIN pl USING (match_id)),
        pos AS (SELECT p.match_id, p.id, p.t, p.x, p.y FROM positions p JOIN sel USING (match_id))
        SELECT zp.match_id, zp.phase, zp.id, zp.team_index, zp.start_shrink_t,
               pos.t, sqrt(power(pos.x - zp.next_x, 2) + power(pos.y - zp.next_y, 2)) / zp.next_r AS d
        FROM zp ASOF JOIN pos
          ON zp.match_id = pos.match_id AND zp.id = pos.id AND zp.start_shrink_t >= pos.t
    """)
    if d.empty:
        r.headline = "No player positions at storm phases in this selection."
        return r
    d = d[(d.start_shrink_t - d.t) <= FRESH_S]
    team = (d.groupby(["match_id", "phase", "team_index"]).agg(d=("d", "mean"), alive=("id", "size")).reset_index())
    places = df(ctx.con, """
        SELECT t.match_id, t.team_index, coalesce(t.team_placement, t.placement) AS placement
        FROM teams t JOIN sel USING (match_id)
    """)
    team = team.merge(places, on=["match_id", "team_index"], how="inner").dropna(subset=["placement"])
    if team.empty:
        r.headline = "No team placements to compare against."
        return r

    # Match-level Spearman rho (distance vs placement), then test the average rho.
    rhos = team.groupby(["match_id", "phase"]).apply(lambda g: spearman(g["d"], g["placement"]), include_groups=False)
    allt = ttest_mean(rhos.groupby(level="match_id").mean().dropna(), 0.0)
    r.test("Per match", "Distance vs placement", allt["n"], f"mean rho {allt['mean']:+.2f}", allt["p"], alpha,
           "Positive rho: farther from the closing circle goes with a worse placement")
    for ph, rh in rhos.groupby(level="phase"):
        t = ttest_mean(rh.dropna(), 0.0)
        if t["n"] >= 5:
            r.test(f"Phase {int(ph)}", "Distance vs placement", t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha,
                   "Teams nearer the circle finish better" if t["mean"] > 0 else "Teams farther out finish better")
    team["tier"] = pd.cut(team["placement"], [0, 5, 15, 999], labels=[t[2] for t in TIERS])
    inside = team.assign(inside=team["d"] <= 1).groupby("tier", observed=False)["inside"].mean()
    r.headline = (f"At shrink start, {inside.get('Top 5', np.nan):.0%} of top-5 teams were already inside the closing "
                  f"circle, against {inside.get('16th or lower', np.nan):.0%} of teams finishing 16th or lower.")
    r.metric("Team snapshots", f"{len(team):,}", "One per team per phase, alive at shrink start")
    r.metric("Matches", f"{team['match_id'].nunique():,}")
    r.metric("Mean rho", f"{allt['mean']:+.2f}" if np.isfinite(allt.get("mean", np.nan)) else "–",
             "Distance vs placement, averaged per match")

    team["bucket"] = pd.cut(team["d"], BUCKETS, labels=BUCKET_LABELS, right=False)
    bk = team.groupby("bucket", observed=False).agg(placement=("placement", "mean"), teams=("placement", "size"))
    shown = bk["placement"].where(bk["teams"] >= MIN_BUCKET)
    r.chart("bar", "Average placement by distance from the closing circle",
            [dict(name="Average placement", x=BUCKET_LABELS, y=shown.round(2).tolist())],
            x_label="Distance from next circle's center, in next-circle radii (≤ 1 = inside)",
            y_label="Average placement (lower is better)")
    tiers = team.groupby(["phase", "tier"], observed=False)["d"].apply(lambda s: (s <= 1).mean()).unstack()
    r.chart("line", "Share of teams inside the closing circle at shrink start",
            [dict(name=str(t), x=[int(p) for p in tiers.index], y=(tiers[t] * 100).round(1).tolist()) for t in tiers.columns],
            x_label="Phase", y_label="% of teams inside")
    tbl = bk.reset_index().rename(columns={"bucket": "Distance (radii)", "placement": "Avg placement", "teams": "Teams"})
    tbl["Avg placement"] = tbl["Avg placement"].round(2)
    r.table("Placement by distance bucket", tbl)
    r.notes += [
        "This is descriptive: it shows how position relates to outcome, not that position causes it. Strong teams "
        "may both rotate early and win fights.",
        f"Distance buckets with fewer than {MIN_BUCKET} team snapshots are left off the chart; the table shows all of them.",
        "Positions are taken just before each storm starts closing, after players already know where the next "
        "circle is. Don't use this as an input for predicting zones.",
    ]
    return r
