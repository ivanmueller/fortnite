"""
Drop spots: where players land, how contested it is, how it sits against the bus and the
first storm circles, and what happens next (eliminated off spawn, early eliminations,
final placement). Built on the landings table (pipeline/python/landings.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

MIN_POI_LANDINGS = 8


def _per_match_rho(d: pd.DataFrame, x: str, y: str = "final") -> pd.Series:
    return d.groupby("match_id").apply(
        lambda g: spearman(g[x], g[y]) if g[x].nunique() > 1 and len(g) >= 8 else np.nan, include_groups=False).dropna()


def _skill_band(pr: pd.Series) -> pd.Series:
    return pd.cut(pr.fillna(10**7), [0, 1000, 10000, 10**8], labels=["PR top 1,000", "PR 1,001–10,000", "Unranked"])


@register("drops", "Drop spots",
          "Where players land, how contested it is, how it sits against the bus route and the first storm circles, "
          "and what happens next: eliminated off spawn, early eliminations, final placement. Includes a table of "
          "every named drop spot.",
          params=[ALPHA_PARAM,
                  Param("players", "Players", "select", "all",
                        [{"value": "all", "label": "All players"},
                         {"value": "ranked", "label": "Power Rankings top 10,000 only"},
                         {"value": "unranked", "label": "Outside the top 10,000 only"}],
                        "Restrict to one skill band.")])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    if "landings" not in {t for (t,) in ctx.con.execute("SELECT table_name FROM information_schema.tables").fetchall()}:
        r.headline = "No landing data yet. Rebuild the tables (data menu option 7) after updating."
        return r
    d = df(ctx.con, "SELECT l.* FROM landings l JOIN sel USING (match_id)")
    who = ctx.params.get("players", "all")
    if who != "all" and "pr_rank" in d and d["pr_rank"].notna().any():
        d = d[d["pr_rank"].notna()] if who == "ranked" else d[d["pr_rank"].isna()]
    d = d.dropna(subset=["final"])
    if d.empty:
        r.headline = "No landings in this selection."
        return r
    d["contested"] = d["opp_150m"] > 0
    has_pr = "pr_rank" in d and d["pr_rank"].notna().any()

    # ---- tests, one summary per match
    def rate_gap(g):
        a, b = g[g["contested"]], g[~g["contested"]]
        return a["off_spawn"].mean() - b["off_spawn"].mean() if len(a) >= 2 and len(b) >= 2 else np.nan
    gaps = d.groupby("match_id").apply(rate_gap, include_groups=False).dropna()
    t = ttest_mean(gaps, 0.0)
    rc, ru = d.loc[d["contested"], "off_spawn"].mean(), d.loc[~d["contested"], "off_spawn"].mean()
    r.test("Per match", "Contested drops eliminated off spawn more", t["n"],
           f"{rc:.0%} of contested vs {ru:.0%} of uncontested eliminated within 2 min", t["p"], alpha,
           "Landing within 150 m of an opponent raises the risk of an early elimination")
    for col, name, reading in [
        ("opp_300m", "More opponents nearby, worse placement", "Landing near more opponents goes with finishing worse"),
        ("outside_zone2_m", "Landing far from zone 2, worse placement", "Landing far outside the second circle goes with finishing worse"),
        ("opp_top1000_300m", "Strong opponents nearby, worse placement", "Landing near Power Rankings top-1,000 players goes with finishing worse"),
    ]:
        if col in d and d[col].notna().any() and (col != "opp_top1000_300m" or has_pr):
            rho = _per_match_rho(d.dropna(subset=[col]), col)
            tt = ttest_mean(rho, 0.0)
            if tt["n"] >= 3:
                r.test("Per match", name, tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha, reading)
    surv = d[~d["off_spawn"]]
    rho = _per_match_rho(surv, "early_kills")
    tt = ttest_mean(rho, 0.0)
    if tt["n"] >= 3:
        r.test("Spawn fights", "Early eliminations, better placement", tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha,
               "Among players who survive the landing, those with early eliminations finish better (negative rho)")
    if "bus_offset_m" in d and d["bus_offset_m"].notna().any():
        rho = d.dropna(subset=["bus_offset_m"]).groupby("match_id").apply(
            lambda g: spearman(g["bus_offset_m"], g["opp_300m"]) if len(g) >= 8 else np.nan, include_groups=False).dropna()
        tt = ttest_mean(rho, 0.0)
        if tt["n"] >= 3:
            r.test("Bus route", "Farther from the bus, less contested", tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha,
                   "Drops farther from the bus line have fewer opponents nearby (negative rho)")

    # ---- numbers
    r.headline = (f"{len(d):,} landings: {d['contested'].mean():.0%} contested within 150 m; contested players were "
                  f"eliminated off spawn {rc:.0%} of the time against {ru:.0%} for uncontested.")
    r.metric("Landings", f"{len(d):,}", "Players whose landing was detected")
    r.metric("Contested", f"{d['contested'].mean():.0%}", "Another team landed within 150 m")
    r.metric("Eliminated off spawn", f"{d['off_spawn'].mean():.0%}", "Eliminated within 2 minutes of landing")
    r.metric("Median nearest opponent", f"{d['nearest_opp_m'].median():.0f} m")
    r.metric("Median glide", f"{d['glide_s'].median():.0f} s", "From appearing after the bus to landing")
    if d["outside_zone2_m"].notna().any():
        r.metric("Landed inside zone 2", f"{(d['outside_zone2_m'] == 0).mean():.0%}")

    # ---- POI table: the per-spot summary a team plans drops from
    if "poi" in d and d["poi"].notna().any():
        exp = d.groupby(_skill_band(d["pr_rank"]) if has_pr else pd.Series("all", index=d.index), observed=True)["final"].transform("mean")
        d["vs_skill"] = d["final"] - exp
        p = d[d["poi"].notna()].groupby("poi").agg(
            landings=("id", "size"), matches=("match_id", "nunique"),
            contested=("contested", "mean"), off_spawn=("off_spawn", "mean"), early_kills=("early_kills", "mean"),
            placement=("final", "mean"), vs_skill=("vs_skill", "mean"),
            in_z1=("outside_zone1_m", lambda s: (s == 0).mean()), out_z2=("outside_zone2_m", "median"),
            bus=("bus_offset_m", "median"),
            strong=("pr_rank", lambda s: (s <= 1000).mean()) if has_pr else ("id", lambda s: np.nan))
        p = p[p["landings"] >= MIN_POI_LANDINGS].sort_values("vs_skill")
        p["per_match"] = p["landings"] / p["matches"]
        tbl = pd.DataFrame({
            "Drop spot": p.index, "Landings": p["landings"].astype(int), "Matches": p["matches"].astype(int),
            "Players per match": p["per_match"].round(1),
            "Contested": (p["contested"] * 100).round(0).astype(int).astype(str) + "%",
            "Eliminated off spawn": (p["off_spawn"] * 100).round(0).astype(int).astype(str) + "%",
            "Early elims": p["early_kills"].round(2),
            "Avg placement": p["placement"].round(1),
            "vs expected for skill": p["vs_skill"].round(1),
            "In zone 1": (p["in_z1"] * 100).round(0).astype(int).astype(str) + "%",
            "Median outside zone 2 (m)": p["out_z2"].round(0),
            "Median from bus line (m)": p["bus"].round(0),
        })
        if has_pr:
            tbl["PR top-1,000 landers"] = (p["strong"] * 100).round(0).astype(int).astype(str) + "%"
        r.table("Drop spots", tbl)
        r.notes.insert(0, f"Drop spots table: spots with at least {MIN_POI_LANDINGS} landings, best first by 'vs expected "
                          "for skill' (average placement minus the average for players of the same Power Rankings band; "
                          "negative = better than their skill predicts).")

    # ---- charts
    bands = pd.cut(d["opp_150m"], [-1, 0, 1, 2, 3, 99], labels=["0", "1", "2", "3", "4+"])
    g = d.groupby(bands, observed=True)
    r.chart("bar", "Eliminated off spawn by opponents within 150 m",
            [dict(name="Eliminated within 2 min", x=[str(b) for b in g.size().index],
                  y=(g["off_spawn"].mean() * 100).round(1).tolist())],
            x_label="Opponents landing within 150 m", y_label="% eliminated off spawn")
    r.chart("bar", "Average placement by opponents within 150 m",
            [dict(name="Average placement", x=[str(b) for b in g.size().index], y=g["final"].mean().round(1).tolist())],
            x_label="Opponents landing within 150 m", y_label="Average placement (lower is better)")
    if d["outside_zone2_m"].notna().any():
        zb = pd.cut(d["outside_zone2_m"], [-1, 0, 200, 400, 1e9], labels=["Inside", "0–200 m", "200–400 m", "400 m+"])
        gz = d.groupby(zb, observed=True)["final"]
        r.chart("bar", "Average placement by distance outside zone 2 at landing",
                [dict(name="Average placement", x=[str(b) for b in gz.mean().index], y=gz.mean().round(1).tolist())],
                x_label="Landing distance outside the second circle", y_label="Average placement (lower is better)")
    sample = d.sample(min(len(d), 5000), random_state=0)
    series = [dict(name="Survived the landing", x=sample.loc[~sample["off_spawn"], "land_x"].tolist(),
                   y=sample.loc[~sample["off_spawn"], "land_y"].tolist()),
              dict(name="Eliminated off spawn", x=sample.loc[sample["off_spawn"], "land_x"].tolist(),
                   y=sample.loc[sample["off_spawn"], "land_y"].tolist())]
    if "pois" in {t for (t,) in ctx.con.execute("SELECT table_name FROM information_schema.tables").fetchall()}:
        pois = df(ctx.con, "SELECT * FROM pois WHERE kind = 'poi'")
        if len(pois):
            series.append(dict(name="Named places", x=pois["x"].tolist(), y=pois["y"].tolist(), text=pois["name"].tolist()))
    r.chart("map_points", "Where players land", series, x_label="X (Unreal units)", y_label="Y (Unreal units)")

    if has_pr:
        sk = d.assign(band=_skill_band(d["pr_rank"]), contest=np.where(d["contested"], "Contested", "Uncontested")) \
              .groupby(["band", "contest"], observed=False)["final"].mean().unstack().round(1).reset_index()
        sk.columns = ["Skill band"] + [f"{c}: avg placement" for c in sk.columns[1:]]
        r.table("Skill control: placement by contest within each Power Rankings band", sk)

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=True,
             takeaway_found="Drop choices are linked to outcome: " + "; ".join(
                 t["reading"].lower() for t in r.tests if t["group"] == "Per match" and t["significant"]) + ".",
             takeaway_none="No consistent link between drop choices and outcome in this selection.",
             next_found=["Use the Drop spots table to compare candidate spots on contest, off-spawn risk and zone luck.",
                         "Check the skill-control table: contest should cost placement within each Power Rankings band.",
                         "Repeat on strong lobbies only (Lobby strength filter): pro drops are planned, not random."],
             next_none=["Try strong lobbies only: in early qualifier rounds, drops are less deliberate."])
    r.notes += [
        "Landings are detected from movement: the first moment a player stays at the same height after descending from "
        "the bus. Season 42 replays don't record the skydive itself.",
        "Contest counts players from other teams landing within 150 m (or 300 m); it doesn't see who is fighting whom.",
        "Loot isn't measured yet: chest spawns and materials aren't in the extracted data. A higher-loot spot shows up "
        "only through its outcomes (survival, placement, rotation).",
        "Drop spot names come from the current map, so they're given only for matches from the latest season in the data.",
    ]
    return r
