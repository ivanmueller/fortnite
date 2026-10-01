"""
events.py - turn the extractor's in-match events (schema fn-research/2) into tables:

  health        t, id, health, shield                     every change, per player
  damage        t, attacker_id, target_id, target_kind, amount, shield_hit, shield_destroyed, fatal, critical, x, y, z
  chests        t, container, kind, tier, searched_by, x, y, z, opener_inferred
  pickups       spawn_t, item, category, rarity, count, x, y, z, tossed, picked_t, picked_by, gone_t, taker_inferred
  weapons_held  t, id, weapon, category, rarity          every change of the weapon in hand
  builds        t, kind, material, team_index, player_placed, max_health, x, y, z, destroyed_t

The game only sends "who opened this chest" and "who took this item" briefly, so when
it's missing, the nearest living player within 3 m at that moment is used (flagged as
inferred). Older replays (schema fn-research/1) simply produce no event tables.
"""
from __future__ import annotations

import re

import duckdb
import numpy as np
import pandas as pd

TABLES = ["health", "damage", "chests", "pickups", "weapons_held", "builds"]
NEAR_CM = 300

RARITY = [("_Mythic", "mythic"), ("_SR_", "legendary"), ("_VR_", "epic"), ("_R_", "rare"), ("_UC_", "uncommon"), ("_C_", "common")]


def item_info(name: str | None) -> tuple[str | None, str | None, str | None]:
    """(short name, category, rarity) from an item's internal path, e.g. WID_Shotgun_Standard_Athena_UC_Ore_T03."""
    if not isinstance(name, str) or not name:
        return None, None, None
    short = name.split("/")[-1].split(".")[-1]
    s = short.lower()
    if "ammo" in s:
        cat = "ammo"
    elif any(k in s for k in ("wooditemdata", "stoneitemdata", "metalitemdata")):
        cat = "materials"
    elif any(k in s for k in ("shield", "bandage", "medkit", "medbox", "chug", "flopper", "slurp", "apple", "pepper", "mushroom", "consumable", "heal")):
        cat = "heal/shield"
    elif "pickaxe" in s or "harvest" in s:
        cat = "pickaxe"
    elif "buildingitemdata" in s or "edittool" in s or "buildingtool" in s:
        cat = "build tool"
    elif s.startswith("wid_") or any(k in s for k in ("shotgun", "assault", "smg", "pistol", "sniper", "rifle", "launcher")):
        cat = "weapon"
    else:
        cat = "other"
    rarity = next((r for tag, r in RARITY if tag.lower() in f"{s}_"), None)
    if rarity is None:
        m = re.search(r"_t0?(\d)\b", s)  # tier suffix, e.g. _T03
        rarity = {"1": "common", "2": "uncommon", "3": "rare", "4": "epic", "5": "legendary"}.get(m.group(1)) if m else None
    return short, cat, rarity


def collect(doc: dict, mid: str) -> dict[str, pd.DataFrame]:
    ev = doc.get("events") or {}
    out = {}
    for name in TABLES:
        t = ev.get(name) or {}
        df = pd.DataFrame(t.get("rows", []), columns=t.get("columns") or None)
        if df.empty:
            continue
        df.insert(0, "match_id", mid)
        out[name] = df
    return out


def finish(out: dict, ev: dict[str, list[pd.DataFrame]]) -> None:
    """Concatenate per-match event frames into out[...], classify items, infer missing openers and takers."""
    for name in TABLES:
        frames = ev.get(name) or []
        out[name] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    for name, col in (("pickups", "item"), ("weapons_held", "weapon")):
        df = out[name]
        if not df.empty:
            info = df[col].map(item_info)
            df[col] = info.map(lambda x: x[0])
            df["category"] = info.map(lambda x: x[1])
            df["rarity"] = info.map(lambda x: x[2])
    b = out["builds"]
    if not b.empty:
        b["material"] = b["kind"].str.extract(r"PBWA_([WSM])\d", expand=False).map({"W": "wood", "S": "stone", "M": "metal"})

    pos = out.get("positions", pd.DataFrame())
    if pos.empty:
        return
    con = duckdb.connect()
    con.register("pos", pos[["match_id", "id", "t", "x", "y", "z"]])
    alive = out["players"][["match_id", "id", "is_bot"]]
    con.register("people", alive[~alive["is_bot"].astype(bool)][["match_id", "id"]])

    def nearest(events: pd.DataFrame, t_col: str, need_xy: bool) -> pd.Series:
        """Nearest human within NEAR_CM of each event's location at time t (position sampled at most 3 s before)."""
        e = events.reset_index()[["index", "match_id", t_col, "x", "y"]].rename(columns={t_col: "et"})
        e = e.dropna(subset=["et", "x", "y"]) if need_xy else e.dropna(subset=["et"])
        if e.empty:
            return pd.Series(dtype="float64")
        con.register("ev", e)
        r = con.execute(f"""
            WITH c AS (SELECT ev."index", ev.match_id, ev.et, ev.x AS ex, ev.y AS ey, p.id
                       FROM ev JOIN people p USING (match_id)),
                 a AS (SELECT c.*, pos.x AS px, pos.y AS py, pos.t AS pt
                       FROM c ASOF JOIN pos ON c.match_id = pos.match_id AND c.id = pos.id AND c.et >= pos.t)
            SELECT "index", arg_min(id, (px - ex) * (px - ex) + (py - ey) * (py - ey)) AS id,
                   min(sqrt((px - ex) * (px - ex) + (py - ey) * (py - ey))) AS d
            FROM a WHERE et - pt <= 3 GROUP BY "index"
        """).df()
        r = r[r["d"] <= NEAR_CM]
        return r.set_index("index")["id"]

    p = out["pickups"]
    if not p.empty:
        p["taker_inferred"] = False
        ends = out["matches"].set_index("match_id")["match_end_t"]
        cand = p[p["picked_by"].isna() & p["gone_t"].notna() & (p["gone_t"] < p["match_id"].map(ends).fillna(np.inf) - 2)]
        if len(cand):
            who = nearest(cand, "gone_t", need_xy=True)
            p.loc[who.index, "picked_by"] = who.to_numpy()
            p.loc[who.index, "picked_t"] = p.loc[who.index, "gone_t"]
            p.loc[who.index, "taker_inferred"] = True
    c = out["chests"]
    if not c.empty:
        c["opener_inferred"] = False
        cand = c[c["searched_by"].isna() & c["x"].notna()]
        if len(cand):
            who = nearest(cand, "t", need_xy=True)
            c.loc[who.index, "searched_by"] = who.to_numpy()
            c.loc[who.index, "opener_inferred"] = True
        # Chests are part of the map, so their position may be missing: use the opener's position.
        miss = c[c["x"].isna() & c["searched_by"].notna()]
        if len(miss):
            m = miss.reset_index()[["index", "match_id", "t", "searched_by"]].rename(columns={"searched_by": "id"})
            m["id"] = m["id"].astype("int64")
            con.register("m", m)
            at = con.execute("""SELECT m."index", pos.x, pos.y, pos.z FROM m ASOF JOIN pos
                                ON m.match_id = pos.match_id AND m.id = pos.id AND m.t >= pos.t""").df().set_index("index")
            c.loc[at.index, ["x", "y", "z"]] = at[["x", "y", "z"]].to_numpy()
