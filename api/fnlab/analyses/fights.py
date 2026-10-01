"""
Health and fights: fights detected from actual damage, health and shield going into each fight,
who shot first, third parties, and storm/fall damage.

Fight: hits between two teams, either direction, with gaps under GAP_S, and at least MIN_HITS hits
(or ending in an elimination). Outcome: a side loses if a
member is eliminated during the fight (or within 5 s after); the other side wins. Health at the
start is health + shield 1 s before the first hit (health updates can lag a hit slightly).
Third party: a third team hits either side during the fight.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, has_tables, player_hits, unexplained_drops

GAP_S = 15
AFTER_S = 60
MIN_HITS = 3   # a single stray hit (a long-range tag) isn't a fight


def fights(ctx: Context) -> pd.DataFrame:
    h = player_hits(ctx.con)
    if h.empty:
        return pd.DataFrame()
    players = df(ctx.con, "SELECT p.match_id, p.id, p.team_index, p.death_t FROM players p JOIN sel USING (match_id) "
                          "WHERE NOT coalesce(p.is_bot, FALSE)")
    hp = df(ctx.con, "SELECT h.match_id, h.id, h.t, coalesce(h.health, 0) + coalesce(h.shield, 0) AS hp "
                     "FROM health h JOIN sel USING (match_id)")
    zones = df(ctx.con, "SELECT z.match_id, z.phase, z.start_shrink_t FROM zones z JOIN sel USING (match_id) "
                        "WHERE z.start_shrink_t IS NOT NULL")
    h["a"] = h[["attacker_team", "target_team"]].min(axis=1)
    h["b"] = h[["attacker_team", "target_team"]].max(axis=1)
    h = h.sort_values(["match_id", "a", "b", "t"])
    h["new"] = (h.groupby(["match_id", "a", "b"])["t"].diff().fillna(1e9) > GAP_S)
    h["fight"] = h["new"].cumsum()

    deaths = players.dropna(subset=["death_t"]).groupby(["match_id", "team_index"])["death_t"].apply(list).to_dict()
    members = players.groupby(["match_id", "team_index"])["id"].apply(list).to_dict()
    hp = hp.sort_values("t")
    hp_by = {k: g for k, g in hp.groupby(["match_id", "id"])}
    all_hits = h.groupby("match_id")

    def side_hp(mid, team, t):
        vals = []
        for pid in members.get((mid, team), []):
            g = hp_by.get((mid, pid))
            if g is None:
                continue
            i = g["t"].searchsorted(t, side="right") - 1
            if i >= 0:
                vals.append(g["hp"].iloc[i])
        return float(np.mean(vals)) if vals else np.nan

    rows = []
    for (mid, fid), g in h.groupby(["match_id", "fight"]):
        a, b = int(g["a"].iloc[0]), int(g["b"].iloc[0])
        t0, t1 = g["t"].min(), g["t"].max()
        lost = {s: any(t0 <= d <= t1 + 5 for d in deaths.get((mid, s), [])) for s in (a, b)}
        winner = b if lost[a] and not lost[b] else a if lost[b] and not lost[a] else None
        mh = all_hits.get_group(mid)
        window = mh[(mh["t"] >= t0) & (mh["t"] <= t1)]
        third = window[~window["attacker_team"].isin([a, b]) & window["target_team"].isin([a, b])]
        first = int(g.sort_values("t")["attacker_team"].iloc[0])
        rows.append(dict(
            match_id=mid, t0=t0, duration=t1 - t0, team_a=a, team_b=b, first=first, winner=winner,
            hp_a=side_hp(mid, a, t0 - 1), hp_b=side_hp(mid, b, t0 - 1),
            dmg_a=g.loc[g["attacker_team"] == a, "amount"].sum(), dmg_b=g.loc[g["attacker_team"] == b, "amount"].sum(),
            hits=len(g), third_party=len(third) > 0,
            winner_died_after=(any(t1 < d <= t1 + AFTER_S for d in deaths.get((mid, winner), [])) if winner is not None else None),
        ))
    f = pd.DataFrame(rows)
    if f.empty:
        return f
    f = f[(f["hits"] >= MIN_HITS) | f["winner"].notna()]
    if f.empty:
        return f
    z = zones.sort_values("start_shrink_t")
    f = pd.merge_asof(f.sort_values("t0"), z.rename(columns={"start_shrink_t": "t0"}), on="t0", by="match_id", direction="backward")
    f["phase"] = f["phase"].fillna(0).astype(int)
    f["decided"] = f["winner"].notna()
    f["hp_gap"] = f["hp_a"] - f["hp_b"]
    f["higher_hp_won"] = np.where(f["hp_gap"] > 0, f["winner"] == f["team_a"], f["winner"] == f["team_b"])
    f["first_won"] = f["winner"] == f["first"]
    return f


@register("fights", "Health and fights",
          "Fights detected from actual damage: health and shield going in, who shot first, third parties, "
          "whether the winner survives, and how much storm and fall damage players take.",
          params=[ALPHA_PARAM,
                  Param("min_gap", "Health gap that counts as an advantage", "select", 25,
                        [{"value": 10, "label": "10 HP"}, {"value": 25, "label": "25 HP"}, {"value": 50, "label": "50 HP"}])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    min_gap = float(ctx.params.get("min_gap", 25))
    r = Result()
    if not has_tables(ctx.con, "damage", "health"):
        r.headline = "No health or damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    f = fights(ctx)
    if f.empty:
        r.headline = "No fights between players found in this selection."
        conclude_without_data(r, ctx)
        return r
    dec = f[f["decided"]]
    adv = dec[dec["hp_gap"].abs() >= min_gap].dropna(subset=["hp_gap"])

    # ---- tests, one summary per match
    t = ttest_mean(adv.groupby("match_id")["higher_hp_won"].mean(), 0.5)
    if t["n"] >= 3:
        r.test("Per match", "More health going in wins", t["n"],
               f"{adv['higher_hp_won'].mean():.0%} of {len(adv):,} fights with a {min_gap:g}+ HP gap", t["p"], alpha,
               ("The side with more health and shield at the start wins more often",
                "The side with more health and shield at the start wins less often"), direction=t.get("mean"), null=0.5)
    t = ttest_mean(dec.groupby("match_id")["first_won"].mean(), 0.5)
    if t["n"] >= 3:
        r.test("Per match", "First to shoot wins", t["n"], f"{dec['first_won'].mean():.0%} of {len(dec):,} decided fights",
               t["p"], alpha, ("The side that lands the first hit wins more often", "The side that lands the first hit wins less often"),
               direction=t.get("mean"), null=0.5)
    tp = dec.dropna(subset=["winner_died_after"])

    def tp_gap(g):
        a, b = g[g["third_party"]], g[~g["third_party"]]
        return a["winner_died_after"].mean() - b["winner_died_after"].mean() if len(a) >= 2 and len(b) >= 2 else np.nan
    gaps = tp.groupby("match_id").apply(tp_gap, include_groups=False).dropna()
    t = ttest_mean(gaps, 0.0)
    if t["n"] >= 3:
        ra = tp.loc[tp["third_party"], "winner_died_after"].mean()
        rb = tp.loc[~tp["third_party"], "winner_died_after"].mean()
        r.test("Per match", "Third-partied fights cost the winner", t["n"],
               f"winner eliminated within {AFTER_S} s: {ra:.0%} when third-partied vs {rb:.0%}", t["p"], alpha,
               ("Winning a fight a third team joined is more often followed by elimination",
                "Winning a fight a third team joined is less often followed by elimination"), direction=t.get("mean"))
    drops = unexplained_drops(ctx.con)
    if not drops.empty:
        storm = drops[drops["in_storm"] == True]  # noqa: E712
        players = df(ctx.con, "SELECT p.match_id, p.id, p.death_t, coalesce(p.team_placement, p.placement) AS final "
                              "FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE)")
        early = storm[storm["phase"].between(2, 4)].groupby(["match_id", "id"])["lost"].sum().rename("storm_early")
        reached = players.merge(df(ctx.con, "SELECT z.match_id, min(z.start_shrink_t) AS t5 FROM zones z JOIN sel USING (match_id) "
                                            "WHERE z.phase = 5 GROUP BY 1"), on="match_id")
        reached = reached[reached["death_t"].isna() | (reached["death_t"] > reached["t5"])]
        sp = reached.merge(early.reset_index(), on=["match_id", "id"], how="left").fillna({"storm_early": 0}).dropna(subset=["final"])
        rho = sp.groupby("match_id").apply(lambda g: spearman(g["storm_early"], g["final"]) if g["storm_early"].nunique() > 1 and len(g) >= 8 else np.nan,
                                           include_groups=False).dropna()
        t = ttest_mean(rho, 0.0)
        if t["n"] >= 3:
            r.test("Per match", "Storm damage in zones 2–4, worse placement", t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha,
                   ("Among players who reach zone 5, those who took more storm damage in zones 2–4 finish worse",
                    "Among players who reach zone 5, those who took more storm damage in zones 2–4 finish better"),
                   direction=t.get("mean"))
        by_phase = storm.groupby(["phase"]).agg(players=("id", "nunique"), damage=("lost", "sum"))
        r.chart("bar", "Storm damage taken, by zone",
                [dict(name="Total storm damage", x=[f"Zone {int(p)}" for p in by_phase.index], y=by_phase["damage"].round(0).tolist())],
                y_label="Health and shield lost in the storm")
        r.metric("Storm damage per player", f"{storm.groupby(['match_id', 'id'])['lost'].sum().median():.0f}",
                 "Median total, players who took any")

    # ---- numbers
    r.headline = (f"{len(f):,} fights detected, {len(dec):,} decided. The side with more health going in won "
                  f"{adv['higher_hp_won'].mean():.0%} of decided fights with a {min_gap:g}+ HP gap; "
                  f"{f['third_party'].mean():.0%} of fights drew a third team.")
    r.metric("Fights", f"{len(f):,}", f"{MIN_HITS}+ hits between two teams with gaps under {GAP_S} s, or ending in an elimination")
    r.metric("Decided", f"{f['decided'].mean():.0%}", "One side lost a player during the fight")
    r.metric("Median fight length", f"{f['duration'].median():.0f} s")
    r.metric("Third-partied", f"{f['third_party'].mean():.0%}")
    r.metric("Median health going in", f"{pd.concat([f['hp_a'], f['hp_b']]).median():.0f}", "Health + shield, 1 s before the first hit")

    # ---- charts and tables
    bins = [-1000, -75, -25, 25, 75, 1000]
    labels = ["75+ behind", "25–75 behind", "Even (±25)", "25–75 ahead", "75+ ahead"]
    s = pd.concat([
        dec.assign(gap=dec["hp_gap"], won=dec["winner"] == dec["team_a"]),
        dec.assign(gap=-dec["hp_gap"], won=dec["winner"] == dec["team_b"]),
    ]).dropna(subset=["gap"])
    g = s.groupby(pd.cut(s["gap"], bins, labels=labels), observed=True)["won"]
    r.chart("bar", "Win rate by health advantage going in",
            [dict(name="Win rate", x=[str(i) for i in g.mean().index], y=(g.mean() * 100).round(1).tolist())],
            x_label="Health + shield compared with the opponent", y_label="% of decided fights won",
            reference_lines=[dict(axis="y", value=50, label="Even")])
    ph = f.groupby("phase").agg(fights=("t0", "size"), third=("third_party", "mean"), length=("duration", "median"))
    r.chart("line", "Third-party rate by zone",
            [dict(name="Fights joined by a third team", x=[int(p) for p in ph.index], y=(ph["third"] * 100).round(1).tolist())],
            x_label="Zone (0 = before the first storm)", y_label="% of fights")
    r.chart("histogram", "Fight length", [dict(name="Fights", values=f["duration"].clip(0, 60).tolist())], bins=30, range=[0, 60],
            x_label="Seconds (over 60 shown at 60)", y_label="Fights")
    tbl = ph.reset_index().rename(columns={"phase": "Zone", "fights": "Fights", "length": "Median length (s)"})
    tbl["Third-partied"] = (tbl.pop("third") * 100).round(0).astype(int).astype(str) + "%"
    r.table("Fights by zone", tbl.round(1))

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=True,
             takeaway_found="Fight outcomes follow clear patterns: " + "; ".join(
                 t["reading"].lower() for t in r.tests if t["group"] == "Per match" and t["significant"]) + ".",
             takeaway_none="No consistent pattern in fight outcomes in this selection.",
             next_found=["Use the health-advantage chart to set a 'take the fight' threshold for the team.",
                         "Check third-party rates by zone: phases where most fights draw a third team call for shorter fights."],
             next_none=["Try strong lobbies only: fight selection differs most at the top."])
    r.notes += [
        f"Fights are grouped from damage between two teams with gaps under {GAP_S} s, with at least {MIN_HITS} hits or an "
        "elimination. A fight is decided when one side "
        "loses a player during it or within 5 s after.",
        "Health going in is read 1 s before the first hit, because health updates can lag a hit slightly.",
        "Storm damage is health or shield lost outside the storm circle with no player hit nearby.",
    ]
    return r
