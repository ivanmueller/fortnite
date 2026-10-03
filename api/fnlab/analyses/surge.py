"""
Surge: when competitive storm surge triggers, who it hits, and how much damage keeps a player safe.

Replays don't record surge directly. It shows up in the data as several players losing health in
the same second while inside the safe zone, with no player hitting them. Detection:
  tick     MIN_PLAYERS+ players inside the zone lose health in the same second with no player hit nearby
  episode  ticks no more than EPISODE_GAP_S apart
Surge targets the players who have dealt the least damage, so for each episode we compare damage
dealt (to players, before the episode) by surged and safe players: the gap estimates the threshold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import ALPHA_PARAM, Context, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, has_tables, player_hits, unexplained_drops

MIN_PLAYERS = 3
EPISODE_GAP_S = 10


def surge_episodes(ctx: Context) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(episodes, per-player rows for each episode)."""
    drops = unexplained_drops(ctx.con)
    if drops.empty:
        return pd.DataFrame(), pd.DataFrame()
    inside = drops[(drops["in_storm"] == False) & drops["phase"].notna()].copy()  # noqa: E712
    inside["sec"] = inside["t"].round(0)
    tick = inside.groupby(["match_id", "sec"]).agg(players=("id", "nunique"), lost=("lost", "median"), phase=("phase", "max")).reset_index()
    tick = tick[tick["players"] >= MIN_PLAYERS].sort_values(["match_id", "sec"])
    if tick.empty:
        return pd.DataFrame(), pd.DataFrame()
    tick["episode"] = (tick.groupby("match_id")["sec"].diff().fillna(1e9) > EPISODE_GAP_S).cumsum()
    ep = tick.groupby("episode").agg(match_id=("match_id", "first"), t0=("sec", "min"), t1=("sec", "max"),
                                     ticks=("sec", "size"), phase=("phase", "max"), per_tick=("lost", "median")).reset_index()

    players = df(ctx.con, "SELECT p.match_id, p.id, p.death_t, coalesce(p.team_placement, p.placement) AS final, "
                          + ("p.pr_rank" if "pr_rank" in set(ctx.con.execute("SELECT * FROM players LIMIT 0").df().columns) else "NULL AS pr_rank")
                          + " FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE)")
    hits = player_hits(ctx.con)
    rows = []
    for _, e in ep.iterrows():
        alive = players[(players["match_id"] == e["match_id"]) & (players["death_t"].isna() | (players["death_t"] > e["t0"]))]
        hit_ids = set(inside[(inside["match_id"] == e["match_id"]) & inside["sec"].between(e["t0"], e["t1"])]["id"])
        dealt = hits[(hits["match_id"] == e["match_id"]) & (hits["t"] < e["t0"])].groupby("attacker_id")["amount"].sum()
        for _, p in alive.iterrows():
            rows.append(dict(episode=e["episode"], match_id=e["match_id"], id=p["id"], surged=p["id"] in hit_ids,
                             dealt=float(dealt.get(p["id"], 0.0)), final=p["final"], pr_rank=p["pr_rank"]))
    per = pd.DataFrame(rows)
    if not per.empty:
        g = per.groupby("episode")
        ep = ep.merge(g.size().rename("alive"), left_on="episode", right_index=True)
        ep = ep.merge(g["surged"].sum().rename("surged"), left_on="episode", right_index=True)
        ep = ep.merge(per[per["surged"]].groupby("episode")["dealt"].max().rename("max_dealt_surged"), left_on="episode", right_index=True, how="left")
        ep = ep.merge(per[~per["surged"]].groupby("episode")["dealt"].min().rename("min_dealt_safe"), left_on="episode", right_index=True, how="left")
    return ep, per


@register("surge", "Surge",
          "When competitive storm surge triggers, how many players are alive, who it hits, and how much damage "
          "a player needed to have dealt to stay safe.",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    if not has_tables(ctx.con, "damage", "health"):
        r.headline = "No health or damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    n_matches = ctx.con.execute("SELECT count(DISTINCT h.match_id) FROM health h JOIN sel USING (match_id)").fetchone()[0]
    ep, per = surge_episodes(ctx)
    r.metric("Matches with in-match data", f"{n_matches:,}")
    if ep.empty:
        r.headline = (f"No surge detected in {n_matches:,} matches. Surge only triggers in some competitive rounds (usually later "
                      "rounds and finals), so this fills in as those matches are collected.")
        r.metric("Surge episodes", "0")
        r.notes += [
            "Surge only triggers in some competitive rounds, when more players are alive than the round allows at a "
            "storm phase. Early qualifier rounds often never trigger it; later rounds and finals do.",
            f"Detection looks for {MIN_PLAYERS}+ players inside the safe zone losing health in the same second with no "
            "player hitting them. Finding none in matches without surge is the expected result.",
        ]
        conclude_without_data(r, ctx, recommended=20, note=f"No surge detected in {n_matches:,} matches. Surge triggers only "
                              "in some competitive rounds; collect later rounds and finals to study it.")
        return r

    # Use the damage window surge actually counts (measured on the Surge study) everywhere on this page, not whole-match damage.
    from .surge_study import WINDOWS, measured
    m = measured(ctx)
    window = "whole match so far"
    if m is not None and len(m["per"]):
        per = m["per"].assign(dealt=m["per"]["dealt_w"])
        window = WINDOWS[m["best"]].lower()
        ep = ep.drop(columns=[c for c in ("max_dealt_surged", "min_dealt_safe") if c in ep])
        ep = ep.merge(per[per["surged"]].groupby("episode")["dealt"].max().rename("max_dealt_surged"), left_on="episode", right_index=True, how="left")
        ep = ep.merge(per[~per["surged"]].groupby("episode")["dealt"].min().rename("min_dealt_safe"), left_on="episode", right_index=True, how="left")
    per_m = ep.groupby("match_id").size()
    r.headline = (f"Surge detected in {len(per_m):,} of {n_matches:,} matches ({len(ep):,} episodes). Counting damage {window}, "
                  f"surged players had dealt a median {per.loc[per['surged'], 'dealt'].median():.0f}, against "
                  f"{per.loc[~per['surged'], 'dealt'].median():.0f} for players it skipped.")
    r.notes.append(f"Damage on this page is counted {window}: the window the Surge study found best explains who gets surged.")
    r.metric("Surge episodes", f"{len(ep):,}")
    r.metric("Matches with surge", f"{len(per_m):,}")
    r.metric("Players alive at surge", f"{ep['alive'].median():.0f}", "Median at the first tick of an episode")
    r.metric("Players hit per episode", f"{ep['surged'].median():.0f}")
    r.metric("Damage per tick", f"{ep['per_tick'].median():.0f}", "Median health or shield lost per surge tick")

    # ---- does surge pick the lowest damage-dealers? per-episode rank test, combined across episodes
    ps = []
    for _, g in per.groupby("episode"):
        if g["surged"].sum() >= 2 and (~g["surged"]).sum() >= 2:
            ps.append(sps.mannwhitneyu(g.loc[g["surged"], "dealt"], g.loc[~g["surged"], "dealt"], alternative="less").pvalue)
    if ps:
        comb = sps.combine_pvalues(ps, method="stouffer").pvalue
        r.test("Per episode", "Surge hits players who dealt less damage", len(ps),
               f"surged median {per.loc[per['surged'], 'dealt'].median():.0f} vs safe {per.loc[~per['surged'], 'dealt'].median():.0f}",
               float(comb), alpha, ("Surge targets the lowest damage-dealers, so dealing damage before it is protection",
                                   "Surged players had not dealt less damage than safe players"),
               direction=per.loc[~per["surged"], "dealt"].median() - per.loc[per["surged"], "dealt"].median())
    gap = per.groupby("match_id").apply(
        lambda g: g.loc[g["surged"], "final"].mean() - g.loc[~g["surged"], "final"].mean()
        if g["surged"].sum() >= 2 and (~g["surged"]).sum() >= 2 else np.nan, include_groups=False).dropna()
    if len(gap) >= 3:
        t = sps.ttest_1samp(gap, 0.0)
        r.test("Per match", "Surged players finish worse", len(gap), f"{gap.mean():+.1f} places on average", float(t.pvalue), alpha,
               ("Being surged goes with finishing worse", "Being surged did not go with finishing worse"), direction=gap.mean())

    # ---- per phase: when it triggers and how much damage was enough
    by = ep.groupby("phase").agg(episodes=("episode", "size"), alive=("alive", "median"), hit=("surged", "median"),
                                 per_tick=("per_tick", "median"), max_surged=("max_dealt_surged", "median"),
                                 min_safe=("min_dealt_safe", "median")).reset_index()
    by = by.rename(columns={"phase": "Zone", "episodes": "Episodes", "alive": "Players alive", "hit": "Players hit",
                            "per_tick": "Damage per tick", "max_surged": "Most damage dealt by a surged player",
                            "min_safe": "Least damage dealt by a safe player"})
    r.table("Surge by zone", by.round(0))
    r.notes.insert(0, "Reading the phase table: a player who had dealt more than 'Most damage dealt by a surged player' "
                      "was never surged in that phase. That number is a practical safe target.")
    r.chart("bar", "Surge episodes by zone", [dict(name="Episodes", x=[f"Zone {int(p)}" for p in by["Zone"]], y=by["Episodes"].tolist())],
            y_label="Episodes")
    # Compare players facing the same surge: rank each player's damage within the episode, using the damage window surge
    # actually counts (measured on the Surge study). Absolute whole-match damage mixes early and late surges: players with a
    # lot of whole-match damage are mostly the ones still alive late, when surge hits a bigger share of a smaller lobby.
    from .surge_study import WINDOWS, measured
    m = measured(ctx)
    if m is not None and len(m["per"]):
        pr = m["per"]
        q = pd.cut(pr["rank_w"], [-0.001, 0.25, 0.5, 0.75, 1.0001], labels=["Bottom quarter", "Second quarter", "Third quarter", "Top quarter"])
        g = pr.groupby(q, observed=False)["surged"].mean()
        r.chart("bar", "Chance of being surged, by damage rank within the same surge",
                [dict(name="Surged", x=[str(i) for i in g.index], y=(g * 100).round(1).tolist())],
                x_label=f"Damage dealt ({WINDOWS[m['best']].lower()}), ranked against the other players alive at that surge",
                y_label="% surged")

    conclude(r, ctx, strategy=True, primary=["Per episode", "Per match"], alpha=alpha, recommended=20, single_season=True,
             takeaway_found="Surge behaves predictably: " + "; ".join(t["reading"].lower() for t in r.tests if t["significant"]) + ".",
             takeaway_none="Surge was detected, but no consistent pattern in who it hits yet.",
             next_found=["Use the phase table's safe target as the damage goal before each surge phase.",
                         "Collect more later-round matches (data menu option T): surge is round-specific."],
             next_none=["Collect more later-round matches (data menu option T): surge is round-specific."])
    r.notes += [
        f"Surge is detected, not recorded: {MIN_PLAYERS}+ players inside the zone losing health in the same second with no "
        "player hitting them. Fall damage is individual and the storm is excluded, so false alarms should be rare.",
        "Damage dealt counts damage to players on other teams before the episode starts.",
    ]
    return r
