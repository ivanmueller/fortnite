"""
Shared helpers for the pages built on in-match events (Loot, Health and fights, Surge).
"""
from __future__ import annotations

import pandas as pd

from ..conclusion import conclude
from ..store import df

NEEDS_EVENTS = ("This page needs in-match data (health, damage, chests, items). Re-process your matches with data "
                "menu option 7 after updating, so they include it. Older matches and the demo data don't have it.")


def has_tables(con, *names: str) -> bool:
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if not set(names) <= have:
        return False
    return all(con.execute(f"SELECT count(*) FROM {n} JOIN sel USING (match_id)").fetchone()[0] > 0 for n in names)


def player_hits(con) -> pd.DataFrame:
    """Damage dealt by one player to a player on another team, with both teams attached."""
    return df(con, """
        SELECT d.match_id, d.t, d.attacker_id, d.target_id, d.amount, d.shield_hit, d.shield_destroyed, d.fatal,
               pa.team_index AS attacker_team, pt.team_index AS target_team
        FROM damage d JOIN sel USING (match_id)
        JOIN players pa ON pa.match_id = d.match_id AND pa.id = d.attacker_id
        JOIN players pt ON pt.match_id = d.match_id AND pt.id = d.target_id
        WHERE d.target_kind = 'player' AND d.attacker_id IS NOT NULL AND d.target_id IS NOT NULL
          AND pa.team_index IS DISTINCT FROM pt.team_index
          AND NOT coalesce(pa.is_bot, FALSE) AND NOT coalesce(pt.is_bot, FALSE)
    """)


def unexplained_drops(con) -> pd.DataFrame:
    """
    Health or shield lost with no player hit on that player in the surrounding seconds: storm, fall,
    or surge damage. Each drop is tagged with whether the player was outside the storm circle
    (reconstructed from storm timings) and the storm phase.
    """
    return df(con, """
        WITH h AS (
            SELECT h.match_id, h.id, h.t, coalesce(h.health, 0) + coalesce(h.shield, 0) AS hp,
                   lag(coalesce(h.health, 0) + coalesce(h.shield, 0)) OVER (PARTITION BY h.match_id, h.id ORDER BY h.t) AS hp_prev
            FROM health h JOIN sel USING (match_id)
            JOIN players p ON p.match_id = h.match_id AND p.id = h.id
            WHERE NOT coalesce(p.is_bot, FALSE)
        ),
        d AS (SELECT match_id, id, t, hp_prev - hp AS lost FROM h WHERE hp_prev - hp > 0.5),
        hit AS (SELECT DISTINCT d.match_id, d.id, d.t FROM d JOIN damage x
                ON x.match_id = d.match_id AND x.target_id = d.id AND x.target_kind = 'player'
                AND x.t BETWEEN d.t - 1.5 AND d.t + 0.5),
        free AS (SELECT d.* FROM d ANTI JOIN hit USING (match_id, id, t)),
        z AS (SELECT z.* FROM zones z JOIN sel USING (match_id) WHERE z.start_shrink_t IS NOT NULL AND z.cur_x IS NOT NULL),
        fz AS (SELECT f.*, z.phase, z.cur_x, z.cur_y, z.cur_r, z.next_x, z.next_y, z.next_r, z.start_shrink_t, z.finish_shrink_t
               FROM free f ASOF LEFT JOIN z ON f.match_id = z.match_id AND f.t >= z.start_shrink_t),
        fp AS (SELECT fz.*, p.x, p.y FROM fz ASOF LEFT JOIN positions p ON fz.match_id = p.match_id AND fz.id = p.id AND fz.t >= p.t),
        c AS (SELECT *, greatest(0, least(1, (t - start_shrink_t) / nullif(finish_shrink_t - start_shrink_t, 0))) AS f FROM fp)
        SELECT match_id, id, t, lost, phase,
               CASE WHEN x IS NULL OR cur_x IS NULL THEN NULL
                    ELSE sqrt(power(x - (cur_x + f * (next_x - cur_x)), 2) + power(y - (cur_y + f * (next_y - cur_y)), 2))
                         > (cur_r + f * (next_r - cur_r)) END AS in_storm
        FROM c
    """)


WEAPON_CLASSES = [("shotgun", "shotgun"), ("assault", "assault rifle"), ("_ar_", "assault rifle"), ("coreAR", "assault rifle"),
                  ("smg", "SMG"), ("drumgun", "SMG"), ("pdw", "SMG"), ("pistol", "pistol"), ("sniper", "sniper"),
                  ("dmr", "sniper"), ("launcher", "explosive"), ("rocket", "explosive"), ("grenade", "explosive")]
RARITY_RANK = {"common": 1, "uncommon": 2, "rare": 3, "epic": 4, "legendary": 5, "mythic": 6}


def weapon_class(item: str | None) -> str | None:
    if not isinstance(item, str):
        return None
    s = item.lower()
    return next((c for k, c in WEAPON_CLASSES if k.lower() in s), "other weapon")


def conclude_without_data(r, ctx, recommended: int = 100, note: str | None = None) -> None:
    """Conclusion for a page that has nothing to test: says why, and how to get the data."""
    if note:
        conclude(r, ctx, primary=["Per match"], alpha=0.005, recommended=recommended, descriptive=note,
                 next_none=["Collect later-round matches with data menu option T."])
    else:
        conclude(r, ctx, primary=["Per match"], alpha=0.005, recommended=recommended,
                 takeaway_none="No in-match data to test yet.",
                 next_none=["Re-process matches with data menu option 7 so they include in-match data."])
