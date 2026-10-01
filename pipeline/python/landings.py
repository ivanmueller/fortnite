"""
landings.py - one row per player per match: where and when they landed, how contested
the landing was, how it sits against the bus route and the first two storm circles,
which named place it's in, and what happened next.

Landing detection (Season 42 replays don't record the skydive itself):
  1. Ignore the pre-game warm-up island: only samples after the bus departs (bus
     flight_start_t when known), or after the player's last sample off the main island.
  2. Landing = the first moment the player's vertical speed stays under 3 m/s for three
     consecutive samples, at least 50 m below where they first appeared after the bus.
     Vertical speed comes from the replay, or from altitude changes when it's missing.

Units: positions are Unreal units (cm); outputs in metres and seconds.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ISLAND_HALF_CM = 140_000          # main island fits within +-1.4 km; the warm-up island sits further out
STILL_VZ_CM_S = 300               # "on the ground": vertical speed under 3 m/s ...
STILL_SAMPLES = 3                 # ... for 3 samples in a row
MIN_DESCENT_CM = 5_000            # ... at least 50 m below the first post-bus sample
CONTEST_NEAR_M, CONTEST_FAR_M = 150, 300
OFF_SPAWN_S = 120                 # eliminated within 2 minutes of landing
EARLY_KILL_S = 180
POI_RADIUS_M = 400


def _load_pois(data: Path) -> pd.DataFrame | None:
    path = data / "pois.csv"
    if not path.exists():
        return None
    p = pd.read_csv(path)
    p = p[~p["name"].str.contains("override console", case=False, na=False)]
    return p if len(p) else None


def build_landings(out: dict, data: Path) -> pd.DataFrame:
    pos, pl, m = out["positions"], out["players"], out["matches"]
    if pos.empty or pl.empty:
        return pd.DataFrame()
    bus = out.get("bus", pd.DataFrame())
    t0 = bus.set_index("match_id")["flight_start_t"] if "flight_start_t" in bus else pd.Series(dtype=float)

    p = pos[["match_id", "id", "t", "x", "y", "z", "vz"]].sort_values(["match_id", "id", "t"]).copy()
    off = (p["x"].abs() > ISLAND_HALF_CM) | (p["y"].abs() > ISLAND_HALF_CM)
    last_off = p[off].groupby(["match_id", "id"])["t"].max().rename("last_off")
    p = p.join(last_off, on=["match_id", "id"])
    p["start"] = p["match_id"].map(t0)
    p["start"] = p[["start", "last_off"]].max(axis=1)
    p = p[(~off) & (p["start"].isna() | (p["t"] > p["start"]))].copy()
    if p.empty:
        return pd.DataFrame()

    g = p.groupby(["match_id", "id"], sort=False)
    vz = pd.to_numeric(p["vz"], errors="coerce")
    derived = g["z"].diff() / g["t"].diff()
    p["vz_"] = vz.where(vz.notna(), derived)
    p["z0"] = g["z"].transform("first")
    still = (p["vz_"].abs() < STILL_VZ_CM_S).astype(int)
    run = still
    for k in range(1, STILL_SAMPLES):
        run = run + g["vz_"].shift(-k).abs().lt(STILL_VZ_CM_S).astype(int)
    p["landed"] = (run == STILL_SAMPLES) & (p["z"] < p["z0"] - MIN_DESCENT_CM)
    land = (p[p["landed"]].groupby(["match_id", "id"], sort=False).first()
            .reset_index()[["match_id", "id", "t", "x", "y", "z"]]
            .rename(columns={"t": "land_t", "x": "land_x", "y": "land_y", "z": "land_z"}))
    first = g[["t"]].first().rename(columns={"t": "jump_t"}).reset_index()
    land = land.merge(first, on=["match_id", "id"], how="left")
    land["glide_s"] = land["land_t"] - land["jump_t"]

    keep = ["match_id", "id", "team_index", "is_bot", "death_t", "placement", "kills"] + \
           [c for c in ("team_placement", "pr_rank") if c in pl]
    land = land.merge(pl[keep], on=["match_id", "id"], how="left")
    land = land[~land["is_bot"].fillna(False).astype(bool)].drop(columns="is_bot")
    if land.empty:
        return land

    # ---- contest: opponents (other teams) landing nearby
    for col in ("opp_150m", "opp_300m", "opp_top1000_300m", "nearest_opp_m"):
        land[col] = np.nan
    for _, gm in land.groupby("match_id"):
        xy = gm[["land_x", "land_y"]].to_numpy(float) / 100
        team = gm["team_index"].to_numpy()
        dist = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
        other = team[:, None] != team[None, :]
        land.loc[gm.index, "opp_150m"] = ((dist <= CONTEST_NEAR_M) & other).sum(1)
        land.loc[gm.index, "opp_300m"] = ((dist <= CONTEST_FAR_M) & other).sum(1)
        dist_other = np.where(other, dist, np.inf)
        land.loc[gm.index, "nearest_opp_m"] = np.where(np.isfinite(dist_other.min(1)), dist_other.min(1), np.nan)
        if "pr_rank" in gm:
            strong = gm["pr_rank"].fillna(1e9).to_numpy() <= 1000
            land.loc[gm.index, "opp_top1000_300m"] = ((dist <= CONTEST_FAR_M) & other & strong[None, :]).sum(1)

    # ---- bus route: perpendicular distance and position along the route
    land["bus_offset_m"] = np.nan
    if not bus.empty and {"start_x", "start_y", "bearing_deg"} <= set(bus.columns):
        b = land[["match_id"]].join(bus.set_index("match_id")[["start_x", "start_y", "bearing_deg", "bus_source"]], on="match_id")
        ux, uy = np.cos(np.radians(b["bearing_deg"])), np.sin(np.radians(b["bearing_deg"]))
        dx, dy = land["land_x"] - b["start_x"], land["land_y"] - b["start_y"]
        reliable = b["bus_source"].eq("aircraft")
        land["bus_offset_m"] = (np.abs(dx * uy - dy * ux) / 100).where(reliable)
        land["bus_along_m"] = ((dx * ux + dy * uy) / 100).where(reliable)

    # ---- storm: the first two safe circles
    z = out.get("zones", pd.DataFrame())
    for ph, tag in ((1, "zone1"), (2, "zone2")):
        land[f"outside_{tag}_m"] = np.nan
        if z.empty:
            continue
        c = z[z["phase"] == ph].drop_duplicates("match_id").set_index("match_id")[["next_x", "next_y", "next_r"]]
        cc = land[["match_id"]].join(c, on="match_id")
        d = np.hypot(land["land_x"] - cc["next_x"], land["land_y"] - cc["next_y"]) - cc["next_r"]
        land[f"outside_{tag}_m"] = (d / 100).clip(lower=0).where(cc["next_r"].notna())

    # ---- named place (current map only)
    land["poi"], land["poi_m"] = None, np.nan
    pois = _load_pois(data)
    if pois is not None and "season" in m:
        latest = sorted(m["season"].dropna().unique())[-1] if m["season"].notna().any() else None
        cur = land["match_id"].isin(m.loc[m["season"] == latest, "match_id"])
        P = pois[["x", "y"]].to_numpy(float)
        xy = land.loc[cur, ["land_x", "land_y"]].to_numpy(float)
        if len(xy):
            dist = np.hypot(xy[:, None, 0] - P[None, :, 0], xy[:, None, 1] - P[None, :, 1]) / 100
            nearest = dist.argmin(1)
            md = dist.min(1)
            land.loc[cur, "poi_m"] = md
            land.loc[cur, "poi"] = np.where(md <= POI_RADIUS_M, pois["name"].to_numpy()[nearest], "Unnamed area")

    # ---- what happened next
    land["off_spawn"] = (land["death_t"] - land["land_t"]).le(OFF_SPAWN_S)
    k = out.get("kills", pd.DataFrame())
    land["early_kills"] = 0
    if not k.empty:
        kk = k[(k["finisher_id"] != k["victim_id"])].merge(
            land[["match_id", "id", "land_t"]], left_on=["match_id", "finisher_id"], right_on=["match_id", "id"])
        kk = kk[kk["t"] <= kk["land_t"] + EARLY_KILL_S]
        land = land.merge(kk.groupby(["match_id", "id"]).size().rename("ek"), on=["match_id", "id"], how="left")
        land["early_kills"] = land["ek"].fillna(0).astype(int)
        land = land.drop(columns="ek")
    land["final"] = land["team_placement"] if "team_placement" in land else land["placement"]
    return land
