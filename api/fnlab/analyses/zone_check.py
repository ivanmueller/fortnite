"""
Zone accuracy: independent checks that the zones in the data are right.

1. Continuity   each zone's "next circle" must be exactly the following zone's circle.
2. Storm damage the storm is reconstructed second by second (current circle while waiting, moving and
                shrinking linearly to the next one during the shrink). Health lost with no player hit
                should happen only outside it; players clearly outside it should take damage.
3. Endgame      when the final zone finishes closing, the winner must be at it (within ENDGAME_M of its edge).
                Matches often run on after that in the storm, so the winner's very last position isn't used.
Plus a zone replay map for one match: every zone to scale over the named places, with the spots where
players took storm damage, to compare against Fortnite's own replay viewer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register

MARGIN_M = 5          # "clearly" inside or outside: more than this from the storm edge
AGREE_OK = 0.95       # a match is flagged below this storm-damage agreement
ENDGAME_M = 25        # the winner must be within this distance of the final zone's edge when it closes


def _tables(con) -> set[str]:
    return {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _storm_samples(con, match: str | None = None) -> pd.DataFrame:
    """Every living human position sample from zone 2 on, with its distance outside the storm edge (m)."""
    only = f"AND p.match_id = '{match}'" if match else ""
    return df(con, f"""
        WITH z AS (
            SELECT z.*, lag(z.finish_shrink_t) OVER (PARTITION BY z.match_id ORDER BY z.phase) AS prev_finish
            FROM zones z JOIN sel USING (match_id)
        ),
        zz AS (SELECT * FROM z WHERE cur_x IS NOT NULL AND prev_finish IS NOT NULL AND finish_shrink_t > start_shrink_t),
        p AS (SELECT p.match_id, p.id, p.t, p.x, p.y FROM positions p JOIN sel USING (match_id)
              JOIN players pl ON pl.match_id = p.match_id AND pl.id = p.id
              WHERE NOT coalesce(pl.is_bot, FALSE) AND (pl.death_t IS NULL OR p.t < pl.death_t - 1) {only}),
        s AS (SELECT p.*, zz.phase AS zone,
                     greatest(0, least(1, (p.t - zz.start_shrink_t) / (zz.finish_shrink_t - zz.start_shrink_t))) AS f,
                     zz.cur_x, zz.cur_y, zz.cur_r, zz.next_x, zz.next_y, zz.next_r
              FROM p JOIN zz ON p.match_id = zz.match_id AND p.t > zz.prev_finish AND p.t <= zz.finish_shrink_t)
        SELECT match_id, id, t, x, y, zone,
               (sqrt(power(x - (cur_x + f * (next_x - cur_x)), 2) + power(y - (cur_y + f * (next_y - cur_y)), 2))
                - (cur_r + f * (next_r - cur_r))) / 100 AS margin_m
        FROM s
    """)


def _free_drops(con, match: str | None = None) -> pd.DataFrame:
    """Health or shield lost with no player hit on that player in the surrounding seconds."""
    only = f"AND h.match_id = '{match}'" if match else ""
    return df(con, f"""
        WITH h AS (SELECT h.match_id, h.id, h.t, coalesce(h.health, 0) + coalesce(h.shield, 0) AS hp,
                          lag(coalesce(h.health, 0) + coalesce(h.shield, 0)) OVER (PARTITION BY h.match_id, h.id ORDER BY h.t) AS prev
                   FROM health h JOIN sel USING (match_id) WHERE TRUE {only}),
             d AS (SELECT match_id, id, t, prev - hp AS lost FROM h WHERE prev - hp > 0.5),
             hit AS (SELECT DISTINCT d.match_id, d.id, d.t FROM d JOIN damage x
                     ON x.match_id = d.match_id AND x.target_id = d.id AND x.target_kind = 'player'
                     AND x.t BETWEEN d.t - 1.5 AND d.t + 0.5)
        SELECT d.* FROM d ANTI JOIN hit USING (match_id, id, t)
    """)


@register("zone_check", "Zone accuracy",
          "Independent checks that the zones are right: zone-to-zone continuity, agreement with the storm damage players "
          "actually took, and the winner inside the final zone. Includes a zone replay map for any match.",
          params=[Param("match", "Match to draw", "match", "")])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    z = df(con, "SELECT z.* FROM zones z JOIN sel USING (match_id) ORDER BY match_id, phase")
    if z.empty:
        r.headline = "No zones in this selection."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
        return r
    n_matches = z["match_id"].nunique()

    # ---- 1. continuity
    z["nx"] = z.groupby("match_id")["cur_x"].shift(-1)
    z["ny"] = z.groupby("match_id")["cur_y"].shift(-1)
    z["nr"] = z.groupby("match_id")["cur_r"].shift(-1)
    t = z.dropna(subset=["nx", "nr"])
    t_err = np.maximum(np.hypot(t["next_x"] - t["nx"], t["next_y"] - t["ny"]), (t["next_r"] - t["nr"]).abs()) / 100
    cont_bad = t.loc[t_err > 1, "match_id"].unique()
    order_bad = z.groupby("match_id").apply(
        lambda g: bool((g["start_shrink_t"].diff().dropna() <= 0).any() or (g["finish_shrink_t"] < g["start_shrink_t"]).any()),
        include_groups=False)
    r.metric("Matches checked", f"{n_matches:,}")
    r.metric("Zone-to-zone continuity", f"{(len(t) - (t_err > 1).sum()):,} of {len(t):,} exact",
             "Each zone's next circle matches the following zone's circle within 1 m")

    # ---- 2. storm damage agreement
    per_match = pd.DataFrame(index=sorted(z["match_id"].unique()))
    per_match["Zones"] = z.groupby("match_id").size()
    per_match["Continuity"] = ["exact" if m not in cont_bad else "mismatch" for m in per_match.index]
    per_match["Timing order"] = ["ok" if not order_bad.get(m, False) else "out of order" for m in per_match.index]
    have = _tables(con)
    if {"health", "damage"} <= have and con.execute("SELECT count(*) FROM health JOIN sel USING (match_id)").fetchone()[0]:
        S = _storm_samples(con)
        H = _free_drops(con)
        if len(S) and len(H):
            S = S.sort_values("t")
            m = pd.merge_asof(H.sort_values("t"), S[["t", "match_id", "id", "margin_m", "zone"]], on="t", by=["match_id", "id"],
                              direction="backward", tolerance=1.5).dropna(subset=["margin_m"])
            nxt = pd.merge_asof(S, H.sort_values("t").rename(columns={"t": "ht"})[["ht", "match_id", "id"]], left_on="t",
                                right_on="ht", by=["match_id", "id"], direction="forward", tolerance=2.5)
            out, ins = nxt[nxt["margin_m"] > MARGIN_M], nxt[nxt["margin_m"] < -MARGIN_M]
            r.metric("Storm damage where our storm says outside", f"{(m['margin_m'] > 0).mean():.1%}",
                     "Of all health lost with no player hit (zone 2 on), the share taken outside the reconstructed storm")
            r.metric("Clearly outside and damaged", f"{out['ht'].notna().mean():.1%}",
                     f"Player-seconds more than {MARGIN_M} m outside the storm that were followed by storm damage")
            r.metric("Clearly inside but damaged", f"{ins['ht'].notna().mean():.2%}",
                     f"Player-seconds more than {MARGIN_M} m inside: should be near zero (fall damage, surge)")
            agree = m.groupby("match_id")["margin_m"].apply(lambda s: (s > 0).mean())
            per_match["Storm damage outside"] = agree.reindex(per_match.index).map(lambda v: f"{v:.1%}" if v == v else "–")
            per_match["Outside and damaged"] = out.groupby("match_id")["ht"].apply(lambda s: s.notna().mean()) \
                .reindex(per_match.index).map(lambda v: f"{v:.1%}" if v == v else "–")
            per_match["_agree"] = agree.reindex(per_match.index)
            by_zone = m.groupby("zone")["margin_m"].agg(lambda s: (s > 0).mean())
            r.chart("bar", "Storm damage taken outside our storm, by zone",
                    [dict(name="Outside the reconstructed storm", x=[f"Zone {int(k)}" for k in by_zone.index],
                          y=(by_zone * 100).round(1).tolist())],
                    y_label="% of storm damage", reference_lines=[dict(axis="y", value=95, label="95%")])
            r.chart("histogram", "Where storm damage happened, relative to our storm edge",
                    [dict(name="Health drops", values=m["margin_m"].clip(-50, 300).tolist())], bins=70, range=[-50, 300],
                    x_label="Metres outside the reconstructed storm edge (negative = inside)", y_label="Health drops",
                    reference_lines=[dict(axis="x", value=0, label="Storm edge")])

    # ---- 3. endgame: the winner inside the final zone
    win = df(con, """SELECT p.match_id, p.id FROM players p JOIN sel USING (match_id) WHERE p.placement = 1 AND NOT coalesce(p.is_bot, FALSE)""")
    if len(win):
        last = z.sort_values("phase").groupby("match_id").tail(1).set_index("match_id")
        lp = df(con, """WITH f AS (SELECT z.match_id, max(z.finish_shrink_t) AS t_end FROM zones z JOIN sel USING (match_id) GROUP BY 1)
                        SELECT p.match_id, p.id, arg_max(p.x, p.t) AS x, arg_max(p.y, p.t) AS y FROM positions p
                        JOIN f ON f.match_id = p.match_id WHERE p.t <= f.t_end GROUP BY 1, 2""")
        w = win.merge(lp, on=["match_id", "id"]).join(last[["next_x", "next_y", "next_r"]], on="match_id")
        w["edge_m"] = (np.hypot(w["x"] - w["next_x"], w["y"] - w["next_y"]) - w["next_r"]) / 100
        # Team modes: every member of the winning team has placement 1; use the one closest to the final zone.
        w = w.groupby("match_id", as_index=False)["edge_m"].min()
        w["inside"] = w["edge_m"] <= ENDGAME_M
        per_match["Winner at final zone"] = w.set_index("match_id")["edge_m"].reindex(per_match.index).map(
            lambda v: "–" if v != v else ("yes" if v <= 0 else f"yes ({v:.0f} m out)" if v <= ENDGAME_M else f"no ({v:.0f} m out)"))
        r.metric("Winner at the final zone", f"{w['inside'].mean():.0%} of {len(w)} matches",
                 f"When the final zone finished closing, the winner was within {ENDGAME_M} m of its edge")

    flagged = per_match[(per_match["Continuity"] != "exact") | (per_match["Timing order"] != "ok")
                        | (per_match.get("_agree", pd.Series(1.0, index=per_match.index)).fillna(1) < AGREE_OK)
                        | (per_match.get("Winner at final zone", pd.Series("yes", index=per_match.index)).astype(str).str.startswith("no"))]
    show = per_match.drop(columns=[c for c in per_match if c.startswith("_")])
    r.table("Matches that need a look" if len(flagged) else "Every match checked",
            (show.loc[flagged.index] if len(flagged) else show.head(50)).reset_index().rename(columns={"index": "Match"}))

    # ---- zone replay map for one match
    mid = ctx.params.get("match") or z.sort_values("start_shrink_t").iloc[-1]["match_id"]
    if mid not in set(z["match_id"]):
        mid = z["match_id"].iloc[-1]
    zm = z[z["match_id"] == mid].sort_values("phase")
    extent = float(zm["next_r"].max()) * 2 or 1.0

    def zone_circles(rows: pd.DataFrame, min_label: float) -> list[dict]:
        # Label only circles big enough to read at this zoom; the close-up labels the small ones.
        return [dict(x=float(q["next_x"]), y=float(q["next_y"]), r=float(q["next_r"]),
                     label=f"Zone {int(q['phase'])}" if q["next_r"] * 2 >= min_label else "") for _, q in rows.iterrows()]
    damage = pd.DataFrame()
    if {"health", "damage"} <= have:
        Sm = _storm_samples(con, mid)
        Hm = _free_drops(con, mid)
        if len(Sm) and len(Hm):
            damage = pd.merge_asof(Hm.sort_values("t"), Sm.sort_values("t")[["t", "id", "x", "y"]], on="t", by="id",
                                   direction="backward", tolerance=1.5).dropna(subset=["x"])
    pois = pd.DataFrame()
    if "pois" in have:
        pois = df(con, "SELECT * FROM pois WHERE kind = 'poi'")

    def layers(dmg: pd.DataFrame, places: pd.DataFrame) -> list[dict]:
        out = []
        if len(dmg):
            out.append(dict(name="Where players took storm damage", x=dmg["x"].round(0).tolist(), y=dmg["y"].round(0).tolist(),
                            color="#D08A12", size=3, opacity=0.35))
        if len(places):
            out.append(dict(name="Named places", x=places["x"].tolist(), y=places["y"].tolist(), text=places["name"].tolist()))
        return out or [dict(name="Zone centres", x=zm["next_x"].tolist(), y=zm["next_y"].tolist())]
    r.chart("map_points", "Zone replay map", layers(damage, pois), x_label="Map X", y_label="Map Y",
            circles=zone_circles(zm, extent * 0.12), zone_circles=True, match=mid)
    # Endgame close-up: zone 5 on, zoomed to zone 5's circle.
    late = zm[zm["phase"] >= 5]
    if len(late):
        c5 = late.iloc[0]
        pad = float(c5["next_r"]) * 1.35
        box = lambda d: d[(d["x"].between(c5["next_x"] - pad, c5["next_x"] + pad)) & (d["y"].between(c5["next_y"] - pad, c5["next_y"] + pad))]  # noqa: E731
        r.chart("map_points", "Endgame zones close-up", layers(box(damage) if len(damage) else damage, box(pois) if len(pois) else pois),
                x_label="Map X", y_label="Map Y", circles=zone_circles(late, 0), zone_circles=True, match=mid,
                range_x=[float(c5["next_x"] - pad), float(c5["next_x"] + pad)], range_y=[float(c5["next_y"] - pad), float(c5["next_y"] + pad)])
    tl = pd.DataFrame({
        "Zone": zm["phase"].astype(int),
        "Centre X (m)": (zm["next_x"] / 100).round(0), "Centre Y (m)": (zm["next_y"] / 100).round(0),
        "Radius (m)": (zm["next_r"] / 100).round(1),
        "Wait (s)": (zm["start_shrink_t"] - zm["finish_shrink_t"].shift(1)).round(0),
        "Shrink starts": zm["start_shrink_t"].map(lambda s: f"{int(s // 60)}:{int(s % 60):02d}"),
        "Shrink length (s)": (zm["finish_shrink_t"] - zm["start_shrink_t"]).round(0),
    })
    r.table("Zone timeline", tl)
    r.notes.insert(0, f"Map and timeline: match {mid}.")

    ok = len(flagged) == 0
    agree_txt = next((m_["value"] for m_ in r.metrics if m_["label"] == "Storm damage where our storm says outside"), None)
    r.headline = (f"Zones checked in {n_matches:,} matches: continuity exact" + (f", {agree_txt} of storm damage taken outside the "
                  "reconstructed storm" if agree_txt else "") + (". No match needs a look." if ok else
                  f". {len(flagged)} match{'es' if len(flagged) != 1 else ''} need{'s' if len(flagged) == 1 else ''} a look (table below)."))
    conclude(r, ctx, primary=[], alpha=0.005, recommended=1, descriptive=r.headline)
    r.notes += [
        "Storm damage is health or shield lost with no player hitting that player in the surrounding seconds. A small share "
        "inside the storm is expected: fall damage, and surge when it triggers.",
        f"Matches are flagged when continuity or timing fails, storm-damage agreement is below {AGREE_OK:.0%}, or the winner "
        f"was more than {ENDGAME_M} m outside the final zone when it closed. Endgames often finish at the edge or in the storm "
        "after the last zone closes, so only gross mismatches are flagged.",
        "Zone 1's starting circle isn't recorded, so storm checks start at zone 2.",
        "On the map, a correct zone set shows storm-damage dots just outside the circles they were caught by.",
    ]
    return r
