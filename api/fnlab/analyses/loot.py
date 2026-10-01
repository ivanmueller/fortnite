"""
Loot: how fast and how well players loot after landing, which drop spots pay off, and whether
early loot predicts placement. Built on chests, pickups, weapons held and landings.

Per player, over the first LOOT_WINDOW_S seconds after landing:
  time to first chest, chests opened, items taken, weapons taken, heals/shields taken,
  best weapon rarity held, and whether they hold both an assault rifle and a shotgun.
Tests use only players still alive at the end of that window, so early eliminations don't
create a fake "little loot, bad finish" link.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, RARITY_RANK, has_tables, weapon_class

RARITY_NAMES = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary", 6: "Mythic"}
MIN_SPOT = 8


def _per_player(ctx: Context, window: float) -> pd.DataFrame:
    con = ctx.con
    L = df(con, "SELECT l.* FROM landings l JOIN sel USING (match_id)")
    if L.empty:
        return L
    L = L[["match_id", "id", "land_t", "poi", "final", "death_t", "opp_150m"] + (["pr_rank"] if "pr_rank" in L else [])]
    ch = df(con, "SELECT c.match_id, c.searched_by AS id, c.t FROM chests c JOIN sel USING (match_id) WHERE c.searched_by IS NOT NULL")
    pk = df(con, "SELECT p.match_id, p.picked_by AS id, p.picked_t AS t, p.category, p.rarity, p.item FROM pickups p "
                 "JOIN sel USING (match_id) WHERE p.picked_by IS NOT NULL AND p.picked_t IS NOT NULL")
    wh = df(con, "SELECT w.match_id, w.id, w.t, w.weapon, w.category, w.rarity FROM weapons_held w JOIN sel USING (match_id) "
                 "WHERE w.category = 'weapon'")
    out = L.copy()

    def window_of(ev: pd.DataFrame) -> pd.DataFrame:
        ev = ev.dropna(subset=["id"]).astype({"id": "int64"}).merge(L[["match_id", "id", "land_t"]], on=["match_id", "id"])
        ev["since"] = ev["t"] - ev["land_t"]
        return ev[(ev["since"] >= -5) & (ev["since"] <= window)]

    c = window_of(ch)
    g = c.groupby(["match_id", "id"])
    out = out.merge(g["since"].min().rename("first_chest_s").reset_index(), how="left", on=["match_id", "id"])
    out = out.merge(g.size().rename("chests").reset_index(), how="left", on=["match_id", "id"])
    p = window_of(pk)
    g = p.groupby(["match_id", "id"])
    out = out.merge(g.size().rename("items").reset_index(), how="left", on=["match_id", "id"])
    out = out.merge(p[p["category"] == "weapon"].groupby(["match_id", "id"]).size().rename("weapons").reset_index(),
                    how="left", on=["match_id", "id"])
    out = out.merge(p[p["category"] == "heal/shield"].groupby(["match_id", "id"]).size().rename("heals").reset_index(),
                    how="left", on=["match_id", "id"])
    w = window_of(wh)
    w["rank"] = w["rarity"].map(RARITY_RANK)
    w["cls"] = w["weapon"].map(weapon_class)
    g = w.groupby(["match_id", "id"])
    out = out.merge(g["rank"].max().rename("best_rarity").reset_index(), how="left", on=["match_id", "id"])
    combo = g["cls"].apply(lambda s: {"assault rifle", "shotgun"} <= set(s)).rename("ar_and_shotgun").reset_index()
    out = out.merge(combo, how="left", on=["match_id", "id"])
    for col in ("chests", "items", "weapons", "heals"):
        out[col] = out[col].fillna(0)
    out["ar_and_shotgun"] = out["ar_and_shotgun"].fillna(False).astype(bool)
    out["survived_window"] = out["death_t"].isna() | (out["death_t"] > out["land_t"] + window)
    return out.dropna(subset=["final"])


def _band(pr: pd.Series) -> pd.Series:
    return pd.cut(pr.fillna(10**7), [0, 1000, 10000, 10**8], labels=["PR top 1,000", "PR 1,001–10,000", "Unranked"])


@register("loot", "Loot",
          "How fast and how well players loot after landing, which drop spots pay off in chests and weapons, "
          "and whether early loot predicts placement.",
          params=[ALPHA_PARAM,
                  Param("window_s", "Looting window (seconds after landing)", "select", 180,
                        [{"value": 120, "label": "2 minutes"}, {"value": 180, "label": "3 minutes"},
                         {"value": 300, "label": "5 minutes"}]),
                  Param("players", "Players", "select", "all",
                        [{"value": "all", "label": "All players"},
                         {"value": "ranked", "label": "Power Rankings top 10,000 only"},
                         {"value": "unranked", "label": "Outside the top 10,000 only"}])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    window = float(ctx.params.get("window_s", 180))
    r = Result()
    if not has_tables(ctx.con, "chests", "pickups", "weapons_held", "landings"):
        r.headline = "No loot data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    d = _per_player(ctx, window)
    who = ctx.params.get("players", "all")
    if who != "all" and "pr_rank" in d and d["pr_rank"].notna().any():
        d = d[d["pr_rank"].notna()] if who == "ranked" else d[d["pr_rank"].isna()]
    if d.empty:
        r.headline = "No landings with loot data in this selection."
        conclude_without_data(r, ctx)
        return r
    s = d[d["survived_window"]]
    mins = f"{window / 60:g} min"

    # ---- tests on survivors of the looting window, one summary per match
    def per_match(col: str) -> dict:
        rho = s.dropna(subset=[col]).groupby("match_id").apply(
            lambda g: spearman(g[col], g["final"]) if g[col].nunique() > 1 and len(g) >= 8 else np.nan, include_groups=False)
        return ttest_mean(rho.dropna(), 0.0)
    for col, name, reading in [
        ("first_chest_s", "Faster first chest, better placement", ("Players who open their first chest sooner finish better",
                                                                   "Players who open their first chest sooner finish worse")),
        ("chests", f"More chests in {mins}, better placement", ("Opening more chests early goes with finishing worse",
                                                               "Opening more chests early goes with finishing better")),
        ("items", f"More items in {mins}, better placement", ("Taking more items early goes with finishing worse",
                                                             "Taking more items early goes with finishing better")),
        ("best_rarity", f"Better weapon rarity at {mins}, better placement", ("Holding higher-rarity weapons early goes with finishing worse",
                                                                             "Holding higher-rarity weapons early goes with finishing better")),
    ]:
        t = per_match(col)
        if t["n"] >= 3:
            r.test("Per match", name, t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha, reading, direction=t.get("mean"))
    gap = s.groupby("match_id").apply(
        lambda g: g.loc[g["ar_and_shotgun"], "final"].mean() - g.loc[~g["ar_and_shotgun"], "final"].mean()
        if g["ar_and_shotgun"].sum() >= 2 and (~g["ar_and_shotgun"]).sum() >= 2 else np.nan, include_groups=False).dropna()
    t = ttest_mean(gap, 0.0)
    if t["n"] >= 3:
        r.test("Per match", f"AR and shotgun by {mins}, better placement", t["n"],
               f"{t['mean']:+.1f} places on average", t["p"], alpha,
               ("Players holding both an assault rifle and a shotgun early finish worse",
                "Players holding both an assault rifle and a shotgun early finish better"), direction=t.get("mean"))

    # ---- numbers
    r.headline = (f"{len(d):,} landings: median {d['first_chest_s'].median():.0f} s to the first chest and "
                  f"{s['chests'].median():.0f} chests in the first {mins} for players who survived it.")
    r.metric("Landings", f"{len(d):,}")
    r.metric("Time to first chest", f"{d['first_chest_s'].median():.0f} s", "Median, from landing")
    r.metric(f"Chests in {mins}", f"{s['chests'].median():.0f}", "Median, players who survived the window")
    r.metric(f"Items taken in {mins}", f"{s['items'].median():.0f}")
    r.metric(f"AR and shotgun by {mins}", f"{s['ar_and_shotgun'].mean():.0%}")
    r.metric(f"Rare or better weapon by {mins}", f"{(s['best_rarity'] >= 3).mean():.0%}")

    # ---- drop spots: loot payoff per named spot
    if d["poi"].notna().any():
        has_pr = "pr_rank" in d and d["pr_rank"].notna().any()
        key = _band(d["pr_rank"]) if has_pr else pd.Series("all", index=d.index)
        d["vs_skill"] = d["final"] - d.groupby(key, observed=True)["final"].transform("mean")
        g = d[d["poi"].notna()].groupby("poi")
        spot = pd.DataFrame({
            "Landings": g.size(),
            "Contested": g["opp_150m"].apply(lambda x: (x > 0).mean()),
            "Time to first chest (s)": g["first_chest_s"].median().round(0),
            f"Chests in {mins}": g.apply(lambda x: x.loc[x["survived_window"], "chests"].median(), include_groups=False),
            f"Rare+ weapon by {mins}": g.apply(lambda x: (x.loc[x["survived_window"], "best_rarity"] >= 3).mean(), include_groups=False),
            f"AR and shotgun by {mins}": g.apply(lambda x: x.loc[x["survived_window"], "ar_and_shotgun"].mean(), include_groups=False),
            "Survived the window": g["survived_window"].mean(),
            "Avg placement": g["final"].mean().round(1),
            "Placement vs similar players": g["vs_skill"].mean().round(1),
        })
        spot = spot[spot["Landings"] >= MIN_SPOT].sort_values("Placement vs similar players")
        for c in ("Contested", f"Rare+ weapon by {mins}", f"AR and shotgun by {mins}", "Survived the window"):
            spot[c] = (spot[c] * 100).round(0).astype("Int64").astype(str) + "%"
        r.table("Loot by drop spot", spot.reset_index().rename(columns={"poi": "Drop spot"}))

    # ---- charts
    r.chart("histogram", "Time from landing to first chest",
            [dict(name="Players", values=d["first_chest_s"].dropna().clip(0, 120).tolist())], bins=24, range=[0, 120],
            x_label="Seconds after landing (over 120 s shown at 120)", y_label="Players")
    cb = pd.cut(s["chests"], [-1, 0, 2, 4, 6, 99], labels=["0", "1–2", "3–4", "5–6", "7+"])
    g = s.groupby(cb, observed=True)["final"]
    r.chart("bar", f"Average placement by chests opened in {mins}",
            [dict(name="Average placement", x=[str(i) for i in g.mean().index], y=g.mean().round(1).tolist())],
            x_label="Chests opened", y_label="Average placement (lower is better)")
    rb = s.dropna(subset=["best_rarity"])
    g = rb.groupby(rb["best_rarity"].map(RARITY_NAMES))["final"]
    order = [v for v in RARITY_NAMES.values() if v in g.mean().index]
    r.chart("bar", f"Average placement by best weapon rarity at {mins}",
            [dict(name="Average placement", x=order, y=g.mean().reindex(order).round(1).tolist())],
            x_label="Best weapon held", y_label="Average placement (lower is better)")

    if "pr_rank" in s and s["pr_rank"].notna().any():
        sk = s.assign(band=_band(s["pr_rank"]), loot=pd.qcut(s["chests"].rank(method="first"), 3, labels=["Fewest chests", "Middle", "Most chests"]))
        tbl = sk.groupby(["band", "loot"], observed=False)["final"].mean().unstack().round(1).reset_index()
        tbl.columns = ["Skill band"] + [f"{c}: avg placement" for c in tbl.columns[1:]]
        r.table("Skill control: placement by early chests within each Power Rankings band", tbl)

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=True,
             takeaway_found="Early loot is linked to placement: " + "; ".join(
                 t["reading"].lower() for t in r.tests if t["group"] == "Per match" and t["significant"]) + ".",
             takeaway_none="No consistent link between early looting and placement in this selection.",
             next_found=["Use 'Loot by drop spot' to weigh a spot's loot against its contest.",
                         "Check the skill-control table: the effect should hold within each Power Rankings band."],
             next_none=["Try strong lobbies only, using the Lobby strength filter."])
    r.notes += [
        f"Tests use players who survived the first {mins} after landing, so early eliminations don't fake the link.",
        "Items count what a player picked up; a few takers are inferred from proximity when the replay didn't record them.",
        "Weapon rarity comes from the item's internal name; 'best weapon' is the best one held in hand in the window.",
    ]
    return r
