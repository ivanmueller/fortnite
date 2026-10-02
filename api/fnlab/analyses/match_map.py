"""
Match map: an interactive replay of one game with the analysed team highlighted, and a rotation plan at every
zone, with its reasoning:
  time budget   when the zone appeared, when it starts and finishes closing, distance to its safe edge
  travel        that distance at the running speed players actually rotate at (this match)
  leave by      when players who arrived ahead of comparable players (same zone, similar distance; all
                selected matches) left after the reveal; compared with when the team left
  traffic       other teams within 60 m of the straight route in, and near the entry point; a less crowded
                entry point when there is one
  surge base    zones 3-4: the spot facing the routes teams outside the zone must take (as in the Game review)
  forecast      zone 2: the spot the zone forecast favoured (trained without this game)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register
from ._events_common import has_tables
from .audit import _find_team, _team_list
from .review import _lane_points, _surge_base

STEP = 2          # seconds between replay samples
ROUTE_M = 60      # a team this close to the straight route in is "on your route"


def _seg_dist(px, py, ax, ay, bx, by):
    """Distance from points (px, py) to the segment a-b."""
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy or 1.0
    t = np.clip(((px - ax) * vx + (py - ay) * vy) / L2, 0, 1)
    return np.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _fmt_t(s: float) -> str:
    return "–" if s != s else f"{int(s // 60)}:{int(s % 60):02d}"


@register("match_map", "Match map",
          "An interactive replay of one game with the team highlighted, and the reasoning behind every rotation: time budget, "
          "when comparable players left, other teams on the route, a better entry point, and the surge base.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game to show", "match", "")])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    text = str(ctx.params.get("team") or "").strip()
    team = _find_team(con, text) if text else pd.DataFrame()
    if team.empty:
        tl = _team_list(con)
        if len(tl):
            r.table("Teams in the selected matches", tl.head(60))
        r.headline = "Type the team's names above, then choose a game."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    games = df(con, "SELECT m.match_id, m.replay_timestamp FROM matches m JOIN sel USING (match_id)")
    games = games[games["match_id"].isin(team["match_id"])].sort_values(["replay_timestamp", "match_id"]).reset_index(drop=True)
    gnum = dict(zip(games["match_id"], games.index + 1))
    mid = ctx.params.get("match") if ctx.params.get("match") in gnum else games["match_id"].iloc[-1]
    me = team[team["match_id"] == mid]
    ti = int(me["team_index"].iat[0])
    ids = set(me["id"].tolist())

    players = df(con, f"""SELECT id, name, team_index, death_t, coalesce(team_placement, placement) AS final, is_bot
                          FROM players WHERE match_id = '{mid}'""")
    pos = df(con, f"SELECT id, t, x, y, z, coalesce(vx, 0) AS vx, coalesce(vy, 0) AS vy FROM positions WHERE match_id = '{mid}' ORDER BY t")
    zones = df(con, f"SELECT * FROM zones WHERE match_id = '{mid}' ORDER BY phase")
    land_t = df(con, f"SELECT id, land_t FROM landings WHERE match_id = '{mid}'") if has_tables(con, "landings") else pd.DataFrame(columns=["id", "land_t"])
    t_start = float(land_t["land_t"].min()) - 20 if len(land_t) else float(pos["t"].min())

    # ---- replay payload: every player's path (every STEP s, metres), the storm, eliminations, the team's health
    tracks = []
    for pid, g in pos[pos["t"] >= t_start].groupby("id"):
        g = g.iloc[::STEP]
        p = players[players["id"] == pid]
        if p.empty:
            continue
        p = p.iloc[0]
        tracks.append(dict(id=int(pid), team=int(p["team_index"]) if p["team_index"] == p["team_index"] else -1,
                           name=str(p["name"]) if not p["is_bot"] else "Bot", bot=bool(p["is_bot"]), final=None if p["final"] != p["final"] else int(p["final"]),
                           death=None if p["death_t"] != p["death_t"] else float(p["death_t"]), mine=pid in ids,
                           t=g["t"].round(1).tolist(), x=(g["x"] / 100).round(1).tolist(), y=(g["y"] / 100).round(1).tolist()))
    storm = []
    prev_finish = None
    for _, z in zones.iterrows():
        storm.append(dict(zone=int(z["phase"]), appear=prev_finish, start=float(z["start_shrink_t"]), finish=float(z["finish_shrink_t"]),
                          cur=None if z["cur_x"] != z["cur_x"] else [z["cur_x"] / 100, z["cur_y"] / 100, z["cur_r"] / 100],
                          next=[z["next_x"] / 100, z["next_y"] / 100, z["next_r"] / 100]))
        prev_finish = float(z["finish_shrink_t"])
    kills = df(con, f"""SELECT k.t, k.x, k.y, pv.team_index AS victim_team, pf.team_index AS killer_team FROM kills k
                        LEFT JOIN players pv ON pv.match_id = k.match_id AND pv.id = k.victim_id
                        LEFT JOIN players pf ON pf.match_id = k.match_id AND pf.id = k.finisher_id
                        WHERE k.match_id = '{mid}' AND NOT coalesce(k.downed, FALSE)""")
    hp = []
    if has_tables(con, "health"):
        h = df(con, f"SELECT id, t, coalesce(health, 0) + coalesce(shield, 0) AS hp FROM health WHERE match_id = '{mid}' ORDER BY t")
        for pid, g in h[h["id"].isin(ids)].groupby("id"):
            hp.append(dict(id=int(pid), t=g["t"].round(1).tolist(), hp=g["hp"].round(0).tolist()))
    land_cells, pois = [], []
    from .zone_forecast import _load as zf_load
    zf = zf_load(ctx)
    land = zf["land"] if zf else None
    if land is None:
        from .. import zone_model as zm
        season = df(con, f"SELECT season FROM matches WHERE match_id = '{mid}'")
        if len(season):
            cells = df(con, zm.land_cells_sql(season["season"].iat[0]))
            land = zm.LandMap(cells) if len(cells) >= 50 else None
    if land is not None:
        ii, jj = np.nonzero(land.land)
        land_cells = dict(cell=25, x=((ii + land.x0) * 25).tolist(), y=((jj + land.y0) * 25).tolist())
    if "pois" in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}:
        P = df(con, "SELECT name, x, y FROM pois WHERE kind = 'poi'")
        pois = [dict(name=n, x=float(x) / 100, y=float(y) / 100) for n, x, y in zip(P["name"], P["x"], P["y"])]

    # ---- rotation plan for every zone
    from .rotation import rotations
    rot, _, _ = rotations(ctx)
    run_ms = 5.5
    mv = np.hypot(pos["vx"], pos["vy"]) / 100
    if (mv > 2.5).sum() > 200:
        run_ms = float(np.median(mv[mv > 2.5]))                       # how fast players actually move when rotating
    reveal = {int(p) + 1: float(t) for p, t in zip(zones["phase"], zones["finish_shrink_t"])}
    circ = {int(p): (float(x), float(y), float(rr)) for p, x, y, rr in zip(zones["phase"], zones["next_x"], zones["next_y"], zones["next_r"])}
    zrow = {int(p): z for p, z in zip(zones["phase"], [row for _, row in zones.iterrows()])}
    my_death = me["death_t"].max() if me["death_t"].notna().all() else np.inf
    plans, rows = [], []
    for k in sorted(z for z in reveal if z in circ):
        tk = reveal[k]
        if tk >= my_death:
            break
        now = pos[pos["t"] <= tk].groupby("id").tail(1)
        mine = now[now["id"].isin(ids)]
        if mine.empty:
            continue
        mx, my = mine["x"].mean(), mine["y"].mean()
        zx, zy, zr = circ[k]
        d_c = np.hypot(mx - zx, my - zy)
        out_m = max(0.0, (d_c - zr) / 100)
        z = zrow[k]
        start, finish = float(z["start_shrink_t"]), float(z["finish_shrink_t"])
        travel = out_m / run_ms
        # when comparable players (same zone, similar distance out) who arrived ahead set off
        comp = rot[(rot["phase"] == k) & (rot["timing"] == "Ahead")] if len(rot) else rot
        if len(comp) and out_m > 0:
            near = comp[comp["outside_m"].between(out_m * 0.5, out_m * 1.5 + 30)] if "outside_m" in comp else comp
            near = near if len(near) >= 10 else comp
            ahead_delay = float(near["depart_delay_s"].median())
        else:
            ahead_delay = np.nan
        latest = finish - travel
        leave_by = tk + ahead_delay if ahead_delay == ahead_delay else latest
        leave_by = min(leave_by, latest) if out_m > 0 else np.nan
        my_rot = rot[(rot["match_id"] == mid) & (rot["phase"] == k) & rot["id"].isin(ids)] if len(rot) else rot
        left = float(my_rot["depart_t"].max()) if len(my_rot) and my_rot["depart_t"].notna().any() else np.nan
        # entry point and traffic: other teams near the straight route in, and near the entry
        others = players[~players["id"].isin(ids) & (players["death_t"].isna() | (players["death_t"] > tk)) & ~players["is_bot"].astype(bool)]
        onow = now.merge(others[["id", "team_index"]], on="id")
        tpos = onow.groupby("team_index")[["x", "y", "z"]].mean()
        entry = (zx + (mx - zx) / max(d_c, 1) * zr * 0.92, zy + (my - zy) / max(d_c, 1) * zr * 0.92)
        on_route = int((_seg_dist(tpos["x"].to_numpy(), tpos["y"].to_numpy(), mx, my, *entry) / 100 <= ROUTE_M).sum()) if out_m > 0 else 0
        at_entry = int((np.hypot(tpos["x"] - entry[0], tpos["y"] - entry[1]) / 100 <= ROUTE_M).sum())
        alt = None
        if out_m > 0 and (on_route + at_entry) > 0:
            base_ang = np.arctan2(my - zy, mx - zx)
            best = (on_route + at_entry, 0.0, entry)
            for da in np.radians(np.arange(-70, 71, 10)):
                e = (zx + np.cos(base_ang + da) * zr * 0.92, zy + np.sin(base_ang + da) * zr * 0.92)
                route = np.hypot(e[0] - mx, e[1] - my) / 100
                traffic = int((_seg_dist(tpos["x"].to_numpy(), tpos["y"].to_numpy(), mx, my, *e) / 100 <= ROUTE_M).sum()) + \
                    int((np.hypot(tpos["x"] - e[0], tpos["y"] - e[1]) / 100 <= ROUTE_M).sum())
                extra = route - out_m
                if traffic < best[0] and extra <= 120:
                    best = (traffic, extra, e)
            if best[2] is not entry:
                alt = dict(x=best[2][0] / 100, y=best[2][1] / 100, traffic=best[0], extra_m=round(best[1]))
        # surge base (zones 3-4), facing the routes teams outside the zone must take
        surge = None
        outside = tpos[np.hypot(tpos["x"] - zx, tpos["y"] - zy) > zr]
        lanes = []
        for _, t_ in outside.iterrows():
            dd = np.hypot(zx - t_["x"], zy - t_["y"])
            lanes.append([t_["x"] / 100, t_["y"] / 100, (zx + (t_["x"] - zx) / dd * zr * 0.85) / 100, (zy + (t_["y"] - zy) / dd * zr * 0.85) / 100])
        if 3 <= k <= 4:
            b = _surge_base(_lane_points(outside, zx, zy, zr), tpos[["x", "y"]].to_numpy(float), zx, zy, zr, land, (mx, my))
            if b is not None:
                surge = dict(x=b[0] / 100, y=b[1] / 100, lanes_in_range=b[2], height_m=round(b[3]),
                             distance_m=round(float(np.hypot(b[0] - mx, b[1] - my) / 100)))
        reasons = [f"Zone {k} appeared at {_fmt_t(tk)}; it starts closing at {_fmt_t(start)} and finishes at {_fmt_t(finish)}."]
        if out_m > 0:
            reasons.append(f"You were {out_m:.0f} m from its safe edge: about {travel:.0f} s at the {run_ms:.1f} m/s players rotate at.")
            if ahead_delay == ahead_delay:
                reasons.append(f"Players who arrived ahead of others starting this far out left a median {ahead_delay:.0f} s after the reveal.")
            reasons.append(f"Leave by {_fmt_t(leave_by)}" + (f"; you left at {_fmt_t(left)} ({left - leave_by:+.0f} s)." if left == left else "."))
            reasons.append(f"{on_route} team{'s' if on_route != 1 else ''} within {ROUTE_M} m of your straight route in, {at_entry} at the entry point."
                           + (f" A less crowded entry {alt['extra_m']} m longer had {alt['traffic']}." if alt else ""))
        else:
            reasons.append("You were already inside the next zone.")
        if len(outside):
            reasons.append(f"{len(outside)} team{'s' if len(outside) != 1 else ''} outside the zone must rotate in (dashed lines).")
        if surge:
            reasons.append(f"Surge base: {surge['distance_m']} m from you, facing {surge['lanes_in_range']} route points at 20–80 m"
                           + (f", from {surge['height_m']} m higher natural ground" if surge["height_m"] >= 5 else "") + ".")
        plans.append(dict(zone=k, t=tk, start=start, finish=finish, out_m=round(out_m), travel_s=round(travel),
                          leave_by=None if leave_by != leave_by else round(leave_by, 1), left=None if left != left else round(left, 1),
                          you=[mx / 100, my / 100], entry=[entry[0] / 100, entry[1] / 100] if out_m > 0 else None, alt_entry=alt,
                          lanes=lanes, surge=surge, reasons=reasons))
        rows.append({"Zone": k, "Appears": _fmt_t(tk), "Closes": f"{_fmt_t(start)}–{_fmt_t(finish)}", "Distance to safe edge": f"{out_m:.0f} m",
                     "Travel": f"{travel:.0f} s" if out_m > 0 else "–", "Leave by": _fmt_t(leave_by), "You left": _fmt_t(left),
                     "Teams on your route": on_route if out_m > 0 else "–", "Better entry": f"{alt['extra_m']} m longer, {alt['traffic']} teams" if alt else "–",
                     "Surge base": f"{surge['distance_m']} m away" if surge else "–"})

    calib = None
    try:
        from ..mapapi import calibration
        calib = calibration()
    except Exception:  # noqa: BLE001
        calib = None
    # the engine's calls for this team (live knowledge only), shown on the timeline and in the panel
    engine_calls = []
    try:
        from .engine_review import decisions as engine_decisions
        from .. import engine as eng
        ed = engine_decisions(ctx, 120.0)
        if ed is not None and len(ed["d"]):
            em = ed["d"][(ed["d"]["match_id"] == mid) & (ed["d"]["team_index"] == ti)].sort_values("t")
            for _, q in em.iterrows():
                opts = sorted(((eng.ACTIONS[k], float(q[f"ev_{k}"])) for k in eng.ACTIONS if q[f"ev_{k}"] > -1e8), key=lambda x: -x[1])
                engine_calls.append(dict(t=float(q["t"]), engine=eng.ACTIONS[q["engine"]], you=eng.ACTIONS[q["actual"]],
                                         stake=round(float(q["stake"]), 1), followed=bool(q["followed"]),
                                         options=[f"{a}: {v:.1f}" for a, v in opts[:4]]))
    except Exception as e:  # noqa: BLE001 - the map works without the engine
        import traceback
        traceback.print_exc()
        r.notes.append(f"The engine's calls couldn't be added to the map ({type(e).__name__}).")
        engine_calls = []
    r.chart("match_replay", "Match map", [], tracks=tracks, storm=storm, plans=plans, hp=hp, land=land_cells, pois=pois, calibration=calib,
            engine=engine_calls,
            kills=[dict(t=float(t), x=float(x) / 100, y=float(y) / 100, victim=int(v) if v == v else -1, killer=int(k_) if k_ == k_ else -1)
                   for t, x, y, v, k_ in zip(kills["t"], kills["x"], kills["y"], kills["victim_team"], kills["killer_team"]) if x == x],
            team=ti, team_name=" & ".join(sorted(me["name"].dropna().unique())), t0=t_start, t1=float(pos["t"].max()), match=mid)
    r.table("Rotation plan", pd.DataFrame(rows) if rows else pd.DataFrame([{"Zone": "–", "Appears": "No zones while alive"}]))
    late = [p_ for p_ in plans if p_["left"] is not None and p_["leave_by"] is not None and p_["left"] - p_["leave_by"] > 10]
    r.metric("Rotations left late", f"{len(late)} of {sum(1 for p_ in plans if p_['out_m'] > 0)}",
             "Zones where they left more than 10 s after the leave-by time")
    r.headline = (f"{' & '.join(sorted(me['name'].dropna().unique()))}, game {gnum[mid]}: press play, or jump to a zone. "
                  f"{len(late)} rotation{'s' if len(late) != 1 else ''} left more than 10 s after the leave-by time.")
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        f"Running speed: {run_ms:.1f} m/s, the median speed of players moving in this match. Mobility items make real rotations faster.",
        "Leave by: when players who arrived ahead of comparable players in the selected matches set off, or the latest time to "
        "reach the zone before it finishes closing, whichever is earlier.",
        f"Teams on your route: other teams within {ROUTE_M} m of the straight line from you to the zone's edge when it appeared.",
    ]
    return r
