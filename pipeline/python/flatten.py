"""
flatten.py - turn per-match research JSON (from fn-extract) into analysis tables.

Usage (from the repo root):
    python pipeline/python/flatten.py                        # reads data/parsed, writes data/tables
    python pipeline/python/flatten.py --data-dir data_synthetic

Tables written (Parquet, or CSV if pyarrow is missing):
    matches        one row per match
    players        one row per player (placement, kills, death info)
    teams          one row per team
    zones          one row per storm phase (current circle -> next circle)
    zone_offsets   zones with the season-independent geometry features
    bus            one row per match (bus line, from aircraft data or derived from skydive starts)
    positions      one row per player per sample (default 1 sample/second)
    kills          kill feed (downs + eliminations, with locations)
    eliminations   elimination events (Epic IDs, gun type)
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # repo root


# --------------------------------------------------------------------------- helpers
def vec(v, i):
    return float(v[i]) if isinstance(v, (list, tuple)) and len(v) > i and v[i] is not None else np.nan


def season_label(branch: str | None) -> str | None:
    """'++Fortnite+Release-32.00' -> 'v32.00'"""
    if not branch:
        return None
    m = re.search(r"Release-(\d+\.\d+)", branch)
    return f"v{m.group(1)}" if m else branch


def mmss_to_seconds(s: str | None) -> float:
    if not s or ":" not in s:
        return np.nan
    mm, ss = s.split(":")[:2]
    try:
        return int(mm) * 60 + int(ss)
    except ValueError:
        return np.nan


def write_table(df: pd.DataFrame, path_no_ext: Path) -> str:
    try:
        df.to_parquet(path_no_ext.with_suffix(".parquet"), index=False)
        return "parquet"
    except ImportError:
        df.to_csv(path_no_ext.with_suffix(".csv"), index=False)
        return "csv"


# --------------------------------------------------------------------------- zones
def zone_phases(match_id: str, zones: list[dict]) -> list[dict]:
    """
    The replay stores SafeZoneIndicator snapshots. Each useful snapshot says
    "the storm is currently radius R, and will shrink to a circle at NextCenter
    with NextRadius". We turn them into one row per phase:
        current circle (center, radius)  ->  next circle (center, radius)
    The current center is LastCenter when present, otherwise the previous
    phase's next center. The very first circle's center is often unknown.
    """
    rows: list[dict] = []
    seen = set()
    prev_center = None
    prev_radius = None

    for z in zones:
        nc = z.get("next_center")
        nr = z.get("next_radius") or 0.0
        lc = z.get("last_center")
        lr = z.get("last_radius") or 0.0
        r = z.get("radius") or 0.0

        if not nc or nr <= 0:
            # Still useful for knowing the starting radius.
            if nr > 0:
                prev_radius = nr
            elif r > 0:
                prev_radius = r
            continue

        key = (round(nc[0]), round(nc[1]), round(nr))
        if key in seen:
            continue
        seen.add(key)

        if lc and lr > 0:
            cur_center, cur_radius = lc, lr
        else:
            cur_center = prev_center
            cur_radius = r if r > 0 else prev_radius

        rows.append(
            dict(
                match_id=match_id,
                phase=len(rows) + 1,
                cur_x=vec(cur_center, 0), cur_y=vec(cur_center, 1),
                cur_r=float(cur_radius) if cur_radius else np.nan,
                next_x=vec(nc, 0), next_y=vec(nc, 1), next_r=float(nr),
                start_shrink_t=z.get("start_shrink_t"),
                finish_shrink_t=z.get("finish_shrink_t"),
            )
        )
        prev_center, prev_radius = nc, nr
    return rows


def add_zone_features(z: pd.DataFrame) -> pd.DataFrame:
    """
    Season-independent geometry of each zone pull.
      offset_ratio : distance moved / current radius
      allowed      : max distance the next center can be from the current one
                     while staying inside it (cur_r - next_r)
      u            : (distance / allowed)^2. If the next center were uniformly
                     random inside the allowed disk, u ~ Uniform(0, 1).
      angle_deg    : direction of the pull (0 = +X, counter-clockwise, Unreal axes)
    """
    z = z.copy()
    z["dx"] = z.next_x - z.cur_x
    z["dy"] = z.next_y - z.cur_y
    z["dist"] = np.hypot(z.dx, z.dy)
    z["offset_ratio"] = z.dist / z.cur_r
    z["shrink_ratio"] = z.next_r / z.cur_r
    z["allowed"] = z.cur_r - z.next_r
    # Shrinking zone: the next circle stays inside the current one. Moving zone: it drifts
    # past the old edge (late game). u is only meaningful for shrinking zones.
    z["kind"] = np.where(z.dist <= z.allowed * 1.01 + 1, "shrinking", "moving")
    z["u"] = np.where(z.kind == "shrinking", (z.dist / z.allowed).clip(upper=1) ** 2, np.nan)
    z["angle_deg"] = np.degrees(np.arctan2(z.dy, z.dx)) % 360
    # Seconds between the previous shrink finishing and this one starting (0 = continuous moving zone).
    prev_finish = z.groupby("match_id")["finish_shrink_t"].shift(1)
    z["wait_s"] = (z.start_shrink_t - prev_finish).round(1)
    return z.dropna(subset=["dist", "cur_r"])


# --------------------------------------------------------------------------- bus
def bus_row(match_id: str, doc: dict, pos: pd.DataFrame) -> dict:
    """Prefer the replay's aircraft data; otherwise derive the bus line from where players start skydiving."""
    for b in doc.get("bus") or []:
        start = b.get("start")
        yaw = b.get("yaw")
        if start and yaw is not None and any(abs(c) > 0 for c in start[:2]):
            bearing = float(yaw) % 360  # Unreal yaw: 0 = +X, increasing toward +Y
            ux, uy = math.cos(math.radians(bearing)), math.sin(math.radians(bearing))
            speed = b.get("speed") or 0
            t0 = b.get("flight_start_time")
            t_end = b.get("flight_end_time")
            dur = (t_end - t0) if (t0 is not None and t_end) else (b.get("time_till_flight_end") or 0)
            at = lambda secs: (start[0] + ux * speed * secs, start[1] + uy * speed * secs)  # noqa: E731
            row = dict(match_id=match_id, bus_source="aircraft",
                       start_x=start[0], start_y=start[1], bearing_deg=bearing, speed=speed or np.nan,
                       end_x=np.nan, end_y=np.nan, drop_start_x=np.nan, drop_start_y=np.nan,
                       drop_end_x=np.nan, drop_end_y=np.nan,
                       flight_start_t=t0, drop_start_t=b.get("drop_start_time"), drop_end_t=b.get("drop_end_time"),
                       n_points=np.nan, fit_rms=0.0, direction_known=True)
            if speed > 0 and dur > 0:
                row["end_x"], row["end_y"] = at(dur)
            if speed > 0 and t0 is not None and b.get("drop_start_time") and b.get("drop_end_time"):
                row["drop_start_x"], row["drop_start_y"] = at(b["drop_start_time"] - t0)
                row["drop_end_x"], row["drop_end_y"] = at(b["drop_end_time"] - t0)
            return row

    # Derived: each player's first skydiving sample lies (roughly) on the bus line.
    empty = dict(match_id=match_id, bus_source="none", start_x=np.nan, start_y=np.nan,
                 end_x=np.nan, end_y=np.nan, bearing_deg=np.nan, n_points=0, fit_rms=np.nan,
                 direction_known=False)
    if pos.empty or "skydiving" not in pos:
        return empty
    sky = pos[pos.skydiving == True].sort_values("t").groupby("id").first()  # noqa: E712
    if len(sky) < 5:
        empty["n_points"] = len(sky)
        return empty

    pts = sky[["x", "y"]].to_numpy(float)
    mean = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - mean, full_matrices=False)
    direction = vt[0]
    proj = (pts - mean) @ direction
    # Orient direction of travel: players later in time jump further along the line.
    t = sky["t"].to_numpy(float)
    direction_known = bool(np.std(t) > 0)
    if direction_known:
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = np.corrcoef(proj, t)[0, 1]
        direction_known = bool(np.isfinite(corr))
        if direction_known and corr < 0:
            direction, proj = -direction, -proj
    perp = (pts - mean) @ np.array([-direction[1], direction[0]])
    start, end = mean + direction * proj.min(), mean + direction * proj.max()
    return dict(match_id=match_id, bus_source="derived_skydive",
                start_x=start[0], start_y=start[1], end_x=end[0], end_y=end[1],
                bearing_deg=float(np.degrees(np.arctan2(direction[1], direction[0])) % 360),
                n_points=len(sky), fit_rms=float(np.sqrt(np.mean(perp ** 2))),
                direction_known=direction_known)


# --------------------------------------------------------------------------- missing winners
def infer_missing_winners(out: dict) -> None:
    """
    Some replays end before the winner's placement is written. If a match has no
    placement 1, and exactly one human without a placement was still being tracked at
    the end of the match (the replay records the winner "leaving" as the match ends, so
    a death time within 5 s of the end counts as alive), that player is the winner.
    Anyone else without a placement (for example players who left early) stays blank.
    Adds players.placement_inferred (True where the placement was filled in here).
    """
    pl, pos = out["players"], out["positions"]
    if pl.empty:
        return
    pl["placement_inferred"] = False
    last_seen = pos.groupby(["match_id", "id"])["t"].max() if not pos.empty else pd.Series(dtype=float)
    pos_end = pos.groupby("match_id")["t"].max() if not pos.empty else pd.Series(dtype=float)
    game_end = out["matches"].set_index("match_id")["match_end_t"] if "match_end_t" in out["matches"] else pd.Series(dtype=float)
    for mid, g in pl[~pl["is_bot"].astype(bool)].groupby("match_id"):
        ends = [v for v in (pos_end.get(mid), game_end.get(mid)) if v is not None and pd.notna(v)]
        if (g["placement"] == 1).any() or not ends:
            continue
        end = max(ends)
        alive = g[g["placement"].isna() & (g["death_t"].isna() | (g["death_t"] >= end - 5))]
        alive = alive[[last_seen.get((mid, i), -1e9) >= end - 30 for i in alive["id"]]]
        if len(alive) == 1:
            idx = alive.index[0]
            pl.loc[idx, "placement"] = 1
            pl.loc[idx, "placement_inferred"] = True
            # Teams table: give the winner's team placement 1 too.
            t = out["teams"]
            if not t.empty:
                t.loc[(t["match_id"] == mid) & (t["team_index"] == pl.loc[idx, "team_index"]), "placement"] = 1
    out["players"] = pl


# --------------------------------------------------------------------------- lobby strength
RANK_TIERS = (100, 500, 1000)


def add_lobby_strength(out: dict, data: Path) -> None:
    """
    Skill context for every player and lobby, from two optional sources:

    1. Epic Power Rankings (power_rankings.csv, from find_matches.js powerrankings):
       a cross-event skill rating for the global top 10,000. Preferred.
         players: pr_rank, pr_score
         matches: lobby_pr_top1000 / lobby_pr_top10000 (share of humans ranked that high),
                  lobby_pr_mean (mean rating of ranked players)
    2. The tournament window's own leaderboard (player_ranks.csv, saved by option 4):
       how players finished that session. Fallback when PR isn't downloaded.
         players: leaderboard_rank, leaderboard_points
         matches: lobby_top100/500/1000, lobby_median_rank, ranks_read

    lobby_strength = lobby_pr_top1000 when PR is available, else lobby_top1000;
    lobby_strength_source says which.
    """
    pl, m = out["players"], out["matches"]
    session_cols = ["ranked_players", "lobby_top100", "lobby_top500", "lobby_top1000", "lobby_median_rank", "ranks_read"]
    pr_cols = ["lobby_pr_top1000", "lobby_pr_top10000", "lobby_pr_mean"]
    for c in session_cols + pr_cols + ["lobby_strength", "lobby_strength_source"]:
        if c in m.columns:
            m = m.drop(columns=c)
    for c in session_cols + pr_cols:
        m[c] = np.nan
    m["lobby_strength"] = np.nan
    m["lobby_strength_source"] = None
    if pl.empty or m.empty:
        out["matches"] = m
        return

    acc = pl["player_id"].astype(str).str.lower()
    is_human = ~pl["is_bot"].astype(bool)
    pl = pl.assign(leaderboard_rank=np.nan, leaderboard_points=np.nan, pr_rank=np.nan, pr_score=np.nan)

    # 1. Power Rankings
    pr_path = data / "power_rankings.csv"
    if pr_path.exists():
        pr = pd.read_csv(pr_path, dtype={"account_id": str}).drop_duplicates("account_id")
        pr["account_id"] = pr["account_id"].str.lower()
        pr_map = pr.set_index("account_id")
        pl["pr_rank"] = acc.map(pr_map["pr_rank"]).to_numpy()
        score = pd.to_numeric(pr_map["pr_score"], errors="coerce")
        if score.isna().all() and "pr_points" in pr_map:
            score = pd.to_numeric(pr_map["pr_points"], errors="coerce")
        pl["pr_score"] = acc.map(score).to_numpy()
        h = pl[is_human].groupby("match_id")
        stats = pd.DataFrame({
            "lobby_pr_top1000": h["pr_rank"].apply(lambda s: (s <= 1000).mean()),
            "lobby_pr_top10000": h["pr_rank"].apply(lambda s: s.notna().mean()),
            "lobby_pr_mean": h["pr_score"].mean(),
        })
        m = m.set_index("match_id")
        m.update(stats)
        m = m.reset_index()

    # 2. Session leaderboard ranks
    rk_path = data / "player_ranks.csv"
    if rk_path.exists():
        rk = pd.read_csv(rk_path, dtype={"event_window_id": str, "account_id": str})
        rk["account_id"] = rk["account_id"].str.lower()
        read = rk.groupby("event_window_id")["ranks_read"].max()
        p = pl[["match_id", "is_bot"]].assign(account_id=acc.to_numpy()).merge(
            m[["match_id", "event_window_id"]], on="match_id", how="left")
        p = p.merge(rk[["event_window_id", "account_id", "rank", "points"]], on=["event_window_id", "account_id"], how="left")
        pl["leaderboard_rank"] = p["rank"].to_numpy()
        pl["leaderboard_points"] = p["points"].to_numpy()
        h = p[~p["is_bot"].astype(bool) & p["event_window_id"].isin(read.index)].groupby("match_id")
        stats = pd.DataFrame({
            "ranked_players": h["rank"].count(),
            **{f"lobby_top{n}": h["rank"].apply(lambda s, n=n: (s <= n).mean()) for n in RANK_TIERS},
            "lobby_median_rank": h["rank"].median(),
        })
        m = m.set_index("match_id")
        m.update(stats)
        m = m.reset_index()
        m["ranks_read"] = m["event_window_id"].map(read)

    has_pr = m["lobby_pr_top1000"].notna()
    m["lobby_strength"] = np.where(has_pr, m["lobby_pr_top1000"], m["lobby_top1000"])
    m["lobby_strength_source"] = np.where(has_pr, "power_rankings",
                                          np.where(m["lobby_top1000"].notna(), "session", None))
    out["players"], out["matches"] = pl, m


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--match-ids", help="defaults to <data-dir>/match_ids.csv")
    args = ap.parse_args()

    data = Path(args.data_dir)
    parsed, raw, tables_dir = data / "parsed", data / "raw", data / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    ids_info = {}
    args.match_ids = args.match_ids or str(data / "match_ids.csv")
    if Path(args.match_ids).exists():
        df_ids = pd.read_csv(args.match_ids, comment="#", dtype=str)
        if "match_id" in df_ids:
            ids_info = df_ids.set_index("match_id").to_dict("index")

    T = {k: [] for k in ["matches", "players", "teams", "zones", "bus", "positions", "kills", "eliminations"]}
    files = sorted(parsed.glob("*.json"))
    if not files:
        raise SystemExit(f"No parsed JSON files found in {parsed}")

    for f in files:
        doc = json.loads(f.read_text())
        mid = doc.get("match_id") or f.stem
        rep, game = doc.get("replay", {}), doc.get("game", {})
        meta_path = raw / f"{mid}.meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        info = ids_info.get(mid, {})
        players = doc.get("players", [])

        # positions
        p = doc.get("positions") or {}
        pos = pd.DataFrame(p.get("rows", []), columns=p.get("columns"))
        if not pos.empty:
            pos.insert(0, "match_id", mid)
            team_of = {pl["id"]: pl.get("team_index") for pl in players}
            pos["team_index"] = pos["id"].map(team_of)
            T["positions"].append(pos)

        humans = [pl for pl in players if not pl.get("is_bot")]
        T["matches"].append(dict(
            match_id=mid, source_file=doc.get("source_file"),
            event_window_id=info.get("event_window_id"), region=info.get("region"),
            session_date=info.get("session_date"),
            match_date=(info.get("session_date") or (rep.get("timestamp") or "")[:10]) or None,
            is_server_replay=(str(info.get("is_server_replay")) == "1") if info.get("is_server_replay") is not None else None,
            replay_timestamp=rep.get("timestamp"), length_s=(rep.get("length_ms") or 0) / 1000,
            season=season_label(rep.get("branch")), branch=rep.get("branch"),
            network_version=rep.get("network_version") or meta.get("NetworkVersion"),
            changelist=rep.get("changelist") or meta.get("Changelist"),
            engine_network_version=rep.get("engine_network_version"),
            is_encrypted=rep.get("is_encrypted"), recorded_on=rep.get("platform"),
            playlist=game.get("playlist"), tournament_round=game.get("tournament_round"),
            max_players=game.get("max_players"),
            aircraft_start_t=game.get("aircraft_start_t"), safe_zones_start_t=game.get("safe_zones_start_t"),
            match_end_t=game.get("match_end_t"), winning_team=game.get("winning_team"),
            n_players=len(players), n_humans=len(humans),
            n_teams=len({pl.get("team_index") for pl in humans if pl.get("team_index") is not None}),
        ))

        for pl in players:
            dl = pl.get("death_location")
            T["players"].append(dict(
                match_id=mid, id=pl.get("id"), player_id=pl.get("player_id"), name=pl.get("name"),
                is_bot=pl.get("is_bot", False), team_index=pl.get("team_index"),
                placement=pl.get("placement"), kills=pl.get("kills"), team_kills=pl.get("team_kills"),
                death_t=pl.get("death_t"), death_cause=pl.get("death_cause"),
                death_x=vec(dl, 0), death_y=vec(dl, 1), death_z=vec(dl, 2),
                disconnected=pl.get("disconnected"), platform=pl.get("platform"),
                raw_location_samples=pl.get("location_samples_raw"),
            ))

        for t in doc.get("teams", []):
            T["teams"].append(dict(match_id=mid, team_index=t.get("team_index"), placement=t.get("placement"),
                                   team_kills=t.get("team_kills"), n_players=len(t.get("player_ids") or []),
                                   player_ids=",".join(str(i) for i in (t.get("player_ids") or []))))

        T["zones"] += zone_phases(mid, doc.get("zones", []))
        T["bus"].append(bus_row(mid, doc, pos))

        for k in doc.get("kill_feed", []):
            loc = k.get("location")
            T["kills"].append(dict(match_id=mid, t=k.get("t"), victim_id=k.get("victim_id"),
                                   finisher_id=k.get("finisher_id"), downed=k.get("downed"),
                                   revived=k.get("revived"), distance=k.get("distance"),
                                   death_cause=k.get("death_cause"),
                                   x=vec(loc, 0), y=vec(loc, 1), z=vec(loc, 2)))

        for e in doc.get("eliminations", []):
            T["eliminations"].append(dict(match_id=mid, time_s=mmss_to_seconds(e.get("time")),
                                          eliminated=(e.get("eliminated") or "").lower() or None,
                                          eliminator=(e.get("eliminator") or "").lower() or None,
                                          knocked=e.get("knocked"), gun_type=e.get("gun_type")))

    out = {}
    for name, rows in T.items():
        if name == "positions":
            out[name] = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        else:
            out[name] = pd.DataFrame(rows)
    out["zone_offsets"] = add_zone_features(out["zones"]) if not out["zones"].empty else pd.DataFrame()

    infer_missing_winners(out)
    add_lobby_strength(out, data)

    # Team placement = best placement of any human on the team. Players who
    # disconnect keep the placement from when they left, so members can differ.
    pl = out["players"]
    if not pl.empty:
        best = (pl[~pl.is_bot.astype(bool)].groupby(["match_id", "team_index"]).placement.min()
                .rename("team_placement").reset_index())
        out["players"] = pl.merge(best, on=["match_id", "team_index"], how="left")
        if not out["teams"].empty:
            out["teams"] = out["teams"].merge(best, on=["match_id", "team_index"], how="left")

    for name, df in out.items():
        fmt = write_table(df, tables_dir / name)
        print(f"{name:13s} {len(df):>9,} rows  -> {tables_dir / name}.{fmt}")


if __name__ == "__main__":
    main()
