from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import circ_mean, ks_uniform, rayleigh, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

MIN_N = 8


def fmt(x, d=2):
    return "–" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def _cols(con, table: str) -> set[str]:
    return set(con.execute(f"SELECT * FROM {table} LIMIT 0").df().columns)


def load_pulls(con, sel: str = "sel", zones: str = "all") -> pd.DataFrame:
    zc, bc = _cols(con, "zone_offsets"), _cols(con, "bus")
    kind = "z.kind" if "kind" in zc else "'shrinking'"
    # Only trust a bus heading that is exact (from the bus itself) or a tight fitted line.
    reliable = "b.bus_source = 'aircraft'" + (" OR (b.fit_rms <= 5000 AND b.n_points >= 10)" if "fit_rms" in bc else "")
    z = df(con, f"""
        SELECT z.match_id, z.phase, z.u, z.offset_ratio, z.shrink_ratio, z.angle_deg, {kind} AS kind,
               CASE WHEN {reliable} THEN b.bearing_deg END AS bearing_deg, b.direction_known
        FROM zone_offsets z JOIN {sel} USING (match_id) LEFT JOIN bus b USING (match_id)
        ORDER BY z.match_id, z.phase
    """)
    if zones in ("shrinking", "moving"):
        z = z[z.kind == zones]
    if z.empty:
        return z
    z["u"] = z["u"].clip(0, 1)
    z["rel_bus_deg"] = (z["angle_deg"] - z["bearing_deg"]) % 360
    z["turn_deg"] = z.groupby("match_id")["angle_deg"].diff() % 360
    return z


def per_match(z: pd.DataFrame) -> pd.DataFrame:
    return z.groupby("match_id").agg(u=("u", "mean"), offset_ratio=("offset_ratio", "mean"),
                                     angle=("angle_deg", circ_mean), rel_bus=("rel_bus_deg", circ_mean),
                                     turn=("turn_deg", circ_mean))


@register("zone_randomness", "Is the storm random?",
          "Tests each storm pull against pure randomness: how far it moves, which way, relative to the bus, "
          "and whether it keeps the previous pull's direction.",
          params=[ALPHA_PARAM,
                  Param("zones", "Zones", "select", "all",
                        [{"value": "all", "label": "All zones"}, {"value": "shrinking", "label": "Shrinking zones only"},
                         {"value": "moving", "label": "Moving zones only"}],
                        "Distance tests always use shrinking zones; this also limits the direction tests."),
                  Param("min_phase", "From zone", "number", 2, help="Zone 1's starting circle is often unknown"),
                  Param("max_phase", "To phase", "number", 12)])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    z = load_pulls(ctx.con, zones=ctx.params.get("zones", "all"))
    if z.empty:
        r.headline = "No storm pulls in this selection."
        return r
    z = z[(z.phase >= int(ctx.params.get("min_phase", 2))) & (z.phase <= int(ctx.params.get("max_phase", 12)))]
    pm = per_match(z)

    # Independent, match-level tests: the ones to trust.
    t_u = ttest_mean(pm["u"], 0.5)
    r_dir, r_bus, r_turn = rayleigh(pm["angle"]), rayleigh(pm["rel_bus"]), rayleigh(pm["turn"])
    g = "Per match"
    r.test(g, "Pull distance", t_u["n"], f"mean u {fmt(t_u['mean'])} (random: 0.50)", t_u["p"], alpha,
           "Pulls hug the circle edge" if t_u.get("mean", 0.5) > 0.5 else "Pulls stay near the center")
    r.test(g, "Compass direction", r_dir["n"], f"{fmt(r_dir['mean_deg'], 0)}°, R {fmt(r_dir['R'])}", r_dir["p"], alpha,
           "Pulls favour one map direction")
    r.test(g, "Direction vs bus", r_bus["n"], f"{fmt(r_bus['mean_deg'], 0)}° from bus, R {fmt(r_bus['R'])}",
           r_bus["p"], alpha, "Pulls relate to the bus heading")
    r.test(g, "Keeps previous direction", r_turn["n"], f"turn {fmt(r_turn['mean_deg'], 0)}°, R {fmt(r_turn['R'])}",
           r_turn["p"], alpha, "Each pull tends to follow the last")

    # Per-phase tests: each phase occurs once per match, so these are independent too.
    for ph, gz in z.groupby("phase"):
        if len(gz) < MIN_N:
            continue
        ks, rd, rb = ks_uniform(gz["u"]), rayleigh(gz["angle_deg"]), rayleigh(gz["rel_bus_deg"])
        moving = (gz["kind"] == "moving").mean() > 0.5
        grp = f"Zone {int(ph)}" + (" (moving)" if moving else "")
        if not moving:
            r.test(grp, "Pull distance", ks["n"], f"mean u {gz['u'].mean():.2f}", ks["p"], alpha)
        r.test(grp, "Compass direction", rd["n"], f"{fmt(rd['mean_deg'], 0)}°, R {fmt(rd['R'])}", rd["p"], alpha)
        r.test(grp, "Direction vs bus", rb["n"], f"{fmt(rb['mean_deg'], 0)}°, R {fmt(rb['R'])}", rb["p"], alpha)
        if gz["turn_deg"].notna().sum() >= MIN_N:
            rt = rayleigh(gz["turn_deg"])
            r.test(grp, "Keeps previous direction", rt["n"], f"turn {fmt(rt['mean_deg'], 0)}°", rt["p"], alpha)

    flagged = [t["name"].lower() for t in r.tests if t["group"] == g and t["significant"]]
    r.headline = (f"Not random across {len(pm):,} matches: {', '.join(flagged)}." if flagged else
                  f"No departure from randomness across {len(pm):,} matches at p < {alpha}.")
    r.metric("Matches", f"{len(pm):,}")
    r.metric("Storm pulls", f"{len(z):,}")
    r.metric("Mean u", fmt(z["u"].mean()), "Shrinking zones only. 0.50 if random, 1.00 if every pull hits the edge")
    r.metric("Moving zones", f"{(z['kind'] == 'moving').sum():,} of {len(z):,}")
    r.metric("Bus route known", f"{z.groupby('match_id').bearing_deg.first().notna().mean():.0%}",
             "Matches with an exact or tightly fitted bus route; others are left out of bus tests")

    r.chart("polar_histogram", "Pull direction", [dict(name="Pulls", theta=z["angle_deg"].tolist())], bins=24)
    r.chart("polar_histogram", "Pull direction relative to bus heading",
            [dict(name="Pulls", theta=z["rel_bus_deg"].dropna().tolist())], bins=24, zero_label="Bus heading")
    r.chart("polar_histogram", "Turn from previous pull", [dict(name="Pulls", theta=z["turn_deg"].dropna().tolist())],
            bins=24)
    us = z["u"].dropna()
    if len(us):
        r.chart("histogram", "Pull distance u", [dict(name="Shrinking zones", values=us.tolist())],
                bins=10, range=[0, 1], x_label="u = (distance moved / max allowed)²", y_label="Pulls",
                reference_lines=[dict(axis="y", value=len(us) / 10, label="If random")])
    found = [t["reading"].lower() for t in r.tests if t["group"] == g and t["significant"] and t.get("reading")]
    conclude(r, ctx, primary=[g], alpha=alpha, recommended=200,
             takeaway_found="Storm pulls are not random in this selection: " + "; ".join(found) + ".",
             takeaway_none="Storm pulls look random on all four measures: distance, compass direction, relation to "
                           "the bus and persistence.",
             next_found=["Expand the phases to find when the effect starts.",
                         "Open Storm pull geometry to see how large it is in each phase.",
                         "Repeat per region: a pattern specific to one area can drive the pooled result."],
             next_none=["Repeat per region or per event window; an effect specific to one part of the map can hide in pooled data."])
    r.notes += [
        "Trust the per-match and per-phase rows. Pooling every pull treats linked pulls as independent and overstates significance.",
        "Island shape (water, edges) can create non-randomness on its own. Confirm any effect on a held-out season.",
        "If pulls follow the bus, consecutive pulls will also look persistent: the two tests are confounded.",
    ]
    return r
