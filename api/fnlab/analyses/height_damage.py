"""
Getting damage from height: when players need damage (surge), which height over their opponents
deals the most damage for the least taken back?

Every player-on-player hit is paired with both players' positions at that moment, giving the
attacker's height over the target and the range. Hits between two teams no more than GAP_S apart
form one exchange; for each exchange and side: damage dealt, damage taken back, the side's average
height over the other, and the typical range.

  trade ratio    damage dealt for every 1 point taken back, per height band (summed over exchanges)
  one-sided tag  an exchange where a side dealt damage and took none back
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, register
from ._events_common import NEEDS_EVENTS, conclude_without_data, has_tables

GAP_S = 15
FROM_ZONE = 4          # surge-relevant part of the game; early fights mostly reflect the landing
MIN_EXCHANGES = 15     # bands with fewer exchanges are left blank
HEIGHT_BANDS = [-1e9, -15, -5, 5, 15, 1e9]
HEIGHT_LABELS = ["15 m+ below", "5–15 m below", "Level (±5 m)", "5–15 m above", "15 m+ above"]
AXIS_LABELS = [l.replace(" (±5 m)", "").replace(" m ", " m<br>") for l in HEIGHT_LABELS]  # two-line chart labels
RANGE_BANDS = [0, 20, 60, 1e9]
RANGE_LABELS = ["Close (under 20 m)", "Mid (20–60 m)", "Long (60 m+)"]


def exchanges(ctx: Context) -> pd.DataFrame:
    """One row per exchange and side: dealt, taken back, height over the other side (m), range (m), zone."""
    h = df(ctx.con, """
        WITH h AS (
            SELECT d.match_id, d.t, d.attacker_id, d.target_id, d.amount,
                   pa.team_index AS a_team, pt.team_index AS t_team
            FROM damage d JOIN sel USING (match_id)
            JOIN players pa ON pa.match_id = d.match_id AND pa.id = d.attacker_id
            JOIN players pt ON pt.match_id = d.match_id AND pt.id = d.target_id
            WHERE d.target_kind = 'player' AND pa.team_index IS DISTINCT FROM pt.team_index
              AND NOT coalesce(pa.is_bot, FALSE) AND NOT coalesce(pt.is_bot, FALSE)
        ),
        a AS (SELECT h.*, p.x AS ax, p.y AS ay, p.z AS az, p.t AS apt
              FROM h ASOF JOIN positions p ON h.match_id = p.match_id AND h.attacker_id = p.id AND h.t >= p.t),
        b AS (SELECT a.*, p.x AS tx, p.y AS ty, p.z AS tz, p.t AS tpt
              FROM a ASOF JOIN positions p ON a.match_id = p.match_id AND a.target_id = p.id AND a.t >= p.t),
        z AS (SELECT z.match_id, z.phase, z.start_shrink_t FROM zones z JOIN sel USING (match_id) WHERE z.start_shrink_t IS NOT NULL)
        SELECT b.match_id, b.t, b.amount, b.a_team, b.t_team, (b.az - b.tz) / 100 AS rel_m,
               sqrt(power(b.ax - b.tx, 2) + power(b.ay - b.ty, 2)) / 100 AS dist_m, coalesce(z.phase, 0) AS zone
        FROM b ASOF LEFT JOIN z ON b.match_id = z.match_id AND b.t >= z.start_shrink_t
        WHERE b.t - b.apt <= 2 AND b.t - b.tpt <= 2
    """)
    if h.empty:
        return h
    h["s1"] = h[["a_team", "t_team"]].min(axis=1)
    h["s2"] = h[["a_team", "t_team"]].max(axis=1)
    h = h.sort_values(["match_id", "s1", "s2", "t"])
    h["ex"] = (h.groupby(["match_id", "s1", "s2"])["t"].diff().fillna(1e9) > GAP_S).cumsum()
    rows = []
    for ex, g in h.groupby("ex"):
        for side, other in ((g["s1"].iat[0], g["s2"].iat[0]), (g["s2"].iat[0], g["s1"].iat[0])):
            by_side = g["a_team"] == side
            # The side's height over the other: attacker-over-target on its own hits, the reverse on hits it took.
            rel = np.concatenate([g.loc[by_side, "rel_m"].to_numpy(), -g.loc[~by_side, "rel_m"].to_numpy()])
            rows.append(dict(match_id=g["match_id"].iat[0], ex=ex, side=side, first=side == g["s1"].iat[0],
                             zone=int(g["zone"].min()), dealt=g.loc[by_side, "amount"].sum(),
                             taken=g.loc[~by_side, "amount"].sum(), rel_m=float(np.mean(rel)),
                             dist_m=float(g["dist_m"].median()), hits=len(g)))
    out = pd.DataFrame(rows)
    out["height"] = pd.cut(out["rel_m"], HEIGHT_BANDS, labels=HEIGHT_LABELS)
    out["range"] = pd.cut(out["dist_m"], RANGE_BANDS, labels=RANGE_LABELS, right=False)
    out["one_sided"] = (out["dealt"] > 0) & (out["taken"] == 0)
    return out


def _ratio(g: pd.DataFrame) -> float:
    if len(g) < MIN_EXCHANGES or g["taken"].sum() <= 0:
        return np.nan
    return float(g["dealt"].sum() / g["taken"].sum())


@register("height_damage", "Getting damage from height",
          "Which height over opponents deals the most damage for the least taken back, by range: where to get damage "
          "when surge is coming.",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    if not has_tables(ctx.con, "damage"):
        r.headline = "No damage data in this selection."
        r.warnings.append(NEEDS_EVENTS)
        conclude_without_data(r, ctx)
        return r
    e = exchanges(ctx)
    if not e.empty:
        e = e[e["zone"] >= FROM_ZONE]
    if e.empty:
        r.headline = f"No exchanges between players from zone {FROM_ZONE} on in this selection."
        conclude_without_data(r, ctx)
        return r

    # ---- test: higher side wins the trade (one row per exchange, so mirrored sides don't count twice)
    one = e[e["first"]]
    rho = one.groupby("match_id").apply(
        lambda g: spearman(g["rel_m"], g["dealt"] - g["taken"]) if len(g) >= 8 else np.nan, include_groups=False).dropna()
    t = ttest_mean(rho, 0.0)
    if t["n"] >= 3:
        r.test("Per match", "Higher side wins the damage trade", t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha,
               ("Being higher than the opponent wins the damage trade", "Being higher than the opponent loses the damage trade"),
               direction=t["mean"])
    for rng, g in one.groupby("range", observed=True):
        rr = g.groupby("match_id").apply(lambda x: spearman(x["rel_m"], x["dealt"] - x["taken"]) if len(x) >= 6 else np.nan,
                                         include_groups=False).dropna()
        tt = ttest_mean(rr, 0.0)
        if tt["n"] >= 3:
            r.test("By range", str(rng), tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha,
                   ("Height wins the trade at this range", "Height loses the trade at this range"), direction=tt["mean"])

    # ---- numbers
    ratios = e.groupby("height", observed=False).apply(_ratio, include_groups=False)
    tags = e.groupby("height", observed=False)["one_sided"].mean()
    # Level ground always trades about 1 : 1 by definition (both sides are level), so compare the two above-bands.
    hi, md = ratios.get("15 m+ above", np.nan), ratios.get("5–15 m above", np.nan)
    r.headline = (f"From zone {FROM_ZONE} on, players 15 m+ above their opponent dealt {hi:.1f} damage for every 1 taken back "
                  f"({tags.get('15 m+ above', np.nan):.0%} of those exchanges took no damage back), against {md:.1f} from "
                  f"5–15 m above." if np.isfinite(hi) and np.isfinite(md) else
                  f"{len(one):,} exchanges between players from zone {FROM_ZONE} on.")
    r.metric("Exchanges", f"{len(one):,}", f"Runs of hits between two teams from zone {FROM_ZONE} on")
    r.metric("Trade from 15 m+ above", f"{hi:.1f} : 1" if np.isfinite(hi) else "–", "Damage dealt for every 1 taken back")
    r.metric("Trade from 5–15 m above", f"{md:.1f} : 1" if np.isfinite(md) else "–")
    r.metric("One-sided tags from 15 m+ above", f"{tags.get('15 m+ above', np.nan):.0%}",
             "Exchanges where the higher side dealt damage and took none back")

    # ---- charts
    mean = e.groupby("height", observed=False).agg(n=("dealt", "size"), dealt=("dealt", "mean"), taken=("taken", "mean"))
    ok = mean["n"] >= MIN_EXCHANGES
    r.chart("bar", "Damage dealt and taken back, by height over the opponent",
            [dict(name="Dealt", x=AXIS_LABELS, y=mean["dealt"].where(ok).round(0).tolist()),
             dict(name="Taken back", x=AXIS_LABELS, y=mean["taken"].where(ok).round(0).tolist())],
            x_label="Height over the opponent during the exchange", y_label="Average damage per exchange")
    by = e.groupby(["range", "height"], observed=False).apply(_ratio, include_groups=False).unstack("height")
    r.chart("line", "Damage dealt for every 1 taken back, by height and range",
            [dict(name=str(rng), x=AXIS_LABELS, y=by.loc[rng].reindex(HEIGHT_LABELS).clip(upper=10).round(2).tolist())
             for rng in by.index],
            x_label="Height over the opponent", y_label="Dealt per 1 taken back",
            reference_lines=[dict(axis="y", value=1, label="Even trade")])
    r.chart("bar", "One-sided tags, by height over the opponent",
            [dict(name="Took no damage back", x=AXIS_LABELS, y=(tags.reindex(HEIGHT_LABELS) * 100).round(0).tolist())],
            y_label="% of exchanges")
    tbl = []
    for (rng, hb), g in e.groupby(["range", "height"], observed=True):
        tbl.append({"Range": str(rng), "Height over opponent": str(hb), "Exchanges": len(g),
                    "Dealt per exchange": round(g["dealt"].mean()), "Taken back per exchange": round(g["taken"].mean()),
                    "Dealt per 1 taken": round(_ratio(g), 2) if np.isfinite(_ratio(g)) else None,
                    "One-sided": f"{g['one_sided'].mean():.0%}"})
    r.table("Damage trade by range and height", pd.DataFrame(tbl))

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=True,
             takeaway_found="Height changes the damage trade: " + "; ".join(
                 x["reading"].lower() for x in r.tests if x["group"] == "Per match" and x["significant"]) + ".",
             takeaway_none="No consistent damage advantage from height in this selection.",
             next_found=["Use the range lines: they show at which distance a height advantage turns into free damage.",
                         "Pair with Surge: the damage needed to stay safe, and where it's cheapest to get."],
             next_none=["Use the Lobby strength filter: tagging from height is most deliberate in strong lobbies."])
    r.notes += [
        f"Exchanges from zone {FROM_ZONE} on, when surge becomes relevant; earlier fights mostly reflect the landing.",
        "Height is the side's average height over the other team across the exchange's hits; range is the typical "
        "distance between the players.",
        f"Height bands with fewer than {MIN_EXCHANGES} exchanges are left blank.",
        "Close-range fights trade damage both ways almost regardless of height; compare height bands within one range.",
        "Level ground trades about 1 : 1 by definition (each side's damage dealt is the other's taken), and each band "
        "above mirrors the band below; the useful comparison is between the bands above.",
    ]
    return r
