"""
Engine vs you. The decision engine (engine.py) knows only what a player knows live. Here it is:
  checked across every team in the selected matches: did teams that happened to follow it at most decisions
  finish better, within the same match and for the same team across games?
  applied to one team's game: every decision where the engine disagreed, what they knew, the engine's call,
  what they did, and the points at stake.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import engine as eng
from ..locks import serialized
from .. import ep_model as ep
from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import Context, Param, register
from .audit import _find_team, _team_list

_CACHE: dict = {}


@serialized
def decisions(ctx: Context, perceive_m: float):
    """Engine decisions for every team in the selection (cached per selection and perception radius)."""
    key = (tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"])), round(perceive_m))
    if key in _CACHE:
        return _CACHE[key]
    from .expected_points import _load as ep_load
    epd = ep_load(ctx)
    if epd is None:
        return None
    from .zone_forecast import _load as zf_load
    zf = zf_load(ctx)
    land = zf["land"] if zf else None
    live = eng.live_states(ctx.con, epd["team"], perceive_m, land)
    model, oos, base = eng.fit_live(live)
    y = live["future_pts"]
    r2_live = 1 - ((y - oos) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    pos = df(ctx.con, "SELECT coalesce(p.vx, 0) AS vx, coalesce(p.vy, 0) AS vy FROM positions p JOIN sel USING (match_id) USING SAMPLE 200000 ROWS")
    sp = np.hypot(pos["vx"], pos["vy"]) / 100
    run_ms = float(np.median(sp[sp > 2.5])) if (sp > 2.5).sum() > 100 else 6.0
    odds = eng.fight_odds(ctx.con)
    # each game priced by the live model that never saw it
    parts = []
    for mid, g in live.groupby("match_id"):
        m = model.fold_models_.get(model.fold_of_.get(mid)) or model
        parts.append(eng.evaluate(g, m, run_ms, eng.hit_risk(epd["expo"]), odds))
    d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    out = dict(d=d, r2_live=r2_live, r2_full=epd["r2"], run_ms=run_ms, cutoff=eng.surge_cutoff(ctx.con))
    if len(_CACHE) > 3:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def _fmt_t(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


@register("engine_review", "Engine vs you",
          "A decision engine that only knows what a player knows live, priced in FNCS points: checked across every team in the "
          "selected matches, then applied to one team's game, decision by decision.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game to show", "match", ""),
                  Param("perceive_m", "How far players can tell where enemies are (m)", "setting", 120)])
def run(ctx: Context) -> Result:
    r = Result()
    perceive = float(ctx.params.get("perceive_m") or 120)
    data = decisions(ctx, perceive)
    if data is None or data["d"].empty:
        r.headline = "Needs at least 6 matches with zones and positions in the selection."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
        return r
    d = data["d"]
    r.metric("Engine knowledge", f"{data['r2_live']:.0%} vs {data['r2_full']:.0%}",
             "How much of the points still to come the live-knowledge model explains on matches it never saw, against the model that "
             "knows everything. The gap is the price of the fog of war.")

    # ---- across every team: does following the engine go with better results?
    # key decisions: some option beats holding by half a point, or the team did something other than hold
    d = d.assign(key=((d["ev_engine"] - d["ev_hold"]) >= 0.5) | (d["actual"] != "hold"))
    tg = d.groupby(["match_id", "team_index"]).agg(n=("followed", "size"), placement=("placement", "min"), kills=("kills_total", "max")).reset_index()
    kf = d[d["key"]].groupby(["match_id", "team_index"]).agg(follow=("followed", "mean"), n_key=("followed", "size")).reset_index()
    tg = tg.merge(kf, on=["match_id", "team_index"], how="inner")
    tg = tg[tg["n_key"] >= 2]
    tg["points"] = tg["placement"].map(ep.PLACEMENT_POINTS).fillna(0) + ep.KILL_POINTS * tg["kills"].fillna(0)
    tg["ahead"] = 1 - tg.groupby("match_id")["placement"].rank(pct=True)
    if len(tg) >= 30:
        bands = pd.cut(tg["follow"], [0, 0.6, 0.75, 0.9, 1.01], labels=["Under 60%", "60–75%", "75–90%", "90%+"], right=False)
        b = tg.groupby(bands, observed=False).agg(teams=("points", "size"), points=("points", "mean"), ahead=("ahead", "mean"))
        r.chart("bar", "Points per game, by how often teams followed the engine",
                [dict(name="Average points", x=[str(i) for i in b.index], y=b["points"].round(1).tolist())],
                x_label="Share of key decisions where the team did what the engine recommends", y_label="Average points per game")
        r.table("Following the engine vs results", pd.DataFrame({"Followed the engine": [str(i) for i in b.index], "Team-games": b["teams"].values,
                                                                  "Average points": b["points"].round(1).values,
                                                                  "Finished ahead of": [f"{v:.0%}" if v == v else "–" for v in b["ahead"]]}))
        rho = tg.groupby("match_id").apply(lambda q: spearman(q["follow"], q["ahead"]) if len(q) >= 6 and q["follow"].nunique() > 1 else np.nan,
                                           include_groups=False).dropna()
        t = ttest_mean(rho, 0.0)
        if t["n"] >= 3:
            r.test("Across teams", "Following the engine goes with finishing better (same match)", int(t["n"]), f"mean rho {t['mean']:+.2f}",
                   t["p"], 0.005, ("Teams that followed the engine more finished better", "Teams that followed the engine more finished worse"),
                   direction=t["mean"])
        # the same team across games (teams identified by their accounts)
        mem = df(ctx.con, """SELECT p.match_id, p.team_index, string_agg(CAST(p.player_id AS VARCHAR), '|' ORDER BY p.player_id) AS members
                             FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE) GROUP BY 1, 2""")
        tm = tg.merge(mem, on=["match_id", "team_index"])
        tm = tm[tm.groupby("members")["members"].transform("size") >= 3]
        if len(tm) >= 30:
            within = tm.groupby("members").apply(lambda q: spearman(q["follow"], q["ahead"]) if q["follow"].nunique() > 1 else np.nan,
                                                 include_groups=False).dropna()
            tw = ttest_mean(within, 0.0)
            if tw["n"] >= 5:
                r.test("Same team", "The same team finished better in games where it followed the engine more", int(tw["n"]),
                       f"mean rho {tw['mean']:+.2f}", tw["p"], 0.005,
                       ("The same teams finished better when they followed the engine more", "The same teams finished worse when they followed the engine more"),
                       direction=tw["mean"])
        hi, lo = b["points"].iloc[-1], b["points"].iloc[0]
        r.metric("Followed 90%+ vs under 60%", f"{hi:.1f} vs {lo:.1f} pts" if hi == hi and lo == lo else "–", "Average points per game")

    # ---- one team's game, decision by decision
    text = str(ctx.params.get("team") or "").strip()
    team = _find_team(ctx.con, text) if text else pd.DataFrame()
    if team.empty:
        tl = _team_list(ctx.con)
        if text and len(tl):
            r.table("Teams in the selected matches", tl.head(60))
        r.headline = ("Across every team in the selection: " + ("; ".join(x["reading"].lower() for x in r.tests if x["significant"]) or
                      "no clear link yet between following the engine and results") + ". Type a team's names to see its decisions.")
        conclude(r, ctx, primary=["Across teams", "Same team"], alpha=0.005, recommended=50, strategy=True,
                 takeaway_found=r.headline, takeaway_none=r.headline)
        _notes(r, data, perceive)
        return r
    games = df(ctx.con, "SELECT m.match_id, m.replay_timestamp FROM matches m JOIN sel USING (match_id)")
    games = games[games["match_id"].isin(team["match_id"])].sort_values(["replay_timestamp", "match_id"]).reset_index(drop=True)
    gnum = dict(zip(games["match_id"], games.index + 1))
    mid = ctx.params.get("match") if ctx.params.get("match") in gnum else games["match_id"].iloc[-1]
    ti = int(team[team["match_id"] == mid]["team_index"].iat[0])
    mine = d[(d["match_id"] == mid) & (d["team_index"] == ti)].sort_values("t")
    names = " & ".join(sorted(team[team["match_id"] == mid]["name"].dropna().unique()))
    if mine.empty:
        r.headline = f"{names}: no decisions recorded in game {gnum[mid]} (out before zone 2?)."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    rows = []
    for _, q in mine.iterrows():
        if q["followed"] and q["stake"] < 1:
            continue
        opts = sorted(((eng.ACTIONS[k], q[f"ev_{k}"]) for k in eng.ACTIONS if q[f"ev_{k}"] > -1e8), key=lambda x: -x[1])
        surge = q["surge_margin"]
        rows.append({"Time": _fmt_t(q["t"]), "Zone": int(q["zone"]),
                     "What you knew": (f"{'inside' if q['outside_m'] == 0 else f'{q.outside_m:.0f} m outside'} the next zone, health {q['hp']:.0f}, "
                                       f"{int(q['teams_alive'])} teams left, {int(q['seen'])} team{'s' if q['seen'] != 1 else ''} within {perceive:.0f} m"
                                       + (f" ({int(q['seen_close'])} within 50 m)" if q["seen_close"] else "")
                                       + f", surge: {'above' if surge >= 0 else 'below'} the cut-off"),
                     "Engine": eng.ACTIONS[q["engine"]], "You": eng.ACTIONS[q["actual"]], "Points at stake": round(float(q["stake"]), 1),
                     "Options (expected points)": "; ".join(f"{a} {v:.1f}" for a, v in opts)})
    tbl = pd.DataFrame(rows)
    r.table("Decisions where the engine disagreed", tbl if len(tbl) else pd.DataFrame([{"Time": "–", "What you knew": "The team followed the engine at every decision"}]))
    mine = mine.assign(key=((mine["ev_engine"] - mine["ev_hold"]) >= 0.5) | (mine["actual"] != "hold"))
    fol = mine.loc[mine["key"], "followed"].mean() if mine["key"].any() else mine["followed"].mean()
    big = tbl[tbl["Points at stake"] >= 1] if len(tbl) else tbl
    r.metric("Decisions followed", f"{fol:.0%} of {int(mine['key'].sum())} key decisions" if mine["key"].any() else "No key decisions",
             f"Key decisions (every {eng.DECISION_S} s, where an option beat holding by half a point or they did something else) where they did "
             "what the engine recommends, within half a point")
    r.metric("Disagreements worth 1+ point", f"{len(big)}", "Decisions where the engine's call was worth at least one expected point more")
    st = mine.assign(m=mine["t"] // 60).groupby("zone")["stake"].apply(lambda s: s.clip(lower=0).sum())
    r.chart("bar", "Points at stake by zone", [dict(name="Points at stake", x=[f"Zone {int(z)}" for z in st.index], y=st.round(1).tolist())],
            y_label="Sum of points at stake (not additive)")
    r.headline = (f"{names}, game {gnum[mid]}: followed the engine at {fol:.0%} of {int(mine['key'].sum())} key decisions; {len(big)} disagreement"
                  f"{'s' if len(big) != 1 else ''} worth a point or more.")
    conclude(r, ctx, primary=["Across teams", "Same team"], alpha=0.005, recommended=50, strategy=True,
             takeaway_found=r.headline + " Teams that followed the engine more finished better across the selection.",
             takeaway_none=r.headline + " Across the selection, following the engine isn't yet clearly linked to better results.")
    _notes(r, data, perceive)
    return r


def _notes(r: Result, data: dict, perceive: float) -> None:
    r.notes += [
        f"Live knowledge only: own position, health, eliminations, teammates, the zones and timers, teams left, damage just taken, "
        f"surge above or below the cut-off (bottom {data['cutoff']:.0%} of damage dealt), enemies within {perceive:.0f} m, and the "
        "natural ground under you. Never far enemies' positions or anyone else's health.",
        f"Every {eng.DECISION_S} s the engine compares hold, rotate (direct or by the less crowded entry), heal and engage, "
        f"{eng.HORIZON_S} s ahead, and prices each with a points model trained on live knowledge only (each game by a model that "
        f"never saw it). Rotation speed: {data['run_ms']:.1f} m/s.",
        "'Followed' means the team did what the engine recommends, or something within half a point of it.",
        "Points at stake are expected points at that moment; they don't add up across a game, because each decision changes the next.",
    ]
