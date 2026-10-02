"""
engine.py - a decision engine that only knows what a player knows live.

Live knowledge (per team, every DECISION_S seconds):
  fully    own position, health + shield, teammates alive, eliminations, the current and next zone and their timers,
           distance outside the next zone and into the storm, teams left, damage taken in the last 10 s
  surge    damage dealt this zone above or below the cut-off, as the HUD shows it (the cut-off rank is the share of
           players surged in detected surge episodes, else 25%)
  partly   enemy teams within the perception radius (adjustable): how many, how close, and how far above or below
  terrain  natural ground height under you (from the playable map), as a rank within the zone
  never    far enemies' positions, any enemy's health or loadout, height rank in the lobby
A points model trained on these live inputs only prices every situation (FNCS scoring, the top-15 cliff).

Options, looked at HORIZON_S seconds ahead:
  hold       stay; storm damage and the chance of a random hit where you stand
  rotate     run toward the next zone's edge by the direct route
  rotate_alt the less crowded entry (perceived enemies only), up to 45 degrees around
  heal       +50 health and shield, staying put
  engage     a visible enemy: win with the measured win rate for your health (else placed now, on the cliff)
The engine picks the option with the most expected points; the team's actual action over the same 20 s is read
from the data and priced the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import ep_model as ep
from .store import df

DECISION_S = 20
HORIZON_S = 20
HEAL = 50
LIVE = ["teams_alive", "zone", "progress", "members", "hp", "outside_m", "storm_m", "off_centre", "kills", "hit_10s",
        "surge_margin", "seen", "seen_close", "seen_above", "ground_rank"]
ACTIONS = {"hold": "Hold", "rotate": "Rotate now (direct)", "rotate_alt": "Rotate by the less crowded entry", "heal": "Heal first",
           "engage": "Engage the visible team"}
STORM_DPS = {2: 1, 3: 1, 4: 2, 5: 5, 6: 8}          # per second; 10 from zone 7


def live_states(con, team: pd.DataFrame, perceive_m: float, land) -> pd.DataFrame:
    """Turn the full-information snapshots into what each team could know live."""
    t = team.copy()
    # enemies within the perception radius, from player positions (not team centroids)
    pos = df(con, """SELECT p.match_id, p.id, p.t, p.x, p.y, p.z, pl.team_index FROM positions p JOIN sel USING (match_id)
                     JOIN players pl ON pl.match_id = p.match_id AND pl.id = p.id WHERE NOT coalesce(pl.is_bot, FALSE)""")
    pos = pos.assign(tt=(pos["t"] // ep.STEP * ep.STEP).astype(float)).drop_duplicates(["match_id", "id", "tt"])
    seen, close, above = np.zeros(len(t)), np.zeros(len(t)), np.zeros(len(t))
    pg = {k: v for k, v in pos.groupby(["match_id", "tt"])}
    for (mid, tt), idx in t.groupby(["match_id", "t"]).indices.items():
        p = pg.get((mid, float(tt)))
        if p is None:
            continue
        rows = t.iloc[idx]
        teams, pteam = np.unique(p["team_index"].to_numpy(), return_inverse=True)
        onehot = np.zeros((len(p), len(teams)))
        onehot[np.arange(len(p)), pteam] = 1
        d = np.hypot(p["x"].to_numpy(float)[None, :] - rows["x"].to_numpy(float)[:, None],
                     p["y"].to_numpy(float)[None, :] - rows["y"].to_numpy(float)[:, None]) / 100
        other = p["team_index"].to_numpy()[None, :] != rows["team_index"].to_numpy()[:, None]
        m = (d <= perceive_m) & other
        mc = (d <= 50) & other
        seen[idx] = ((m.astype(float) @ onehot) > 0).sum(1)
        close[idx] = ((mc.astype(float) @ onehot) > 0).sum(1)
        cnt = m.sum(1)
        zsum = (m * p["z"].to_numpy(float)[None, :]).sum(1)
        above[idx] = np.where(cnt > 0, (zsum / np.maximum(cnt, 1) - rows["z"].to_numpy(float)) / 100, 0.0)
    t["seen"], t["seen_close"], t["seen_above"] = seen, close, above
    # surge as the HUD shows it: dealt rank above or below the cut-off
    t["surge_margin"] = t["dealt_rank"] - surge_cutoff(con)
    # natural ground height rank under you, within the zone
    if land is not None:
        _, h = land.lookup(t["x"].to_numpy(), t["y"].to_numpy())
        t["ground"] = h
        t["ground_rank"] = t.groupby(["match_id", "zone"])["ground"].rank(pct=True).fillna(0.5)
    else:
        t["ground_rank"] = 0.5
    return t


_CUTOFF: dict = {}


def surge_cutoff(con) -> float:
    key = id(con)
    if key in _CUTOFF:
        return _CUTOFF[key]
    share = 0.25
    try:
        from .analyses._events_common import has_tables
        if has_tables(con, "health", "damage"):
            from .analyses.surge import surge_episodes
            from .analyses import Context
            ep_s, per = surge_episodes(Context(con=con, filters=None, n_matches=0, params={}))
            if len(per):
                share = float(per.groupby("episode")["surged"].mean().median())
    except Exception:  # noqa: BLE001
        pass
    _CUTOFF.clear()
    _CUTOFF[key] = share
    return share


def fit_live(t: pd.DataFrame):
    """The points model on live knowledge only, checked on matches it never saw."""
    old = ep.FEATURES
    try:
        ep.FEATURES = LIVE
        return ep.fit(t)
    finally:
        ep.FEATURES = old


def fight_odds(con) -> pd.Series:
    """Win rate of the side that starts a fight, by its health going in (measured from the selected matches)."""
    try:
        from .analyses._events_common import has_tables
        from .analyses.fights import fights
        from .analyses import Context
        if not has_tables(con, "damage", "health"):
            raise ValueError
        f = fights(Context(con=con, filters=None, n_matches=0, params={})).dropna(subset=["first", "winner"])
        hp = np.where(f["first"] == f["team_a"], f["hp_a"], f["hp_b"])
        bins = pd.cut(hp, [0, 75, 125, 175, 1e9], labels=["low", "mid", "high", "full"])
        return f.assign(b=bins, won=f["winner"] == f["first"]).groupby("b", observed=False)["won"].mean().fillna(0.45)
    except Exception:  # noqa: BLE001
        return pd.Series({"low": 0.3, "mid": 0.42, "high": 0.52, "full": 0.6})


def _storm_circle(row, s):
    f = np.clip((s - row["start"]) / max(row["finish"] - row["start"], 1e-6), 0, 1)
    return (row["cur_x"] + f * (row["next_x"] - row["cur_x"]), row["cur_y"] + f * (row["next_y"] - row["cur_y"]),
            row["cur_r"] + f * (row["next_r"] - row["cur_r"]))


def evaluate(t: pd.DataFrame, model, run_ms: float, p_hit: dict, odds: pd.Series, decision_s: int = DECISION_S) -> pd.DataFrame:
    """Price each option, and what the team actually did, at every decision point (all at once)."""
    t = t.sort_values(["match_id", "team_index", "t"]).reset_index(drop=True)
    nxt = t.groupby(["match_id", "team_index"]).shift(-int(HORIZON_S / ep.STEP))
    keep = ((t["t"] % decision_s) < ep.STEP) & nxt["t"].notna() & (nxt["t"] - t["t"] <= HORIZON_S + ep.STEP)
    d, n = t[keep].copy(), nxt[keep]
    zone = d["zone"].to_numpy(int)
    dps = np.array([STORM_DPS.get(z, 10) for z in zone], float)
    s1 = d["t"].to_numpy(float) + HORIZON_S
    f = np.clip((s1 - d["start"].to_numpy(float)) / np.maximum(d["finish"].to_numpy(float) - d["start"].to_numpy(float), 1e-6), 0, 1)
    scx = d["cur_x"].to_numpy(float) + f * (d["next_x"].to_numpy(float) - d["cur_x"].to_numpy(float))
    scy = d["cur_y"].to_numpy(float) + f * (d["next_y"].to_numpy(float) - d["cur_y"].to_numpy(float))
    scr = d["cur_r"].to_numpy(float) + f * (d["next_r"].to_numpy(float) - d["cur_r"].to_numpy(float))
    x, y, hp = d["x"].to_numpy(float), d["y"].to_numpy(float), d["hp"].to_numpy(float)
    dx, dy = d["next_x"].to_numpy(float) - x, d["next_y"].to_numpy(float) - y
    dist = np.hypot(dx, dy)
    to_edge = np.maximum(0.0, (dist - d["next_r"].to_numpy(float)) / 100)
    ux, uy = np.where(dist > 0, dx / np.maximum(dist, 1e-9), 0), np.where(dist > 0, dy / np.maximum(dist, 1e-9), 0)
    prog = np.minimum(1.0, d["progress"].to_numpy(float) + HORIZON_S / np.maximum(d["finish"].to_numpy(float) - d["prev_finish"].to_numpy(float), 1))
    grp = np.where(zone >= 7, "late", np.where(zone >= 5, "mid", "early"))
    full_hp = float(np.nanquantile(t["hp"], 0.99)) or 100.0       # the most health the data shows (200 with shields)
    hold_p = np.array([p_hit.get((g, "hold"), (0.05, 40))[0] * p_hit.get((g, "hold"), (0.05, 40))[1] for g in grp])
    rot_p = np.array([p_hit.get((g, "rotate"), (0.08, 45))[0] * p_hit.get((g, "rotate"), (0.08, 45))[1] for g in grp])
    base = d[LIVE].copy()

    def state(px, py, hp_, outside, **extra):
        storm_out = np.maximum(0.0, (np.hypot(px - scx, py - scy) - scr) / 100)
        s_ = base.copy()
        s_["progress"] = prog
        s_["hp"] = np.maximum(1.0, hp_ - np.where(storm_out > 0, dps * HORIZON_S * np.minimum(1.0, storm_out / 30), 0))
        s_["outside_m"] = outside
        s_["storm_m"] = storm_out
        s_["off_centre"] = np.hypot(px - d["cur_x"].to_numpy(float), py - d["cur_y"].to_numpy(float)) / np.maximum(d["cur_r"].to_numpy(float), 1)
        s_["hit_10s"] = 0.0
        for k, v in extra.items():
            s_[k] = v
        return s_
    step = np.minimum(run_ms * HORIZON_S, to_edge + 20) * 100
    crowd = d["seen_close"].to_numpy(float)
    a = np.radians(35)
    ax, ay = ux * np.cos(a) - uy * np.sin(a), ux * np.sin(a) + uy * np.cos(a)
    S = {
        "hold": state(x, y, hp - hold_p, d["outside_m"].to_numpy(float)),
        "rotate": state(x + ux * step, y + uy * step, hp - rot_p, np.maximum(0.0, to_edge - step / 100)),
        "rotate_alt": state(x + ax * step, y + ay * step, hp - rot_p * np.where(crowd > 0, 0.6, 1.0),
                            np.maximum(0.0, to_edge - step / 100 * np.cos(a)), seen_close=np.maximum(0.0, crowd - 1)),
        "heal": state(x, y, np.minimum(full_hp, hp + HEAL) - hold_p, d["outside_m"].to_numpy(float)),
        "win": state(x, y, np.maximum(1.0, hp - 60), d["outside_m"].to_numpy(float), kills=d["kills"].to_numpy(float) + 1,
                     seen=np.maximum(0.0, d["seen"].to_numpy(float) - 1)),
    }
    P = {k: model.predict(v[LIVE].to_numpy(float)) for k, v in S.items()}
    hb = pd.cut(hp, [-1, 75, 125, 175, 1e9], labels=["low", "mid", "high", "full"]).astype(str)
    p_win = np.array([float(odds.get(b, 0.45)) for b in hb])
    lose = np.array([ep.PLACEMENT_POINTS.get(int(v), 0) for v in d["teams_alive"]], float)
    NEG = -1e9
    d["ev_hold"] = P["hold"]
    d["ev_rotate"] = np.where(to_edge > 0, P["rotate"], NEG)
    d["ev_rotate_alt"] = np.where((to_edge > 0) & (crowd > 0), P["rotate_alt"], NEG)
    d["ev_heal"] = np.where(hp <= full_hp - 25, P["heal"], NEG)                # only worth considering when there's health to gain
    d["ev_engage"] = np.where(d["seen"].to_numpy(float) > 0, p_win * (P["win"] + ep.KILL_POINTS) + (1 - p_win) * lose, NEG)
    # what they actually did over the same 20 s
    moved_in = d["outside_m"].to_numpy(float) - n["outside_m"].to_numpy(float)
    same_zone = n["zone"].to_numpy() == zone
    dealt = np.where(same_zone & ("dealt" in d), (n["dealt"].to_numpy(float) if "dealt" in n else 0) - (d["dealt"].to_numpy(float) if "dealt" in d else 0), 0)
    healed = n["hp"].to_numpy(float) - hp
    mvx, mvy = n["x"].to_numpy(float) - x, n["y"].to_numpy(float) - y
    ang = np.degrees(np.arccos(np.clip((mvx * ux + mvy * uy) / (np.hypot(mvx, mvy) + 1e-9), -1, 1)))
    act = np.where(np.nan_to_num(dealt) > 20, "engage",
                   np.where((to_edge > 0) & (moved_in >= 30), np.where(ang < 35, "rotate", "rotate_alt"),
                            np.where(healed >= 25, "heal", "hold")))
    d["actual"] = act
    keys = list(ACTIONS)
    evs = d[[f"ev_{k}" for k in keys]].to_numpy(float)
    d["engine"] = [keys[i] for i in evs.argmax(1)]
    d["ev_engine"] = evs.max(1)
    col = {k: i for i, k in enumerate(keys)}
    ev_act = evs[np.arange(len(d)), [col[a] for a in act]]
    # an action that wasn't one of the options at that moment is priced as its nearest option
    # (a rotation by another route as a rotation; healing at full health as holding)
    nearest = np.array([col["rotate"] if a == "rotate_alt" else col["hold"] for a in act])
    ev_near = evs[np.arange(len(d)), nearest]
    d["ev_actual"] = np.where(ev_act > -1e8, ev_act, np.where(ev_near > -1e8, ev_near, d["ev_hold"]))
    d["stake"] = d["ev_engine"] - d["ev_actual"]
    d["followed"] = (d["engine"] == d["actual"]) | (d["stake"] < 0.5)       # within half a point counts as following
    return d


def hit_risk(expo: pd.DataFrame) -> dict:
    """(zone group, hold/rotate) -> (chance of a hit from a new team within 20 s, damage when hit)."""
    if expo is None or expo.empty:
        return {}
    e = expo.assign(grp=np.where(expo["zone"] >= 7, "late", np.where(expo["zone"] >= 5, "mid", "early")),
                    st=np.where(expo["outside_m"] > 0, "rotate", "hold"))
    out = {}
    for (g, s), q in e.groupby(["grp", "st"]):
        p10 = q["hit"].mean()
        out[(g, s)] = (1 - (1 - p10) ** 2, float(q.loc[q["hit"], "dmg"].mean()) if q["hit"].any() else 40.0)
    return out
