"""
make_synthetic.py - generate fake matches in fn-extract's exact JSON schema.

Use it to test the pipeline end to end and to check that zone_analysis.py
finds a pattern when one is planted, and finds nothing when zones are random.

Usage:
    python pipeline/python/make_synthetic.py --pattern random  --matches 200
    python pipeline/python/make_synthetic.py --pattern bus     --matches 200 --strength 1.5
    python pipeline/python/make_synthetic.py --pattern edge    --matches 200
    python pipeline/python/make_synthetic.py --pattern persist --matches 200 --strength 2
then:
    python pipeline/python/flatten.py       --data-dir data_synthetic
    python pipeline/python/validate.py      --data-dir data_synthetic
    python pipeline/python/zone_analysis.py --data-dir data_synthetic

Dashboard demo (three seasons with different planted behaviour, four regions):
    python pipeline/python/make_synthetic.py --preset demo

Patterns:
    random   next center uniform inside the allowed area (the null hypothesis)
    bus      pull direction biased toward the bus heading (von Mises, kappa = strength)
    edge     every pull moves the maximum allowed distance (like the real sample replays)
    persist  each pull tends to go the same way as the previous one
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]  # repo root

RADII = [100000, 60000, 42500, 32000, 26500, 20500, 14000, 11500, 8500, 5000]
MAP_HALF = 130000
TEAM_SIZE, N_TEAMS = 3, 33
SAMPLE_DT = 2.0


def next_center(rng, pattern, cur, R, r, bus_bearing, prev_angle, strength):
    allowed = R - r
    if pattern == "edge":
        d = allowed
    else:
        d = allowed * math.sqrt(rng.random())  # uniform over the allowed disk
    if pattern == "bus":
        ang = rng.vonmises(math.radians(bus_bearing), strength)
    elif pattern == "persist" and prev_angle is not None:
        ang = rng.vonmises(prev_angle, strength)
    else:
        ang = rng.uniform(0, 2 * math.pi)
    return [cur[0] + d * math.cos(ang), cur[1] + d * math.sin(ang), 0.0], ang


def terrain(x: float, y: float) -> float:
    """Rolling hills, roughly -10 m to +40 m (Unreal units are centimetres)."""
    return 1500 + 2500 * math.sin(x / 40000) * math.cos(y / 33000)


def make_match(i: int, rng: np.random.Generator, pattern: str, strength: float,
               when: str = "2026-01-01T00:00:00", branch: str = "++Fortnite+Release-99.00",
               sample_dt: float = SAMPLE_DT, position_effect: float = 0.0) -> dict:
    mid = f"synth_{pattern}_{i:04d}"

    # Bus: a chord across the island.
    bearing = rng.uniform(0, 360)
    b = math.radians(bearing)
    offset = rng.uniform(-50000, 50000)
    perp = np.array([-math.sin(b), math.cos(b)])
    mid_pt = perp * offset
    bus_start = mid_pt - np.array([math.cos(b), math.sin(b)]) * MAP_HALF
    aircraft_t = 60.0
    bus_speed = 2200.0  # units per second

    # Zones
    zones, t = [], 180.0
    cur = [rng.uniform(-30000, 30000), rng.uniform(-30000, 30000), 0.0]
    zones.append(dict(seq=0, radius=150000.0, start_shrink_t=t, finish_shrink_t=t,
                      last_radius=0.0, next_center=cur, next_radius=float(RADII[0]), next_next_radius=0.0))
    prev_ang = None
    centers = [cur]
    for k in range(1, len(RADII)):
        t += 90
        R, r = RADII[k - 1], RADII[k]
        nxt, prev_ang = next_center(rng, pattern, cur, R, r, bearing, prev_ang, strength)
        zones.append(dict(seq=k, radius=float(R), start_shrink_t=t, finish_shrink_t=t + 45,
                          last_center=cur if rng.random() < 0.5 else None, last_radius=float(R) if rng.random() < 0.5 else 0.0,
                          next_center=nxt, next_radius=float(r), next_next_radius=0.0))
        cur = nxt
        centers.append(cur)
    end_t = t + 60

    # Teams and players
    order = rng.permutation(N_TEAMS)  # order[k] = team that placed k+1
    placement_of = {int(team): k + 1 for k, team in enumerate(order)}
    players, rows, kill_feed, elims, teams = [], [], [], [], []
    pid = 256
    for team in range(N_TEAMS):
        tidx = team + 3
        place = placement_of[team]
        # Higher placement survives longer.
        team_death = end_t if place == 1 else 200 + (end_t - 200) * (1 - (place - 1) / N_TEAMS) * rng.uniform(0.8, 1.0)
        jump_s = rng.uniform(0.15, 0.85)  # where along the bus the team jumps
        member_ids = []
        for m in range(TEAM_SIZE):
            member_ids.append(pid)
            jump_t = aircraft_t + 10 + jump_s * 80 + rng.uniform(0, 2)
            pos = bus_start + np.array([math.cos(b), math.sin(b)]) * bus_speed * (jump_t - aircraft_t)
            death_t = min(end_t, team_death + rng.uniform(-20, 0))
            ts = np.arange(jump_t, death_t, sample_dt)
            # position_effect > 0: better-placed teams hold tighter to the zone center.
            noise = 300 * (1 + position_effect * (place - 1) / N_TEAMS * 4)
            pull = 700 * (1 + position_effect * (1 - (place - 1) / N_TEAMS))
            # Lower-placing teams sometimes rotate a phase late (they chase the previous circle).
            late = position_effect > 0 and place > 5 and rng.random() < position_effect * (place / N_TEAMS)
            # Each team holds a spot inside the circle it follows (fixed per phase). Better teams sit deeper.
            spot_ang = rng.uniform(0, 2 * math.pi, len(RADII))
            spot_frac = np.sqrt(rng.random(len(RADII))) * min(0.95, 0.55 + position_effect * place / N_TEAMS)
            x, y = float(pos[0]), float(pos[1])
            for j, tt in enumerate(ts):
                # Drift toward the current zone center plus noise.
                zi = max(0, min(len(centers) - 1, int((tt - 180) // 90) + 1))
                if late:
                    zi = max(0, zi - 1)
                cx = centers[zi][0] + math.cos(spot_ang[zi]) * spot_frac[zi] * RADII[zi]
                cy = centers[zi][1] + math.sin(spot_ang[zi]) * spot_frac[zi] * RADII[zi]
                dx, dy = cx - x, cy - y
                dist = math.hypot(dx, dy) or 1.0
                step = min(dist, pull * sample_dt / 2)
                x += dx / dist * step + rng.normal(0, noise)
                y += dy / dist * step + rng.normal(0, noise)
                sky = j < 10
                # Height: terrain plus built height. With position_effect > 0, better teams build
                # higher as the game goes on (no advantage before the first storm, full from phase 5).
                build = position_effect * max(0.0, 1 - 2 * (place - 1) / N_TEAMS) * min(1.0, max(0, zi - 1) / 4) * 1500
                z = 15000 - j * 1400 if sky else terrain(x, y) + build + abs(rng.normal(0, 150))
                rows.append([pid, round(float(tt), 2), round(x, 1), round(y, 1), round(z, 1),
                             None, None, None, False, False, sky])
            alive_winner = place == 1
            players.append(dict(id=pid, player_id=f"{rng.integers(0, 2**63):016X}{rng.integers(0, 2**63):016X}"[:32],
                                name=f"player{pid}", is_bot=False, team_index=tidx, placement=place,
                                kills=int(rng.poisson(1.5)), team_kills=None,
                                death_t=None if alive_winner else float(death_t),
                                death_cause=None if alive_winner else 4,
                                death_location=None if alive_winner else [x, y, z],
                                platform="WIN", location_samples_raw=len(ts), location_samples_untimed=0))
            if not alive_winner:
                kill_feed.append(dict(t=float(death_t), victim_id=pid, finisher_id=None, downed=False,
                                      revived=False, location=[x, y, z], _place=place))
            pid += 1
        teams.append(dict(team_index=tidx, placement=place, player_ids=member_ids))

    # Credit each death to the nearest player alive at that moment on a better-placed team.
    arr = np.array([[r[0], r[1], r[2], r[3], r[4]] for r in rows], dtype=float)
    place_of_pid = {p["id"]: p["placement"] for p in players}
    pid_place = np.array([place_of_pid[int(i)] for i in arr[:, 0]])
    for k in kill_feed:
        near_t = np.abs(arr[:, 1] - k["t"]) <= sample_dt
        better = pid_place < k.pop("_place")
        cand = arr[near_t & better]
        if len(cand):
            d = np.hypot(cand[:, 2] - k["location"][0], cand[:, 3] - k["location"][1])
            k["finisher_id"] = int(cand[np.argmin(d), 0])

    return dict(
        schema="fn-research/1", match_id=mid, source_file=f"{mid}.replay",
        replay=dict(length_ms=int(end_t * 1000), network_version=0, changelist=0,
                    timestamp=when, is_encrypted=False,
                    branch=branch, engine_network_version=0, platform="Synthetic"),
        game=dict(playlist=f"Synthetic_{pattern}_Trios", team_size=TEAM_SIZE, max_players=N_TEAMS * TEAM_SIZE,
                  aircraft_start_t=aircraft_t, safe_zones_start_t=180.0, match_end_t=end_t,
                  winning_team=int(order[0]) + 3),
        bus=[],  # left empty on purpose: exercises the derived-from-skydive bus path
        zones=zones, players=players, teams=teams,
        positions=dict(columns=["id", "t", "x", "y", "z", "vx", "vy", "vz", "in_storm", "dbno", "skydiving"], rows=rows),
        kill_feed=kill_feed, eliminations=elims,
    )


# Demo preset for the dashboard: three synthetic "seasons" across 2026, each with
# different planted zone behaviour, four regions and weekly event windows.
# Season labels v96-v98 are made up so they can't be confused with real data.
DEMO_SEASONS = [
    # (branch, first day, last day, pattern, strength)
    ("++Fortnite+Release-96.10", "2026-01-06", "2026-03-28", "random", 0.0),
    ("++Fortnite+Release-97.10", "2026-04-07", "2026-06-27", "bus", 1.2),
    ("++Fortnite+Release-98.10", "2026-07-07", "2026-09-26", "edge", 0.0),
]
DEMO_REGIONS = ["NAC", "EU", "BR", "OCE"]


def write_demo(out: Path, data_dir: Path, per_season: int, seed: int, sample_dt: float) -> None:
    import datetime as dt
    rng = np.random.default_rng(seed)
    ids = ["match_id,event_window_id,region,session_date,is_server_replay,source"]
    n = 0
    for branch, d0, d1, pattern, strength in DEMO_SEASONS:
        start, end = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
        span = (end - start).days
        version = branch.split("-")[-1].replace(".", "")
        for _ in range(per_season):
            day = start + dt.timedelta(days=int(rng.integers(0, span + 1)))
            region = DEMO_REGIONS[int(rng.integers(0, len(DEMO_REGIONS)))]
            window = f"S{version}_DemoCup_W{day.isocalendar()[1]:02d}_{region}"
            when = f"{day.isoformat()}T{int(rng.integers(14, 23)):02d}:00:00"
            doc = make_match(n, rng, pattern, strength, when=when, branch=branch,
                             sample_dt=sample_dt, position_effect=0.6)
            doc["match_id"] = f"demo_{n:05d}"
            doc["game"]["playlist"] = "Demo_Trios_Cup"
            (out / f"{doc['match_id']}.json").write_text(json.dumps(doc))
            ids.append(f"{doc['match_id']},{window},{region},{day.isoformat()},1,synthetic")
            n += 1
    (data_dir / "match_ids.csv").write_text("\n".join(ids) + "\n")
    print(f"wrote {n} demo matches across {len(DEMO_SEASONS)} seasons to {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=["demo"], help="multi-season demo dataset for the dashboard")
    ap.add_argument("--per-season", type=int, default=80)
    ap.add_argument("--sample-dt", type=float, default=SAMPLE_DT, help="seconds between position samples")
    ap.add_argument("--pattern", choices=["random", "bus", "edge", "persist"], default="random")
    ap.add_argument("--matches", type=int, default=200)
    ap.add_argument("--strength", type=float, default=1.5, help="von Mises kappa for bus/persist (0 = no effect)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--data-dir", default=str(ROOT / "data_synthetic"))
    ap.add_argument("--keep", action="store_true", help="don't clear existing synthetic matches first")
    args = ap.parse_args()

    out = Path(args.data_dir) / "parsed"
    if out.exists() and not args.keep:
        shutil.rmtree(Path(args.data_dir))
    out.mkdir(parents=True, exist_ok=True)
    if args.preset == "demo":
        write_demo(out, Path(args.data_dir), args.per_season, args.seed, args.sample_dt)
        return
    rng = np.random.default_rng(args.seed)
    for i in range(args.matches):
        doc = make_match(i, rng, args.pattern, args.strength, sample_dt=args.sample_dt)
        (out / f"{doc['match_id']}.json").write_text(json.dumps(doc))
    print(f"wrote {args.matches} '{args.pattern}' matches to {out}")


if __name__ == "__main__":
    main()
