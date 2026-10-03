"""
Team audit: one team, game by game. Where did their points come from, and where could they have done better?

Find the team by typing names (a name matches if it contains what's typed; separate players with commas).
For every selected match the team played:
  points        placement + elimination points on the active scoring scheme (scoring.json; FNCS 2026 Duos finals by default)
  drop          landing spot and whether it was contested; eliminated off spawn
  each zone     already inside the next zone when it appeared; distance from the current zone's centre (50/50,
                shifted and moving zones); rotation timing vs comparable players; storm damage; endgame height
  the end       zone, who eliminated them and where that team finished, health going into the last fight,
                third party, outside the zone at the time
Flags tie each weakness to a Playbook rule. Everything is compared with the tournament's top teams.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import scoring
from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register
from ._events_common import has_tables, unexplained_drops

# Points come from scoring.py (tables in scoring.json); no table lives here.
TOP_TEAMS = 5


def _find_team(con, text: str) -> pd.DataFrame:
    tokens = [t.strip().lower() for t in text.split(",") if t.strip()]
    if not tokens:
        return pd.DataFrame()
    pl = df(con, """SELECT pl.match_id, pl.id, pl.name, pl.team_index, coalesce(pl.team_placement, pl.placement) AS placement,
                           pl.kills, pl.death_t, pl.death_cause FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    pl = pl.merge(df(con, "SELECT pl.match_id, pl.id, pl.player_id FROM players pl JOIN sel USING (match_id)"), on=["match_id", "id"], how="left")
    low = (pl["name"].fillna("") + " " + pl["player_id"].fillna("").astype(str)).str.lower()
    hit = pl[[any(t in n for t in tokens) for n in low]].copy()
    if hit.empty:
        return hit
    hit["token"] = [next(t for t in tokens if t in n) for n in hit["name"].fillna("").str.lower()]
    # The team in each match is the team holding the most of the typed names.
    best = hit.groupby(["match_id", "team_index"])["token"].nunique().reset_index().sort_values("token", ascending=False)
    best = best.drop_duplicates("match_id")
    need = 2 if len(tokens) > 1 else 1      # with several names, the team must hold at least two of them
    best = best[best["token"] >= need]
    return pl.merge(best[["match_id", "team_index"]], on=["match_id", "team_index"])


def _team_list(con) -> pd.DataFrame:
    """Every team in the selected matches, followed across games by its accounts (event accounts stay the same all
    tournament), with games, wins, computed points, average placement and usual drop: to identify a team by its results."""
    pl = df(con, """SELECT pl.match_id, pl.id, pl.player_id, pl.name, pl.team_index, coalesce(pl.team_placement, pl.placement) AS placement, pl.kills
                    FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    if pl.empty:
        return pl
    order = df(con, "SELECT m.match_id, m.replay_timestamp, m.match_date FROM matches m JOIN sel USING (match_id)")
    order["start"] = pd.to_datetime(order["replay_timestamp"].astype(str), errors="coerce").fillna(pd.to_datetime(order["match_date"].astype(str), errors="coerce"))
    order = order.sort_values(["start", "match_id"]).reset_index(drop=True)
    gnum = dict(zip(order["match_id"], order.index + 1))
    key = pl.groupby(["match_id", "team_index"])["player_id"].apply(lambda s: "|".join(sorted(map(str, s)))).rename("members")
    t = pl.groupby(["match_id", "team_index"]).agg(placement=("placement", "min"), kills=("kills", "sum")).join(key).reset_index()
    t["points"] = scoring.active().total(t["placement"], t["kills"]).to_numpy(float)
    t["game"] = t["match_id"].map(gnum)
    names = pl.groupby("player_id")["name"].agg(lambda s: s.mode().iat[0] if s.notna().any() else "?")
    drops = pd.Series(dtype=object)
    if "landings" in {x for (x,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}:
        L = df(con, "SELECT l.match_id, l.id, l.poi FROM landings l JOIN sel USING (match_id)").merge(pl[["match_id", "id", "team_index"]], on=["match_id", "id"])
        L = L.merge(t[["match_id", "team_index", "members"]], on=["match_id", "team_index"])
        drops = L.groupby("members")["poi"].agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else "–")
    g = t.groupby("members").agg(games=("game", "size"), points=("points", "sum"), avg=("placement", "mean"),
                                 wins=("game", lambda s: ", ".join(str(int(x)) for x in sorted(s[t.loc[s.index, "placement"] == 1])) or "–"))
    g["players"] = [", ".join(names.get(p, p) for p in m.split("|")) for m in g.index]
    g["drop"] = [drops.get(m, "–") for m in g.index]
    g = g.sort_values("points", ascending=False).reset_index(drop=True)
    return pd.DataFrame({"Rank": np.arange(1, len(g) + 1), "Players (type these above)": g["players"], "Games": g["games"],
                         "Won games": g["wins"], "Points (computed)": g["points"].astype(int), "Average placement": g["avg"].round(1),
                         "Usual drop": g["drop"]})


@register("audit", "Team audit",
          "One team, game by game: where their points came from, how every zone went, how each game ended, and where they "
          "could have done better, compared with the tournament's top teams.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game to draw", "match", "")])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    scheme = scoring.active()
    text = str(ctx.params.get("team") or "").strip()
    team = _find_team(con, text) if text else pd.DataFrame()
    if team.empty:
        tl = _team_list(con)
        if len(tl):
            r.table("Teams in the selected matches", tl.head(60))
        r.headline = ((f"No team found for '{text}'. " if text else "") +
                      "Find the team in the table below (at a LAN, players use event accounts, so identify them by their results: "
                      "which games they won, their points and their usual drop), then type their names above.")
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    names = sorted(team["name"].dropna().unique())
    games = df(con, "SELECT m.match_id, m.match_date, m.replay_timestamp FROM matches m JOIN sel USING (match_id)")
    games = games[games["match_id"].isin(team["match_id"])].copy()
    games["start"] = pd.to_datetime(games["replay_timestamp"].astype(str), errors="coerce").fillna(pd.to_datetime(games["match_date"].astype(str), errors="coerce"))
    games = games.sort_values(["start", "match_id"]).reset_index(drop=True)   # game 1 = earliest replay
    games["game"] = np.arange(1, len(games) + 1)
    gnum = dict(zip(games["match_id"], games["game"]))

    allp = df(con, """SELECT pl.match_id, pl.id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS placement, pl.kills, pl.death_t
                      FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    allp = allp[allp["match_id"].isin(games["match_id"])]
    teams = allp.groupby(["match_id", "team_index"]).agg(placement=("placement", "min"), kills=("kills", "sum"),
                                                          out_t=("death_t", lambda s: np.nan if s.isna().any() else s.max())).reset_index()
    teams["points"] = scheme.total(teams["placement"], teams["kills"]).to_numpy(float)
    totals = teams.groupby("team_index").size()  # not used across matches; team indices differ per match
    tid = {(m, t) for m, t in zip(team["match_id"], team["team_index"])}
    mine = teams[[(m, t) in tid for m, t in zip(teams["match_id"], teams["team_index"])]].copy()
    top = teams[teams["placement"] <= TOP_TEAMS]

    # ---- zones: per team per zone (when the next zone appears)
    zones = df(con, "SELECT z.match_id, z.phase, z.finish_shrink_t, z.next_x, z.next_y, z.next_r FROM zones z JOIN sel USING (match_id) ORDER BY 1, 2")
    zones = zones[zones["match_id"].isin(games["match_id"])]
    nxt = zones.assign(zone=zones["phase"] + 1)[["match_id", "zone", "finish_shrink_t", "next_x", "next_y", "next_r"]] \
        .rename(columns={"finish_shrink_t": "t", "next_x": "cx", "next_y": "cy", "next_r": "cr"})
    nxt = nxt.merge(zones[["match_id", "phase", "next_x", "next_y", "next_r"]].rename(columns={"phase": "zone", "next_x": "nx", "next_y": "ny", "next_r": "nr"}),
                    on=["match_id", "zone"])
    con.register("zone_moments", nxt[["match_id", "zone", "t"]])
    pos = df(con, """
        WITH a AS (SELECT m.match_id, m.zone, m.t, p.id, p.team_index FROM zone_moments m JOIN players p USING (match_id)
                   WHERE NOT coalesce(p.is_bot, FALSE) AND (p.death_t IS NULL OR p.death_t > m.t))
        SELECT a.*, pos.x, pos.y, pos.z FROM a ASOF JOIN positions pos ON a.match_id = pos.match_id AND a.id = pos.id AND a.t >= pos.t""")
    tz = pos.groupby(["match_id", "zone", "team_index"]).agg(x=("x", "mean"), y=("y", "mean"), z=("z", "mean")).reset_index().merge(nxt, on=["match_id", "zone"])
    tz["outside_m"] = ((np.hypot(tz["x"] - tz["nx"], tz["y"] - tz["ny"]) - tz["nr"]) / 100).clip(lower=0)
    tz["off_centre"] = np.hypot(tz["x"] - tz["cx"], tz["y"] - tz["cy"]) / tz["cr"]
    tz["height_rank"] = tz.groupby(["match_id", "zone"])["z"].rank(pct=True)
    zt = df(con, "SELECT o.match_id, o.phase AS zone, o.zone_type FROM zone_offsets o JOIN sel USING (match_id)") \
        if "zone_offsets" in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()} else pd.DataFrame()
    if len(zt):
        tz = tz.merge(zt, on=["match_id", "zone"], how="left")
    else:
        tz["zone_type"] = None

    from .rotation import rotations
    rot, _, _ = rotations(ctx)
    behind = pd.DataFrame()
    if len(rot):
        rr = rot[rot["match_id"].isin(games["match_id"])].merge(allp[["match_id", "id", "team_index"]], on=["match_id", "id"])
        behind = rr.groupby(["match_id", "phase", "team_index"]).agg(behind=("timing", lambda s: (s == "Behind").any()),
                                                                     lag=("lag_first_s", "max")).reset_index().rename(columns={"phase": "zone"})
        tz = tz.merge(behind, on=["match_id", "zone", "team_index"], how="left")
    storm = pd.DataFrame()
    if has_tables(con, "health", "damage"):
        d = unexplained_drops(con)
        if len(d):
            d = d[(d["in_storm"] == True) & d["match_id"].isin(games["match_id"])].merge(allp[["match_id", "id", "team_index"]], on=["match_id", "id"])  # noqa: E712
            storm = d.groupby(["match_id", "phase", "team_index"])["lost"].sum().reset_index().rename(columns={"phase": "zone", "lost": "storm"})
            tz = tz.merge(storm, on=["match_id", "zone", "team_index"], how="left")
    tz["storm"] = tz.get("storm", pd.Series(0, index=tz.index)).fillna(0)
    mz = tz[[(m, t) in tid for m, t in zip(tz["match_id"], tz["team_index"])]]
    topz = tz.merge(top[["match_id", "team_index"]], on=["match_id", "team_index"])

    # ---- how each game ended
    from .fights import fights
    fz = fights(ctx) if has_tables(con, "damage", "health") else pd.DataFrame()
    ends = {}
    for _, g in mine.iterrows():
        mid, ti = g["match_id"], g["team_index"]
        if g["placement"] == 1:
            ends[mid] = "Won the game"
            continue
        out_t = g["out_t"]
        if out_t != out_t:
            ends[mid] = "–"
            continue
        zone = int(zones[(zones["match_id"] == mid) & (zones["finish_shrink_t"] <= out_t)]["phase"].max() + 1) \
            if ((zones["match_id"] == mid) & (zones["finish_shrink_t"] <= out_t)).any() else 1
        bits = [f"Out in zone {zone}"]
        if len(fz):
            last = fz[(fz["match_id"] == mid) & ((fz["team_a"] == ti) | (fz["team_b"] == ti)) & (fz["t0"] <= out_t + 1)].sort_values("t0").tail(1)
            if len(last):
                lf = last.iloc[0]
                opp = lf["team_b"] if lf["team_a"] == ti else lf["team_a"]
                my_hp, op_hp = (lf["hp_a"], lf["hp_b"]) if lf["team_a"] == ti else (lf["hp_b"], lf["hp_a"])
                opp_place = teams[(teams["match_id"] == mid) & (teams["team_index"] == opp)]["placement"]
                bits.append(f"to the team that placed {int(opp_place.iat[0])}" if len(opp_place) else "in a fight")
                if my_hp == my_hp and op_hp == op_hp:
                    bits.append(f"health going in {my_hp:.0f} vs {op_hp:.0f}")
                if lf["third_party"]:
                    bits.append("third-partied")
        row = mz[(mz["match_id"] == mid) & (mz["zone"] == zone)]
        if len(row) and row["outside_m"].iat[0] > 50:
            bits.append(f"{row['outside_m'].iat[0]:.0f} m outside the zone")
        ends[mid] = ", ".join(bits)

    # ---- drop
    drops = pd.DataFrame()
    if "landings" in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}:
        L = df(con, "SELECT l.match_id, l.id, l.poi, l.opp_150m, l.off_spawn FROM landings l JOIN sel USING (match_id)")
        drops = L.merge(team[["match_id", "id"]], on=["match_id", "id"]).groupby("match_id").agg(
            poi=("poi", lambda s: s.dropna().mode().iat[0] if s.notna().any() else "–"),
            contested=("opp_150m", lambda s: (s > 0).any()), off_spawn=("off_spawn", "any"))

    # ---- flags: weaknesses tied to Playbook rules
    def flags(mid):
        f = []
        if mid in drops.index:
            if drops.loc[mid, "contested"]:
                f.append("Contested drop")
            if drops.loc[mid, "off_spawn"]:
                f.append("Eliminated off spawn")
        z = mz[mz["match_id"] == mid]
        for _, q in z.iterrows():
            k = int(q["zone"])
            if q.get("behind") is True:
                f.append(f"Rotated behind (zone {k})")
            if k >= 5 and q["off_centre"] > 0.75:
                f.append(f"Edge of the zone when zone {k} appeared")
            if q["storm"] >= 50:
                f.append(f"{q['storm']:.0f} storm damage (zone {k})")
            if k >= 6 and q["height_rank"] < 1 / 3:
                f.append(f"Low ground (zone {k})")
        return f

    rows = []
    for _, g in games.iterrows():
        mid = g["match_id"]
        me = mine[mine["match_id"] == mid].iloc[0]
        z = mz[mz["match_id"] == mid]
        fl = flags(mid)
        rows.append({"Game": int(g["game"]), "Placement": int(me["placement"]), "Elims": int(me["kills"]),
                     "Points": int(me["points"]),
                     "Drop": (f"{drops.loc[mid, 'poi']}{' (contested)' if drops.loc[mid, 'contested'] else ''}" if mid in drops.index else "–"),
                     "Already inside the next zone": f"{(z['outside_m'] == 0).sum()} of {len(z)} zones",
                     "Rotated behind": int(z["behind"].fillna(False).astype(bool).sum()) if "behind" in z else 0,
                     "How it ended": ends.get(mid, "–"),
                     "Risks taken": "; ".join(fl) if fl else "–",
                     "Match": mid})
    tbl = pd.DataFrame(rows)
    r.table("Game by game", tbl)

    # ---- compared with the tournament's top teams
    def avg(frame, cond, col):
        x = frame[cond(frame)] if cond else frame
        return x[col].mean() if len(x) else np.nan
    late = lambda f: f["zone"] >= 5  # noqa: E731
    endg = lambda f: f["zone"] >= 6  # noqa: E731
    cmp = [
        ("Average placement", mine["placement"].mean(), top["placement"].mean(), teams["placement"].mean(), "{:.1f}"),
        ("Eliminations per game", mine["kills"].mean(), top["kills"].mean(), teams["kills"].mean(), "{:.1f}"),
        ("Points per game", mine["points"].mean(), top["points"].mean(), teams["points"].mean(), "{:.0f}"),
        ("Already inside the next zone when it appeared", (mz["outside_m"] == 0).mean(), (topz["outside_m"] == 0).mean(), (tz["outside_m"] == 0).mean(), "{:.0%}"),
        ("Edge of the zone when the next appeared (zones 5+)", (mz[late(mz)]["off_centre"] > 0.75).mean(), (topz[late(topz)]["off_centre"] > 0.75).mean(),
         (tz[late(tz)]["off_centre"] > 0.75).mean(), "{:.0%}"),
        ("Low ground (zones 6+)", (mz[endg(mz)]["height_rank"] < 1 / 3).mean(), (topz[endg(topz)]["height_rank"] < 1 / 3).mean(),
         (tz[endg(tz)]["height_rank"] < 1 / 3).mean(), "{:.0%}"),
        ("Storm damage per zone", mz["storm"].mean(), topz["storm"].mean(), tz["storm"].mean(), "{:.0f}"),
    ]
    if "behind" in tz:
        cmp.insert(5, ("Rotated behind comparable teams", mz["behind"].fillna(False).astype(bool).mean(),
                       topz["behind"].fillna(False).astype(bool).mean(), tz["behind"].fillna(False).astype(bool).mean(), "{:.0%}"))
    r.table("Compared with the top teams", pd.DataFrame([{"Measure": n, "This team": f.format(a) if a == a else "–",
                                                         f"Top {TOP_TEAMS} each game": f.format(b) if b == b else "–",
                                                         "Everyone": f.format(c) if c == c else "–"} for n, a, b, c, f in cmp]))

    # ---- points chart and flags summary
    r.chart("stacked_bar", "Points per game", [dict(name="Placement points", x=[f"Game {g}" for g in tbl["Game"]],
                                             y=[p - scheme.elimination * e for p, e in zip(tbl["Points"], tbl["Elims"])]),
                                        dict(name="Elimination points", x=[f"Game {g}" for g in tbl["Game"]], y=(scheme.elimination * tbl["Elims"]).tolist())],
            y_label="Points")
    allflags = pd.Series([f.split(" (")[0].split(" when")[0] for fl in tbl["Risks taken"] if fl != "–" for f in fl.split("; ")])
    if len(allflags):
        fc = allflags.value_counts()
        r.chart("bar", "Risks taken most often", [dict(name="Times", x=fc.index.tolist(), y=fc.values.tolist())], x_label="Times", horizontal=True)

    # ---- map: the team's path vs the winner's in one game
    mid = ctx.params.get("match") if ctx.params.get("match") in gnum else games["match_id"].iloc[-1]
    ti = mine[mine["match_id"] == mid]["team_index"].iat[0]
    win_ti = teams[(teams["match_id"] == mid) & (teams["placement"] == 1)]["team_index"]
    ids = allp[(allp["match_id"] == mid) & ((allp["team_index"] == ti) | (allp["team_index"].isin(win_ti)))][["id", "team_index"]]
    if len(ids):
        tr = df(con, f"""SELECT p.id, p.t, p.x, p.y FROM positions p JOIN landings l ON l.match_id = p.match_id AND l.id = p.id
                         WHERE p.match_id = '{mid}' AND p.id IN ({",".join(str(int(i)) for i in ids["id"])}) AND p.t >= l.land_t ORDER BY p.t""") \
            if "landings" in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()} else pd.DataFrame()
        series = []
        if len(tr):
            tr = tr.merge(ids, on="id")
            mine_tr = tr[tr["team_index"] == ti].iloc[::3]
            series.append(dict(name=f"{' & '.join(names[:2])}", x=mine_tr["x"].round(0).tolist(), y=mine_tr["y"].round(0).tolist(), color="#0F766E", size=3, opacity=0.8))
            if len(win_ti) and win_ti.iat[0] != ti:
                w = tr[tr["team_index"] == win_ti.iat[0]].iloc[::3]
                series.append(dict(name="Game winner", x=w["x"].round(0).tolist(), y=w["y"].round(0).tolist(), color="#D08A12", size=3, opacity=0.8))
        zm_ = zones[zones["match_id"] == mid]
        if series:
            r.chart("map_points", "Their path vs the winner's", series, x_label="Map X", y_label="Map Y",
                    circles=[dict(x=float(z["next_x"]), y=float(z["next_y"]), r=float(z["next_r"]), label=f"Zone {int(z['phase'])}")
                             for _, z in zm_.iterrows()], zone_circles=True)
            r.notes.insert(0, f"Map: game {gnum[mid]} ({mid}).")

    total = int(tbl["Points"].sum())
    wins = int((tbl["Placement"] == 1).sum())
    r.metric("Games found", f"{len(tbl)}", f"Found as: {', '.join(names)}")
    r.metric("Points (computed)", f"{total}", f"{scheme.describe()} Compare with the official total to check the data.")
    r.metric("Average placement", f"{tbl['Placement'].mean():.1f}", f"Wins: {wins}")
    top_flag = allflags.value_counts().index[0] if len(allflags) else None
    r.headline = (f"{' & '.join(names[:2])}: {len(tbl)} games, {total} points, average placement {tbl['Placement'].mean():.1f}, {wins} win{'s' if wins != 1 else ''}."
                  + (f" Most frequent risk: {top_flag.lower()} ({allflags.value_counts().iat[0]} times)." if top_flag else ""))
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        "'Risks taken' lists the moments the team didn't follow a Playbook rule: contested drop, rotating behind comparable teams, "
        "being at the edge of the zone when a 50/50 or moving zone appears, storm damage of 50+ in a zone, low ground in zones 6+. "
        "They aren't mistakes on their own: compare them with how each game ended to see which risks cost points.",
        "'Already inside the next zone' is zone luck plus positioning; in zones 5+ the direction is close to random, so being at the "
        "edge is the controllable part.",
        f"'Top {TOP_TEAMS} each game' are the teams that finished in the top {TOP_TEAMS} of each game, as a benchmark.",
        f"Scoring: {scheme.describe()} Change the scheme or the table in api/fnlab/scoring.json. Check the total against the official one.",
    ]
    return r
