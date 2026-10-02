"""
What decides tier-1 games? Three views, with the strongest third of lobbies shown against the rest:

1. Players alive at each zone: how crowded endgames are.
2. Loot convergence: players who looted best and worst in their first 3 minutes, compared on the best weapon
   rarity they hold around each later zone. If the lines meet, early loot evens out.
3. Among players alive when zone 6 appears: how much of their finishing (against the others alive then) each
   group of factors explains, measured on matches the model never saw:
     skill    Power Rankings
     early    contested drop, chests and best weapon rarity in the first 3 minutes
     mid      share of zones 2-5 rotations where they fell behind comparable players; storm damage in zones 2-5
     endgame  when zone 6 appears: distance outside it, distance from the current zone's centre, height rank,
              health and shield
   All factors are compared within each match (standardised per match), so lobby differences don't leak in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, register
from ._events_common import RARITY_RANK

GROUPS = {"skill": "Skill", "early": "Early game", "mid": "Mid game", "endgame": "Endgame position"}
FACTORS = {
    "skill": ("skill", "Power Rankings (higher = stronger)"),
    "contested": ("early", "Contested drop"),
    "chests": ("early", "Chests in the first 3 minutes"),
    "rarity3": ("early", "Best weapon rarity at 3 minutes"),
    "behind": ("mid", "Fell behind on rotations (zones 2–5)"),
    "storm": ("mid", "Storm damage in zones 2–5"),
    "outside6": ("endgame", "Distance outside zone 6 when it appeared"),
    "off_centre": ("endgame", "Distance from the zone's centre"),
    "height": ("endgame", "Height rank in the lobby"),
    "hp": ("endgame", "Health + shield"),
}
FOLDS = 5
_CACHE: dict = {}


def _lobby_groups(con) -> pd.Series:
    m = df(con, "SELECT m.match_id, m.lobby_strength FROM matches m JOIN sel USING (match_id)").set_index("match_id")["lobby_strength"]
    if m.notna().mean() < 0.5 or m.dropna().nunique() < 3:
        return pd.Series("All lobbies", index=m.index)
    cut = m.quantile(2 / 3)
    return pd.Series(np.where(m >= cut, "Strongest third of lobbies", "Other lobbies"), index=m.index)


def _players_at_zone6(ctx: Context) -> pd.DataFrame:
    """One row per player alive when zone 6 appears, with every factor and the outcome."""
    from .height import _snapshots
    from .loot import _per_player
    from .rotation import rotations
    from ._events_common import has_tables, unexplained_drops
    con = ctx.con
    base = df(con, """
        WITH t6 AS (SELECT z.match_id, z.finish_shrink_t AS t, z.next_x AS zx, z.next_y AS zy, z.next_r AS zr
                    FROM zones z JOIN sel USING (match_id) WHERE z.phase = 5),
             z6 AS (SELECT z.match_id, z.next_x AS x6, z.next_y AS y6, z.next_r AS r6 FROM zones z JOIN sel USING (match_id) WHERE z.phase = 6),
             a AS (SELECT pl.match_id, pl.id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS final, pl.pr_rank, t6.*
                   FROM players pl JOIN t6 USING (match_id)
                   WHERE NOT coalesce(pl.is_bot, FALSE) AND (pl.death_t IS NULL OR pl.death_t > t6.t))
        SELECT a.*, z6.x6, z6.y6, z6.r6, pos.x, pos.y FROM a JOIN z6 USING (match_id)
        ASOF JOIN positions pos ON a.match_id = pos.match_id AND a.id = pos.id AND a.t >= pos.t
    """).dropna(subset=["final", "x"])
    if base.empty:
        return base
    base["ahead"] = base.groupby("match_id")["final"].rank(pct=True, method="average")
    base["finish"] = 1 - base["ahead"]
    base["skill"] = -np.log10(base["pr_rank"].fillna(100000).clip(lower=1))
    base["outside6"] = ((np.hypot(base["x"] - base["x6"], base["y"] - base["y6"]) - base["r6"]) / 100).clip(lower=0)
    base["off_centre"] = np.hypot(base["x"] - base["zx"], base["y"] - base["zy"]) / base["zr"]

    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "landings" in have and has_tables(con, "chests", "weapons_held"):
        lp = _per_player(ctx, 180)
        if len(lp):
            base = base.merge(lp[["match_id", "id", "opp_150m", "chests", "best_rarity"]], on=["match_id", "id"], how="left")
            base["contested"] = (base["opp_150m"] > 0).astype(float)
            base["rarity3"] = base["best_rarity"]
    elif "landings" in have:
        L = df(con, "SELECT l.match_id, l.id, l.opp_150m FROM landings l JOIN sel USING (match_id)")
        base = base.merge(L, on=["match_id", "id"], how="left")
        base["contested"] = (base["opp_150m"] > 0).astype(float)
    rot, _, _ = rotations(ctx)
    if len(rot):
        b = rot[rot["phase"].between(2, 5)].dropna(subset=["timing"]).groupby(["match_id", "id"])["timing"].apply(lambda s: (s == "Behind").mean())
        base = base.merge(b.rename("behind").reset_index(), on=["match_id", "id"], how="left")
        base["behind"] = base["behind"].fillna(0.0)   # never had to rotate = never fell behind
    if has_tables(con, "health", "damage"):
        d = unexplained_drops(con)
        if len(d):
            s = d[(d["in_storm"] == True) & d["phase"].between(2, 5)].groupby(["match_id", "id"])["lost"].sum()  # noqa: E712
            base = base.merge(s.rename("storm").reset_index(), on=["match_id", "id"], how="left")
            base["storm"] = base["storm"].fillna(0)
        hp = df(con, """WITH t6 AS (SELECT z.match_id, z.finish_shrink_t AS t FROM zones z JOIN sel USING (match_id) WHERE z.phase = 5),
                             h AS (SELECT h.match_id, h.id, h.t, coalesce(h.health, 0) + coalesce(h.shield, 0) AS hp FROM health h JOIN sel USING (match_id)),
                             p AS (SELECT pl.match_id, pl.id, t6.t FROM players pl JOIN t6 USING (match_id))
                        SELECT p.match_id, p.id, h.hp FROM p ASOF JOIN h ON p.match_id = h.match_id AND p.id = h.id AND p.t >= h.t""")
        base = base.merge(hp, on=["match_id", "id"], how="left")
    team = _snapshots(ctx)
    if len(team):
        t6 = team[team["phase"] == 6][["match_id", "team_index", "height_pct"]].rename(columns={"height_pct": "height"})
        base = base.merge(t6, on=["match_id", "team_index"], how="left")
    return base


def _standardise(d: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Within-match z-scores (missing values become the match average)."""
    out = d.copy()
    for c in cols:
        g = out.groupby("match_id")[c]
        out[c] = ((out[c] - g.transform("mean")) / g.transform("std").replace(0, np.nan)).fillna(0.0)
    out["y"] = out["finish"] - out.groupby("match_id")["finish"].transform("mean")
    return out


def _cv_r2(d: pd.DataFrame, cols: list[str], seed: int = 5) -> float:
    """Out-of-sample share of finishing explained (R^2), folds grouped by match."""
    if not cols:
        return np.nan
    matches = d["match_id"].unique()
    fold = dict(zip(np.random.default_rng(seed).permutation(matches), np.arange(len(matches)) % FOLDS))
    f = d["match_id"].map(fold)
    pred = np.zeros(len(d))
    for k in range(FOLDS):
        tr, te = f != k, f == k
        if tr.sum() < 20 or te.sum() == 0:
            continue
        X = d.loc[tr, cols].to_numpy()
        beta = np.linalg.lstsq(X.T @ X + 1.0 * np.eye(len(cols)), X.T @ d.loc[tr, "y"].to_numpy(), rcond=None)[0]
        pred[te.to_numpy()] = d.loc[te, cols].to_numpy() @ beta
    y = d["y"].to_numpy()
    return float(1 - ((y - pred) ** 2).sum() / (y ** 2).sum())


@register("decides", "What decides tier-1 games?",
          "How crowded endgames are, whether early loot evens out, and how much of the finish among players alive at zone 6 "
          "comes from skill, the early game, the mid game and endgame position.",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    groups = _lobby_groups(con)
    order = [g for g in ["Strongest third of lobbies", "Other lobbies", "All lobbies"] if g in set(groups)]

    # ---- 1. how crowded: players alive when each zone appears
    alive = df(con, """
        WITH t AS (SELECT z.match_id, z.phase + 1 AS zone, z.finish_shrink_t AS t FROM zones z JOIN sel USING (match_id))
        SELECT t.match_id, t.zone, count(*) FILTER (WHERE pl.death_t IS NULL OR pl.death_t > t.t) AS alive
        FROM t JOIN players pl USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE) GROUP BY 1, 2""")
    if len(alive):
        alive["group"] = alive["match_id"].map(groups)
        a = alive.groupby(["group", "zone"])["alive"].median().unstack(0)
        a = a[a.index <= 12]
        r.chart("line", "Players still alive when each zone appears",
                [dict(name=g, x=[int(z) for z in a.index], y=a[g].round(0).tolist()) for g in order if g in a],
                x_label="Zone", y_label="Players alive (median)")
        z6 = alive[alive["zone"] == 6].groupby("group")["alive"].median()
        short = {"Strongest third of lobbies": "strongest lobbies", "Other lobbies": "other lobbies", "All lobbies": "all lobbies"}
        r.metric("Alive at zone 6", " · ".join(f"{z6[g]:.0f} ({short[g]})" for g in order if g in z6),
                 "Median players alive when zone 6 appears")

    # ---- 2. loot convergence
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    from ._events_common import has_tables
    if "landings" in have and has_tables(con, "weapons_held", "chests"):
        from .loot import _per_player
        lp = _per_player(ctx, 180).dropna(subset=["best_rarity"])
        if len(lp) >= 50:
            lp["early"] = lp.groupby("match_id")["best_rarity"].rank(pct=True, method="average")
            lp = lp[(lp["early"] <= 1 / 3) | (lp["early"] > 2 / 3)]
            lp["loot_group"] = np.where(lp["early"] > 2 / 3, "Looted best early", "Looted worst early")
            con.register("lootg", lp[["match_id", "id", "loot_group"]])
            w = df(con, """
                WITH t AS (SELECT z.match_id, z.phase + 1 AS zone, z.finish_shrink_t AS t FROM zones z JOIN sel USING (match_id)),
                     w AS (SELECT w.match_id, w.id, w.t, w.rarity FROM weapons_held w JOIN sel USING (match_id) WHERE w.category = 'weapon')
                SELECT t.zone, g.loot_group, w.match_id, w.id, max(CASE w.rarity WHEN 'common' THEN 1 WHEN 'uncommon' THEN 2 WHEN 'rare' THEN 3
                       WHEN 'epic' THEN 4 WHEN 'legendary' THEN 5 WHEN 'mythic' THEN 6 END) AS best
                FROM t JOIN w ON w.match_id = t.match_id AND w.t BETWEEN t.t - 90 AND t.t
                JOIN lootg g ON g.match_id = w.match_id AND g.id = w.id
                GROUP BY 1, 2, 3, 4""")
            if len(w):
                c = w.groupby(["zone", "loot_group"])["best"].mean().unstack()
                c = c[c.index <= 11]
                r.chart("line", "Does early loot even out?",
                        [dict(name=g, x=[int(z) for z in c.index], y=c[g].round(2).tolist()) for g in ["Looted best early", "Looted worst early"] if g in c],
                        x_label="Zone", y_label="Best weapon rarity in hand (1 common – 5 legendary)")
                gap = (c.get("Looted best early") - c.get("Looted worst early")).dropna()
                if len(gap) >= 2:
                    first, z_close = gap.iloc[0], next((int(z) for z, v in gap.items() if v <= 0.15 * max(gap.iloc[0], 1e-9) or v <= 0.1), None)
                    r.metric("Loot gap closes by", f"zone {z_close}" if z_close else "not within the match",
                             "The zone by which players who looted worst early hold about the same weapon quality as those who looted best")

    # ---- 3. what decides the finish among players alive at zone 6
    key = ("decides",) + tuple(sorted(df(con, "SELECT match_id FROM sel")["match_id"]))
    base = _CACHE.get(key)
    if base is None:
        base = _players_at_zone6(ctx)
        _CACHE.clear()
        _CACHE[key] = base
    if base.empty or base["match_id"].nunique() < 10:
        r.headline = "Not enough matches with players alive at zone 6 to measure what decides the finish (at least 10)."
        conclude(r, ctx, primary=[], alpha=alpha, recommended=100, descriptive=r.headline)
        return r
    base["group"] = base["match_id"].map(groups)
    cols = [c for c in FACTORS if c in base and base[c].notna().mean() > 0.5 and base[c].nunique() > 1]
    rows, series = [], []
    for g in order:
        d = _standardise(base[base["group"] == g], cols)
        if d["match_id"].nunique() < 10:
            continue
        shares = {grp: max(0.0, _cv_r2(d, [c for c in cols if FACTORS[c][0] == grp])) for grp in GROUPS}
        total = max(0.0, _cv_r2(d, cols))
        series.append(dict(name=g, x=list(GROUPS.values()) + ["All together"],
                           y=[round(shares[k] * 100, 1) for k in GROUPS] + [round(total * 100, 1)]))
        if g == order[0]:
            X = d[cols].to_numpy()
            beta = np.linalg.lstsq(X.T @ X + np.eye(len(cols)), X.T @ d["y"].to_numpy(), rcond=None)[0]
            for c, b in zip(cols, beta):
                rho = d.groupby("match_id").apply(lambda q: spearman(q[c], q["finish"]) if q[c].nunique() > 1 and len(q) >= 6 else np.nan,
                                                  include_groups=False).dropna()
                t = ttest_mean(rho, 0.0)
                grp, label = FACTORS[c]
                if t["n"] >= 3:
                    r.test(GROUPS[grp], label, int(t["n"]), f"{b * 100:+.1f} points of finishing per step", t["p"], alpha,
                           (f"More of this ({label.lower()}) goes with finishing better among players alive at zone 6",
                            f"More of this ({label.lower()}) goes with finishing worse among players alive at zone 6"), direction=t["mean"])
                p = t["p"]
                rows.append({"Factor": label, "Part of the game": GROUPS[grp], "Effect": f"{b * 100:+.1f} pts",
                             "Verdict": ("Strong evidence" if p < alpha else "Some evidence" if p < 0.05 else "No clear link") if p == p else "Too few",
                             "_abs": abs(b)})
            top = shares
            r.metric("Explained by endgame position", f"{shares['endgame']:.0%}", "Share of the finish (among players alive at zone 6) "
                     "explained by position when zone 6 appears, on matches the model never saw")
            r.metric("Explained by the early game", f"{shares['early']:.0%}", "The same for drop and early loot")
            r.metric("Explained by skill", f"{shares['skill']:.0%}", "The same for Power Rankings")
    if series:
        r.chart("bar", "What decides the finish, among players alive at zone 6", series,
                x_label="Share of the finish explained (%)", horizontal=True)
    if rows:
        r.table("Each factor among players alive at zone 6",
                pd.DataFrame(rows).sort_values("_abs", ascending=False).drop(columns="_abs"))
    if series:
        s0 = dict(zip(series[0]["x"], series[0]["y"]))
        ranked = [k for k in sorted(GROUPS.values(), key=lambda k: -s0.get(k, 0)) if s0.get(k, 0) >= 1]
        if ranked:
            rest = [k for k in GROUPS.values() if k not in ranked]
            r.headline = (f"In the {series[0]['name'].lower()}, among players alive at zone 6, the finish is explained most by "
                          + ", then ".join(f"{k.lower()} ({s0[k]:.0f}%)" for k in ranked[:3])
                          + (f"; {', '.join(k.lower() for k in rest)} explain{'s' if len(rest) == 1 else ''} almost none of it." if rest else "."))
        else:
            r.headline = "Among players alive at zone 6, none of the measured factors explains much of the finish yet."
    advice = r.headline
    if series:
        s0 = dict(zip(series[0]["x"], series[0]["y"]))
        e, early, mid, sk = s0.get("Endgame position", 0), s0.get("Early game", 0), s0.get("Mid game", 0), s0.get("Skill", 0)
        if e >= 2 * max(early, 0.5) and e >= max(mid, sk):
            advice = ("Where players stand when zone 6 appears decides far more than how they dropped or looted: plan and practise "
                      "the rotations into zones 4–6 first.")
        elif early >= max(e, mid, sk):
            advice = "The early game still decides a lot here: the drop and early loot carry into the endgame."
        elif mid >= max(e, early, sk):
            advice = "Mid-game rotations decide the most: falling behind in zones 2–5 is costly even for players who survive to zone 6."
        elif sk >= max(e, early, mid):
            advice = "Skill decides the most in this group; decisions still add points, but less than in other lobbies."
    conclude(r, ctx, strategy=True, primary=list(GROUPS.values()), alpha=alpha, recommended=100, single_season=False,
             takeaway_found=advice, takeaway_none="No factor clearly decides the finish among players alive at zone 6 yet.",
             next_found=["If endgame position explains the most, practise and plan zones 4–6 above early looting.",
                         "Use 'Does early loot even out?' to decide how much early looting is worth."],
             next_none=["Add more strong-lobby matches."])
    r.notes += [
        "'Strongest third of lobbies' is the top third of the selected matches by lobby strength (Power Rankings top-1,000 share).",
        "The finish is how many of the players alive at zone 6 a player finished ahead of. Factors are compared within each match.",
        "'Share explained' is measured on matches the model never saw (5-fold by match), so it isn't inflated by overfitting. "
        "Placement is noisy in any battle royale, so even the most important factors explain a modest share; compare the bars "
        "with each other.",
        "Endgame position is measured when zone 6 appears; materials aren't in replays, so they can't be included.",
    ]
    return r
