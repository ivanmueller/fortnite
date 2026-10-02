"""
Surge study: how competitive surge actually works in the selected matches, and how top players handle it.

1. The rule: which window of damage dealt surge counts. For every surge episode, players are ranked by damage dealt
   over several candidate windows (whole match, since the zone appeared, since the previous surge check, last 60 /
   120 / 180 s); the window that best separates surged from safe players (mean AUC) is the one the game uses, most
   likely. Everything below uses it.
2. When and how much: in which zones surge hits, how long after the zone appears, players alive, how many are hit,
   damage per tick and in total, and the damage that kept players safe (the cut-off) per zone.
3. What players do before a surge check, for everyone alive when the zone appeared:
     held and hunted   moved less than 80 m and dealt 25+ damage before the check
     held passively    moved less than 80 m and dealt less
     rotated           moved 80 m or more
   Shares for top-10 finishers vs the rest.
4. Which approach pays off for players BELOW the cut-off when the zone appeared (grouped by what they did, from the
   same starting situation): surged, eliminated before the next zone, and finish vs the players alive then.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, has_tables, player_hits

WINDOWS = {"match": "Whole match so far", "zone": "Since the zone appeared", "prev": "Since the previous surge check",
           "60": "Last 60 s", "120": "Last 120 s", "180": "Last 180 s"}
MOVED_M = 80
HUNT_DMG = 25
BEHAVIOURS = ["Held and hunted", "Held passively", "Rotated"]


def _auc(safe: np.ndarray, surged: np.ndarray) -> float:
    """P(a safe player dealt more than a surged one): 1 = the window separates them perfectly, 0.5 = no better than chance."""
    if not len(safe) or not len(surged):
        return np.nan
    s, u = safe[:, None], surged[None, :]
    return float(((s > u).sum() + 0.5 * (s == u).sum()) / (len(safe) * len(surged)))


@register("surge_study", "Surge study",
          "How surge works in these matches (which damage counts, when it hits, the cut-off by zone) and what top players do "
          "about it: hold and hunt, hold passively, or rotate, and which pays off when below the cut-off.",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    if not has_tables(con, "health", "damage"):
        r.headline = "No in-match health and damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    from .surge import surge_episodes
    ep, per = surge_episodes(ctx)
    if ep.empty or per.empty:
        r.headline = "No surge detected in these matches. Surge triggers in some competitive rounds (often later rounds and finals)."
        conclude_without_data(r, ctx)
        return r
    zones = df(con, "SELECT z.match_id, z.phase, z.start_shrink_t, z.finish_shrink_t, z.next_x, z.next_y, z.next_r FROM zones z JOIN sel USING (match_id)")
    reveal = {(m, int(p) + 1): float(t) for m, p, t in zip(zones["match_id"], zones["phase"], zones["finish_shrink_t"])}
    shrink = {(m, int(p)): float(t) for m, p, t in zip(zones["match_id"], zones["phase"], zones["start_shrink_t"])}
    ep = ep.sort_values(["match_id", "t0"]).reset_index(drop=True)
    ep["zone"] = ep["phase"].astype(int)
    ep["appear"] = [reveal.get((m, z), np.nan) for m, z in zip(ep["match_id"], ep["zone"])]
    ep["prev_t1"] = ep.groupby("match_id")["t1"].shift(1)
    hits = player_hits(con)

    # ---- 1. which damage window surge counts
    auc = {k: [] for k in WINDOWS}
    dealt_best: dict = {}
    for _, e in ep.iterrows():
        g = per[per["episode"] == e["episode"]]
        if g["surged"].sum() < 2 or (~g["surged"]).sum() < 2:
            continue
        h = hits[(hits["match_id"] == e["match_id"]) & (hits["t"] < e["t0"])]
        starts = {"match": -1e9, "zone": e["appear"] if e["appear"] == e["appear"] else -1e9,
                  "prev": e["prev_t1"] if e["prev_t1"] == e["prev_t1"] else -1e9, "60": e["t0"] - 60, "120": e["t0"] - 120, "180": e["t0"] - 180}
        for k, t_start in starts.items():
            d = h[h["t"] >= t_start].groupby("attacker_id")["amount"].sum()
            v = g["id"].map(d).fillna(0).to_numpy(float)
            auc[k].append(_auc(v[~g["surged"].to_numpy()], v[g["surged"].to_numpy()]))
            dealt_best.setdefault(k, {})[e["episode"]] = dict(zip(g["id"], v))
    mean_auc = {k: float(np.nanmean(v)) if v else np.nan for k, v in auc.items()}
    if not any(v == v for v in mean_auc.values()):
        r.headline = "Surge was detected, but too few players were hit per episode to measure how it chooses them."
        conclude_without_data(r, ctx)
        return r
    best = max(mean_auc, key=lambda k: mean_auc[k] if mean_auc[k] == mean_auc[k] else -1)
    r.table("Which damage surge counts", pd.DataFrame({
        "Damage window": [WINDOWS[k] for k in WINDOWS],
        "How well it separates surged from safe players": [f"{mean_auc[k]:.2f}" if mean_auc[k] == mean_auc[k] else "–" for k in WINDOWS],
        "Episodes": [int(np.sum(~np.isnan(auc[k]))) for k in WINDOWS],
        "": ["Best fit" if k == best else "" for k in WINDOWS]}))
    r.metric("Damage surge counts", WINDOWS[best], f"The window that best separates surged from safe players (score {mean_auc[best]:.2f}; "
             "1.00 = perfectly, 0.50 = no better than chance)")

    # recompute each episode's damage and cut-off with the best window
    per = per.copy()
    per["dealt_w"] = [dealt_best.get(best, {}).get(ep_, {}).get(i, np.nan) for ep_, i in zip(per["episode"], per["id"])]
    per = per.dropna(subset=["dealt_w"])
    cut = per.groupby("episode").apply(lambda g: (g.loc[g["surged"], "dealt_w"].max() + g.loc[~g["surged"], "dealt_w"].min()) / 2
                                       if g["surged"].any() and (~g["surged"]).any() else np.nan, include_groups=False).rename("cutoff")
    ep = ep.merge(cut, left_on="episode", right_index=True, how="left")

    # ---- 2. when and how much
    ep["after_reveal_s"] = ep["t0"] - ep["appear"]
    ep["vs_shrink_s"] = ep["t0"] - [shrink.get((m, z), np.nan) for m, z in zip(ep["match_id"], ep["zone"])]
    ep["total"] = ep["ticks"] * ep["per_tick"]
    bz = ep.groupby("zone").agg(episodes=("episode", "size"), matches=("match_id", "nunique"), after=("after_reveal_s", "median"),
                                vs_shrink=("vs_shrink_s", "median"), alive=("alive", "median"), hit=("surged", "median"),
                                per_tick=("per_tick", "median"), total=("total", "median"), cutoff=("cutoff", "median"))
    r.table("How surge works, by zone", pd.DataFrame({
        "Zone": bz.index, "Surge episodes": bz["episodes"].values, "In matches": bz["matches"].values,
        "Seconds after the zone appears": bz["after"].round(0).values,
        "Relative to the shrink starting": [f"{v:+.0f} s" if v == v else "–" for v in bz["vs_shrink"]],
        "Players alive": bz["alive"].round(0).values, "Players hit": bz["hit"].round(0).values,
        "Damage per tick": bz["per_tick"].round(0).values, "Damage per player hit": bz["total"].round(0).values,
        "Damage that kept players safe": bz["cutoff"].round(0).values}))
    r.chart("bar", "Damage needed to stay safe from surge, by zone",
            [dict(name="Cut-off (median)", x=[f"Zone {z}" for z in bz.index], y=bz["cutoff"].round(0).tolist())],
            y_label=f"Damage dealt ({WINDOWS[best].lower()})")
    r.chart("bar", "When surge checks happen", [dict(name="Seconds after the zone appears", x=[f"Zone {z}" for z in bz.index],
                                                     y=bz["after"].round(0).tolist())], y_label="Seconds after the zone appears")
    r.metric("Surge episodes", f"{len(ep):,}", f"In {ep['match_id'].nunique()} matches")
    r.metric("Typical cut-off", f"{ep['cutoff'].median():.0f} damage", f"Median damage ({WINDOWS[best].lower()}) that kept players safe")

    # ---- 3 & 4. what players do between the zone appearing and its first surge check
    first = ep.dropna(subset=["appear"]).groupby(["match_id", "zone"]).agg(t_check=("t0", "min"), appear=("appear", "first"),
                                                                           episode=("episode", "first"), cutoff=("cutoff", "first")).reset_index()
    first = first[(first["t_check"] - first["appear"]) >= 15]
    if len(first):
        con.register("ss_first", first)
        rows = df(con, """
            WITH a AS (SELECT f.*, p.id, p.team_index, p.death_t, coalesce(p.team_placement, p.placement) AS final, p.pr_rank
                       FROM ss_first f JOIN players p ON p.match_id = f.match_id
                       WHERE NOT coalesce(p.is_bot, FALSE) AND (p.death_t IS NULL OR p.death_t > f.appear)),
                 s AS (SELECT a.*, pos.x AS x0, pos.y AS y0 FROM a ASOF JOIN positions pos ON pos.match_id = a.match_id AND pos.id = a.id AND a.appear >= pos.t),
                 e AS (SELECT s.*, pos.x AS x1, pos.y AS y1 FROM s ASOF JOIN positions pos ON pos.match_id = s.match_id AND pos.id = s.id AND s.t_check >= pos.t)
            SELECT * FROM e""")
        rows = rows.dropna(subset=["x0", "x1", "final"])
        rows = rows[rows["death_t"].isna() | (rows["death_t"] > rows["t_check"] - 5)]
        if len(rows) >= 30:
            rows["moved_m"] = np.hypot(rows["x1"] - rows["x0"], rows["y1"] - rows["y0"]) / 100
            # damage dealt between the reveal and the check, and the window damage at the reveal (the HUD status then)
            got, start = [], []
            for _, q in rows.iterrows():
                h = hits[(hits["match_id"] == q["match_id"]) & (hits["attacker_id"] == q["id"])]
                got.append(float(h[h["t"].between(q["appear"], q["t_check"])]["amount"].sum()))
                e = ep[ep["episode"] == q["episode"]].iloc[0]
                w0 = {"match": -1e9, "zone": q["appear"], "prev": e["prev_t1"] if e["prev_t1"] == e["prev_t1"] else -1e9,
                      "60": q["appear"] - 60, "120": q["appear"] - 120, "180": q["appear"] - 180}[best]
                start.append(float(h[h["t"].between(w0, q["appear"])]["amount"].sum()))
            rows["dealt_before_check"], rows["dealt_at_reveal"] = got, start
            rows["behaviour"] = np.where(rows["moved_m"] >= MOVED_M, "Rotated",
                                         np.where(rows["dealt_before_check"] >= HUNT_DMG, "Held and hunted", "Held passively"))
            # at risk when the zone appeared: at or below the cut-off (with a window that resets at the reveal, that's everyone)
            rows["below"] = rows["dealt_at_reveal"] <= rows["cutoff"]
            surged = per[["episode", "id", "surged"]]
            rows = rows.merge(surged, on=["episode", "id"], how="left")
            rows["surged"] = rows["surged"].fillna(False).astype(bool)
            nxt = {(m, z): reveal.get((m, z + 1), np.inf) for m, z in zip(rows["match_id"], rows["zone"])}
            rows["out_in_zone"] = [d == d and d <= nxt[(m, z)] for d, m, z in zip(rows["death_t"], rows["match_id"], rows["zone"])]
            rows["ahead"] = 1 - rows.groupby(["match_id", "zone"])["final"].rank(pct=True)
            rows["top10"] = rows["final"] <= 10
            # what top finishers do
            mix = rows.groupby(["top10", "behaviour"]).size().unstack(fill_value=0)
            mix = mix.div(mix.sum(1), axis=0).reindex(columns=BEHAVIOURS, fill_value=0)
            r.chart("bar", "What players do before a surge check: top-10 finishers vs the rest",
                    [dict(name=b, x=["Top-10 finishers" if i else "Everyone else" for i in mix.index], y=(mix[b] * 100).round(0).tolist())
                     for b in BEHAVIOURS], y_label="% of players", x_label="")
            # which approach pays off when below the cut-off at the reveal
            below = rows[rows["below"]]
            out = below.groupby("behaviour").agg(players=("id", "size"), surged=("surged", "mean"), out=("out_in_zone", "mean"),
                                                 ahead=("ahead", "mean")).reindex(BEHAVIOURS)
            r.table("Below the cut-off when the zone appeared: what happened, by what they did", pd.DataFrame({
                "What they did before the surge check": BEHAVIOURS, "Players": out["players"].fillna(0).astype(int).values,
                "Surged": [f"{v:.0%}" if v == v else "–" for v in out["surged"]],
                "Eliminated before the next zone": [f"{v:.0%}" if v == v else "–" for v in out["out"]],
                "Finished ahead of the players alive then": [f"{v:.0%}" if v == v else "–" for v in out["ahead"]]}))
            r.chart("bar", "Below the cut-off: which approach paid off",
                    [dict(name=lab, x=BEHAVIOURS, y=(out[col] * 100).round(0).tolist()) for col, lab in
                     (("surged", "Surged"), ("out", "Eliminated before the next zone"), ("ahead", "Finished ahead of players alive then"))],
                    y_label="%")
            # tests: within each zone-hold, holding and hunting vs rotating, for players below the cut-off
            for b_vs in ("Rotated", "Held passively"):
                diff = below.groupby(["match_id", "zone"]).apply(
                    lambda g: g.loc[g["behaviour"] == "Held and hunted", "ahead"].mean() - g.loc[g["behaviour"] == b_vs, "ahead"].mean()
                    if (g["behaviour"] == "Held and hunted").any() and (g["behaviour"] == b_vs).any() else np.nan, include_groups=False).dropna()
                t = ttest_mean(diff, 0.0)
                if t["n"] >= 3:
                    r.test("Below the cut-off", f"Held and hunted vs {b_vs.lower()}", int(t["n"]), f"{t['mean'] * 100:+.0f} points of finish",
                           t["p"], alpha, (f"Below the cut-off, holding and hunting finished better than {b_vs.lower()}",
                                           f"Below the cut-off, holding and hunting finished worse than {b_vs.lower()}"), direction=t["mean"])
            tag_rate = rows[rows["behaviour"] == "Held and hunted"]
            if len(tag_rate):
                rate = (tag_rate["dealt_before_check"] / ((tag_rate["t_check"] - tag_rate["appear"]) / 60)).median()
                r.metric("Damage per minute, holding and hunting", f"{rate:.0f}", "Median damage per minute from a held position before a surge check")
            r.metric("Top-10 finishers holding and hunting", f"{mix.loc[True, 'Held and hunted']:.0%}" if True in mix.index else "–",
                     "Share of top-10 finishers who held position and dealt damage before a surge check")

    sig = [x for x in r.tests if x["significant"]]
    r.headline = (f"Surge counts damage dealt {WINDOWS[best].lower()} (best fit, score {mean_auc[best]:.2f}). It hit in "
                  f"{ep['match_id'].nunique()} matches, a median {ep['after_reveal_s'].median():.0f} s after a zone appeared; the typical cut-off "
                  f"was {ep['cutoff'].median():.0f} damage.")
    conclude(r, ctx, strategy=True, primary=["Below the cut-off"], alpha=alpha, recommended=50,
             takeaway_found=r.headline + " " + "; ".join(x["reading"] for x in sig) + ".",
             takeaway_none=r.headline + " Which approach pays off when below the cut-off isn't clear yet.",
             next_found=["Compare the cut-off by zone with your team's damage at each zone's reveal.",
                         "Use 'Damage per minute, holding and hunting' to judge whether there's time to tag before the check."],
             next_none=["Add later-round and final matches, where surge hits more often."])
    r.notes += [
        "Surge is detected as several players inside the zone losing health in the same second with no player hitting them.",
        "Which damage counts: for each episode, players are ranked by each window's damage; the window whose ranking best "
        "separates surged from safe players is used everywhere else. The cut-off is midway between the most damage of a surged "
        "player and the least of a safe one.",
        f"Before the check: from the zone appearing to its first surge check. Held = moved under {MOVED_M} m; hunted = dealt "
        f"{HUNT_DMG}+ damage in that time; rotated = moved {MOVED_M} m or more.",
        "Below the cut-off: the window's damage at the moment the zone appeared was at or under that episode's cut-off (what the "
        "HUD's 'below' would have shown; if the window resets when the zone appears, everyone starts below). Players are compared "
        "by what they did from the same starting situation.",
    ]
    return r
