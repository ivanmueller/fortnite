"""
Adds in-match health and damage events, with planted surge, to the synthetic demo tables.

The demo data has no events, so the surge pages can't be checked on it. This writes health.parquet and
damage.parquet next to the demo tables: random hits between players on different teams in zones 3-6, and
surge ticks (25 health every 5 s, no player hitting) on the teams that score lowest on a chosen rule:

  rule="team_net"       team net damage (dealt minus taken) since the zone appeared  (tournaments since Oct 2025)
  rule="player_dealt"   each player's own damage dealt since the zone appeared        (the old rule)

so tests can check the Surge study recovers whichever rule was planted.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SURGE_ZONES = (3, 4, 5, 6)
CHECK_AFTER_S = 40          # first surge check, seconds after the zone appears
SURGED_SHARE = 0.3          # the lowest 30% are surged
TICK = 25.0


def _alive(players: pd.DataFrame, t: float) -> pd.DataFrame:
    return players[players["death_t"].isna() | (players["death_t"] > t + 20)]


def add_events(tables: Path, rule: str = "team_net", seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    players = pd.read_parquet(tables / "players.parquet")
    players = players[~players["is_bot"].fillna(False).astype(bool)] if "is_bot" in players else players
    zones = pd.read_parquet(tables / "zones.parquet")
    dmg_rows, drops = [], []
    for mid, pl in players.groupby("match_id"):
        z = zones[zones["match_id"] == mid].set_index("phase")
        for k in SURGE_ZONES:
            if k - 1 not in z.index:
                continue
            appear = float(z.loc[k - 1, "finish_shrink_t"])
            check = appear + CHECK_AFTER_S
            alive = _alive(pl, check + 15)
            if alive["team_index"].nunique() < 6:
                continue
            ids = alive["id"].to_numpy()
            team = dict(zip(alive["id"], alive["team_index"]))
            # random exchanges between players on different teams, between the zone appearing and the check
            for _ in range(len(ids) * 2):
                a, b = rng.choice(ids, 2, replace=False)
                if team[a] == team[b]:
                    continue
                t = float(rng.uniform(appear + 1, check - 2))
                dmg_rows.append(dict(match_id=mid, t=round(t, 2), attacker_id=a, target_id=b, amount=float(rng.integers(4, 20))))
            # who the planted rule surges
            h = pd.DataFrame([r for r in dmg_rows if r["match_id"] == mid and appear <= r["t"] < check])
            dealt_p = h.groupby("attacker_id")["amount"].sum() if len(h) else pd.Series(dtype=float)
            taken_p = h.groupby("target_id")["amount"].sum() if len(h) else pd.Series(dtype=float)
            sc = alive[["id", "team_index"]].copy()
            sc["dealt"] = sc["id"].map(dealt_p).fillna(0)
            sc["taken"] = sc["id"].map(taken_p).fillna(0)
            if rule == "team_net":
                tn = sc.groupby("team_index").apply(lambda g: g["dealt"].sum() - g["taken"].sum(), include_groups=False)
                low = set(tn.sort_values().index[: max(2, int(len(tn) * SURGED_SHARE))])
                victims = sc[sc["team_index"].isin(low)]["id"]
            elif rule == "player_dealt":
                victims = sc.sort_values("dealt")["id"].head(max(3, int(len(sc) * SURGED_SHARE)))
            else:
                raise ValueError(rule)
            for v in victims:
                for i in range(3):
                    drops.append(dict(match_id=mid, id=v, t=round(check + 5 * i, 2)))

    damage = pd.DataFrame(dmg_rows)
    damage = damage.assign(target_kind="player", shield_hit=False, shield_destroyed=False, fatal=False, critical=False,
                           x=np.nan, y=np.nan, z=np.nan)
    # health: everyone starts at 200 (100 + 100 shield); hits and surge ticks lower it, never below 1
    ev = pd.concat([damage[["match_id", "t", "target_id", "amount"]].rename(columns={"target_id": "id"}),
                    pd.DataFrame(drops).assign(amount=TICK)], ignore_index=True).sort_values(["match_id", "id", "t"])
    rows = [dict(match_id=m, id=i, t=0.0, health=100.0, shield=100.0) for m, i in zip(players["match_id"], players["id"])]
    for (m, i), g in ev.groupby(["match_id", "id"]):
        hp = 200.0
        for t, a in zip(g["t"], g["amount"]):
            hp = max(1.0, hp - a)
            rows.append(dict(match_id=m, id=i, t=float(t), health=min(hp, 100.0), shield=max(hp - 100.0, 0.0)))
    damage.to_parquet(tables / "damage.parquet", index=False)
    pd.DataFrame(rows).to_parquet(tables / "health.parquet", index=False)


def add_recurring_team(tables: Path, names=("clix", "rapid", "third"), spot=(20_000.0, 20_000.0), share=0.7, seed=0) -> None:
    """
    Make one team recur across every demo match (same names and accounts), landing near `spot` (cm) in `share` of games,
    and give every landing a named place (400 m grid cells), so the game plan has a team history and named drop spots.
    """
    rng = np.random.default_rng(seed)
    players = pd.read_parquet(tables / "players.parquet")
    landings = pd.read_parquet(tables / "landings.parquet")
    for mid, pl in players.groupby("match_id"):
        team = int(pl["team_index"].min())
        idx = pl.index[pl["team_index"] == team]
        for j, i in enumerate(idx[: len(names)]):
            players.loc[i, "name"] = names[j]
            players.loc[i, "player_id"] = f"ACCOUNT_{names[j].upper()}"
            players.loc[i, "pr_rank"] = 50.0 + j
        if rng.random() < share:
            li = landings.index[(landings["match_id"] == mid) & (landings["team_index"] == team)]
            landings.loc[li, "land_x"] = spot[0] + rng.normal(0, 3_000, len(li))
            landings.loc[li, "land_y"] = spot[1] + rng.normal(0, 3_000, len(li))
    cell = 40_000.0
    landings["poi"] = [f"Spot {int(np.floor(x / cell))},{int(np.floor(y / cell))}" for x, y in zip(landings["land_x"], landings["land_y"])]
    players.to_parquet(tables / "players.parquet", index=False)
    landings.to_parquet(tables / "landings.parquet", index=False)
