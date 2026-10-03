"""
Surge study: how competitive surge actually works in the selected matches, and how top players handle it.

1. The rule: which damage surge counts. Every candidate in surge_rule.py is tried on every surge episode: damage
   dealt or net damage (dealt minus taken), per player or per team, over several windows (whole match, since the
   zone appeared, since the previous surge check, last 60 / 120 / 180 s). The candidate that best separates surged
   from safe players (mean AUC) is the rule the game most likely uses; Epic's announced rule (team net damage)
   wins near-ties. Everything below, and the Surge page, the team audit and the points model, uses it.
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
from .. import surge_rule as sr
from . import ALPHA_PARAM, Context, Param, register
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


_MEASURED: dict = {}


def measured(ctx: Context) -> dict | None:
    """Surge episodes with the rule surge most likely uses, measured from the selected matches.
    Returns ep (episodes with zone, appear time, cut-off), per (players per episode with score_w, the rule's score, and
    rank_w within the episode), best ((window, measure, level)), label (the rule in words), mean_auc and auc (per
    candidate), hits, team_agree (share of teams whose members were surged together). Cached per selection."""
    key = tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"]))
    if key in _MEASURED:
        return _MEASURED[key]
    from .surge import surge_episodes
    if not has_tables(ctx.con, "health", "damage"):
        return None
    ep, per = surge_episodes(ctx)
    if ep.empty or per.empty:
        return None
    zones = df(ctx.con, "SELECT z.match_id, z.phase, z.finish_shrink_t FROM zones z JOIN sel USING (match_id)")
    reveal = {(m, int(p) + 1): float(t) for m, p, t in zip(zones["match_id"], zones["phase"], zones["finish_shrink_t"])}
    ep = ep.sort_values(["match_id", "t0"]).reset_index(drop=True)
    ep["zone"] = ep["phase"].astype(int)
    ep["appear"] = [reveal.get((m, z), np.nan) for m, z in zip(ep["match_id"], ep["zone"])]
    ep["prev_t1"] = ep.groupby("match_id")["t1"].shift(1)
    hits = player_hits(ctx.con)
    by_match = {m: g for m, g in hits.groupby("match_id")}
    cands = [(w, m_, lv) for w in WINDOWS for m_ in sr.MEASURES for lv in sr.LEVELS]
    auc = {c: [] for c in cands}
    score: dict = {c: {} for c in cands}
    for _, e in ep.iterrows():
        g = per[per["episode"] == e["episode"]]
        hm = by_match.get(e["match_id"], hits.iloc[0:0])
        hm = hm[hm["t"] < e["t0"]]
        starts = {"match": -1e9, "zone": e["appear"] if e["appear"] == e["appear"] else -1e9,
                  "prev": e["prev_t1"] if e["prev_t1"] == e["prev_t1"] else -1e9, "60": e["t0"] - 60, "120": e["t0"] - 120, "180": e["t0"] - 180}
        surged = g["surged"].to_numpy(bool)
        scorable = surged.sum() >= 2 and (~surged).sum() >= 2
        for w, t_start in starts.items():
            for (m_, lv), v in sr.scores(hm[hm["t"] >= t_start], g["id"], g["team_index"]).items():
                score[(w, m_, lv)][e["episode"]] = dict(zip(g["id"], v))
                if scorable:
                    auc[(w, m_, lv)].append(_auc(v[~surged], v[surged]))
    mean_auc = {c: float(np.nanmean(v)) if v else np.nan for c, v in auc.items()}
    best = sr.choose(mean_auc)
    per = per.copy()
    per["score_w"] = [score[best].get(e_, {}).get(i, np.nan) for e_, i in zip(per["episode"], per["id"])]
    per = per.dropna(subset=["score_w"])
    # 0 lowest – 1 highest, within the same surge; tied players take the lowest rank of their group (scoring nothing is the bottom)
    per["rank_w"] = per.groupby("episode")["score_w"].rank(pct=True, method="min")
    cut = per.groupby("episode").apply(lambda g: (g.loc[g["surged"], "score_w"].max() + g.loc[~g["surged"], "score_w"].min()) / 2
                                       if g["surged"].any() and (~g["surged"]).any() else np.nan, include_groups=False).rename("cutoff")
    ep = ep.merge(cut, left_on="episode", right_index=True, how="left")
    out = dict(ep=ep, per=per, best=best, label=sr.label(WINDOWS[best[0]], best[1], best[2]), mean_auc=mean_auc, auc=auc,
               hits=hits, team_agree=sr.teammates_agree(per))
    _MEASURED.clear()
    _MEASURED[key] = out
    return out


def rule_score_at(hits_by_match: dict, empty: pd.DataFrame, match_id, ids, teams, t_from: float, t_to: float,
                  best: tuple[str, str, str]) -> np.ndarray:
    """The rule's score for these players from hits in [t_from, t_to] of one match."""
    hm = hits_by_match.get(match_id, empty)
    hm = hm[hm["t"].between(t_from, t_to)]
    return sr.scores(hm, ids, teams)[(best[1], best[2])]


APPROACHES = ["Held and hunted", "Held passively", "Rotated early, then held", "Rotated late"]
HEIGHTS = ["On the ground", "Low build (3–10 m)", "Tall base (10–25 m)", "Very tall (25 m+)"]
SPOTS = ["Centre half", "Inner", "Edge, inside", "Just outside", "Far outside"]
EARLY_S = 20        # "rotated early": inside the new zone at least this long before the check


def _land(ctx: Context):
    from .. import zone_model as zm
    from .zone_forecast import _load as zf_load
    zf = zf_load(ctx)
    if zf:
        return zf["land"]
    season = df(ctx.con, "SELECT m.season FROM matches m JOIN sel USING (match_id) LIMIT 1")
    if len(season):
        cells = df(ctx.con, zm.land_cells_sql(season["season"].iat[0]))
        return zm.LandMap(cells) if len(cells) >= 50 else None
    return None


def _pct(v) -> str:
    return "–" if v is None or v != v else f"{v:.0%}"


@register("surge_study", "Surge study",
          "Which damage surge counts, when it hits and the cut-off by zone; then where and how players capture surge damage: "
          "sitting in zone hunting, sitting passively, rotating early and building a base, or rotating late, at every base height.",
          params=[ALPHA_PARAM, Param("match", "Game to draw", "match", ""),
                  Param("zone", "Zone to draw", "zone", 5, [{"value": z, "label": f"Zone {z}"} for z in range(2, 12)])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    if not has_tables(con, "health", "damage"):
        r.headline = "No in-match health and damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    m = measured(ctx)
    if m is None:
        r.headline = "No surge detected in these matches. Surge triggers in some competitive rounds (often later rounds and finals)."
        conclude_without_data(r, ctx)
        return r
    ep, per, best, mean_auc, auc, hits = m["ep"], m["per"], m["best"], m["mean_auc"], m["auc"], m["hits"]
    rule = m["label"]
    hits_by_match = {mm: g for mm, g in hits.groupby("match_id")}

    # ---- 1. the rule
    order = sorted(mean_auc, key=lambda c: -(mean_auc[c] if mean_auc[c] == mean_auc[c] else -1))
    r.table("Which damage surge counts", pd.DataFrame({
        "Measure": [sr.MEASURES[c[1]].capitalize() for c in order],
        "Counted per": [sr.LEVELS[c[2]] for c in order],
        "Damage window": [WINDOWS[c[0]] for c in order],
        "How well it separates surged from safe players": [f"{mean_auc[c]:.2f}" if mean_auc[c] == mean_auc[c] else "–" for c in order],
        "Episodes": [int(np.sum(~np.isnan(auc[c]))) for c in order],
        "Epic's announced rule": ["Yes" if c[1:] == sr.OFFICIAL else "" for c in order],
        "": ["Used" if c == best else "" for c in order]}))
    r.metric("Damage surge counts", rule[0].upper() + rule[1:],
             f"The rule that best separates surged from safe players (score {mean_auc[best]:.2f}; 1.00 = perfectly, 0.50 = no better "
             f"than chance). Epic's announced rule, team net damage, is used unless another rule separates them by more than "
             f"{sr.TIE_MARGIN:.2f}." if mean_auc[best] == mean_auc[best] else "")
    ta = m.get("team_agree", np.nan)
    if ta == ta:
        r.metric("Teammates surged together", f"{ta:.0%}", "Of the teams with two or more players alive at a surge, the share where "
                 "all of them were surged or none were. Near 100% means surge picks teams, not players.")

    # ---- 2. when and how much
    zones = df(con, "SELECT z.match_id, z.phase, z.start_shrink_t, z.finish_shrink_t, z.next_x, z.next_y, z.next_r, z.cur_x, z.cur_y, z.cur_r "
                    "FROM zones z JOIN sel USING (match_id)")
    reveal = {(mm, int(p) + 1): float(t) for mm, p, t in zip(zones["match_id"], zones["phase"], zones["finish_shrink_t"])}
    shrink = {(mm, int(p)): float(t) for mm, p, t in zip(zones["match_id"], zones["phase"], zones["start_shrink_t"])}
    ep = ep.assign(after_reveal_s=ep["t0"] - ep["appear"],
                   vs_shrink_s=ep["t0"] - [shrink.get((mm, z), np.nan) for mm, z in zip(ep["match_id"], ep["zone"])],
                   total=ep["ticks"] * ep["per_tick"])
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
            y_label=f"Surge score ({rule})")
    r.metric("Surge episodes", f"{len(ep):,}", f"In {ep['match_id'].nunique()} matches")
    r.metric("Typical cut-off", f"{ep['cutoff'].median():.0f}", f"Median {rule} that kept players safe")

    # ---- 3. every player alive when a surge zone appeared, at its first check
    first = ep.dropna(subset=["appear"]).groupby(["match_id", "zone"]).agg(t_check=("t0", "min"), appear=("appear", "first"),
                                                                           episode=("episode", "first"), cutoff=("cutoff", "first")).reset_index()
    first = first[(first["t_check"] - first["appear"]) >= 15]
    zc = zones.assign(zone=zones["phase"].astype(int))[["match_id", "zone", "next_x", "next_y", "next_r", "cur_x", "cur_y", "cur_r"]]
    first = first.merge(zc, on=["match_id", "zone"], how="left")
    rows = pd.DataFrame()
    if len(first):
        con.register("ss_first", first)
        rows = df(con, """
            WITH a AS (SELECT f.*, p.id, p.team_index, p.death_t, coalesce(p.team_placement, p.placement) AS final
                       FROM ss_first f JOIN players p ON p.match_id = f.match_id
                       WHERE NOT coalesce(p.is_bot, FALSE) AND (p.death_t IS NULL OR p.death_t > f.appear)),
                 s AS (SELECT a.*, pos.x AS x0, pos.y AS y0 FROM a ASOF JOIN positions pos ON pos.match_id = a.match_id AND pos.id = a.id AND a.appear >= pos.t),
                 e AS (SELECT s.*, pos.x AS x1, pos.y AS y1, pos.z AS z1 FROM s ASOF JOIN positions pos
                       ON pos.match_id = s.match_id AND pos.id = s.id AND s.t_check >= pos.t),
                 inside AS (SELECT a.match_id, a.id, a.zone, min(p.t) AS t_in FROM a JOIN positions p ON p.match_id = a.match_id AND p.id = a.id
                            AND p.t BETWEEN a.appear AND a.t_check
                            WHERE sqrt(power(p.x - a.next_x, 2) + power(p.y - a.next_y, 2)) <= a.next_r GROUP BY 1, 2, 3)
            SELECT e.*, inside.t_in FROM e LEFT JOIN inside USING (match_id, id, zone)""")
        rows = rows.dropna(subset=["x0", "x1", "final"])
        rows = rows[rows["death_t"].isna() | (rows["death_t"] > rows["t_check"] - 5)]
    if len(rows) >= 30:
        h = hits[["match_id", "attacker_id", "t", "amount"]]
        con.register("ss_rows", rows[["match_id", "id", "appear", "t_check"]])
        con.register("ss_hits", h)
        got = df(con, """SELECT r.match_id, r.id, r.appear, coalesce(sum(h.amount), 0) AS dealt_before_check FROM ss_rows r
                         LEFT JOIN ss_hits h ON h.match_id = r.match_id AND h.attacker_id = r.id AND h.t BETWEEN r.appear AND r.t_check
                         GROUP BY 1, 2, 3""")
        rows = rows.merge(got, on=["match_id", "id", "appear"], how="left")
        land = _land(ctx)
        ground = np.full(len(rows), np.nan)
        if land is not None:
            _, ground = land.lookup(rows["x1"].to_numpy(float), rows["y1"].to_numpy(float))
        rows["built_m"] = np.clip((rows["z1"].to_numpy(float) - np.nan_to_num(ground, nan=np.nanmedian(ground) if np.isfinite(ground).any() else 0)) / 100, 0, None)
        rows["height"] = pd.cut(rows["built_m"], [-1, 3, 10, 25, 1e9], labels=HEIGHTS)
        rows["moved_m"] = np.hypot(rows["x1"] - rows["x0"], rows["y1"] - rows["y0"]) / 100
        early = rows["t_in"].notna() & (rows["t_in"] <= rows["t_check"] - EARLY_S)
        rows["approach"] = np.where(rows["moved_m"] < MOVED_M, np.where(rows["dealt_before_check"] >= HUNT_DMG, APPROACHES[0], APPROACHES[1]),
                                    np.where(early, APPROACHES[2], APPROACHES[3]))
        rows["radial"] = np.hypot(rows["x1"] - rows["next_x"], rows["y1"] - rows["next_y"]) / rows["next_r"]
        rows["spot"] = pd.cut(rows["radial"], [-1, 0.5, 0.85, 1.0, 1.25, 1e9], labels=SPOTS)
        rows = rows.merge(per[["episode", "id", "surged"]], on=["episode", "id"], how="left")
        rows["surged"] = rows["surged"].fillna(False).astype(bool)
        nxt = {(mm, z): reveal.get((mm, z + 1), np.inf) for mm, z in zip(rows["match_id"], rows["zone"])}
        rows["out_in_zone"] = [d == d and d <= nxt[(mm, z)] for d, mm, z in zip(rows["death_t"], rows["match_id"], rows["zone"])]
        rows["safe_alive"] = ~rows["surged"] & ~rows["out_in_zone"]
        rows["dpm"] = rows["dealt_before_check"] / ((rows["t_check"] - rows["appear"]) / 60)
        rows["top10"] = rows["final"] <= 10

        # the main answer: approach x base height -> safe from surge and still alive
        g = rows.groupby(["approach", "height"], observed=False)
        cell = g.agg(n=("id", "size"), safe=("surged", lambda v: 1 - v.mean()), out=("out_in_zone", "mean"),
                     ok=("safe_alive", "mean"), dpm=("dpm", "median")).reset_index()
        cell.loc[cell["n"] < 10, ["safe", "out", "ok", "dpm"]] = np.nan
        series = []
        for hgt in HEIGHTS:
            c = cell[cell["height"] == hgt].set_index("approach").reindex(APPROACHES)
            series.append(dict(name=hgt, x=APPROACHES, y=(c["ok"] * 100).round(0).tolist()))
        r.chart("bar", "Safe from surge and still alive, by approach and surge-base height", series,
                y_label="% safe from surge and alive at the next zone", x_label="What they did between the zone appearing and the surge check")
        tbl = cell.assign(approach=pd.Categorical(cell["approach"], APPROACHES), height=pd.Categorical(cell["height"], HEIGHTS)) \
            .sort_values(["approach", "height"])
        r.table("Approach and base height: what worked", pd.DataFrame({
            "Approach": tbl["approach"].astype(str), "Base height at the check": tbl["height"].astype(str), "Players": tbl["n"].astype(int),
            "Safe from surge": [_pct(v) for v in tbl["safe"]], "Eliminated before the next zone": [_pct(v) for v in tbl["out"]],
            "Safe and alive": [_pct(v) for v in tbl["ok"]], "Damage per minute": [f"{v:.0f}" if v == v else "–" for v in tbl["dpm"]]}))

        # where the damage is captured: position relative to the new zone, ground vs built
        rows["built"] = np.where(rows["built_m"] >= 3, "Built up (3 m+)", "On the ground")
        sp = rows.groupby(["spot", "built"], observed=False)["dpm"].median().unstack()
        r.chart("bar", "Where surge damage is captured: position in the new zone",
                [dict(name=col, x=SPOTS, y=sp[col].reindex(SPOTS).round(0).tolist()) for col in ["On the ground", "Built up (3 m+)"] if col in sp],
                y_label="Damage per minute before the check (median)", x_label="Position relative to the new zone at the check")

        # the best surge capturers vs everyone
        top = rows[rows["dealt_before_check"] >= rows["dealt_before_check"].quantile(0.9)]
        def prof(d):
            return [f"{(d['radial'] <= 1).mean():.0%}", f"{d['radial'].between(0.85, 1.25).mean():.0%}", f"{d['built_m'].median():.0f} m",
                    f"{(d['built_m'] >= 10).mean():.0%}"] + [f"{(d['approach'] == a).mean():.0%}" for a in APPROACHES] + \
                   [f"{d['safe_alive'].mean():.0%}", f"{d['dpm'].median():.0f}"]
        labels = ["Inside the new zone at the check", "At the zone's edge (85–125% of its radius)", "Base height (median)", "Built 10 m+"] + \
                 [f"Approach: {a.lower()}" for a in APPROACHES] + ["Safe and alive", "Damage per minute"]
        r.table("Who captures the most surge damage: the top 10% vs everyone", pd.DataFrame({
            "": labels, "Top 10% damage before checks": prof(top), "Everyone": prof(rows),
            "Top-10 finishers": prof(rows[rows["top10"]]) if rows["top10"].any() else ["–"] * len(labels)}))

        # what top finishers do
        mix = rows.groupby(["top10", "approach"]).size().unstack(fill_value=0)
        mix = mix.div(mix.sum(1), axis=0).reindex(columns=APPROACHES, fill_value=0)
        r.chart("bar", "What players do before a surge check: top-10 finishers vs the rest",
                [dict(name=a, x=["Top-10 finishers" if i else "Everyone else" for i in mix.index], y=(mix[a] * 100).round(0).tolist()) for a in APPROACHES],
                y_label="% of players")

        # tests, within the same zone of the same match, among players at risk when the zone appeared
        e_ = ep.set_index("episode")
        start_score = pd.Series(np.nan, index=rows.index)
        for (mm, _z), q in rows.groupby(["match_id", "zone"]):
            ee = e_.loc[q["episode"].iat[0]]
            ap = float(q["appear"].iat[0])
            w0 = {"match": -1e9, "zone": ap, "prev": ee["prev_t1"] if ee["prev_t1"] == ee["prev_t1"] else -1e9,
                  "60": ap - 60, "120": ap - 120, "180": ap - 180}[best[0]]
            start_score.loc[q.index] = rule_score_at(hits_by_match, hits.iloc[0:0], mm, q["id"], q["team_index"], w0, ap, best)
        rows["at_risk"] = start_score.to_numpy(float) <= rows["cutoff"].fillna(np.inf).to_numpy(float)
        risk = rows[rows["at_risk"]]

        def compare(mask_a, mask_b, label, readings):
            diff = risk.groupby(["match_id", "zone"]).apply(
                lambda q: q.loc[mask_a(q), "safe_alive"].mean() - q.loc[mask_b(q), "safe_alive"].mean()
                if mask_a(q).sum() >= 2 and mask_b(q).sum() >= 2 else np.nan, include_groups=False).dropna()
            t = ttest_mean(diff, 0.0)
            if t["n"] >= 3:
                r.test("At risk when the zone appeared", label, int(t["n"]), f"{t['mean'] * 100:+.0f} points safe and alive", t["p"], alpha,
                       readings, direction=t["mean"])
        compare(lambda q: q["approach"] == APPROACHES[2], lambda q: q["approach"] == APPROACHES[0], "Rotating early, then holding vs holding and hunting",
                ("Rotating early and holding in the new zone kept more players safe and alive than holding and hunting where they were",
                 "Holding and hunting where they were kept more players safe and alive than rotating early"))
        compare(lambda q: q["approach"] == APPROACHES[0], lambda q: q["approach"] == APPROACHES[1], "Holding and hunting vs holding passively",
                ("Hunting from a held spot kept more players safe and alive than sitting passively",
                 "Sitting passively kept more players safe and alive than hunting from a held spot"))
        compare(lambda q: q["built_m"] >= 10, lambda q: q["built_m"] < 3, "A tall base (10 m+) vs the ground",
                ("A tall surge base kept more players safe and alive than staying on the ground",
                 "Staying on the ground kept more players safe and alive than a tall surge base"))
        compare(lambda q: q["approach"] == APPROACHES[3], lambda q: q["approach"] != APPROACHES[3], "Rotating late vs everything else",
                ("Rotating late kept more players safe and alive than the other approaches",
                 "Rotating late left fewer players safe and alive than the other approaches"))

        # the map: everyone at the moment of one zone's surge check
        games = sorted(rows["match_id"].unique())
        mid = ctx.params.get("match") if ctx.params.get("match") in games else games[0]
        want = int(ctx.params.get("zone") or 5)
        zs = sorted(rows[rows["match_id"] == mid]["zone"].unique())
        zk = want if want in zs else zs[0]
        q = rows[(rows["match_id"] == mid) & (rows["zone"] == zk)]
        top_cut = q["dealt_before_check"].quantile(0.75) if len(q) else 0
        def pts(d, name, color, size):
            return dict(name=name, x=d["x1"].round(0).tolist(), y=d["y1"].round(0).tolist(), color=color, size=size)
        fz = first[(first["match_id"] == mid) & (first["zone"] == zk)].iloc[0]
        series = [pts(q[q["surged"]], "Surged", "#B4535F", 8),
                  pts(q[~q["surged"] & (q["dealt_before_check"] < top_cut)], "Safe", "#8A97A6", 7),
                  pts(q[~q["surged"] & (q["dealt_before_check"] >= top_cut)], "Safe, top quarter for damage", "#0F766E", 11)]
        circles = [dict(x=float(fz["cur_x"]), y=float(fz["cur_y"]), r=float(fz["cur_r"]), label=f"Zone {zk - 1}"),
                   dict(x=float(fz["next_x"]), y=float(fz["next_y"]), r=float(fz["next_r"]), label=f"Zone {zk}")] if fz["cur_x"] == fz["cur_x"] else \
                  [dict(x=float(fz["next_x"]), y=float(fz["next_y"]), r=float(fz["next_r"]), label=f"Zone {zk}")]
        pad = float(fz["cur_r"] if fz["cur_r"] == fz["cur_r"] else fz["next_r"]) * 1.3
        r.chart("map_points", "Everyone at the surge check", series, circles=circles, zone_circles=True,
                range_x=[float(fz["next_x"]) - pad, float(fz["next_x"]) + pad], range_y=[float(fz["next_y"]) - pad, float(fz["next_y"]) + pad])
        r.notes.insert(0, f"Map: match {mid}, zone {zk}, at its first surge check.")
        r.metric("Best approach (safe and alive)", (lambda c: f"{c.iloc[0]['approach']}, {c.iloc[0]['height'].lower()}" if len(c) else "–")(
            cell.dropna(subset=["ok"]).sort_values("ok", ascending=False)), "The approach and base height with the highest share safe from "
            "surge and still alive at the next zone (cells with at least 10 players)")

    sig = [x for x in r.tests if x["significant"]]
    r.headline = (f"Surge counts {rule}. It hit in {ep['match_id'].nunique()} matches, a median "
                  f"{ep['after_reveal_s'].median():.0f} s after a zone appeared; the typical cut-off was {ep['cutoff'].median():.0f}.")
    conclude(r, ctx, strategy=True, primary=["At risk when the zone appeared"], alpha=alpha, recommended=50,
             takeaway_found="; ".join(x["reading"] for x in sig) + ".",
             takeaway_none=r.headline + " No approach is clearly better yet for players at risk of surge.",
             next_found=["Use the approach and base-height table to set a default surge plan for each zone.",
                         "Check the map for a few games: where the top damage-gatherers stood when the surge check came."],
             next_none=["Add later-round and final matches, where surge hits more often."])
    r.notes += [
        "Which damage counts: for each episode, players are ranked by every candidate rule (damage dealt or net damage, per player or "
        "per team, over each window); the rule whose ranking best separates surged from safe players is used everywhere. Epic's "
        "announced rule (team net damage) wins near-ties. The cut-off is midway between the highest score of a surged player and the "
        "lowest of a safe one.",
        f"Approaches, from the zone appearing to its first surge check: held = moved under {MOVED_M} m (hunted if they dealt "
        f"{HUNT_DMG}+ damage); rotated early = moved and were inside the new zone at least {EARLY_S} s before the check; rotated late = "
        "moved but weren't.",
        "Base height: height above the natural ground (mapped from where players stood before the storm moved) at the check, so it "
        "measures how tall they had built.",
        "Safe and alive: not hit by that surge and still alive when the next zone appeared. The comparisons only include players at "
        "risk when the zone appeared (at or below the cut-off), within the same zone of the same match.",
    ]
    return r
