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

TABLES = ["health", "damage", "chests", "pickups", "weapons_held", "builds", "attributes"]
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


def detect_slots(attr: pd.DataFrame, hits: pd.DataFrame) -> tuple[int | None, int | None, dict]:
    """
    Which slots of the raw health record are health and shield, for one match. A slot whose value
    drops right after hits flagged as shield hits is the shield; one that drops after hits that
    aren't shield hits is health. Returns (health_slot, shield_slot, scores).
    """
    hits = hits[hits["target_id"].notna()].copy()
    if attr.empty or hits.empty:
        return None, None, {}
    hits["target_id"] = hits["target_id"].astype("int64")
    hits["shield_hit"] = hits["shield_hit"].fillna(False).astype(bool)
    scores = {}
    for h, a in attr.groupby("handle"):
        # Health and shield live in 0-100-ish ranges; ignore rare extremes (e.g. a boss or bot with 2,000 health).
        if len(a) < 10 or a["value"].quantile(0.99) > 250 or a["value"].quantile(0.01) < -100:
            continue
        a = a.dropna(subset=["id"]).astype({"id": "int64"}).sort_values("t")
        e = hits.sort_values("t")
        before = pd.merge_asof(e.assign(tb=e["t"] - 0.05).sort_values("tb"), a.rename(columns={"t": "tb", "value": "v0", "id": "target_id"})[["tb", "target_id", "v0"]],
                               on="tb", by="target_id", direction="backward")
        after = pd.merge_asof(e.assign(ta=e["t"] + 1.0).sort_values("ta"), a.rename(columns={"t": "ta", "value": "v1", "id": "target_id"})[["ta", "target_id", "v1"]],
                              on="ta", by="target_id", direction="backward")
        m = before.merge(after[["t", "target_id", "v1"]], on=["t", "target_id"], how="inner").dropna(subset=["v0", "v1"])
        if len(m) < 10:
            continue
        drop = m["v1"] < m["v0"]
        scores[int(h)] = (drop[m["shield_hit"]].mean() if m["shield_hit"].any() else 0.0,
                          drop[~m["shield_hit"]].mean() if (~m["shield_hit"]).any() else 0.0)
    if not scores:
        return None, None, {}
    # Shield: drops on shield hits far more than on other hits. Health: of the slots that drop more on
    # non-shield hits, the one that drops most often. Ties (base/current copies of one attribute) go to
    # the lowest slot.
    shield = max(sorted(scores), key=lambda k: scores[k][0] - scores[k][1])
    cands = [k for k in sorted(scores) if scores[k][1] > scores[k][0]]
    health = max(cands, key=lambda k: scores[k][1]) if cands else None
    shield = shield if scores[shield][0] >= 0.5 and scores[shield][0] - scores[shield][1] >= 0.2 else None
    health = health if health is not None and scores[health][1] >= 0.5 else None
    return health, shield, scores


def health_from_attributes(attr: pd.DataFrame, dmg: pd.DataFrame, players: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Rebuild the health table (t, id, health, shield) from raw record slots, per match."""
    frames, notes = [], []
    bots = set(zip(players["match_id"], players["id"])) if players.empty else \
        set(zip(players.loc[players["is_bot"].astype(bool), "match_id"], players.loc[players["is_bot"].astype(bool), "id"]))
    for mid, a in attr.groupby("match_id"):
        a = a[[(mid, i) not in bots for i in a["id"]]]
        hits = dmg[(dmg["match_id"] == mid) & (dmg["target_kind"] == "player")] if not dmg.empty else dmg
        hs, ss, sc = detect_slots(a, hits)
        fallback = False
        if hs is None:
            # Health has sat in slot 0 (base) / 1 (current) in every season checked (2021-2026), and
            # slot 0 read realistic health in Season 42. Use it when its values look like health.
            s0 = a.loc[a["handle"] == 0, "value"]
            if len(s0) >= 10 and 1 <= s0.median() <= 100 and s0.quantile(0.99) <= 250 and ss != 0:
                hs, fallback = 0, True
        note = f"{mid}: health slot {hs}{' (fallback)' if fallback else ''}, shield slot {ss}"
        if hs is None or ss is None or fallback:
            top = sorted(sc.items(), key=lambda kv: -max(kv[1]))[:6]
            note += ("  [slot: drop rate on shield hits / on other hits: " +
                     ", ".join(f"{k}: {v[0]:.2f}/{v[1]:.2f}" for k, v in top) + "]") if top else \
                    f"  [no usable slots: {a['handle'].nunique()} slots, {len(hits)} player hits]"
        notes.append(note)
        if hs is None:
            continue
        keep = a[a["handle"].isin([hs, ss])].dropna(subset=["id"])
        wide = keep.pivot_table(index=["id", "t"], columns="handle", values="value", aggfunc="last").sort_index()
        wide = wide.groupby(level="id").ffill()
        f = pd.DataFrame({"health": wide[hs].clip(lower=0) if hs in wide else np.nan,
                          "shield": wide[ss].fillna(0).clip(lower=0) if ss is not None and ss in wide else np.nan}).reset_index()
        f.insert(0, "match_id", mid)
        frames.append(f)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), notes


def finish(out: dict, ev: dict[str, list[pd.DataFrame]]) -> None:
    """Concatenate per-match event frames into out[...], classify items, infer missing openers and takers."""
    for name in TABLES:
        frames = ev.get(name) or []
        out[name] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # Health and shield from the raw health record, where the extractor captured it.
    attr = out.pop("attributes")
    if not attr.empty:
        rebuilt, notes = health_from_attributes(attr, out["damage"], out["players"])
        for n in notes[:3]:
            print(f"  health record: {n}")
        if len(notes) > 3:
            print(f"  health record: ... {len(notes) - 3} more matches")
        if not rebuilt.empty:
            done = set(rebuilt["match_id"])
            old = out["health"]
            out["health"] = pd.concat([rebuilt, old[~old["match_id"].isin(done)]] if not old.empty else [rebuilt], ignore_index=True)

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
