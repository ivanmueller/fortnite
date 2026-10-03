"""
Storm hunt or rotate first? Teams that must rotate (outside the next zone when it appears) either go in first and look for
surge inside, or stay back, in or just behind the storm, to tag the teams rotating late. This compares the two among teams
that started as far out with a similar surge standing, in the same zone of the same match.

Per team and zone (every zone from zone 1 with a wait of at least MIN_WAIT_S before the storm moves; zone 1 appears at the
match's safe-zones start, later zones when the previous one finishes closing):
  start      distance from the team's centre to the next zone's edge when it appears; surge standing: the team's net damage
             (dealt minus taken, between players) over the previous zone against the lobby's median then (at or below it =
             bottom half; teams that hadn't fought, on exactly 0, usually sit there)
  approach   judged halfway through the wait: inside the zone or covered at least half the distance = Rotated first;
             covered under a fifth = Stayed back (in the storm if they took storm damage that zone, else behind it); in
             between = not compared. Judged from movement, so teams sprayed and eliminated on the way in still count as
             rotating first (judging by who got in would hide exactly that cost).
  outcomes   over the zone, from appearing to closing: net damage gained, damage taken from players, storm damage, surged at
             any surge check, eliminated, safe and alive (neither), and the team's placement points in the end
  spray      damage taken from players during the wait, by the enemy teams already set up inside the next zone within
             LOOK_E_M of the team's way in (the nearest point of the zone's edge) when it appeared
Comparison: groups of the same match, zone, distance band (under / over SPLIT_M) and surge half; within each group with both
approaches, the difference in averages (stayed back minus rotated first); differences averaged across those groups and tested,
each group one observation. A zone-3 team is never compared with a zone-6 team.
By zone and stage: staying back is an early-game option (the storm is weak and surge is just starting); later, nearly every team
rotates, and the few that stay back are often stuck rather than choosing it. So results are also given per zone and per stage
(zones 1-3, 4-5, 6+), with how common staying back is in each; zones with fewer than MIN_STAYED teams that stayed back get no
verdict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import scoring
from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, has_tables, player_hits, unexplained_drops

MIN_WAIT_S = 20
MIN_STAYED = 10        # teams that stayed back in a zone (or stage) before it gets a verdict
COMMON = 0.15          # staying back is "common" in a zone while at least this share of the teams outside choose it
STAGES = [(1, 3, "Zones 1–3"), (4, 5, "Zones 4–5"), (6, 99, "Zones 6+")]


def stage(zone: int) -> str:
    return next(lab for lo, hi, lab in STAGES if lo <= zone <= hi)
MIN_OUT_M = 50
SPLIT_M = 300
LOOK_E_M = 150
ROTATED, STAYED, BETWEEN = "Rotated first", "Stayed back", "In between"
STORM, BEHIND = "Stayed back, in the storm", "Stayed back, behind the storm"
# outcome column -> (label, format, higher is better)
OUTCOMES = {
    "net": ("Net damage gained over the zone", "{:+.0f}", True),
    "taken": ("Damage taken from players", "{:.0f}", False),
    "storm": ("Storm damage taken", "{:.0f}", False),
    "surged": ("Surged during the zone", "{:.0%}", False),
    "out": ("Eliminated during the zone", "{:.0%}", False),
    "safe_alive": ("Safe from surge and alive", "{:.0%}", True),
    "points": ("Placement points in the end", "{:.1f}", True),
}


def _windows(con) -> pd.DataFrame:
    z = df(con, """SELECT z.match_id, z.phase, z.next_x, z.next_y, z.next_r, z.start_shrink_t, z.finish_shrink_t
                   FROM zones z JOIN sel USING (match_id) ORDER BY 1, 2""")
    cols = set(con.execute("SELECT * FROM matches LIMIT 0").df().columns)
    first = dict(df(con, "SELECT m.match_id, m.safe_zones_start_t FROM matches m JOIN sel USING (match_id)").itertuples(index=False, name=None)) \
        if "safe_zones_start_t" in cols else {}
    rows = []
    for mid, g in z.groupby("match_id"):
        fin = dict(zip(g["phase"].astype(int), g["finish_shrink_t"]))
        fin[0] = first.get(mid, np.nan)                     # zone 1 appears when the safe zones start
        for _, q in g.iterrows():
            k = int(q["phase"])
            reveal, prev = fin.get(k - 1), (fin.get(k - 2, 0.0) if k >= 2 else 0.0)
            if k < 1 or reveal is None or reveal != reveal or q["next_x"] != q["next_x"]:
                continue
            wait = float(q["start_shrink_t"]) - float(reveal)
            if wait < MIN_WAIT_S:
                continue
            rows.append(dict(match_id=mid, zone=k, prev=float(prev if prev == prev else 0.0), reveal=float(reveal),
                             mid=float(reveal) + wait / 2, start=float(q["start_shrink_t"]), finish=float(q["finish_shrink_t"]),
                             nx=float(q["next_x"]), ny=float(q["next_y"]), nr=float(q["next_r"])))
    return pd.DataFrame(rows)


def team_zones(ctx: Context) -> pd.DataFrame:
    """One row per team per zone that had to rotate: start, approach and outcomes (see the module docstring)."""
    con = ctx.con
    w = _windows(con)
    if w.empty:
        return pd.DataFrame()
    con.register("sh_w", w)
    a = df(con, """
        WITH a AS (SELECT w.*, p.id, p.team_index, p.death_t FROM sh_w w JOIN players p ON p.match_id = w.match_id
                   WHERE NOT coalesce(p.is_bot, FALSE) AND (p.death_t IS NULL OR p.death_t > w.reveal)),
             b AS (SELECT a.*, least(a.mid, coalesce(a.death_t - 0.5, a.mid)) AS t_mid FROM a),
             s AS (SELECT b.*, pos.x AS x0, pos.y AS y0 FROM b ASOF JOIN positions pos
                   ON pos.match_id = b.match_id AND pos.id = b.id AND b.reveal >= pos.t)
        SELECT s.*, pos.x AS x1, pos.y AS y1 FROM s ASOF JOIN positions pos ON pos.match_id = s.match_id AND pos.id = s.id AND s.t_mid >= pos.t
    """).dropna(subset=["x0", "x1"])
    if a.empty:
        return pd.DataFrame()
    key = ["match_id", "zone", "team_index"]
    t = a.groupby(key).agg(x0=("x0", "mean"), y0=("y0", "mean"), x1=("x1", "mean"), y1=("y1", "mean"), members=("id", "size"),
                           last_death=("death_t", lambda s: np.nan if s.isna().any() else s.max())).reset_index()
    t = t.merge(w, on=["match_id", "zone"])
    c0 = np.hypot(t["x0"] - t["nx"], t["y0"] - t["ny"])
    t["dist_m"] = ((c0 - t["nr"]) / 100).clip(lower=0)
    t["dist_mid_m"] = ((np.hypot(t["x1"] - t["nx"], t["y1"] - t["ny"]) - t["nr"]) / 100).clip(lower=0)
    t["inside0"] = t["dist_m"] <= 0
    # lookers: enemy teams already inside the zone near this team's way in (the nearest point of the edge), at the reveal
    ex = t["nx"] + (t["x0"] - t["nx"]) / c0.replace(0, np.nan) * t["nr"]
    ey = t["ny"] + (t["y0"] - t["ny"]) / c0.replace(0, np.nan) * t["nr"]
    look = np.zeros(len(t), dtype=int)
    for _, idx in t.groupby(["match_id", "zone"]).indices.items():
        g = t.iloc[idx]
        ins = g["inside0"].to_numpy()
        d = np.hypot(ex.iloc[idx].to_numpy()[:, None] - g["x0"].to_numpy()[None, :], ey.iloc[idx].to_numpy()[:, None] - g["y0"].to_numpy()[None, :]) / 100
        look[idx] = ((d <= LOOK_E_M) & ins[None, :] & (g["team_index"].to_numpy()[:, None] != g["team_index"].to_numpy()[None, :])).sum(1)
    t["lookers"] = look

    # surge standing at the reveal: net damage over the previous zone, ranked among every team alive then
    hits = player_hits(con)
    hm = {m: g for m, g in hits.groupby("match_id")}
    net_prev, dealt, taken, taken_wait = [], [], [], []
    for _, q in t.iterrows():
        h = hm.get(q["match_id"])
        if h is None:
            net_prev.append(0.0); dealt.append(0.0); taken.append(0.0); taken_wait.append(0.0)
            continue
        ti = q["team_index"]
        hp = h[h["t"].between(q["prev"], q["reveal"])]
        net_prev.append(float(hp.loc[hp["attacker_team"] == ti, "amount"].sum() - hp.loc[hp["target_team"] == ti, "amount"].sum()))
        hz = h[h["t"].between(q["reveal"], q["finish"])]
        dealt.append(float(hz.loc[hz["attacker_team"] == ti, "amount"].sum()))
        taken.append(float(hz.loc[hz["target_team"] == ti, "amount"].sum()))
        hw = hz[hz["t"] <= q["start"]]
        taken_wait.append(float(hw.loc[hw["target_team"] == ti, "amount"].sum()))
    t["net_prev"], t["dealt"], t["taken"], t["taken_wait"] = net_prev, dealt, taken, taken_wait
    t["net"] = t["dealt"] - t["taken"]
    # split at the lobby's median; ties (often many teams on exactly 0, having not fought) go to the bottom half
    med = t.groupby(["match_id", "zone"])["net_prev"].transform("median")
    t["surge_half"] = np.where(t["net_prev"] <= med, "Bottom half", "Top half")

    # storm damage in the zone: unexplained drops outside the storm circle
    drops = unexplained_drops(con)
    storm = np.zeros(len(t))
    if len(drops):
        st = drops[drops["in_storm"] == True].merge(a[["match_id", "id", "team_index", "zone"]].drop_duplicates(),  # noqa: E712
                                                   on=["match_id", "id"])
        st = st.merge(w[["match_id", "zone", "reveal", "finish"]], on=["match_id", "zone"])
        st = st[st["t"].between(st["reveal"], st["finish"])]
        sums = st.groupby(key)["lost"].sum()
        storm = np.array([float(sums.get((m, z, ti), 0.0)) for m, z, ti in zip(t["match_id"], t["zone"], t["team_index"])])
    t["storm"] = storm

    # surged: any surge check in the zone's window where a team member was hit (no surge in that zone = not surged)
    from .surge_study import measured
    m = measured(ctx)
    t["surged"] = False
    if m is not None and len(m["per"]):
        per = m["per"].merge(m["ep"][["episode", "t0"]], on="episode")
        hit = per[per["surged"]].groupby(["match_id", "team_index"])["t0"].apply(list).to_dict()
        t["surged"] = [any(q["reveal"] <= x <= q["finish"] + 1 for x in hit.get((q["match_id"], q["team_index"]), []))
                       for _, q in t.iterrows()]
    t["out"] = t["last_death"].notna() & (t["last_death"] <= t["finish"])
    t["safe_alive"] = ~t["surged"].astype(bool) & ~t["out"]

    # placement points in the end
    scheme = scoring.active()
    pl = df(con, """SELECT pl.match_id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS placement, pl.kills
                    FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    tp = pl.groupby(["match_id", "team_index"]).agg(placement=("placement", "min"), kills=("kills", "sum")).reset_index()
    tp["points"] = scheme.placement_points(tp["placement"]).to_numpy(float)
    t = t.merge(tp[["match_id", "team_index", "points", "placement"]], on=["match_id", "team_index"], how="left")

    # the teams that had to rotate, and how they did it
    t = t[t["dist_m"] >= MIN_OUT_M].copy()
    progress = (t["dist_m"] - t["dist_mid_m"]) / t["dist_m"]
    t["approach"] = np.select([(t["dist_mid_m"] <= 0) | (progress >= 0.5), progress < 0.2], [ROTATED, STAYED], BETWEEN)
    t["detail"] = np.where(t["approach"] == STAYED, np.where(t["storm"] > 0, STORM, BEHIND), t["approach"])
    t["dist_band"] = np.where(t["dist_m"] < SPLIT_M, f"Under {SPLIT_M} m", f"{SPLIT_M} m or more")
    t["stage"] = [stage(int(k)) for k in t["zone"]]
    for c in ("surged", "out", "safe_alive"):
        t[c] = t[c].astype(float)
    return t.reset_index(drop=True)


def like_for_like(t: pd.DataFrame, col: str) -> dict:
    """Stayed back minus rotated first, within groups of the same match, zone, distance band and surge half."""
    groups = []
    for _, g in t.groupby(["match_id", "zone", "dist_band", "surge_half"]):
        a, b = g.loc[g["approach"] == STAYED, col].dropna(), g.loc[g["approach"] == ROTATED, col].dropna()
        if len(a) and len(b):
            groups.append(a.mean() - b.mean())
    return ttest_mean(pd.Series(groups, dtype=float), 0.0)


RATES = {"surged", "out", "safe_alive"}


def _fmt(col: str, v) -> str:
    return "–" if v is None or v != v else OUTCOMES[col][1].format(v)


def _diff(col: str, v) -> str:
    """A difference: percentage points for shares, signed numbers otherwise."""
    if v is None or v != v:
        return "–"
    return f"{v * 100:+.0f} points" if col in RATES else (f"{v:+.1f}" if col == "points" else f"{v:+.0f}")


@register("storm_hunt", "Storm hunt or rotate first?",
          "For teams outside the next zone when it appears: going in first and looking for surge inside, against staying back in "
          "or behind the storm to tag late rotators, among teams as far out with a similar surge standing.",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    if not has_tables(ctx.con, "damage", "health"):
        r.headline = "No health or damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    t = team_zones(ctx)
    cmp = t[t["approach"].isin([ROTATED, STAYED])] if len(t) else t
    if cmp.empty or cmp["approach"].nunique() < 2:
        r.headline = "Not enough teams outside the next zone that either rotated first or stayed back in these matches."
        conclude_without_data(r, ctx, recommended=50, note=r.headline)
        return r

    # ---- like for like
    rows, results = [], {}
    for col, (label, _, better_high) in OUTCOMES.items():
        res = like_for_like(cmp, col)
        if res["p"] is None or res["p"] != res["p"]:
            res = {**res, "p": 1.0}           # every group identical: no difference to test
        results[col] = res
        ra, sa = cmp.loc[cmp["approach"] == ROTATED, col].mean(), cmp.loc[cmp["approach"] == STAYED, col].mean()
        if res["n"] >= 3:
            good = (res["mean"] > 0) == better_high
            r.test("Like for like", f"{label}: staying back vs rotating first", int(res["n"]), _diff(col, res["mean"]),
                   res["p"], alpha, (f"Staying back went with more {label.lower()} than rotating first",
                                     f"Staying back went with less {label.lower()} than rotating first"), direction=res["mean"])
        rows.append({"Outcome": label, ROTATED: _fmt(col, ra), STAYED: _fmt(col, sa),
                     "Staying back, like for like": _diff(col, res["mean"]) if res["n"] else "–",
                     "Groups compared": int(res["n"]),
                     "Verdict": ("Too few" if res["n"] < 3 else "Strong evidence" if res["p"] < alpha else "Some evidence" if res["p"] < 0.05
                                 else "No clear difference") + ("" if res["n"] < 3 or res["p"] >= 0.05 else
                                                                (": staying back is better" if (res["mean"] > 0) == better_high else ": rotating first is better"))})
    r.table("Stay back or rotate first: like for like", pd.DataFrame(rows))

    # ---- who it pays off for
    who = []
    order = {lab: i for i, (_, _, lab) in enumerate(STAGES)}
    for (stg, half), g in sorted(cmp.groupby(["stage", "surge_half"]), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        ro, sb = g[g["approach"] == ROTATED], g[g["approach"] == STAYED]
        pts, safe = like_for_like(g, "points"), like_for_like(g, "safe_alive")
        enough = pts["n"] >= 3 and len(sb) >= MIN_STAYED
        who.append({"Stage": stg, "Surge standing": half, "Teams (rotated / stayed)": f"{len(ro)} / {len(sb)}",
                    "Safe and alive, rotated": _fmt("safe_alive", ro["safe_alive"].mean()), "Safe and alive, stayed": _fmt("safe_alive", sb["safe_alive"].mean()),
                    "Points, rotated": _fmt("points", ro["points"].mean()), "Points, stayed": _fmt("points", sb["points"].mean()),
                    "Staying back, like for like": (f"{pts['mean']:+.1f} points, {safe['mean'] * 100:+.0f} safe and alive" if enough
                                                    else "Too few to judge"),
                    "Groups": int(pts["n"])})
    r.table("Who it pays off for", pd.DataFrame(who))

    # ---- by zone: how common staying back is, and where it stops paying
    zrows, common_through, zchart = [], None, []
    for k, g in t[t["approach"] != BETWEEN].groupby("zone"):
        outside = int((t["zone"] == k).sum())
        ro, sb = g[g["approach"] == ROTATED], g[g["approach"] == STAYED]
        share = len(sb) / outside if outside else np.nan
        if share == share and share >= COMMON and outside >= MIN_STAYED:
            common_through = int(k)
        pts, safe = like_for_like(g, "points"), like_for_like(g, "safe_alive")
        if pts["p"] is None or pts["p"] != pts["p"]:
            pts = {**pts, "p": 1.0}
        judged = pts["n"] >= 3 and len(sb) >= MIN_STAYED
        verdict = ("Too few to judge" if not judged else
                   ("Staying back paid" if pts["mean"] > 0 else "Rotating first paid") + (" (strong evidence)" if pts["p"] < alpha else
                                                                                        " (some evidence)" if pts["p"] < 0.05 else ", but no clear difference"))
        zrows.append({"Zone": int(k), "Teams outside": outside, "Stayed back": f"{len(sb)} ({share:.0%})" if share == share else "–",
                      "Storm damage, stayed back": _fmt("storm", sb["storm"].mean()),
                      "Net gained, rotated / stayed": f"{_fmt('net', ro['net'].mean())} / {_fmt('net', sb['net'].mean())}",
                      "Safe and alive, rotated / stayed": f"{_fmt('safe_alive', ro['safe_alive'].mean())} / {_fmt('safe_alive', sb['safe_alive'].mean())}",
                      "Staying back, like for like": f"{pts['mean']:+.1f} points, {safe['mean'] * 100:+.0f} safe and alive" if judged else "–",
                      "Groups": int(pts["n"]), "Verdict": verdict})
        zchart.append((int(k), share * 100 if share == share else None))
    r.table("By zone: where staying back stops paying", pd.DataFrame(zrows))
    if zchart:
        r.chart("bar", "How often teams outside the zone stayed back, by zone",
                [dict(name="Stayed back", x=[f"Zone {k}" for k, _ in zchart], y=[None if v is None else round(v) for _, v in zchart])],
                y_label="% of teams outside the next zone", reference_lines=[dict(axis="y", value=COMMON * 100, label="Common")])

    # ---- in the storm or behind it
    det = t[t["approach"] != BETWEEN].groupby("detail").agg(teams=("team_index", "size"), net=("net", "mean"), storm=("storm", "mean"),
                                                           safe=("safe_alive", "mean"), out=("out", "mean"), points=("points", "mean"))
    det = det.reindex([x for x in (ROTATED, BEHIND, STORM) if x in det.index])
    r.table("Staying back: in the storm or behind it", pd.DataFrame({
        "Approach": det.index, "Teams": det["teams"].astype(int), "Net damage gained": [_fmt("net", v) for v in det["net"]],
        "Storm damage": [_fmt("storm", v) for v in det["storm"]], "Safe and alive": [_fmt("safe_alive", v) for v in det["safe"]],
        "Eliminated in the zone": [_fmt("out", v) for v in det["out"]], "Placement points": [_fmt("points", v) for v in det["points"]]}))

    # ---- getting sprayed: damage taken during the wait, by the teams set up at your way in
    sp = cmp.assign(look=pd.cut(cmp["lookers"], [-1, 0, 1, 2, 99], labels=["0", "1", "2", "3+"]))
    g = sp.groupby(["approach", "look"], observed=True).agg(teams=("team_index", "size"), taken=("taken_wait", "mean"),
                                                            hit50=("taken_wait", lambda s: (s >= 50).mean()), out=("out", "mean")).reset_index()
    g = g[g["teams"] >= 5]
    r.table("Getting sprayed on the way in", pd.DataFrame({
        "Approach": g["approach"], "Teams set up at your way in": g["look"].astype(str), "Teams": g["teams"].astype(int),
        "Damage taken during the wait": g["taken"].round(0), "Took 50+": [f"{v:.0%}" for v in g["hit50"]],
        "Eliminated in the zone": [f"{v:.0%}" for v in g["out"]]}))
    rot = g[g["approach"] == ROTATED]
    if len(rot):
        r.chart("bar", "Damage taken while rotating first, by teams set up at your way in",
                [dict(name="Damage taken during the wait", x=rot["look"].astype(str).tolist(), y=rot["taken"].round(0).tolist())],
                x_label=f"Enemy teams already inside the zone within {LOOK_E_M} m of your way in", y_label="Damage taken (average)")
    sa = cmp.groupby(["surge_half", "approach"])["safe_alive"].mean().unstack()
    r.chart("bar", "Safe from surge and alive through the zone",
            [dict(name=ap, x=list(sa.index), y=(sa[ap] * 100).round(0).tolist()) for ap in (ROTATED, STAYED) if ap in sa],
            x_label="Surge standing when the zone appeared", y_label="% of teams")

    # ---- summary
    r.metric("Team-zones compared", f"{len(cmp):,}", f"Teams outside the next zone (by {MIN_OUT_M} m+) that rotated first or stayed back; "
             f"{int((t['approach'] == BETWEEN).sum())} in between are left out")
    r.metric("Rotated first / stayed back", f"{int((cmp['approach'] == ROTATED).sum())} / {int((cmp['approach'] == STAYED).sum())}")
    r.metric("Staying back is common through", f"zone {common_through}" if common_through else "–",
             f"The last zone where at least {COMMON:.0%} of the teams outside the next zone stayed back (with {MIN_STAYED}+ teams outside). "
             "After it nearly everyone rotates, and the few who stay back are often stuck rather than choosing it.")
    rp, rs = results["points"], results["safe_alive"]
    r.metric("Staying back, like for like", f"{rp['mean']:+.1f} points" if rp["n"] else "–",
             f"Placement points, staying back minus rotating first, in {int(rp['n'])} groups of the same match, zone, distance and surge standing")
    sig = [x for x in r.tests if x["significant"]]
    r.headline = (f"{len(cmp):,} teams had to rotate. Like for like, staying back changed placement points by {rp['mean']:+.1f} and the share "
                  f"safe from surge and alive by {rs['mean'] * 100:+.0f} points ({int(rp['n'])} comparable groups)." if rp["n"] else
                  f"{len(cmp):,} teams had to rotate, but too few groups had both approaches to compare like for like.")
    conclude(r, ctx, strategy=True, primary=["Like for like"], alpha=alpha, recommended=100,
             takeaway_found="; ".join(x["reading"] for x in sig) + ".",
             takeaway_none=r.headline + " No clear difference yet between staying back and rotating first.",
             next_found=["Read 'Who it pays off for': the answer can differ for teams low on surge and teams far out.",
                         "Check 'Getting sprayed on the way in' before rotating first into a lined-up edge."],
             next_none=["Add later-round matches with in-match data (data options T and 7): more surge zones, more comparisons."])
    r.notes += [
        f"Approach is judged halfway through the zone's wait before the storm moves: inside or half the distance covered = {ROTATED.lower()}; "
        f"under a fifth = {STAYED.lower()}. Judged from movement, so teams eliminated on the way in still count as rotating first.",
        "Like for like: only teams in the same match and zone, the same distance band and the same half of the lobby by net damage over the "
        "previous zone are compared; each such group counts once in the test.",
        "Surged: hit at any surge check while the zone was showing or closing. Safe and alive: not surged and not eliminated before it closed.",
        f"By zone and stage: staying back is an early-game choice. Late in the game nearly every team rotates, so zones (and stages) with fewer "
        f"than {MIN_STAYED} teams that stayed back get no verdict: the few there are often stuck, not choosing it. Surge is usually off in "
        "the first zones, so there staying back is about the storm and position, not surge.",
        f"Getting sprayed: damage taken from players between the zone appearing and the storm moving, by enemy teams already inside the zone "
        f"within {LOOK_E_M} m of the nearest point of its edge (your way in) when it appeared.",
    ]
    return r
