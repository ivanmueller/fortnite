"""
Team audit, part 2: where did the team set up each zone, were there better surge bases, and how was their surge?

Resting place: the team's median position while the next zone was showing and before it started closing
(the hold). Surge bases are scored in zones 2-5, where traditional surge bases are built. Candidate bases:
a grid over the next zone. Each spot:
  tag opportunities  rotating enemies (running, and outside the next zone or heading into it) within tagging
                     range (learned from where one-sided tags actually happened), on the outward side of the
                     spot (looking toward the storm, zone at your back); counted double when the spot's natural
                     ground is 15 m+ above them
  danger             enemy player-seconds within 30 m (fight risk rather than tags)
The best alternative is the spot with the most tag opportunities and no more danger than the team's own.
Validation: for every team's base in zones 2-5, how well each scoring (this one, and simple nearness to
enemies) predicts the damage that team actually dealt during the hold.
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

TAG_MIN, TAG_MAX, DANGER = 30, 120, 30   # metres (tagging range is re-learned from the data when possible)
GRID = 28
SURGE_ZONES = (2, 5)
RUN_SPEED = 250        # cm/s: faster than this counts as moving
HIGH_GROUND = 1500     # cm: 15 m above the target


def tag_range(ctx: Context) -> tuple[float, float]:
    """Where one-sided tags actually happen in zones 2-5: the middle 60% of their distances."""
    try:
        from .height_damage import exchanges
        ex = exchanges(ctx)
        d = ex[ex["one_sided"] & ex["zone"].between(*SURGE_ZONES)]["dist_m"]
        if len(d) >= 30:
            lo, hi = float(np.quantile(d, 0.2)), float(np.quantile(d, 0.8))
            return max(20.0, lo), min(250.0, max(hi, lo + 30))   # floor of 20 m: closer than that is a fight, not a tag
    except Exception:  # noqa: BLE001
        pass
    return float(TAG_MIN), float(TAG_MAX)


def score_spots(spots: np.ndarray, spot_z: np.ndarray, enemy: pd.DataFrame, zx: float, zy: float, zr: float,
                rng: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(tag opportunities facing rotators, simple nearness, danger) for each spot."""
    e = enemy[["x", "y", "z", "vx", "vy"]].to_numpy(float)
    speed = np.hypot(e[:, 3], e[:, 4])
    to_c = np.c_[zx - e[:, 0], zy - e[:, 1]]
    heading_in = (e[:, 3] * to_c[:, 0] + e[:, 4] * to_c[:, 1]) / (speed * np.hypot(to_c[:, 0], to_c[:, 1]) + 1e-9) > 0.5
    outside = np.hypot(to_c[:, 0], to_c[:, 1]) > zr
    rotating = (speed >= RUN_SPEED) & (outside | heading_in)
    tag, near, danger = np.zeros(len(spots)), np.zeros(len(spots)), np.zeros(len(spots))
    out_x, out_y = spots[:, 0] - zx, spots[:, 1] - zy
    out_n = np.hypot(out_x, out_y) + 1e-9
    for i in range(0, len(e), 3000):
        b = e[i:i + 3000]
        dx, dy = b[None, :, 0] - spots[:, None, 0], b[None, :, 1] - spots[:, None, 1]
        d = np.hypot(dx, dy) / 100
        facing = (dx * out_x[:, None] + dy * out_y[:, None]) / (d * 100 * out_n[:, None] + 1e-9) > 0.3
        in_rng = (d >= rng[0]) & (d <= rng[1])
        high = (spot_z[:, None] - b[None, :, 2]) >= HIGH_GROUND
        tag += (in_rng & facing & rotating[i:i + 3000][None, :] * 1).astype(float).sum(1) + (in_rng & facing & high & rotating[i:i + 3000][None, :]).sum(1)
        near += ((d >= TAG_MIN) & (d <= TAG_MAX)).sum(1)
        danger += (d < DANGER).sum(1)
    return tag, near, danger


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
                  Param("zone", "Zone to draw", "zone", 4, [{"value": z, "label": f"Zone {z}"} for z in range(2, 6)])])
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

    rng = tag_range(ctx)
    rows, maps, valid = [], {}, []
    for mid in games:
        my_team = tid[mid]
        pos = df(con, f"""SELECT p.id, p.t, p.x, p.y, p.z, coalesce(p.vx, 0) AS vx, coalesce(p.vy, 0) AS vy FROM positions p
                          WHERE p.match_id = '{mid}'""").merge(pl[pl["match_id"] == mid][["id", "team_index", "death_t"]], on="id")
        pos = pos[pos["death_t"].isna() | (pos["t"] < pos["death_t"])]
        dealt = df(con, f"""SELECT d.t, d.amount, pa.team_index AS att FROM damage d JOIN players pa ON pa.match_id = d.match_id AND pa.id = d.attacker_id
                            JOIN players pt ON pt.match_id = d.match_id AND pt.id = d.target_id
                            WHERE d.match_id = '{mid}' AND d.target_kind = 'player' AND pa.team_index IS DISTINCT FROM pt.team_index""") \
            if has_tables(con, "damage") else pd.DataFrame(columns=["t", "amount", "att"])
        for _, h in holds[(holds["match_id"] == mid) & holds["zone"].between(*SURGE_ZONES)].iterrows():
            w = pos[(pos["t"] >= h["appear"]) & (pos["t"] <= h["start_shrink_t"])]
            if w.empty:
                continue
            g = np.linspace(-1, 1, GRID)
            gx, gy = np.meshgrid(g, g)
            inside = gx ** 2 + gy ** 2 <= 0.92
            grid = np.c_[h["next_x"] + gx[inside] * h["next_r"], h["next_y"] + gy[inside] * h["next_r"]]
            bases = w.groupby("team_index").agg(x=("x", "median"), y=("y", "median"), z=("z", "median"))
            spots = np.vstack([grid, bases[["x", "y"]].to_numpy(float)])
            if land is not None:
                _, gz = land.lookup(spots[:, 0], spots[:, 1])
                gz = np.where(np.isnan(gz), np.nanmedian(gz) if np.isfinite(gz).any() else 0, gz)
            else:
                gz = np.zeros(len(spots))
            gz[len(grid):] = np.maximum(gz[len(grid):], bases["z"].to_numpy(float))     # a team's base height includes its build
            # score every spot against the other teams' positions (for team bases: everyone but that team)
            tag = np.zeros(len(spots)); near = np.zeros(len(spots)); dang = np.zeros(len(spots))
            tg, nr, dg = score_spots(grid, gz[:len(grid)], w[w["team_index"] != my_team], h["next_x"], h["next_y"], h["next_r"], rng)
            tag[:len(grid)], near[:len(grid)], dang[:len(grid)] = tg, nr, dg
            for j, ti in enumerate(bases.index):
                a, b_, c = score_spots(spots[[len(grid) + j]], gz[[len(grid) + j]], w[w["team_index"] != ti], h["next_x"], h["next_y"], h["next_r"], rng)
                tag[len(grid) + j], near[len(grid) + j], dang[len(grid) + j] = a[0], b_[0], c[0]
                got = dealt[(dealt["att"] == ti) & dealt["t"].between(h["appear"], h["start_shrink_t"])]["amount"].sum() if len(dealt) else np.nan
                valid.append(dict(match_id=mid, zone=int(h["zone"]), team=ti, tag=a[0], near=b_[0], dealt=got))
            if my_team not in bases.index:
                continue
            k = len(grid) + list(bases.index).index(my_team)
            bx, by = spots[k]
            mt, md, mh = tag[k], dang[k], gz[k] - np.median(gz[:len(grid)])
            ok = dang[:len(grid)] <= max(md, 5)
            best = int(np.argmax(np.where(ok, tag[:len(grid)], -1))) if ok.any() else None
            dist_best = float(np.hypot(grid[best, 0] - bx, grid[best, 1] - by) / 100) if best is not None else np.nan
            centre = float(np.hypot(bx - h["next_x"], by - h["next_y"]) / h["next_r"])
            rows.append(dict(match_id=mid, game=gnum[mid], zone=int(h["zone"]), hold_s=float(h["start_shrink_t"] - h["appear"]),
                             base_tag=mt, base_danger=md, base_height=mh / 100, centre=centre,
                             median_tag=float(np.median(tag[:len(grid)])), best_tag=float(tag[best]) if best is not None else np.nan,
                             best_height=float((gz[best] - np.median(gz[:len(grid)])) / 100) if best is not None else np.nan, dist_best=dist_best,
                             appear=h["appear"], until=h["start_shrink_t"]))
            enemy = w[w["team_index"] != my_team]
            spd = np.hypot(enemy["vx"], enemy["vy"])
            maps[(mid, int(h["zone"]))] = dict(spots=grid, tag=tag[:len(grid)], base=(bx, by), best=grid[best] if best is not None else None, h=h,
                                               rotating=enemy[spd >= RUN_SPEED], holding=enemy[spd < RUN_SPEED])
    zt = pd.DataFrame(rows)
    if zt.empty:
        r.headline = "No zone holds found for this team in the selected matches."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    zt["tag_share"] = zt["base_tag"] / zt["best_tag"].replace(0, np.nan)

    # ---- which scoring predicts the damage teams actually dealt from their bases?
    v = pd.DataFrame(valid)
    if len(v) >= 30 and v["dealt"].notna().any():
        from ..stats import spearman, ttest_mean
        res = []
        for col, name in (("tag", "Facing rotating players (this page)"), ("near", "Simply near enemies")):
            rho = v.groupby(["match_id", "zone"]).apply(lambda q: spearman(q[col], q["dealt"]) if q[col].nunique() > 1 and len(q) >= 6 else np.nan,
                                                        include_groups=False).dropna()
            t = ttest_mean(rho, 0.0)
            res.append({"Scoring": name, "Link with damage actually dealt": f"{t['mean']:+.2f}" if t["n"] else "–",
                        "Zone holds": int(t["n"]), "p": f"{t['p']:.3g}" if t["n"] >= 3 else "–"})
        r.table("Which scoring predicts real damage", pd.DataFrame(res))
        r.notes.append("Link with damage: within each zone hold, the rank correlation between a base's score and the damage its team "
                       "dealt during the hold, averaged over holds (+1 = perfectly predictive, 0 = no link).")
    r.notes.append(f"Tagging range used: {rng[0]:.0f}–{rng[1]:.0f} m (the middle 60% of one-sided tag distances in zones 2–5, when enough "
                   "were found; never closer than 20 m).")

    # ---- loadouts during each hold
    if has_tables(con, "weapons_held"):
        wh = df(con, "SELECT w.match_id, w.t, w.id, w.weapon, w.category, w.rarity FROM weapons_held w JOIN sel USING (match_id) WHERE w.category = 'weapon'")
        wh = wh[wh["match_id"].isin(games)].merge(team[["match_id", "id", "name"]], on=["match_id", "id"])
        lo = []
        for _, q in zt.iterrows():
            ww = wh[(wh["match_id"] == q["match_id"]) & (wh["t"] <= q["until"])]
            for nm, g_ in ww.groupby("name"):
                recent = g_.sort_values("t").drop_duplicates("weapon", keep="last").tail(5)
                lo.append({"Game": q["game"], "Zone": q["zone"], "Player": nm,
                           "Weapons held up to this zone (latest 5)": ", ".join(f"{_pretty(x)} ({r_ or '?'})" for x, r_ in zip(recent["weapon"], recent["rarity"]))})
        if lo:
            r.table("Loadouts during each hold", pd.DataFrame(lo).sort_values(["Game", "Zone", "Player"]))
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
    want = int(ctx.params.get("zone") or 4)
    key = (mid, want) if (mid, want) in maps else next((k for k in maps if k[0] == mid), next(iter(maps)))
    m = maps[key]
    h = m["h"]
    tg = m["tag"]
    q = np.quantile(tg, [0.5, 0.85]) if len(tg) else [0, 0]
    series = []
    hold_ = m["holding"].iloc[::4]
    rot_ = m["rotating"].iloc[::2]
    series.append(dict(name="Other teams holding", x=hold_["x"].round(0).tolist(), y=hold_["y"].round(0).tolist(), color="#C9D3DD", size=2, opacity=0.6))
    series.append(dict(name="Other teams rotating", x=rot_["x"].round(0).tolist(), y=rot_["y"].round(0).tolist(), color="#B4535F", size=3, opacity=0.6))
    for lab, mask, col, size in (("Most tag opportunities", tg >= q[1], "#0F766E", 7), ("Above typical", (tg >= q[0]) & (tg < q[1]), "#7FC8C0", 6)):
        series.append(dict(name=lab, x=m["spots"][mask, 0].round(0).tolist(), y=m["spots"][mask, 1].round(0).tolist(), color=col, size=size))
    series.append(dict(name="Their base", x=[round(m["base"][0])], y=[round(m["base"][1])], color="#D08A12", size=15))
    if m["best"] is not None:
        series.append(dict(name="Best spot nearby", x=[round(m["best"][0])], y=[round(m["best"][1])], color="#0F3B5F", size=13))
    pad = h["next_r"] * 1.4
    r.chart("map_points", "Their base vs stronger surge bases", series, x_label="Map X", y_label="Map Y",
            circles=[dict(x=float(h["next_x"]), y=float(h["next_y"]), r=float(h["next_r"]), label=f"Zone {key[1]}")], zone_circles=True,
            range_x=[h["next_x"] - pad, h["next_x"] + pad], range_y=[h["next_y"] - pad, h["next_y"] + pad])
    r.notes.insert(0, f"Map: game {gnum[key[0]]}, zone {key[1]} ({key[0]}). Red dots are other players rotating during the hold, grey "
                      "players holding; teal spots had the most tag opportunities; amber is their base; navy the best spot with no more danger.")
    r.headline = (f"{' & '.join(names[:2])}: in {zt['better_anywhere'].mean():.0%} of {len(zt)} zone holds, the zone had a spot with at least "
                  f"twice their base's tag opportunities and no more danger ({share:.0%} within 150 m of where they were).")
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        "Tag opportunities: rotating enemies (running, and outside the next zone or heading into it) within tagging range, on the "
        "outward side of the spot, counted double from 15 m+ above; while the next zone was showing and before it closed, zones 2–5. "
        f"Danger: enemy player-seconds within {DANGER} m.",
        "These use where other teams actually went in that game: they show what a base would have offered, not what was knowable "
        "beforehand. Patterns across many zones (for example, always holding the quiet side) are the takeaway.",
        "Height is the natural ground height from the playable map, not builds.",
    ]
    return r


def _pretty(weapon: str) -> str:
    """WID_Assault_AutoHigh_Athena_SR_Ore_T03 -> Assault AutoHigh."""
    w = str(weapon)
    for cut in ("WID_", "_Athena", "Athena_"):
        w = w.replace(cut, "_" if cut.endswith("_") and not cut.startswith("WID") else "")
    parts = [p for p in w.split("_") if p and not (len(p) <= 3 and p.upper() in {"C", "UC", "R", "VR", "SR", "UR", "T01", "T02", "T03", "T04", "T05", "ORE"})
             and not p.startswith("T0") and p not in {"Ore", "Athena"}]
    return " ".join(parts[:3]) or str(weapon)
