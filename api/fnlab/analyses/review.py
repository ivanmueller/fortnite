"""
Game review: one team, one game, in three stages. For each moment: what they did, what the system recommends
knowing only what they could know then, why (the evidence from the selected matches), and what it was worth
in expected points (the expected-points model, the team's real situation vs the recommended one).

  Early game  drop, time to a full loadout, position when zone 2 appeared vs the forecast's best spot
  Mid game    zones 3-6: rotation timing; zones 3-4 surge base facing the lanes teams outside the zone must take
              (from their positions when the zone appeared, not where they later went); zones 5-6 the centre
  Endgame     zones 7+: centre, inside, height, health, teammate spacing, how it ended
Endgame evidence from the selected matches: duo spacing vs finish, and pushing up onto a higher team.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import softmax

from .. import ep_model as ep
from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register
from ._events_common import has_tables
from .audit import PLACEMENT_POINTS, KILL_POINTS, _find_team, _team_list

TAG_BAND = (20, 80)        # metres: tagging range for predicted lanes
_EVID: dict = {}


def _pos_at(pos: pd.DataFrame, ids, t: float) -> pd.DataFrame:
    p = pos[pos["id"].isin(ids) & (pos["t"] <= t)]
    return p.sort_values("t").groupby("id").tail(1)


def _ep_value(epd, mid, team_index, t, **changes):
    """Expected points now (actual) and with the given changes (recommended), at the snapshot just after t."""
    if epd is None or not epd.get("reliable"):
        return np.nan, np.nan
    model = epd["model"].fold_models_.get(epd["model"].fold_of_.get(mid))    # a model that never saw this game
    if model is None:
        return np.nan, np.nan
    tm = epd["team"]
    s = tm[(tm["match_id"] == mid) & (tm["team_index"] == team_index) & (tm["t"] >= t - 0.5)].sort_values("t").head(1)
    if s.empty:
        return np.nan, np.nan
    row = s[model.features_].iloc[0].to_dict()
    alt = dict(row, **{k: (v(row) if callable(v) else v) for k, v in changes.items()})
    a, b = model.predict(pd.DataFrame([row, alt])[model.features_].to_numpy(float))
    return float(a), float(b)


def _fmt_pts(a, b) -> str:
    return "–" if a != a or b != b else f"{b - a:+.1f} pts ({a:.0f} → {b:.0f})"


def _evidence(ctx: Context) -> dict:
    """Selection-wide endgame evidence, cached: duo spacing vs finish, and pushing up onto a higher team."""
    key = tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"]))
    if key in _EVID:
        return _EVID[key]
    con, out = ctx.con, {}
    sp = df(con, """
        WITH r AS (SELECT z.match_id, z.phase + 1 AS zone, z.finish_shrink_t AS t FROM zones z JOIN sel USING (match_id) WHERE z.phase >= 6),
             a AS (SELECT r.*, p.id, p.team_index, coalesce(p.team_placement, p.placement) AS final FROM r JOIN players p USING (match_id)
                   WHERE NOT coalesce(p.is_bot, FALSE) AND (p.death_t IS NULL OR p.death_t > r.t))
        SELECT a.*, pos.x, pos.y FROM a ASOF JOIN positions pos ON a.match_id = pos.match_id AND a.id = pos.id AND a.t >= pos.t""")
    if len(sp):
        g = sp.groupby(["match_id", "zone", "team_index"])
        duo = g.agg(n=("id", "size"), final=("final", "min"), x0=("x", "first"), x1=("x", "last"), y0=("y", "first"), y1=("y", "last")).reset_index()
        duo["ahead"] = duo.groupby(["match_id", "zone"])["final"].rank(pct=True)
        duo = duo[duo["n"] == 2]
        duo["spacing"] = np.hypot(duo["x0"] - duo["x1"], duo["y0"] - duo["y1"]) / 100
        if len(duo) >= 30:
            duo["band"] = pd.cut(duo["spacing"], [0, 10, 30, 60, 1e9], labels=["Within 10 m", "10–30 m", "30–60 m", "60 m+"], right=False)
            t = duo.groupby("band", observed=False).agg(teams=("ahead", "size"), ahead=("ahead", "mean"))
            out["spacing"] = pd.DataFrame({"Teammates' distance apart (zones 7+)": t.index.astype(str), "Team moments": t["teams"].values,
                                           "Finished ahead of the teams alive then": [f"{1 - v:.0%}" if v == v else "–" for v in t["ahead"]]})
            out["spacing_raw"] = t
    if has_tables(con, "damage", "health"):
        from .fights import fights
        f = fights(ctx)
        if len(f):
            con.register("rv_f", f[["match_id", "t0", "team_a", "team_b", "first", "winner"]])
            z = df(con, """
                WITH fz AS (SELECT f.*, (SELECT max(z.phase) + 1 FROM zones z WHERE z.match_id = f.match_id AND z.finish_shrink_t <= f.t0) AS zone FROM rv_f f),
                     pp AS (SELECT p.match_id, p.id, p.team_index FROM players p JOIN sel USING (match_id)),
                     h AS (SELECT fz.match_id, fz.t0, pp.team_index, avg(pos.z) AS z FROM fz JOIN pp ON pp.match_id = fz.match_id
                           AND pp.team_index IN (fz.team_a, fz.team_b)
                           ASOF JOIN positions pos ON pos.match_id = pp.match_id AND pos.id = pp.id AND fz.t0 >= pos.t
                           GROUP BY 1, 2, 3)
                SELECT fz.*, ha.z AS za, hb.z AS zb FROM fz JOIN h ha ON ha.match_id = fz.match_id AND ha.t0 = fz.t0 AND ha.team_index = fz.team_a
                JOIN h hb ON hb.match_id = fz.match_id AND hb.t0 = fz.t0 AND hb.team_index = fz.team_b WHERE fz.zone >= 7""")
            z = z.dropna(subset=["first", "za", "zb"])
            if len(z) >= 20:
                init_lower = np.where(z["first"] == z["team_a"], z["zb"] - z["za"], z["za"] - z["zb"]) / 100   # >0: initiator below
                z["kind"] = pd.cut(init_lower, [-1e9, -5, 5, 1e9], labels=["Attacking down from higher ground", "Level", "Pushing up onto a higher team"])
                z["won"] = z["winner"] == z["first"]
                t = z.groupby("kind", observed=False).agg(fights=("won", "size"), won=("won", "mean"))
                out["uphill"] = pd.DataFrame({"Endgame fight (zones 7+), from the attacker's side": t.index.astype(str), "Fights": t["fights"].values,
                                              "Attacker won": [f"{v:.0%}" if v == v else "–" for v in t["won"]]})
                out["uphill_raw"] = t
    _EVID.clear()
    _EVID[key] = out
    return out


def _lane_points(teams_out: pd.DataFrame, zx: float, zy: float, zr: float) -> np.ndarray:
    """Straight-line routes from teams outside the zone to its edge, sampled every 10 m (where they'll have to rotate)."""
    pts = []
    for _, t in teams_out.iterrows():
        dx, dy = zx - t["x"], zy - t["y"]
        d = np.hypot(dx, dy)
        if d <= zr:
            continue
        entry = d - zr
        n = max(2, int(entry / 1000))
        for f in np.linspace(0, 1, n):
            s = entry * f + zr * 0.15 * f        # continue a little inside the edge
            pts.append((t["x"] + dx / d * s, t["y"] + dy / d * s, t["z"]))
    return np.array(pts) if pts else np.zeros((0, 3))


def _surge_base(lane: np.ndarray, danger_xy: np.ndarray, zx, zy, zr, land, near_xy) -> tuple | None:
    """Best spot in the zone facing the predicted lanes at tagging range, from natural height, with no enemy within 30 m."""
    if not len(lane):
        return None
    g = np.linspace(-1, 1, 30)
    gx, gy = np.meshgrid(g, g)
    keep = gx ** 2 + gy ** 2 <= 0.9
    sx, sy = zx + gx[keep] * zr, zy + gy[keep] * zr
    gz = np.zeros(len(sx))
    if land is not None:
        _, h = land.lookup(sx, sy)
        gz = np.nan_to_num(h, nan=np.nanmedian(h) if np.isfinite(h).any() else 0)
    dx, dy = lane[None, :, 0] - sx[:, None], lane[None, :, 1] - sy[:, None]
    d = np.hypot(dx, dy) / 100
    ox, oy = sx - zx, sy - zy
    facing = (dx * ox[:, None] + dy * oy[:, None]) / (d * 100 * (np.hypot(ox, oy)[:, None] + 1e-9) + 1e-9) > 0.3
    score = ((d >= TAG_BAND[0]) & (d <= TAG_BAND[1]) & facing).sum(1) + ((d >= TAG_BAND[0]) & (d <= TAG_BAND[1]) & facing &
                                                                          (gz[:, None] - lane[None, :, 2] >= 1500)).sum(1)
    if len(danger_xy):
        close = (np.hypot(danger_xy[None, :, 0] - sx[:, None], danger_xy[None, :, 1] - sy[:, None]) / 100 < 30).any(1)
        score = np.where(close, -1, score)
    if score.max() <= 0:
        return None
    top = score >= 0.9 * score.max()
    i = np.argmin(np.where(top, np.hypot(sx - near_xy[0], sy - near_xy[1]), np.inf))   # the strong spot closest to them
    return float(sx[i]), float(sy[i]), int(score[i]), float((gz[i] - np.median(gz)) / 100)


@register("review", "Game review",
          "One team, one game, in three stages: what they did, what the system recommends knowing only what they knew then, "
          "the evidence, and what each decision was worth in expected points.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game to review", "match", "")])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    text = str(ctx.params.get("team") or "").strip()
    team = _find_team(con, text) if text else pd.DataFrame()
    if team.empty:
        tl = _team_list(con)
        if len(tl):
            r.table("Teams in the selected matches", tl.head(60))
        r.headline = "Type the team's names above (at a LAN, find them in the table below by their results), then choose a game."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    games = df(con, "SELECT m.match_id, m.replay_timestamp FROM matches m JOIN sel USING (match_id)")
    games = games[games["match_id"].isin(team["match_id"])].sort_values(["replay_timestamp", "match_id"]).reset_index(drop=True)
    gnum = dict(zip(games["match_id"], games.index + 1))
    mid = ctx.params.get("match") if ctx.params.get("match") in gnum else games["match_id"].iloc[-1]
    me = team[team["match_id"] == mid]
    ti = int(me["team_index"].iat[0])
    ids = me["id"].tolist()
    names = " & ".join(sorted(me["name"].dropna().unique()))
    placement = int(me["placement"].min())
    kills = int(me["kills"].fillna(0).sum())
    pts = PLACEMENT_POINTS.get(placement, 0) + KILL_POINTS * kills

    zones = df(con, f"SELECT * FROM zones WHERE match_id = '{mid}' ORDER BY phase")
    reveal = {int(p) + 1: float(t) for p, t in zip(zones["phase"], zones["finish_shrink_t"])}       # zone k appears
    circle = {int(p): (float(x), float(y), float(rr)) for p, x, y, rr in zip(zones["phase"], zones["next_x"], zones["next_y"], zones["next_r"])}
    ztype = dict(zip(*[df(con, f"SELECT phase, zone_type FROM zone_offsets WHERE match_id = '{mid}'")[c] for c in ("phase", "zone_type")])) \
        if "zone_offsets" in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()} else {}
    pos = df(con, f"SELECT p.id, p.t, p.x, p.y, p.z FROM positions p WHERE p.match_id = '{mid}'")
    pl = df(con, f"SELECT id, team_index, death_t, coalesce(team_placement, placement) AS final FROM players WHERE match_id = '{mid}' AND NOT coalesce(is_bot, FALSE)")
    out_t = me["death_t"].max() if me["death_t"].notna().all() else np.inf

    from .expected_points import _load as ep_load
    try:
        epd = ep_load(ctx)
    except Exception:  # noqa: BLE001 - the review still works without expected points
        epd = None
    from .zone_forecast import _load as zf_load
    zf = zf_load(ctx)
    land = zf["land"] if zf else None
    from .rotation import rotations
    rot, _, _ = rotations(ctx)
    rot = rot[(rot["match_id"] == mid) & rot["id"].isin(ids)] if len(rot) else rot

    # ======================================================================= early game
    early, maps = [], {}
    L = df(con, f"SELECT * FROM landings WHERE match_id = '{mid}'")
    myl = L[L["id"].isin(ids)]
    if len(myl):
        contested = bool((myl["opp_150m"] > 0).any())
        poi = myl["poi"].dropna().mode().iat[0] if myl["poi"].notna().any() else "–"
        allL = df(con, "SELECT l.match_id, l.id, l.opp_150m, coalesce(p.team_placement, p.placement) AS final FROM landings l JOIN sel USING (match_id) "
                       "JOIN players p ON p.match_id = l.match_id AND p.id = l.id")
        allL["top10"] = allL["final"] <= 10
        c_rate = allL.groupby("top10")["opp_150m"].apply(lambda s: (s > 0).mean())
        early.append({"Moment": "Drop", "What you did": f"{poi}, {'contested' if contested else 'uncontested'}"
                      + (", eliminated off spawn" if myl.get("off_spawn", pd.Series(False)).any() else ""),
                      "Recommended": "Keep it" if not myl.get("off_spawn", pd.Series(False)).any() else "Reconsider the spot or the landing order",
                      "Why": f"Top-10 finishers landed contested {c_rate.get(True, np.nan):.0%} of the time, others {c_rate.get(False, np.nan):.0%}",
                      "Worth": "–"})
    if has_tables(con, "weapons_held"):
        ready = df(con, """
            WITH f AS (SELECT w.match_id, w.id, w.weapon, min(w.t) AS t FROM weapons_held w JOIN sel USING (match_id)
                       WHERE w.category = 'weapon' AND w.rarity IN ('rare', 'epic', 'legendary', 'mythic') GROUP BY 1, 2, 3),
                 r AS (SELECT match_id, id, t, row_number() OVER (PARTITION BY match_id, id ORDER BY t) AS rn FROM f)
            SELECT r.match_id, r.id, r.t - l.land_t AS ready_s, coalesce(p.team_placement, p.placement) AS final
            FROM r JOIN landings l ON l.match_id = r.match_id AND l.id = r.id JOIN players p ON p.match_id = r.match_id AND p.id = r.id
            WHERE r.rn = 2""")
        mine_r = ready[(ready["match_id"] == mid) & ready["id"].isin(ids)]["ready_s"]
        if len(mine_r):
            top = ready[ready["final"] <= 10]["ready_s"].median()
            early.append({"Moment": "Loot", "What you did": f"Two rare-or-better weapons {mine_r.max():.0f} s after landing",
                          "Recommended": f"Aim for about {top:.0f} s, the top-10 median" if mine_r.max() > top * 1.2 else "On pace",
                          "Why": f"Top-10 finishers: {top:.0f} s median; everyone: {ready['ready_s'].median():.0f} s", "Worth": "–"})
    if 2 in reveal and 2 in circle and 1 in circle:
        t2 = reveal[2]
        p2 = _pos_at(pos, ids, t2)
        if len(p2):
            mx, my = p2["x"].mean(), p2["y"].mean()
            zx, zy, zr = circle[2]
            out_m = max(0.0, (np.hypot(mx - zx, my - zy) - zr) / 100)
            rec, why = None, "Zone 2 hugs the edge of zone 1"
            if zf:
                st = next((s for s in zf["lad"]["states"] if s["match_id"] == mid and s["zone"] == 2), None)
                if st is not None:
                    fm = zf["lad"]["fold_models"][zf["lad"]["folds"][mid]]
                    _, c = zf["lad"]["with_prior"](st, fm["prior"].get(2))
                    w = softmax(fm["models"][zf["lad"]["best"]].scores(c, st["type"]))
                    c1 = circle[1]
                    g = np.linspace(-1, 1, 40)
                    gx, gy = np.meshgrid(g, g)
                    k = gx ** 2 + gy ** 2 <= 1
                    sx, sy = c1[0] + gx[k] * c1[2], c1[1] + gy[k] * c1[2]
                    pin = np.array([w[np.hypot(st["cand_x"] - x, st["cand_y"] - y) <= st["next_r"]].sum() for x, y in zip(sx, sy)])
                    good = pin >= 0.9 * pin.max()
                    j = np.argmin(np.where(good, np.hypot(sx - mx, sy - my), np.inf))
                    rec = (sx[j], sy[j], pin[j])
                    mine_p = w[np.hypot(st["cand_x"] - mx, st["cand_y"] - my) <= st["next_r"]].sum()
                    hit = zf["lad"]["results"][zf["lad"]["best"]]
                    hz = hit[hit["zone"] == 2]["hit"].mean()
                    why = (f"From zone 1 alone, the forecast gave your spot a {mine_p:.0%} chance of being inside zone 2, and the "
                           f"recommended spot {rec[2]:.0%}. Its most likely area held zone 2 in {hz:.0%} of matches it never saw.")
            a, b = _ep_value(epd, mid, ti, t2, outside_m=0.0)
            early.append({"Moment": "Zone 2 appears", "What you did": "Already inside zone 2" if out_m == 0 else f"{out_m:.0f} m outside zone 2",
                          "Recommended": (f"Be {np.hypot(rec[0] - mx, rec[1] - my) / 100:.0f} m away, at the marked spot, before zone 2 shows"
                                          if rec is not None and out_m > 0 else
                                          "Move toward the edge of zone 1 early: zone 2 lands near it" if out_m > 0 else "Keep it"),
                          "Why": why, "Worth": _fmt_pts(a, b) if out_m > 0 else "–"})
            land_t = float(myl["land_t"].min()) if len(myl) and "land_t" in myl else 0.0
            seg = pos[pos["id"].isin(ids) & pos["t"].between(land_t, t2)].iloc[::3]
            series = [dict(name="Your path to zone 2", x=seg["x"].round(0).tolist(), y=seg["y"].round(0).tolist(), color="#0F766E", size=3, opacity=0.8)]
            if rec is not None:
                series.append(dict(name="Recommended spot (forecast)", x=[round(rec[0])], y=[round(rec[1])], color="#0F3B5F", size=14))
            series.append(dict(name="You when zone 2 appeared", x=[round(mx)], y=[round(my)], color="#D08A12", size=14))
            maps["early"] = (series, [dict(x=circle[1][0], y=circle[1][1], r=circle[1][2], label="Zone 1"), dict(x=zx, y=zy, r=zr, label="Zone 2")])


    # ======================================================================= mid game
    mid_rows = []
    for k in range(3, 7):
        if k not in reveal or k not in circle or reveal[k] >= out_t:
            continue
        tk = reveal[k]
        pk = _pos_at(pos, ids, tk)
        if pk.empty:
            continue
        mx, my = pk["x"].mean(), pk["y"].mean()
        zx, zy, zr = circle[k]
        cx, cy, cr = circle[k - 1]
        out_m = max(0.0, (np.hypot(mx - zx, my - zy) - zr) / 100)
        off = np.hypot(mx - cx, my - cy) / cr
        rr = rot[rot["phase"] == k] if len(rot) else rot
        timing = ", ".join(sorted(rr["timing"].dropna().unique())) if len(rr) else "–"
        behind = len(rr) and (rr["timing"] == "Behind").any()
        kind = ztype.get(k, "shrinking")
        if kind == "shrinking":
            others = pl[(pl["team_index"] != ti) & (pl["death_t"].isna() | (pl["death_t"] > tk))]
            op = _pos_at(pos, others["id"], tk).merge(others[["id", "team_index"]], on="id")
            tp = op.groupby("team_index")[["x", "y", "z"]].mean()
            outside = tp[np.hypot(tp["x"] - zx, tp["y"] - zy) > zr]
            lane = _lane_points(outside, zx, zy, zr)
            base = _surge_base(lane, tp[["x", "y"]].to_numpy(float), zx, zy, zr, land, (mx, my))
            if base is not None:
                d = np.hypot(base[0] - mx, base[1] - my) / 100
                rec = f"Surge base {d:.0f} m away (marked), facing the routes in" if d > 40 else "Your spot faces the routes in: keep it"
                why = (f"{len(outside)} teams were outside zone {k} when it appeared; their straight routes in pass {TAG_BAND[0]}–{TAG_BAND[1]} m "
                       f"in front of this spot ({base[2]} lane points in range{', from ' + str(round(base[3])) + ' m higher ground' if base[3] >= 5 else ''})")
                maps.setdefault("mid", []).append((k, base, (mx, my), outside, (zx, zy, zr)))
            else:
                rec, why = "Hold inside, toward the side teams rotate in from", f"{len(outside)} teams outside zone {k}"
            a, b = _ep_value(epd, mid, ti, tk, outside_m=0.0) if out_m > 0 else (np.nan, np.nan)
        else:
            rec = "Hold the inner half of the zone" if off > 0.5 else "Keep it: central"
            why = "From zone 5 the next zone's direction is close to a coin flip; the centre is the only spot equally close to every possible next zone"
            a, b = _ep_value(epd, mid, ti, tk, off_centre=lambda r_: min(r_["off_centre"], 0.4), outside_m=0.0) if (off > 0.5 or out_m > 0) else (np.nan, np.nan)
        mid_rows.append({"Moment": f"Zone {k} appears ({kind})",
                         "What you did": ("inside the next zone" if out_m == 0 else f"{out_m:.0f} m outside the next zone") +
                                         f", {off:.0%} of the way to the edge; rotation: {timing.lower()}",
                         "Recommended": rec + ("; leave earlier: you fell behind comparable teams" if behind else ""),
                         "Why": why, "Worth": _fmt_pts(a, b)})


    # ======================================================================= endgame
    ev = _evidence(ctx)
    end_rows = []
    for k in sorted(z for z in reveal if z >= 7):
        tk = reveal[k]
        if tk >= out_t or k not in circle:
            continue
        pk = _pos_at(pos, ids, tk)
        if pk.empty:
            continue
        mx, my, mz = pk["x"].mean(), pk["y"].mean(), pk["z"].mean()
        zx, zy, zr = circle[k]
        cx, cy, cr = circle.get(k - 1, circle[k])
        out_m = max(0.0, (np.hypot(mx - zx, my - zy) - zr) / 100)
        off = np.hypot(mx - cx, my - cy) / cr
        alive_ids = pl[pl["death_t"].isna() | (pl["death_t"] > tk)]
        allp = _pos_at(pos, alive_ids["id"], tk)
        hrank = float((allp["z"] < mz).mean()) if len(allp) else np.nan
        spacing = float(np.hypot(pk["x"].iloc[0] - pk["x"].iloc[-1], pk["y"].iloc[0] - pk["y"].iloc[-1]) / 100) if len(pk) == 2 else np.nan
        recs, whys = [], []
        if out_m > 0 or off > 0.6:
            recs.append("Be inside, toward the centre, before the zone appears")
            whys.append("Moving zones: direction is close to random, the centre minimises the worst rotation")
        if spacing == spacing and spacing > 30 and "spacing_raw" in ev:
            t = ev["spacing_raw"]
            close, far = 1 - t["ahead"].iloc[0], 1 - t["ahead"].iloc[-1]
            recs.append("Stay within 10 m of each other")
            whys.append(f"Duos within 10 m finished ahead of {close:.0%} of the teams alive then; 60 m+ apart, {far:.0%}")
        if hrank == hrank and hrank < 1 / 3:
            recs.append("Take natural height when it's free; don't force a push up")
            if "uphill_raw" in ev:
                u = ev["uphill_raw"]
                whys.append(f"Pushing up onto a higher team won {u['won'].iloc[-1]:.0%} of endgame fights, attacking down {u['won'].iloc[0]:.0%}")
        a, b = _ep_value(epd, mid, ti, tk, outside_m=0.0, off_centre=lambda r_: min(r_["off_centre"], 0.4)) if (out_m > 0 or off > 0.6) else (np.nan, np.nan)
        end_rows.append({"Moment": f"Zone {k} appears",
                         "What you did": ("inside" if out_m == 0 else f"{out_m:.0f} m outside") + f", {off:.0%} to the edge, height rank {hrank:.0%}"
                                         + (f", {spacing:.0f} m apart" if spacing == spacing else ""),
                         "Recommended": "; ".join(recs) or "Keep it",
                         "Why": "; ".join(whys) or "Good position on every measure", "Worth": _fmt_pts(a, b)})
    # how it ended goes in the stage where it happened; stages they never reached say so
    out_zone = max([k for k, t in reveal.items() if t <= out_t], default=1) if out_t != np.inf else 99
    ending = ({"Moment": "Won the game", "What you did": f"{kills} eliminations", "Recommended": "", "Why": "", "Worth": ""} if placement == 1 else
              {"Moment": "How it ended", "What you did": f"Out at {int(out_t // 60)}:{int(out_t % 60):02d} (zone {out_zone}), placed {placement}",
               "Recommended": "See the Team audit for the last fight", "Why": "", "Worth": ""})
    stage_of_end = "early" if out_zone <= 2 else "mid" if out_zone <= 6 else "end"
    {"early": early, "mid": mid_rows, "end": end_rows}[stage_of_end if placement != 1 else "end"].append(ending)
    if placement != 1:
        if out_zone <= 2:
            mid_rows.append({"Moment": "–", "What you did": "Out before zone 3", "Recommended": "", "Why": "", "Worth": ""})
        if out_zone <= 6:
            end_rows.append({"Moment": "–", "What you did": "Out before zone 7", "Recommended": "", "Why": "", "Worth": ""})
    review = ([dict(Stage="Early game", **row) for row in early] + [dict(Stage="Mid game", **row) for row in mid_rows]
              + [dict(Stage="Endgame", **row) for row in end_rows])
    r.table("Review", pd.DataFrame(review)[["Stage", "Moment", "What you did", "Recommended", "Why", "Worth"]] if review else
            pd.DataFrame([{"Stage": "–", "Moment": "–", "What you did": "No data for this game"}]))
    for nm, title in (("spacing", "Evidence: staying together"), ("uphill", "Evidence: pushing a higher team")):
        if nm in ev:
            r.table(title, ev[nm])

    # ======================================================================= maps
    if "early" in maps:
        s, c = maps["early"]
        r.chart("map_points", "Early game map", s, x_label="Map X", y_label="Map Y", circles=c, zone_circles=True)
    if "mid" in maps:
        k, base, (mx, my), outside, (zx, zy, zr) = maps["mid"][0]
        tk = reveal[k]
        seg = pos[pos["id"].isin(ids) & pos["t"].between(reveal.get(3, 0), reveal.get(7, tk + 300))].iloc[::3]
        s = [dict(name="Your path, zones 3–6", x=seg["x"].round(0).tolist(), y=seg["y"].round(0).tolist(), color="#0F766E", size=3, opacity=0.8),
             dict(name=f"Teams outside zone {k} when it appeared", x=outside["x"].round(0).tolist(), y=outside["y"].round(0).tolist(), color="#B4535F", size=9),
             dict(name=f"Recommended surge base (zone {k})", x=[round(base[0])], y=[round(base[1])], color="#0F3B5F", size=14),
             dict(name=f"You when zone {k} appeared", x=[round(mx)], y=[round(my)], color="#D08A12", size=14)]
        circles = [dict(x=circle[q][0], y=circle[q][1], r=circle[q][2], label=f"Zone {q}") for q in range(2, 7) if q in circle]
        r.chart("map_points", "Mid game map", s, x_label="Map X", y_label="Map Y", circles=circles, zone_circles=True)
    if 7 in reveal and reveal[7] < out_t:
        seg = pos[pos["id"].isin(ids) & (pos["t"] >= reveal[7])].iloc[::2]
        circles = [dict(x=circle[q][0], y=circle[q][1], r=circle[q][2], label=f"Zone {q}") for q in sorted(circle) if q >= 6]
        r.chart("map_points", "Endgame map", [dict(name="Your path, zone 7 on", x=seg["x"].round(0).tolist(), y=seg["y"].round(0).tolist(),
                                                   color="#0F766E", size=3, opacity=0.8)],
                x_label="Map X", y_label="Map Y", circles=circles, zone_circles=True)

    worth = [float(str(row["Worth"]).split(" pts")[0]) for tbl in (early, mid_rows, end_rows) for row in tbl
             if str(row.get("Worth", "")).startswith(("+", "-")) and "pts" in str(row.get("Worth", ""))]
    r.metric("Result", f"Placed {placement}, {kills} elims, {pts} pts", f"Game {gnum[mid]} of {len(gnum)}: {names}")
    if epd is None or not epd.get("reliable"):
        r.notes.insert(0, "Worth isn't shown: the expected-points model doesn't yet clearly beat its baseline on matches it never saw "
                          + (f"(it explains {epd['r2']:.0%} vs {epd['r2b']:.0%}). " if epd else ". ")
                          + "Select more matching lobbies in the left panel.")
    if worth:
        r.metric("Biggest single decision", f"{max(worth):+.1f} expected points", "The largest gain from following one recommendation, at "
                 "the moment it applied, from the expected-points model")
    r.headline = (f"{names}, game {gnum[mid]}: placed {placement} ({pts} pts). "
                  + (f"{sum(1 for w in worth if w >= 1)} decision{'s' if sum(1 for w in worth if w >= 1) != 1 else ''} worth 1+ expected point each."
                     if worth else ""))
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        "Recommendations use only what was knowable at that moment: the zone forecast is trained without this game, and surge bases "
        "face the routes teams outside the zone must take from where they were when it appeared.",
        "Worth: the expected-points model's value of the team's real situation against the recommended one, at the moment the "
        "recommendation applies (FNCS scoring). Each row stands alone; they don't add up, because one decision changes the next.",
        "Evidence comes from the matches selected in the left panel: select matching lobbies (for a tier-1 duo, tier-1 duo games).",
    ]
    return r
