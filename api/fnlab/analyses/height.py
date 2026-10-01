"""
High ground: does height matter, and from which point in the game?

Raw elevation is mostly terrain (a hill on one side of the map vs a valley on the
other), so both measures here are relative:

1. Fights. For each knock or elimination, compare the winner's height with the
   victim's at that moment. Among fights where one player was clearly higher,
   how often did the higher player win? 50% means height made no difference.
2. Standing. When each storm starts closing, rank the teams in that match by
   average height into thirds (low, mid, high ground) and compare placements.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df, has_table
from . import ALPHA_PARAM, Context, Param, register

FRESH_S = 3.0          # winner's position must be this recent at the moment of the kill
SNAPSHOT_FRESH_S = 10.0
TIERS = ["Low ground", "Mid ground", "High ground"]
DZ_BUCKETS = [-np.inf, -10, -3, 3, 10, np.inf]


def _fights(ctx: Context, include_knocks: bool, max_dist_m: float) -> pd.DataFrame:
    if not has_table(ctx.con, "kills"):
        return pd.DataFrame()
    knocks = "" if include_knocks else "AND NOT coalesce(k.downed, FALSE)"
    f = df(ctx.con, f"""
        WITH k AS (
            SELECT k.match_id, k.t, k.finisher_id, k.victim_id, k.x, k.y, k.z, k.downed
            FROM kills k JOIN sel USING (match_id)
            WHERE k.t > 0 AND k.finisher_id IS NOT NULL AND k.finisher_id <> k.victim_id
              AND k.x IS NOT NULL AND k.z IS NOT NULL {knocks}
        ),
        pos AS (SELECT p.match_id, p.id, p.t, p.x, p.y, p.z, p.skydiving FROM positions p JOIN sel USING (match_id)),
        z AS (SELECT z.match_id, z.phase, z.start_shrink_t FROM zones z JOIN sel USING (match_id)
              WHERE z.start_shrink_t IS NOT NULL)
        SELECT k.match_id, k.t, k.downed, coalesce(z.phase, 0) AS phase,
               (pos.z - k.z) / 100.0 AS dz_m,
               sqrt(power(pos.x - k.x, 2) + power(pos.y - k.y, 2)) / 100.0 AS dist_m,
               k.t - pos.t AS lag, coalesce(pos.skydiving, FALSE) AS winner_skydiving
        FROM k
        ASOF JOIN pos ON k.match_id = pos.match_id AND k.finisher_id = pos.id AND k.t >= pos.t
        ASOF LEFT JOIN z ON k.match_id = z.match_id AND k.t >= z.start_shrink_t
    """)
    if f.empty:
        return f
    return f[(f.lag <= FRESH_S) & (f.dist_m <= max_dist_m) & ~f.winner_skydiving.astype(bool)]


def _snapshots(ctx: Context) -> pd.DataFrame:
    d = df(ctx.con, """
        WITH z AS (
            SELECT z.match_id, z.phase, z.start_shrink_t FROM zones z JOIN sel USING (match_id)
            WHERE z.start_shrink_t IS NOT NULL
        ),
        pl AS (
            SELECT p.match_id, p.id, p.team_index FROM players p JOIN sel USING (match_id)
            WHERE NOT coalesce(p.is_bot, FALSE)
        ),
        zp AS (SELECT * FROM z JOIN pl USING (match_id)),
        pos AS (SELECT p.match_id, p.id, p.t, p.z, p.skydiving FROM positions p JOIN sel USING (match_id))
        SELECT zp.match_id, zp.phase, zp.id, zp.team_index, zp.start_shrink_t, pos.t, pos.z, pos.skydiving
        FROM zp ASOF JOIN pos ON zp.match_id = pos.match_id AND zp.id = pos.id AND zp.start_shrink_t >= pos.t
    """)
    if d.empty:
        return d
    d = d[((d.start_shrink_t - d.t) <= SNAPSHOT_FRESH_S) & ~d.skydiving.fillna(False).astype(bool)]
    team = d.groupby(["match_id", "phase", "team_index"]).agg(z=("z", "mean")).reset_index()
    places = df(ctx.con, """
        SELECT t.match_id, t.team_index, coalesce(t.team_placement, t.placement) AS placement
        FROM teams t JOIN sel USING (match_id)
    """)
    team = team.merge(places, on=["match_id", "team_index"]).dropna(subset=["placement"])
    # Height rank within the match at that moment: 0 = lowest team, 1 = highest.
    team["height_pct"] = team.groupby(["match_id", "phase"])["z"].rank(pct=True)
    n = team.groupby(["match_id", "phase"])["z"].transform("size")
    team = team[n >= 3]
    team["tier"] = pd.cut(team["height_pct"], [0, 1 / 3, 2 / 3, 1.0], labels=TIERS, include_lowest=True)
    return team


@register("height", "High ground",
          "Does being higher than your opponents win fights and games, and from which storm phase does it start to matter?",
          params=[ALPHA_PARAM,
                  Param("level_m", "Counts as level within", "select", 3,
                        [{"value": v, "label": f"{v} m"} for v in (1.5, 3, 5, 10)],
                        "Fights where the height difference is smaller than this count as level and are left out of the win rate."),
                  Param("include_knocks", "Count knocks as fights", "boolean", True,
                        help="A knock is where most fights are decided, so it's included by default."),
                  Param("max_dist_m", "Max fight distance", "select", 150,
                        [{"value": v, "label": f"{v} m"} for v in (50, 100, 150, 300)])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    level = float(ctx.params.get("level_m", 3))
    r = Result()

    # ---------------- Fights
    f = _fights(ctx, bool(ctx.params.get("include_knocks", True)), float(ctx.params.get("max_dist_m", 150)))
    decided = f[f.dz_m.abs() > level] if not f.empty else f
    if not decided.empty:
        decided = decided.assign(higher_won=decided.dz_m > 0)
        per_match = decided.groupby("match_id")["higher_won"].mean()
        t = ttest_mean(per_match, 0.5)
        r.test("Fights, per match", "Higher player wins", t["n"],
               f"{decided.higher_won.mean():.0%} of {len(decided):,} fights", t["p"], alpha,
               "Height wins fights" if t.get("mean", 0.5) > 0.5 else "Height loses fights")
        for ph, g in decided.groupby("phase"):
            if len(g) < 20:
                continue
            b = sps.binomtest(int(g.higher_won.sum()), len(g), 0.5)
            label = "Before first storm" if ph == 0 else f"Zone {int(ph)}"
            r.test("Fights by zone", label, len(g), f"higher player won {g.higher_won.mean():.0%}", b.pvalue, alpha)

    # ---------------- Standing at each storm phase
    team = _snapshots(ctx)
    if not team.empty:
        rhos = team.groupby(["match_id", "phase"]).apply(
            lambda g: spearman(-g["height_pct"], g["placement"]), include_groups=False)
        allt = ttest_mean(rhos.groupby(level="match_id").mean().dropna(), 0.0)
        r.test("Standing, per match", "Higher teams finish better", allt["n"], f"mean rho {allt.get('mean', np.nan):+.2f}",
               allt["p"], alpha, "Teams holding height at shrink start place better")
        for ph, rh in rhos.groupby(level="phase"):
            tt = ttest_mean(rh.dropna(), 0.0)
            if tt["n"] >= 5:
                r.test("Standing by zone", f"Zone {int(ph)}", tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha)

    if decided.empty and team.empty:
        r.headline = ("No height data to compare in this selection. Fights need eliminations with a known eliminator; "
                      "standings need player positions at storm phases.")
        return r

    # ---------------- Headline and numbers
    parts = []
    if not decided.empty:
        parts.append(f"the higher player won {decided.higher_won.mean():.0%} of {len(decided):,} fights with a clear height gap")
    if not team.empty:
        tier_place = team.groupby("tier", observed=False)["placement"].mean()
        parts.append(f"high-ground teams averaged {tier_place.get('High ground', np.nan):.1f} place against "
                     f"{tier_place.get('Low ground', np.nan):.1f} for low ground")
    r.headline = (parts[0][0].upper() + parts[0][1:] + (f", and {parts[1]}." if len(parts) > 1 else "."))

    if not f.empty:
        r.metric("Fights matched", f"{len(f):,}", "Knocks/eliminations where the winner's position at that moment is known")
        r.metric("Clear height gap", f"{len(decided):,}", f"Height difference above {level:g} m")
        r.metric("Median height gap", f"{f.dz_m.abs().median():.1f} m")
    if not team.empty:
        r.metric("Team snapshots", f"{len(team):,}", "One per team per storm phase")

    # ---------------- Charts
    if not decided.empty:
        by_phase = decided.groupby("phase")["higher_won"].agg(["mean", "size"])
        by_phase = by_phase[by_phase["size"] >= 20]
        r.chart("bar", "Fights won by the higher player, by zone",
                [dict(name="Higher player won", x=["Pre-storm" if p == 0 else f"Zone {int(p)}" for p in by_phase.index],
                      y=(by_phase["mean"] * 100).round(1).tolist())],
                y_label="% of fights with a clear height gap",
                reference_lines=[dict(axis="y", value=50, label="No advantage")])
    if not f.empty:
        f = f.assign(bucket=pd.cut(f.dz_m, DZ_BUCKETS, labels=["10 m+ below", "3–10 m below", "Level", "3–10 m above", "10 m+ above"]))
        counts = f.groupby("bucket", observed=False).size()
        r.chart("bar", "Height of the winner relative to the player they beat",
                [dict(name="Fights", x=[str(b) for b in counts.index], y=counts.tolist())], y_label="Fights")
    if not team.empty:
        tp = team.groupby(["phase", "tier"], observed=False)["placement"].mean().unstack()
        r.chart("line", "Average placement by height at each storm phase",
                [dict(name=t, x=[int(p) for p in tp.index], y=tp[t].round(2).tolist()) for t in TIERS if t in tp],
                x_label="Zone (snapshot when the storm starts closing)", y_label="Average placement (lower is better)")
        tbl = (team.groupby(["phase", "tier"], observed=False)
               .agg(teams=("placement", "size"), placement=("placement", "mean")).reset_index()
               .pivot(index="phase", columns="tier", values="placement").round(2).reset_index())
        tbl.columns = ["Zone"] + [f"{c} avg placement" for c in tbl.columns[1:]]
        r.table("Average placement by height tier and phase", tbl)

    first = next((t["name"] for t in r.tests if t["group"] == "Fights by zone" and t["significant"]), None)
    conclude(r, ctx, strategy=True, primary=["Fights, per match", "Standing, per match"], alpha=alpha, recommended=100,
             single_season=False,
             takeaway_found=("Height is linked to winning" + (f", in fights from {first.lower()} onward" if first else "")
                             + ". Check both main tests below: fights and final placement."),
             takeaway_none="No consistent advantage for the higher player or team in this selection.",
             next_found=["Expand Fights by zone and Standing by zone to see when the advantage starts.",
                         "Raise the 'level' setting: an effect that survives larger height gaps is more robust."],
             next_none=["Try a single late phase or a smaller fight distance: height may matter only in close endgame fights."])
    r.notes += [
        "Height is relative: the winner vs the player they beat, or each team vs the other teams in the same match at "
        "that moment. Raw elevation mostly reflects terrain.",
        "Height includes both terrain and builds. Separating natural high ground from built height needs a terrain "
        "map for the season, which can be estimated from the lowest heights players are seen at in each map cell.",
        "Associations, not causes: strong players both take height and win fights.",
    ]
    return r
