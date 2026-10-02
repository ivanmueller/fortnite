"""
Playbook: did players who happened to follow the system do better than their lobby?

Each rule is judged only on what the system would have advised at that moment (no hindsight):
  early_zone   zones 2-4: when the next zone appears, the forecast (trained without this match) gave the player's
               spot at least FOLLOW_P chance of already being inside it
  centre       zones 5+: when the next zone appears, the player is in the inner half of the current zone
  rotation     each zone the player had to rotate in: arrived ahead of players starting a similar distance out
  drop         no opponent landed within 150 m
  height       zones 6+: on mid or high ground (not the lowest third) when the zone starts closing

Outcome: the share of rivals alive at that same moment who finished ahead (0 = finished best of them).
Each rule is compared within the same match and moment, within the same player across matches, and per
Power Rankings band. Two playbook scores combine the rules: early game (judged among players alive at
zone 5) and late game (among players alive at zone 9).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import softmax

from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

FOLLOW_P = 0.8
RULES = {
    "drop": ("Uncontested drop", "Landing"),
    "early_zone": ("Positioned for the next zone", "Zones 2–4"),
    "rotation_early": ("Rotated ahead of comparable players", "Zones 2–4"),
    "centre": ("Held the centre of the zone", "Zones 5+"),
    "rotation_late": ("Rotated ahead of comparable players", "Zones 5–8"),
    "height": ("Held mid or high ground", "Zones 6+"),
}
EARLY = {"drop", "early_zone", "rotation_early"}
LATE = {"centre", "rotation_late", "height"}
_CACHE: dict = {}


def _alive_positions(con, moments: pd.DataFrame) -> pd.DataFrame:
    """Every living human's position at each (match, zone, t) moment."""
    con.register("moments", moments)
    return df(con, """
        WITH m AS (SELECT * FROM moments),
             p AS (SELECT pl.match_id, pl.id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS final, pl.death_t,
                          pl.name, pl.pr_rank
                   FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)),
             a AS (SELECT m.match_id, m.zone, m.t, p.id, p.team_index, p.final, p.name, p.pr_rank FROM m JOIN p USING (match_id)
                   WHERE p.death_t IS NULL OR p.death_t > m.t)
        SELECT a.*, pos.x, pos.y FROM a ASOF JOIN positions pos ON a.match_id = pos.match_id AND a.id = pos.id AND a.t >= pos.t
    """)


def _rivals_ahead(rows: pd.DataFrame, by: list[str]) -> pd.Series:
    return rows.groupby(by)["final"].rank(pct=True, method="average")


def applications(ctx: Context) -> pd.DataFrame:
    """One row per (player, match, rule, zone): followed or not, and the share of rivals alive then who finished ahead."""
    from .zone_forecast import _load
    from .rotation import rotations
    from .height import TIERS, _snapshots
    con, out = ctx.con, []
    players = df(con, """SELECT pl.match_id, pl.id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS final, pl.name, pl.pr_rank
                         FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    if "pr_rank" not in players:
        players["pr_rank"] = np.nan

    # drop: everyone who landed, compared within the match
    L = df(con, "SELECT l.match_id, l.id, l.opp_150m FROM landings l JOIN sel USING (match_id)")
    if len(L):
        d = L.merge(players, on=["match_id", "id"]).dropna(subset=["final"])
        d = d.assign(rule="drop", zone=0, followed=d["opp_150m"] == 0)
        d["ahead"] = _rivals_ahead(d, ["match_id"])
        out.append(d[["match_id", "id", "rule", "zone", "followed", "ahead"]])

    zones = df(con, "SELECT z.match_id, z.phase, z.finish_shrink_t, z.next_x, z.next_y, z.next_r FROM zones z JOIN sel USING (match_id) ORDER BY 1, 2")
    reveal = zones.assign(zone=zones["phase"] + 1, t=zones["finish_shrink_t"])[["match_id", "zone", "t", "next_x", "next_y", "next_r"]] \
        .rename(columns={"next_x": "cur_x", "next_y": "cur_y", "next_r": "cur_r"})   # zone k appears when zone k-1 finishes closing

    # early_zone: forecast probability of already being inside the next zone, from a model that never saw this match
    data = _load(ctx)
    if data is not None:
        lad, best = data["lad"], data["lad"]["best"]
        forecasts = {}
        for st in lad["states"]:
            if 2 <= st["zone"] <= 4:
                fm = lad["fold_models"][lad["folds"][st["match_id"]]]
                _, c = lad["with_prior"](st, fm["prior"].get(st["zone"]))
                forecasts[(st["match_id"], st["zone"])] = (st["cand_x"], st["cand_y"], softmax(fm["models"][best].scores(c, st["type"])), st["next_r"])
        m = reveal[reveal["zone"].between(2, 4)]
        m = m[[(a, b) in forecasts for a, b in zip(m["match_id"], m["zone"])]]
        if len(m):
            pos = _alive_positions(con, m[["match_id", "zone", "t"]]).dropna(subset=["x", "final"])
            p_in = []
            for row in pos.itertuples():
                cx, cy, w, r = forecasts[(row.match_id, row.zone)]
                p_in.append(float(w[np.hypot(cx - row.x, cy - row.y) <= r].sum()))
            pos["p_in"] = p_in
            pos = pos.assign(rule="early_zone", followed=pos["p_in"] >= FOLLOW_P)
            pos["ahead"] = _rivals_ahead(pos, ["match_id", "zone"])
            out.append(pos[["match_id", "id", "rule", "zone", "followed", "ahead"]])

    # centre: zones 5+, inner half of the current zone when the next one appears
    m = reveal[reveal["zone"] >= 5].dropna(subset=["cur_x"])
    if len(m):
        pos = _alive_positions(con, m[["match_id", "zone", "t"]]).dropna(subset=["x", "final"]).merge(m, on=["match_id", "zone", "t"])
        pos = pos.assign(rule="centre", followed=np.hypot(pos["x"] - pos["cur_x"], pos["y"] - pos["cur_y"]) <= 0.5 * pos["cur_r"])
        pos["ahead"] = _rivals_ahead(pos, ["match_id", "zone"])
        out.append(pos[["match_id", "id", "rule", "zone", "followed", "ahead"]])

    # rotation timing (same definitions as the Rotation timing section)
    rot, _, _ = rotations(ctx)
    if len(rot):
        rt = rot.dropna(subset=["timing", "final"])
        rt = rt[rt["phase"] <= 8]
        rt = rt.assign(rule=np.where(rt["phase"] <= 4, "rotation_early", "rotation_late"), zone=rt["phase"], followed=rt["timing"] == "Ahead")
        rt["ahead"] = _rivals_ahead(rt, ["match_id", "zone"])
        out.append(rt[["match_id", "id", "rule", "zone", "followed", "ahead"]])

    # endgame height: team snapshots, zones 6+
    team = _snapshots(ctx)
    if len(team):
        tm = team[team["phase"] >= 6].dropna(subset=["placement"])
        tm = tm.assign(followed=tm["tier"] != TIERS[0], zone=tm["phase"])
        tm["ahead"] = tm.groupby(["match_id", "zone"])["placement"].rank(pct=True, method="average")
        h = tm[["match_id", "team_index", "zone", "followed", "ahead"]].merge(players[["match_id", "id", "team_index"]], on=["match_id", "team_index"])
        out.append(h.assign(rule="height")[["match_id", "id", "rule", "zone", "followed", "ahead"]])

    if not out:
        return pd.DataFrame()
    a = pd.concat(out, ignore_index=True)
    return a.merge(players[["match_id", "id", "name", "pr_rank", "final"]], on=["match_id", "id"], how="left")


def _pct(v) -> str:
    return "–" if v is None or v != v else f"{v:.0%}"


def _band(pr: pd.Series) -> pd.Series:
    return pd.cut(pr.fillna(10**7), [0, 1000, 10000, 10**8], labels=["PR top 1,000", "PR 1,001–10,000", "Unranked"])


def _score(a: pd.DataFrame, rules: set[str], alive_zone: int, ctx: Context) -> pd.DataFrame:
    """A playbook score per player-match: the share of these rules followed, among players alive when zone
    `alive_zone` appears; outcome = rivals alive then who finished ahead."""
    con = ctx.con
    alive = df(con, f"""
        WITH t AS (SELECT z.match_id, z.finish_shrink_t AS t FROM zones z JOIN sel USING (match_id) WHERE z.phase = {alive_zone - 1})
        SELECT pl.match_id, pl.id, coalesce(pl.team_placement, pl.placement) AS final FROM players pl JOIN t USING (match_id)
        WHERE NOT coalesce(pl.is_bot, FALSE) AND (pl.death_t IS NULL OR pl.death_t > t.t)""").dropna(subset=["final"])
    if alive.empty:
        return alive
    alive["ahead"] = alive.groupby("match_id")["final"].rank(pct=True, method="average")
    s = a[a["rule"].isin(rules)].groupby(["match_id", "id"]).agg(n=("followed", "size"), share=("followed", "mean"))
    s = alive.merge(s.reset_index(), on=["match_id", "id"])
    return s[s["n"] >= 3]


@register("playbook", "Did following the system pay off?",
          "Players who happened to do what the system recommends, judged only on what the system would have advised at the "
          "time, compared with rivals alive at the same moment.",
          params=[ALPHA_PARAM, Param("match", "Example match", "match", "")])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    key = ("pb",) + tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"]))
    a = _CACHE.get(key)
    if a is None:
        a = applications(ctx)
        _CACHE.clear()
        _CACHE[key] = a
    if a.empty:
        r.headline = "Not enough data to judge the playbook in this selection."
        conclude(r, ctx, primary=[], alpha=alpha, recommended=100, descriptive=r.headline)
        return r

    # ---- each rule's value: within the same match and moment, within the same player, and per skill band
    rows = []
    for rule, (label, when) in RULES.items():
        x = a[a["rule"] == rule]
        if len(x) < 30 or x["followed"].all() or not x["followed"].any():
            continue
        per = x.groupby("match_id").apply(lambda g: g.loc[g["followed"], "ahead"].mean() - g.loc[~g["followed"], "ahead"].mean()
                                          if g["followed"].sum() >= 2 and (~g["followed"]).sum() >= 2 else np.nan, include_groups=False).dropna()
        t = ttest_mean(per, 0.0)
        within = x.groupby("id").apply(lambda g: g.loc[g["followed"], "ahead"].mean() - g.loc[~g["followed"], "ahead"].mean()
                                       if g["followed"].sum() >= 2 and (~g["followed"]).sum() >= 2 else np.nan, include_groups=False).dropna()
        tw = ttest_mean(within, 0.0)
        f_ahead, n_ahead = 1 - x.loc[x["followed"], "ahead"].mean(), 1 - x.loc[~x["followed"], "ahead"].mean()
        if t["n"] >= 3:
            r.test("Each rule", f"{label} ({when})", int(t["n"]), f"finished ahead of {f_ahead:.0%} vs {n_ahead:.0%} of rivals",
                   t["p"], alpha, (f"{label} ({when.lower()}) went with finishing worse than rivals",
                                   f"{label} ({when.lower()}) went with finishing better than rivals"), direction=t["mean"])
        if tw["n"] >= 5:
            r.test("Same player", f"{label} ({when})", int(tw["n"]), f"{-tw['mean'] * 100:+.0f} points of finishing rank when followed",
                   tw["p"], alpha, (f"The same players finished worse when they did this", f"The same players finished better when they did this"),
                   direction=tw["mean"])
        p = t["p"]
        rows.append({"Rule": label, "When": when, "Followed by": f"{x['followed'].mean():.0%}",
                     "Finished ahead of rivals, if followed": f"{f_ahead:.0%}", "If not": f"{n_ahead:.0%}",
                     "Same player, when followed": (f"{-tw['mean'] * 100:+.0f} pts" if tw["n"] >= 5 else "–"),
                     "Verdict": ("Strong evidence" if p < alpha else "Some evidence" if p < 0.05 else "No clear difference") if p == p else "Too few",
                     "_gain": f_ahead - n_ahead})
    tbl = pd.DataFrame(rows)
    if len(tbl):
        r.table("Each rule's value", tbl.drop(columns=["_gain"]))
        r.chart("bar", "What each rule is worth",
                [dict(name="Followed", x=[f"{a_} ({w})" for a_, w in zip(tbl["Rule"], tbl["When"])],
                      y=[float(v.rstrip("%")) for v in tbl["Finished ahead of rivals, if followed"]]),
                 dict(name="Didn't", x=[f"{a_} ({w})" for a_, w in zip(tbl["Rule"], tbl["When"])], y=[float(v.rstrip("%")) for v in tbl["If not"]])],
                x_label="Finished ahead of rivals (%)", horizontal=True)

    # ---- playbook scores: how many rules a player followed, against how they finished
    bands = [0, 0.4, 0.7, 1.01]
    labels = ["Followed few (under 40%)", "Some (40–70%)", "Most (70%+)"]
    score_series = []
    for name, rules, alive_zone in (("Early game", EARLY, 5), ("Late game", LATE, 9)):
        s = _score(a, rules, alive_zone, ctx)
        if len(s) < 30:
            continue
        s["band"] = pd.cut(s["share"], bands, labels=labels, right=False)
        g = s.groupby("band", observed=False)["ahead"].mean()
        score_series.append(dict(name=f"{name} (alive at zone {alive_zone})", x=labels, y=((1 - g) * 100).round(0).tolist()))
        rho = s.groupby("match_id").apply(lambda q: q["share"].corr(q["ahead"], method="spearman") if q["share"].nunique() > 1 and len(q) >= 6 else np.nan,
                                          include_groups=False).dropna()
        t = ttest_mean(rho, 0.0)
        if t["n"] >= 3:
            r.test("Playbook score", f"{name}: following more rules", int(t["n"]),
                   f"most-rules players finished ahead of {_pct(1 - g.iloc[-1])}, few-rules {_pct(1 - g.iloc[0])}", t["p"], alpha,
                   (f"{name}: following more of the playbook went with finishing worse", f"{name}: following more of the playbook went with finishing better"),
                   direction=t["mean"])
        dm = s.assign(sd=s["share"] - s.groupby("id")["share"].transform("mean"), od=s["ahead"] - s.groupby("id")["ahead"].transform("mean"))
        multi = dm[dm.groupby("id")["id"].transform("size") >= 3]
        if len(multi) >= 30 and multi["sd"].std() > 0:
            pl_rho = multi.groupby("id").apply(lambda q: q["sd"].corr(q["od"]) if q["sd"].std() > 0 else np.nan, include_groups=False).dropna()
            tw = ttest_mean(pl_rho, 0.0)
            if tw["n"] >= 5:
                r.test("Same player", f"{name}: following more rules", int(tw["n"]), f"mean link {tw['mean']:+.2f}", tw["p"], alpha,
                       (f"{name}: the same players finished worse when they followed more of the playbook",
                        f"{name}: the same players finished better when they followed more of the playbook"), direction=tw["mean"])
        if name == "Early game":
            r.metric("Early playbook", f"{_pct(1 - g.iloc[-1])} vs {_pct(1 - g.iloc[0])}",
                     "Players alive at zone 5 who followed most early rules, against those who followed few: the share of rivals alive then they finished ahead of")
        else:
            r.metric("Late playbook", f"{_pct(1 - g.iloc[-1])} vs {_pct(1 - g.iloc[0])}",
                     "The same for late-game rules, among players alive at zone 9")
        if "pr_rank" in a and s.merge(a[["match_id", "id", "pr_rank"]].drop_duplicates(), on=["match_id", "id"])["pr_rank"].notna().any():
            sk = s.merge(a[["match_id", "id", "pr_rank"]].drop_duplicates(), on=["match_id", "id"])
            sk = sk.assign(skill=_band(sk["pr_rank"])).groupby(["skill", "band"], observed=False)["ahead"].mean().unstack()
            sk = (1 - sk).mul(100).round(0).reset_index()
            sk.columns = ["Skill band"] + [str(c) for c in sk.columns[1:]]
            r.table(f"Skill control, {name.lower()}: rivals finished ahead of (%)", sk)
    if score_series:
        r.chart("bar", "Following the playbook vs finishing", score_series, x_label="Share of the playbook followed",
                y_label="Finished ahead of rivals (%)", reference_lines=[dict(axis="y", value=50, label="Average")])
    r.metric("Rule checks", f"{len(a):,}", f"Moments a rule could be followed, across {a['match_id'].nunique():,} matches")

    # ---- real examples
    per_pm = a.groupby(["match_id", "id"]).agg(name=("name", "first"), checks=("followed", "size"), followed=("followed", "sum"),
                                                final=("final", "first"), pr_rank=("pr_rank", "first")).reset_index()
    per_pm = per_pm[per_pm["checks"] >= 8]
    if len(per_pm):
        per_pm["share"] = per_pm["followed"] / per_pm["checks"]
        dates = df(ctx.con, "SELECT m.match_id, m.match_date, m.event_window_id FROM matches m JOIN sel USING (match_id)")
        ex = per_pm.sort_values(["share", "checks"], ascending=False).head(15).merge(dates, on="match_id", how="left")
        r.table("Players who followed the system most closely", pd.DataFrame({
            "Player": ex["name"], "Date": ex["match_date"].astype(str).str[:10], "Tournament": ex["event_window_id"],
            "Rules followed": [f"{int(f)} of {int(c)}" for f, c in zip(ex["followed"], ex["checks"])],
            "Placement": ex["final"].astype("Int64"), "PR rank": ex["pr_rank"].astype("Int64"), "Match": ex["match_id"]}))

        # example map: the closest follower and the least close in one match, over that match's zones
        mid = ctx.params.get("match") or ex.iloc[0]["match_id"]
        pm = per_pm[per_pm["match_id"] == mid]
        if len(pm) >= 2:
            hi, lo = pm.sort_values("share").iloc[-1], pm.sort_values("share").iloc[0]
            tracks = df(ctx.con, f"""SELECT p.id, p.t, p.x, p.y FROM positions p JOIN landings l ON l.match_id = p.match_id AND l.id = p.id
                                     WHERE p.match_id = '{mid}' AND p.id IN ({int(hi['id'])}, {int(lo['id'])}) AND p.t >= l.land_t ORDER BY p.t""")
            zm_ = df(ctx.con, f"SELECT phase, next_x, next_y, next_r FROM zones WHERE match_id = '{mid}' ORDER BY phase")
            series = []
            for who, lab, col in ((hi, f"Followed the system: {hi['name']} ({int(hi['followed'])}/{int(hi['checks'])}, placed {int(hi['final'])})", "#0F766E"),
                                  (lo, f"Didn't: {lo['name']} ({int(lo['followed'])}/{int(lo['checks'])}, placed {int(lo['final'])})", "#D08A12")):
                tr = tracks[tracks["id"] == who["id"]].iloc[::3]
                series.append(dict(name=lab, x=tr["x"].round(0).tolist(), y=tr["y"].round(0).tolist(), color=col, size=3, opacity=0.8))
            r.chart("map_points", "A follower and a non-follower in the same match", series, x_label="Map X", y_label="Map Y",
                    circles=[dict(x=float(z["next_x"]), y=float(z["next_y"]), r=float(z["next_r"]), label=f"Zone {int(z['phase'])}")
                             for _, z in zm_.iterrows()], zone_circles=True)
            r.notes.insert(0, f"Example map: match {mid}.")

    best_rules = tbl[tbl["Verdict"] == "Strong evidence"].sort_values("_gain", ascending=False) if len(tbl) else tbl
    r.headline = (f"{len(a):,} moments where a playbook rule could be followed, across {a['match_id'].nunique():,} matches. "
                  + (f"Strongest: {best_rules.iloc[0]['Rule'].lower()} ({best_rules.iloc[0]['When'].lower()}): rivals finished ahead of "
                     f"{best_rules.iloc[0]['Finished ahead of rivals, if followed']} vs {best_rules.iloc[0]['If not']}." if len(best_rules) else ""))
    m = {x["label"]: x["value"] for x in r.metrics}
    parts = []
    if "Early playbook" in m:
        e_hi, e_lo = m["Early playbook"].split(" vs ")
        parts.append(f"players who followed most of the early playbook finished ahead of {e_hi} of rivals, against {e_lo} for those who followed few")
    if "Late playbook" in m:
        l_hi, l_lo = m["Late playbook"].split(" vs ")
        parts.append(f"late game {l_hi} against {l_lo}")
    conclude(r, ctx, strategy=True, primary=["Each rule", "Playbook score"], alpha=alpha, recommended=100, single_season=False,
             takeaway_found=("Following the system paid off: " + "; ".join(parts) + "." if parts else
                             "Following the system paid off for individual rules: " + "; ".join(
                                 x["reading"].lower() for x in r.tests if x["group"] == "Each rule" and x["significant"])[:400] + "."),
             takeaway_none="No consistent advantage yet for players who followed the system.",
             next_found=["Check the 'Same player' results: they remove skill, so they're the strongest evidence a rule helps.",
                         "Look at the examples table and map: they're the cases to show a team."],
             next_none=["Add strong-lobby matches; check each rule separately in Details."])
    r.notes += [
        "No hindsight: each rule is judged on what the system would have advised at that moment, including the zone forecast "
        "from a model trained without that match.",
        "Outcomes compare players with rivals alive at the same moment in the same match, so surviving longer isn't credited to a rule.",
        "'Same player' compares each player with themselves across matches (followed vs didn't), which removes skill.",
        "Associations, not proof: players who follow a rule may differ in ways not measured. The same-player and skill-band checks narrow that.",
    ]
    return r
