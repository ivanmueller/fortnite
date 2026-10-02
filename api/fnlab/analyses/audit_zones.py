"""
Team audit, part 2: where did the team set up each zone, were there better surge bases, and how was their surge?

Resting place: the team's median position while the next zone was showing and before it started closing
(the hold). Candidate bases: a grid over the part of the next zone that's safe during the hold. Each spot:
  tag opportunities  enemy player-seconds within tagging range (30-120 m) during the hold
  danger             enemy player-seconds within 30 m (fight risk rather than tags)
  height             natural ground height (from the playable map), relative to the zone's median
The best alternative is the spot with the most tag opportunities and no more danger than the team's own.
This uses where other teams actually went: it shows what a base would have offered that game, so read it
across many zones for a habit, not as a guarantee for one.
Surge: detected surge episodes (players inside the zone losing health together with no player hitting them);
for each, whether the team was hit and their damage dealt beforehand against what kept others safe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import zone_model as zm
from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register
from ._events_common import has_tables
from .audit import _find_team, _team_list

TAG_MIN, TAG_MAX, DANGER = 30, 120, 30   # metres
GRID = 28


def _holds(con, games: list[str]) -> pd.DataFrame:
    """Every zone's hold window: from the next zone appearing to it starting to close."""
    z = df(con, "SELECT z.match_id, z.phase, z.next_x, z.next_y, z.next_r, z.start_shrink_t, z.finish_shrink_t FROM zones z JOIN sel USING (match_id) ORDER BY 1, 2")
    z = z[z["match_id"].isin(games)]
    z["appear"] = z.groupby("match_id")["finish_shrink_t"].shift(1)
    z = z.dropna(subset=["appear"])
    return z[z["start_shrink_t"] - z["appear"] >= 10].rename(columns={"phase": "zone"})


@register("audit_zones", "Zone positions and surge",
          "For one team: where they set up each zone, whether a stronger surge base was available given where other teams were "
          "rotating, and how their surge went.",
          params=[Param("team", "Players (comma-separated)", "text", ""), Param("match", "Game to draw", "match", ""),
                  Param("zone", "Zone to draw", "zone", 5, [{"value": z, "label": f"Zone {z}"} for z in range(2, 12)])])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    text = str(ctx.params.get("team") or "").strip()
    team = _find_team(con, text) if text else pd.DataFrame()
    if team.empty:
        tl = _team_list(con)
        if len(tl):
            r.table("Teams in the selected matches", tl.head(60))
        r.headline = "Type the team's names above (find them in the table below by their results at a LAN)."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    games = sorted(team["match_id"].unique())
    names = sorted(team["name"].dropna().unique())
    order = df(con, "SELECT m.match_id, m.replay_timestamp FROM matches m JOIN sel USING (match_id)")
    order = order[order["match_id"].isin(games)].sort_values(["replay_timestamp", "match_id"]).reset_index(drop=True)
    gnum = dict(zip(order["match_id"], order.index + 1))
    tid = dict(zip(team["match_id"], team["team_index"]))
    holds = _holds(con, games)
    pl = df(con, "SELECT p.match_id, p.id, p.team_index, p.death_t FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE)")
    pl = pl[pl["match_id"].isin(games)]

    land = None
    season = df(con, "SELECT m.season FROM matches m JOIN sel USING (match_id) LIMIT 1")
    if len(season):
        cells = df(con, zm.land_cells_sql(season["season"].iat[0]))
        land = zm.LandMap(cells) if len(cells) >= 50 else None

    rows, maps = [], {}
    for mid in games:
        my_team = tid[mid]
        pos = df(con, f"""SELECT p.id, p.t, p.x, p.y, p.z FROM positions p WHERE p.match_id = '{mid}'""").merge(
            pl[pl["match_id"] == mid][["id", "team_index", "death_t"]], on="id")
        pos = pos[pos["death_t"].isna() | (pos["t"] < pos["death_t"])]
        for _, h in holds[holds["match_id"] == mid].iterrows():
            w = pos[(pos["t"] >= h["appear"]) & (pos["t"] <= h["start_shrink_t"])]
            mine = w[w["team_index"] == my_team]
            if mine.empty:
                continue
            bx, by = float(mine["x"].median()), float(mine["y"].median())
            enemy = w[w["team_index"] != my_team][["x", "y"]].to_numpy(float)
            # candidate spots: a grid over the next zone (safe during the hold)
            g = np.linspace(-1, 1, GRID)
            gx, gy = np.meshgrid(g, g)
            inside = gx ** 2 + gy ** 2 <= 0.92
            cx = h["next_x"] + gx[inside] * h["next_r"]
            cy = h["next_y"] + gy[inside] * h["next_r"]
            spots = np.c_[np.append(cx, bx), np.append(cy, by)]           # last row = the team's own base

            def score(pts):
                tag = np.zeros(len(pts))
                dang = np.zeros(len(pts))
                for i in range(0, len(enemy), 4000):
                    e = enemy[i:i + 4000]
                    d = np.hypot(pts[:, None, 0] - e[None, :, 0], pts[:, None, 1] - e[None, :, 1]) / 100
                    tag += ((d >= TAG_MIN) & (d <= TAG_MAX)).sum(1)
                    dang += (d < DANGER).sum(1)
                return tag, dang
            tag, dang = score(spots)
            hgt = np.zeros(len(spots))
            if land is not None:
                _, hh = land.lookup(spots[:, 0], spots[:, 1])
                hgt = np.nan_to_num((hh - np.nanmedian(hh)) / 100)
            mt, md, mh = tag[-1], dang[-1], hgt[-1]
            ok = dang[:-1] <= max(md, 5)
            best = int(np.argmax(np.where(ok, tag[:-1], -1))) if ok.any() else None
            dist_best = float(np.hypot(spots[best, 0] - bx, spots[best, 1] - by) / 100) if best is not None else np.nan
            centre = float(np.hypot(bx - h["next_x"], by - h["next_y"]) / h["next_r"])
            rows.append(dict(match_id=mid, game=gnum[mid], zone=int(h["zone"]), hold_s=float(h["start_shrink_t"] - h["appear"]),
                             base_tag=mt, base_danger=md, base_height=mh, centre=centre,
                             median_tag=float(np.median(tag[:-1])), best_tag=float(tag[best]) if best is not None else np.nan,
                             best_height=float(hgt[best]) if best is not None else np.nan, dist_best=dist_best))
            maps[(mid, int(h["zone"]))] = dict(spots=spots[:-1], tag=tag[:-1], base=(bx, by), best=spots[best] if best is not None else None,
                                               h=h, enemy_paths=w[w["team_index"] != my_team])
    zt = pd.DataFrame(rows)
    if zt.empty:
        r.headline = "No zone holds found for this team in the selected matches."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    zt["tag_share"] = zt["base_tag"] / zt["best_tag"].replace(0, np.nan)
    zt["better_anywhere"] = zt["best_tag"] >= 2 * zt["base_tag"].clip(lower=1)
    zt["better_nearby"] = zt["better_anywhere"] & (zt["dist_best"] <= 150)

    # ---- surge: each detected episode in their games
    surge_rows = []
    if has_tables(con, "health", "damage"):
        from .surge import surge_episodes
        ep_s, per = surge_episodes(ctx)
        if len(per):
            per = per[per["match_id"].isin(games)].merge(pl[["match_id", "id", "team_index"]], on=["match_id", "id"])
            for epi, g in per.groupby("episode"):
                mid = g["match_id"].iat[0]
                me = g[g["team_index"] == tid[mid]]
                if me.empty:
                    continue
                e = ep_s[ep_s["episode"] == epi].iloc[0]
                safe_min = g.loc[~g["surged"], "dealt"].min() if (~g["surged"]).any() else np.nan
                surge_rows.append({"Game": gnum[mid], "Zone": int(e["phase"]), "Players alive": int(e.get("alive", len(g))),
                                   "Surged": "Yes" if me["surged"].any() else "No",
                                   "Their damage dealt (max of the two)": round(float(me["dealt"].max())),
                                   "Least damage of a safe player": round(float(safe_min)) if safe_min == safe_min else None,
                                   "Their rank (0 least – 1 most)": round(float(g["dealt"].rank(pct=True)[me.index].max()), 2)})
    if surge_rows:
        sr = pd.DataFrame(surge_rows).sort_values(["Game", "Zone"])
        r.table("Surge episodes in their games", sr)
        r.metric("Surged", f"{(sr['Surged'] == 'Yes').sum()} of {len(sr)} surge episodes", "Episodes where at least one of them took surge damage")
    else:
        r.notes.append("No surge episodes were detected in these games, so the surge table is empty; tag opportunities still show where "
                       "damage could have been gathered.")

    # ---- tags: one-sided damage exchanges they won, by height
    if has_tables(con, "damage"):
        from .height_damage import exchanges
        ex = exchanges(ctx)
        if len(ex):
            ex = ex[ex["match_id"].isin(games)]
            mine = ex[[tid.get(m) == s for m, s in zip(ex["match_id"], ex["side"])]]
            if len(mine):
                tags = mine[mine["one_sided"]]
                r.metric("Their tags", f"{len(tags)} one-sided exchanges", "Exchanges where they dealt damage and took none back")
                tbh = mine.groupby("height", observed=False).agg(n=("dealt", "size"), dealt=("dealt", "sum"), taken=("taken", "sum"))
                tbh["Dealt per 1 taken"] = (tbh["dealt"] / tbh["taken"].replace(0, np.nan)).round(2)
                r.table("Their damage trade by height", tbh.reset_index().rename(columns={"height": "Height over the opponent", "n": "Exchanges",
                                                                                         "dealt": "Damage dealt", "taken": "Damage taken back"}).round(0))

    # ---- tables and charts
    tbl = pd.DataFrame({
        "Game": zt["game"], "Zone": zt["zone"], "Hold (s)": zt["hold_s"].round(0),
        "Base": [f"inside, {c:.0%} of the way to the edge" if c <= 1 else f"outside, {c:.1f}× the radius from the centre" for c in zt["centre"]],
        "Base: tag opportunities": zt["base_tag"].astype(int),
        "Best spot: tag opportunities": zt["best_tag"].fillna(0).astype(int),
        "Best spot: distance (m)": zt["dist_best"].round(0),
        "Base: danger": zt["base_danger"].astype(int),
        "Base height vs zone (m)": zt["base_height"].round(0), "Best spot height (m)": zt["best_height"].round(0),
        "Stronger base": np.where(zt["better_nearby"], "Yes, within 150 m", np.where(zt["better_anywhere"], "Yes, elsewhere in the zone", "")),
    }).sort_values(["Game", "Zone"])
    r.table("Where they set up each zone", tbl)
    by_zone = zt.groupby("zone").agg(base=("base_tag", "mean"), best=("best_tag", "mean"), med=("median_tag", "mean"))
    r.chart("bar", "Tag opportunities: their base vs the best spot nearby",
            [dict(name="Their base", x=[f"Zone {z}" for z in by_zone.index], y=by_zone["base"].round(0).tolist()),
             dict(name="Best spot with no more danger", x=[f"Zone {z}" for z in by_zone.index], y=by_zone["best"].round(0).tolist()),
             dict(name="Typical spot in the zone", x=[f"Zone {z}" for z in by_zone.index], y=by_zone["med"].round(0).tolist())],
            y_label="Enemy player-seconds in tag range")
    share = zt["better_nearby"].mean()
    r.metric("Zones with a stronger base nearby", f"{share:.0%}", "Zones where a spot within 150 m had at least twice the tag opportunities "
             "with no more danger")
    r.metric("Zones with a stronger base anywhere", f"{zt['better_anywhere'].mean():.0%}", "Zones where any spot in the zone had at least "
             "twice the tag opportunities with no more danger")
    r.metric("Their bases vs the zone", f"{(zt['base_tag'] / zt['median_tag'].replace(0, np.nan)).median():.1f}× a typical spot",
             "Tag opportunities at their base compared with a typical spot in the same zone")

    # ---- map for one game and zone
    mid = ctx.params.get("match") if ctx.params.get("match") in gnum else games[0]
    want = int(ctx.params.get("zone") or 5)
    key = (mid, want) if (mid, want) in maps else next((k for k in maps if k[0] == mid), next(iter(maps)))
    m = maps[key]
    h = m["h"]
    tg = m["tag"]
    q = np.quantile(tg, [0.5, 0.85]) if len(tg) else [0, 0]
    series = []
    ep_ = m["enemy_paths"].iloc[::4]
    series.append(dict(name="Other teams during the hold", x=ep_["x"].round(0).tolist(), y=ep_["y"].round(0).tolist(), color="#C9D3DD", size=2, opacity=0.6))
    for lab, mask, col, size in (("Most tag opportunities", tg >= q[1], "#0F766E", 7), ("Above typical", (tg >= q[0]) & (tg < q[1]), "#7FC8C0", 6)):
        series.append(dict(name=lab, x=m["spots"][mask, 0].round(0).tolist(), y=m["spots"][mask, 1].round(0).tolist(), color=col, size=size))
    series.append(dict(name="Their base", x=[round(m["base"][0])], y=[round(m["base"][1])], color="#D08A12", size=15))
    if m["best"] is not None:
        series.append(dict(name="Best spot nearby", x=[round(m["best"][0])], y=[round(m["best"][1])], color="#0F3B5F", size=13))
    pad = h["next_r"] * 1.4
    r.chart("map_points", "Their base vs stronger surge bases", series, x_label="Map X", y_label="Map Y",
            circles=[dict(x=float(h["next_x"]), y=float(h["next_y"]), r=float(h["next_r"]), label=f"Zone {key[1]}")], zone_circles=True,
            range_x=[h["next_x"] - pad, h["next_x"] + pad], range_y=[h["next_y"] - pad, h["next_y"] + pad])
    r.notes.insert(0, f"Map: game {gnum[key[0]]}, zone {key[1]} ({key[0]}). Grey dots are other teams' positions during the hold; teal spots "
                      "had the most tag opportunities; amber is their base; navy the best spot with no more danger.")
    r.headline = (f"{' & '.join(names[:2])}: in {zt['better_anywhere'].mean():.0%} of {len(zt)} zone holds, the zone had a spot with at least "
                  f"twice their base's tag opportunities and no more danger ({share:.0%} within 150 m of where they were).")
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        f"Tag opportunities: enemy player-seconds within {TAG_MIN}–{TAG_MAX} m of a spot while the next zone was showing and before it "
        f"closed. Danger: enemy player-seconds within {DANGER} m.",
        "These use where other teams actually went in that game: they show what a base would have offered, not what was knowable "
        "beforehand. Patterns across many zones (for example, always holding the quiet side) are the takeaway.",
        "Height is the natural ground height from the playable map, not builds.",
    ]
    return r
