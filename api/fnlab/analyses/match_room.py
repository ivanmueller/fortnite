"""
Match room: one team's game, played back on the map, with what they had and what they decided at every moment.

Built on the match map's replay (every player's path, the storm, eliminations, the rotation plan per zone) and adds:
  HUD        per player over time: health and shield, knocked and eliminated, weapons seen in hand (with rarity),
             items picked up (materials, heals, ammo); per team: build pieces placed by material
  surge      the team's net damage (dealt minus taken, between players) since the current zone appeared, against
             the line that kept teams safe in that zone across the selected matches, and the surges in this game
  decisions  every engine decision point (live knowledge only): what the team did, the best option by expected points,
             every option's value, and for a fight its parts: chance to win, points if won (with the elimination),
             points if lost (placed now). The points at stake are the gap between the best option and what they did.
  games      the team's games in the selection, to switch between them

What the replays don't give (yet): material and item counts. Replays record weapons in hand, pickups and build
pieces, not the inventory itself; the extractor's survey shows whether a season's replays carry it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import scoring
from ..result import Result
from ..store import df
from . import Context, Param, register
from ._events_common import has_tables
from .audit import _find_team

SURGE_STEP = 5        # seconds between surge-score samples
PERCEIVE_M = 120.0    # the engine's perception radius, as on the match map


def _q(con, sql: str) -> pd.DataFrame:
    try:
        return df(con, sql)
    except Exception:  # noqa: BLE001 - a table missing in older data just leaves that part of the HUD empty
        return pd.DataFrame()


def _pretty(weapon) -> str:
    from .audit_zones import _pretty as p
    return p(weapon)


def _hud(con, mid: str, members: pd.DataFrame, ti: int) -> dict:
    ids = [int(i) for i in members["id"]]
    idl = ", ".join(str(i) for i in ids)
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    hp = _q(con, f"SELECT id, t, health, shield FROM health WHERE match_id = '{mid}' AND id IN ({idl}) ORDER BY t") if "health" in have else pd.DataFrame()
    wh = _q(con, f"SELECT id, t, weapon, rarity FROM weapons_held WHERE match_id = '{mid}' AND id IN ({idl}) ORDER BY t") if "weapons_held" in have else pd.DataFrame()
    pk = _q(con, f"""SELECT picked_by AS id, picked_t AS t, item, category, count FROM pickups
                     WHERE match_id = '{mid}' AND picked_by IN ({idl}) AND picked_t IS NOT NULL ORDER BY picked_t""") if "pickups" in have else pd.DataFrame()
    kl = _q(con, f"SELECT victim_id AS id, t, downed, revived FROM kills WHERE match_id = '{mid}' AND victim_id IN ({idl}) ORDER BY t") if "kills" in have else pd.DataFrame()
    players = []
    for _, m in members.iterrows():
        pid = int(m["id"])
        h = hp[hp["id"] == pid] if len(hp) else hp
        w = wh[wh["id"] == pid] if len(wh) else wh
        p = pk[pk["id"] == pid] if len(pk) else pk
        k = kl[kl["id"] == pid] if len(kl) else kl
        knocks = [float(t) for t, dn in zip(k.get("t", []), k.get("downed", [])) if bool(dn)]
        players.append(dict(
            id=pid, name=str(m["name"]), death=None if m["death_t"] != m["death_t"] else float(m["death_t"]),
            knocks=knocks, revives=[float(t) for t, rv in zip(k.get("t", []), k.get("revived", [])) if bool(rv)],
            hp=dict(t=h["t"].round(2).tolist(), health=h["health"].fillna(0).round(0).tolist(), shield=h["shield"].fillna(0).round(0).tolist())
            if len(h) else None,
            weapons=[dict(t=float(t), name=_pretty(n), rarity=None if r != r else str(r)) for t, n, r in zip(w["t"], w["weapon"], w["rarity"])]
            if len(w) else [],
            pickups=[dict(t=float(t), item=_pretty(i), category=None if c != c else str(c), count=None if n != n else int(n))
                     for t, i, c, n in zip(p["t"], p["item"], p["category"], p["count"])] if len(p) else [],
        ))
    builds = []
    if "builds" in have:
        b = _q(con, f"""SELECT t, material FROM builds WHERE match_id = '{mid}' AND team_index = {ti}
                        AND coalesce(player_placed, TRUE) ORDER BY t""")
        builds = [dict(t=float(t), material=None if mt != mt else str(mt)) for t, mt in zip(b.get("t", []), b.get("material", []))]
    return dict(players=players, builds=builds, has=dict(health="health" in have and len(hp) > 0, weapons=len(wh) > 0,
                                                          pickups=len(pk) > 0, builds=len(builds) > 0))


def _surge(ctx: Context, mid: str, ti: int, t0: float, t1: float) -> dict | None:
    """The team's net damage since the current zone appeared, sampled over the game, and the zone's safe line."""
    con = ctx.con
    if not has_tables(con, "damage"):
        return None
    from ._events_common import player_hits
    hits = player_hits(con)
    hits = hits[hits["match_id"] == mid]
    zones = df(con, f"SELECT phase, finish_shrink_t FROM zones WHERE match_id = '{mid}' ORDER BY phase")
    appear = {int(p) + 1: float(t) for p, t in zip(zones["phase"], zones["finish_shrink_t"]) if t == t}
    if not appear:
        return None
    ts = np.arange(t0, t1 + SURGE_STEP, SURGE_STEP)
    zone_at = np.array([max([k for k, a in appear.items() if a <= t], default=0) for t in ts])
    dealt = hits[hits["attacker_team"] == ti][["t", "amount"]].to_numpy(float)
    taken = hits[hits["target_team"] == ti][["t", "amount"]].to_numpy(float)
    net, dl, tk = [], [], []
    for t, k in zip(ts, zone_at):
        a = appear.get(int(k), -1e9)
        d_ = dealt[(dealt[:, 0] >= a) & (dealt[:, 0] <= t), 1].sum() if len(dealt) else 0.0
        t_ = taken[(taken[:, 0] >= a) & (taken[:, 0] <= t), 1].sum() if len(taken) else 0.0
        dl.append(round(float(d_))); tk.append(round(float(t_))); net.append(round(float(d_ - t_)))
    lines, episodes, rule = {}, [], None
    from .surge_study import measured
    m = measured(ctx)
    if m is not None and len(m["per"]):
        rule = m["label"]
        first = m["ep"].dropna(subset=["cutoff"]).sort_values("t0").drop_duplicates(["match_id", "zone"])
        lines = {int(k): round(float(q["cutoff"].median())) for k, q in first.groupby("zone") if len(q) >= 3}
        per = m["per"][(m["per"]["match_id"] == mid)]
        for epi, q in per.groupby("episode"):
            e = m["ep"][m["ep"]["episode"] == epi].iloc[0]
            mine = q[q["team_index"] == ti]
            episodes.append(dict(t=float(e["t0"]), zone=int(e["zone"]), surged=bool(mine["surged"].any()) if len(mine) else None))
    return dict(t=ts.round(1).tolist(), zone=zone_at.tolist(), net=net, dealt=dl, taken=tk, lines=lines, episodes=episodes, rule=rule)


def _decisions(ctx: Context, mid: str, ti: int) -> list[dict]:
    from .engine_review import decisions
    from .. import engine as eng
    ed = decisions(ctx, PERCEIVE_M)
    if ed is None or not len(ed["d"]):
        return []
    em = ed["d"][(ed["d"]["match_id"] == mid) & (ed["d"]["team_index"] == ti)].sort_values("t")
    out = []
    for _, q in em.iterrows():
        opts = sorted(((k, eng.ACTIONS[k], float(q[f"ev_{k}"])) for k in eng.ACTIONS if q[f"ev_{k}"] > -1e8), key=lambda x: -x[2])
        fight = None
        if "p_win" in q and q["p_win"] == q["p_win"]:
            fight = dict(p_win=round(float(q["p_win"]), 2), if_won=round(float(q["ev_win"]), 1), if_lost=round(float(q["ev_lose"]), 1))
        out.append(dict(
            t=float(q["t"]), zone=int(q["zone"]), engine=q["engine"], actual=q["actual"], stake=round(float(q["stake"]), 1),
            followed=bool(q["followed"]), options=[dict(key=k, label=lab, ev=round(v, 1)) for k, lab, v in opts], fight=fight,
            knew=dict(outside_m=round(float(q["outside_m"])), hp=round(float(q["hp"])), teams=int(q["teams_alive"]),
                      members=int(q["members"]), seen=int(q["seen"]), seen_close=int(q["seen_close"]),
                      surge="above" if q["surge_margin"] >= 0 else "below", kills=int(q["kills"]))))
    return out


@register("match_room", "Match room",
          "One team's game played back on the map: both players' health, shields and weapons, the team's surge score against the "
          "line, and at every decision what they did against the best option by expected points.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game", "match", "")])
def run(ctx: Context) -> Result:
    from .match_map import run as match_map
    r = match_map(ctx)
    chart = next((c for c in r.charts if c["kind"] == "match_replay"), None)
    if chart is None:                      # no team typed or found: the match map's team list and prompt
        return r
    o = chart["options"]
    mid, ti, con = o["match"], int(o["team"]), ctx.con
    scheme = scoring.active()
    team = _find_team(con, str(ctx.params.get("team") or ""))
    members = df(con, f"""SELECT id, name, death_t FROM players WHERE match_id = '{mid}' AND team_index = {ti}
                          AND NOT coalesce(is_bot, FALSE) ORDER BY id""")
    hud = _hud(con, mid, members, ti)
    surge = None
    try:
        surge = _surge(ctx, mid, ti, float(o["t0"]), float(o["t1"]))
    except Exception as e:  # noqa: BLE001 - the room works without the surge strip
        r.warnings.append(f"Couldn't build the surge score: {type(e).__name__}: {e}")
    decisions = []
    try:
        decisions = _decisions(ctx, mid, ti)
    except Exception as e:  # noqa: BLE001 - the room works without the engine
        r.warnings.append(f"Couldn't price the decisions: {type(e).__name__}: {e}")

    games = df(con, "SELECT m.match_id, m.replay_timestamp, m.match_date FROM matches m JOIN sel USING (match_id)")
    games = games[games["match_id"].isin(team["match_id"])].sort_values(["replay_timestamp", "match_id"]).reset_index(drop=True)
    tg = team.groupby("match_id").agg(placement=("placement", "min"), kills=("kills", "sum")).reset_index()
    tg["points"] = scheme.total(tg["placement"], tg["kills"]).to_numpy(float)
    games = games.merge(tg, on="match_id", how="left")
    o["games"] = [dict(match=m, game=i + 1, date=str(d)[:10], placement=None if p != p else int(p), points=None if pt != pt else round(float(pt)))
                  for i, (m, d, p, pt) in enumerate(zip(games["match_id"], games["match_date"], games["placement"], games["points"]))]
    o["hud"], o["surge"], o["decisions"] = hud, surge, decisions
    o["scoring"] = scheme.label
    from .. import engine as eng
    o["actions"] = dict(eng.ACTIONS)
    chart["title"] = "Match room"
    g = next((x for x in o["games"] if x["match"] == mid), None)
    missed = [d for d in decisions if not d["followed"] and d["stake"] >= 1]
    r.headline = (f"{o['team_name']}, game {g['game'] if g else '?'}: placed {g['placement'] if g else '?'}, {g['points'] if g else '?'} points. "
                  f"{len(missed)} decision{'s' if len(missed) != 1 else ''} where another option was worth a point or more.")
    r.metric("Decisions worth 1+ point", f"{len(missed)} of {len(decisions)}", "Decision points where the best option beat what the team did by a point or more")
    if not hud["has"]["health"]:
        r.notes.append("No health data for this game: re-process it with data option 7 for health and shield bars.")
    r.notes.append("Materials and item counts aren't in the replay data the pipeline reads: the HUD shows weapons seen in hand, items "
                   "picked up and the team's build pieces. The extractor's season survey shows whether these replays carry the inventory.")
    return r
