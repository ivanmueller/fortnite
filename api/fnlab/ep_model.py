"""
ep_model.py - expected points from any moment in a match.

Snapshots: every STEP seconds from zone 2 appearing until a team is out, one row per living team:
  teams_alive, zone, progress (through the current zone), members (alive), hp (health + shield per alive
  member), outside_m (outside the next zone), storm_m (outside the storm edge now; 0 = safe), off_centre
  (distance from the current zone's centre in zone widths), height_rank (among living teams), enemies_50 /
  enemies_150 (other teams within 50 / 150 m), hit_10s (damage taken in the last 10 s), dealt_rank (damage
  dealt this zone, ranked against living teams: what surge looks at), kills (eliminations so far).
Target: points still to come = placement points (FNCS 2026 table: 65, 56, 52 ... 22 for 15th, 0 below) +
  4 per future elimination. Expected total points = banked eliminations x 4 + the model's prediction.
Model: gradient-boosted trees, checked on matches it never saw (folds grouped by match).
Exposure: the chance of being hit within 10 s by a team you weren't already fighting, rotating vs holding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .store import df

STEP = 10
PLACEMENT_POINTS = {1: 65, 2: 56, 3: 52, 4: 48, 5: 44, 6: 40, 7: 38, 8: 36, 9: 34, 10: 32, 11: 30, 12: 28, 13: 26, 14: 24, 15: 22}
KILL_POINTS = 4
FEATURES = ["teams_alive", "zone", "progress", "members", "hp", "outside_m", "storm_m", "off_centre", "height_rank",
            "enemies_50", "enemies_150", "hit_10s", "dealt_rank", "kills"]
LABELS = {"teams_alive": "Teams left", "zone": "Zone", "progress": "Progress through the zone", "members": "Teammates alive",
          "hp": "Health + shield (per player)", "outside_m": "Distance outside the next zone (m)", "storm_m": "Distance into the storm (m)",
          "off_centre": "Distance from the zone's centre (zone widths)", "height_rank": "Height rank (0 low – 1 high)",
          "enemies_50": "Enemy teams within 50 m", "enemies_150": "Enemy teams within 150 m", "hit_10s": "Damage taken in the last 10 s",
          "dealt_rank": "Damage dealt this zone, rank (0 least – 1 most)", "kills": "Eliminations so far"}


def snapshots(con) -> pd.DataFrame:
    """One row per living team every STEP seconds, from zone 2 appearing to the team going out."""
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    events = {"health", "damage"} <= have and all(
        con.execute(f"SELECT count(*) FROM {t} JOIN sel USING (match_id)").fetchone()[0] > 0 for t in ("health", "damage"))
    snaps = df(con, f"""
        WITH z AS (SELECT z.match_id, z.phase, z.cur_x, z.cur_y, z.cur_r, z.next_x, z.next_y, z.next_r, z.start_shrink_t, z.finish_shrink_t,
                          lag(z.finish_shrink_t) OVER (PARTITION BY z.match_id ORDER BY z.phase) AS prev_finish
                   FROM zones z JOIN sel USING (match_id)),
             zz AS (SELECT * FROM z WHERE prev_finish IS NOT NULL AND cur_x IS NOT NULL AND finish_shrink_t > start_shrink_t),
             tt AS (SELECT zz.*, unnest(range(CAST(ceil(prev_finish) AS BIGINT), CAST(floor(finish_shrink_t) AS BIGINT), {STEP})) AS t FROM zz),
             pl AS (SELECT p.match_id, p.id, p.team_index, p.death_t, coalesce(p.team_placement, p.placement) AS placement
                    FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE)),
             a AS (SELECT tt.match_id, tt.phase AS zone, tt.t, (tt.t - tt.prev_finish) / (tt.finish_shrink_t - tt.prev_finish) AS progress,
                          tt.cur_x, tt.cur_y, tt.cur_r, tt.next_x, tt.next_y, tt.next_r, tt.start_shrink_t, tt.finish_shrink_t, tt.prev_finish,
                          pl.id, pl.team_index, pl.placement
                   FROM tt JOIN pl USING (match_id) WHERE pl.death_t IS NULL OR pl.death_t > tt.t)
        SELECT a.*, pos.x, pos.y, pos.z FROM a ASOF JOIN positions pos ON a.match_id = pos.match_id AND a.id = pos.id AND a.t >= pos.t
    """)
    if snaps.empty:
        return snaps
    if events:
        con.register("ep_snaps", snaps[["match_id", "id", "t"]])
        hp = df(con, """SELECT s.match_id, s.id, s.t, coalesce(h.health, 0) + coalesce(h.shield, 0) AS hp
                        FROM ep_snaps s ASOF LEFT JOIN health h ON s.match_id = h.match_id AND s.id = h.id AND s.t >= h.t""")
        snaps = snaps.merge(hp, on=["match_id", "id", "t"], how="left")
    else:
        snaps["hp"] = np.nan
    g = snaps.groupby(["match_id", "t", "team_index"])
    team = g.agg(zone=("zone", "first"), progress=("progress", "first"), members=("id", "size"), hp=("hp", "mean"),
                 x=("x", "mean"), y=("y", "mean"), z=("z", "mean"), placement=("placement", "min"),
                 cur_x=("cur_x", "first"), cur_y=("cur_y", "first"), cur_r=("cur_r", "first"), next_x=("next_x", "first"),
                 next_y=("next_y", "first"), next_r=("next_r", "first"), start=("start_shrink_t", "first"),
                 finish=("finish_shrink_t", "first"), prev_finish=("prev_finish", "first")).reset_index()
    team["teams_alive"] = team.groupby(["match_id", "t"])["team_index"].transform("size")
    team["outside_m"] = ((np.hypot(team["x"] - team["next_x"], team["y"] - team["next_y"]) - team["next_r"]) / 100).clip(lower=0)
    f = ((team["t"] - team["start"]) / (team["finish"] - team["start"])).clip(0, 1)
    sx, sy = team["cur_x"] + f * (team["next_x"] - team["cur_x"]), team["cur_y"] + f * (team["next_y"] - team["cur_y"])
    sr = team["cur_r"] + f * (team["next_r"] - team["cur_r"])
    team["storm_m"] = ((np.hypot(team["x"] - sx, team["y"] - sy) - sr) / 100).clip(lower=0)
    team["off_centre"] = np.hypot(team["x"] - team["cur_x"], team["y"] - team["cur_y"]) / team["cur_r"]
    team["height_rank"] = team.groupby(["match_id", "t"])["z"].rank(pct=True)
    # enemy teams nearby
    e50, e150 = np.zeros(len(team)), np.zeros(len(team))
    for _, idx in team.groupby(["match_id", "t"]).indices.items():
        xy = team.iloc[idx][["x", "y"]].to_numpy(float)
        d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1]) / 100
        np.fill_diagonal(d, np.inf)
        e50[idx], e150[idx] = (d <= 50).sum(1), (d <= 150).sum(1)
    team["enemies_50"], team["enemies_150"] = e50, e150
    # damage taken in the last 10 s, damage dealt this zone (rank), eliminations so far and still to come
    team["hit_10s"], team["dealt_rank"], team["kills"] = 0.0, 0.5, 0
    kills = df(con, """SELECT k.match_id, k.t, pf.team_index AS team FROM kills k JOIN sel USING (match_id)
                       JOIN players pf ON pf.match_id = k.match_id AND pf.id = k.finisher_id
                       JOIN players pv ON pv.match_id = k.match_id AND pv.id = k.victim_id
                       WHERE NOT coalesce(k.downed, FALSE) AND pf.team_index IS DISTINCT FROM pv.team_index AND NOT coalesce(pv.is_bot, FALSE)""")
    if len(kills):
        kills = kills.sort_values("t")
        tot = kills.groupby(["match_id", "team"]).size()
        team["t"] = team["t"].astype(float)
        team = team.sort_values("t")
        kk = kills.assign(n=1, t=kills["t"].astype(float)).sort_values("t")
        kk["team"] = kk["team"].astype(team["team_index"].dtype)
        kk["cum"] = kk.groupby(["match_id", "team"])["n"].cumsum()
        team = pd.merge_asof(team, kk[["t", "match_id", "team", "cum"]].rename(columns={"team": "team_index"}), on="t",
                             by=["match_id", "team_index"], direction="backward")
        team["kills"] = team["cum"].fillna(0).astype(int)
        team = team.drop(columns="cum")
        team["kills_total"] = [tot.get((m, ti), 0) for m, ti in zip(team["match_id"], team["team_index"])]
    else:
        team["kills_total"] = 0
    if events:
        hits = df(con, """SELECT d.match_id, d.t, d.amount, pa.team_index AS att, pt.team_index AS tgt
                          FROM damage d JOIN sel USING (match_id)
                          JOIN players pa ON pa.match_id = d.match_id AND pa.id = d.attacker_id
                          JOIN players pt ON pt.match_id = d.match_id AND pt.id = d.target_id
                          WHERE d.target_kind = 'player' AND pa.team_index IS DISTINCT FROM pt.team_index""")
        if len(hits):
            con.register("ep_hits", hits)
            con.register("ep_team", team[["match_id", "t", "team_index", "prev_finish"]])
            agg = df(con, """
                SELECT s.match_id, s.t, s.team_index,
                       coalesce(sum(h.amount) FILTER (WHERE h.tgt = s.team_index AND h.t > s.t - 10 AND h.t <= s.t), 0) AS hit_10s,
                       coalesce(sum(h.amount) FILTER (WHERE h.att = s.team_index AND h.t > s.prev_finish AND h.t <= s.t), 0) AS dealt
                FROM ep_team s LEFT JOIN ep_hits h ON h.match_id = s.match_id AND h.t > s.prev_finish - 10 AND h.t <= s.t
                GROUP BY 1, 2, 3""")
            team = team.drop(columns=["hit_10s"]).merge(agg, on=["match_id", "t", "team_index"], how="left")
            team["hit_10s"] = team["hit_10s"].fillna(0)
            team["dealt_rank"] = team.groupby(["match_id", "t"])["dealt"].rank(pct=True)
    team["future_pts"] = team["placement"].map(PLACEMENT_POINTS).fillna(0) + KILL_POINTS * (team["kills_total"] - team["kills"]).clip(lower=0)
    team["hp"] = team["hp"].fillna(team["hp"].median() if team["hp"].notna().any() else 100)
    return team.reset_index(drop=True)


def fit(team: pd.DataFrame, folds: int = 6, seed: int = 3):
    """Gradient-boosted expected points, plus its out-of-sample check and a situation-blind baseline."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    X, y = team[FEATURES].to_numpy(float), team["future_pts"].to_numpy(float)
    matches = team["match_id"].unique()
    fold = dict(zip(np.random.default_rng(seed).permutation(matches), np.arange(len(matches)) % folds))
    f = team["match_id"].map(fold).to_numpy()

    def model():
        return HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=24, min_samples_leaf=60,
                                             l2_regularization=1.0, random_state=seed)
    oos, base = np.zeros(len(y)), np.zeros(len(y))
    bcols = [FEATURES.index("teams_alive"), FEATURES.index("zone")]
    fold_models = {}
    for k in range(folds):
        tr, te = f != k, f == k
        if tr.sum() < 200 or te.sum() == 0:
            continue
        fold_models[k] = model().fit(X[tr], y[tr])
        oos[te] = fold_models[k].predict(X[te])
        base[te] = model().fit(X[tr][:, bcols], y[tr]).predict(X[te][:, bcols])
    full = model().fit(X, y)
    full.fold_models_, full.fold_of_ = fold_models, dict(zip(matches, [int(fold[m]) for m in matches]))   # models that never saw each match
    return full, oos, base


def exposure(team: pd.DataFrame, con) -> pd.DataFrame:
    """The chance of being hit within 10 s by a team you weren't already fighting (no hits between you in the last 30 s),
    rotating (outside the next zone) vs holding (inside it), by zone."""
    hits = df(con, """SELECT d.match_id, d.t, d.amount, pa.team_index AS att, pt.team_index AS tgt
                      FROM damage d JOIN sel USING (match_id)
                      JOIN players pa ON pa.match_id = d.match_id AND pa.id = d.attacker_id
                      JOIN players pt ON pt.match_id = d.match_id AND pt.id = d.target_id
                      WHERE d.target_kind = 'player' AND pa.team_index IS DISTINCT FROM pt.team_index""")
    if hits.empty:
        return pd.DataFrame()
    hits = hits.sort_values("t")
    hits["pair_prev"] = hits.groupby(["match_id", "att", "tgt"])["t"].shift(1)
    fresh = hits[hits["pair_prev"].isna() | (hits["t"] - hits["pair_prev"] > 30)]     # a new attacker
    con.register("ep_fresh", fresh[["match_id", "t", "tgt"]])
    con.register("ep_team2", team[["match_id", "t", "team_index"]])
    hit = df(con, """SELECT s.match_id, s.t, s.team_index, count(f.t) > 0 AS hit FROM ep_team2 s LEFT JOIN ep_fresh f
                     ON f.match_id = s.match_id AND f.tgt = s.team_index AND f.t > s.t AND f.t <= s.t + 10 GROUP BY 1, 2, 3""")
    dmg = df(con, """SELECT s.match_id, s.t, s.team_index, coalesce(sum(h.amount), 0) AS dmg FROM ep_team2 s
                     JOIN ep_fresh f ON f.match_id = s.match_id AND f.tgt = s.team_index AND f.t > s.t AND f.t <= s.t + 10
                     LEFT JOIN damage h ON h.match_id = s.match_id AND h.t > s.t AND h.t <= s.t + 10
                     JOIN players pt ON pt.match_id = h.match_id AND pt.id = h.target_id AND pt.team_index = s.team_index
                     GROUP BY 1, 2, 3""")
    e = team[["match_id", "t", "team_index", "zone", "teams_alive", "outside_m", "enemies_150"]].merge(hit, on=["match_id", "t", "team_index"])
    e = e.merge(dmg, on=["match_id", "t", "team_index"], how="left")
    e["state"] = np.where(e["outside_m"] > 0, "Rotating (outside the next zone)", "Holding (inside the next zone)")
    return e
