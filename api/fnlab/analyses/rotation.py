"""
Rotation timing, measured relative to the lobby and the phase type.

Phases differ in kind, so they're analysed by type:
  shrinking            the next circle sits inside the current one
  moving (with wait)   the circle moves past the old edge after a pause
  moving (continuous)  the circle keeps moving with no pause (late endgame)
In moving phases almost nobody can arrive "before the storm moves", so timing is
measured against the other players in the same match and phase, not against the clock.

For each player who must rotate (outside the next circle when it's revealed) and is
alive when the storm starts moving:
  outside_m          distance outside the next circle at the reveal
  lag_first_s        seconds behind the first player to reach that circle
  arrival_pct        arrival order among rotators in that match and phase (0 = first in)
  timing_resid_s     arrival time vs what's typical for players starting as far out
                     (linear fit of arrival on distance within the match and phase);
                     positive = later than players at a similar distance
  timing class       Ahead / Typical / Behind: thirds of timing_resid_s within the match
                     and phase; players who never reached the circle are Behind
  storm_s            seconds outside the storm circle, reconstructed second by second
  neighbours_100m    other living players within 100 m at the reveal
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

CLASSES = ["Ahead", "Typical", "Behind"]
TYPES = ["Shrinking", "Moving (with wait)", "Moving (continuous)"]
MIN_GROUP = 6      # rotators needed in a match-phase to rank them
MIN_POINT = 10     # chart points based on fewer players are left out


def _player_phases(ctx: Context) -> pd.DataFrame:
    """One row per (match, phase, player) tracked in the phase window. Positions are cm; output in m and s."""
    return df(ctx.con, """
        WITH z AS (
            SELECT z.match_id, z.phase, z.cur_x, z.cur_y, z.cur_r, z.next_x, z.next_y, z.next_r,
                   z.start_shrink_t, z.finish_shrink_t,
                   lag(z.finish_shrink_t) OVER (PARTITION BY z.match_id ORDER BY z.phase) AS reveal_t
            FROM zones z JOIN sel USING (match_id)
        ),
        zz AS (
            SELECT *,
                   sqrt(power(next_x - cur_x, 2) + power(next_y - cur_y, 2)) > cur_r - next_r + 100 AS moving
            FROM z
            WHERE reveal_t IS NOT NULL AND cur_x IS NOT NULL AND start_shrink_t >= reveal_t
              AND finish_shrink_t > start_shrink_t
        ),
        p AS (SELECT p.match_id, p.id, p.t, p.x, p.y FROM positions p JOIN sel USING (match_id)),
        s AS (
            SELECT zz.match_id, zz.phase, p.id, p.t, p.x, p.y, zz.next_r, zz.cur_r, zz.reveal_t,
                   zz.start_shrink_t, zz.finish_shrink_t, zz.moving,
                   sqrt(power(p.x - zz.next_x, 2) + power(p.y - zz.next_y, 2)) AS d_next,
                   greatest(0, least(1, (p.t - zz.start_shrink_t) / (zz.finish_shrink_t - zz.start_shrink_t))) AS f,
                   zz.cur_x, zz.cur_y, zz.next_x, zz.next_y
            FROM zz JOIN p ON p.match_id = zz.match_id AND p.t >= zz.reveal_t AND p.t <= zz.finish_shrink_t
        ),
        s2 AS (
            SELECT *,
                   sqrt(power(x - (cur_x + f * (next_x - cur_x)), 2) + power(y - (cur_y + f * (next_y - cur_y)), 2))
                       > (cur_r + f * (next_r - cur_r)) AS in_storm,
                   first_value(d_next) OVER (PARTITION BY match_id, phase, id ORDER BY t) AS d_reveal
            FROM s
        )
        SELECT match_id, phase, id,
               min(t) AS first_t, max(t) AS last_t, count(*) AS n,
               arg_min(x, t) AS x0, arg_min(y, t) AS y0,
               any_value(d_reveal) / 100 AS d_reveal_m, any_value(next_r) / 100 AS next_r_m,
               any_value(cur_r) / 100 AS cur_r_m, any_value(moving) AS moving,
               any_value(reveal_t) AS reveal_t, any_value(start_shrink_t) AS start_t,
               any_value(finish_shrink_t) AS finish_t,
               min(t) FILTER (WHERE d_next <= next_r) AS entry_t,
               min(t) FILTER (WHERE d_next <= d_reveal - greatest(5000, 0.25 * (d_reveal - next_r))) AS depart_t,
               count(*) FILTER (WHERE in_storm) AS storm_n
        FROM s2 GROUP BY 1, 2, 3
    """)


def _neighbours(g: pd.DataFrame, radius_cm: float = 10000) -> np.ndarray:
    xy = g[["x0", "y0"]].to_numpy(float)
    dist = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    return (dist <= radius_cm).sum(axis=1) - 1


def _resid(g: pd.DataFrame) -> pd.Series:
    """Arrival time vs what's typical for the starting distance, within one match-phase."""
    a = g.dropna(subset=["arrival_rel_s"])
    out = pd.Series(np.inf, index=g.index)  # never arrived: behind everyone
    if len(a) >= 3 and a["outside_m"].std() > 0:
        slope, icept = np.polyfit(a["outside_m"], a["arrival_rel_s"], 1)
        out.loc[a.index] = a["arrival_rel_s"] - (icept + slope * a["outside_m"])
    elif len(a):
        out.loc[a.index] = a["arrival_rel_s"] - a["arrival_rel_s"].median()
    return out


def rotations(ctx: Context) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """(rotations, phase context, players eliminated before the shrink)."""
    r = _player_phases(ctx)
    if r.empty:
        return r, r, 0
    players = df(ctx.con, "SELECT * FROM players JOIN sel USING (match_id)")
    keep = ["match_id", "id", "is_bot", "death_t", "placement"] + [c for c in ("team_placement", "pr_rank") if c in players]
    r = r.merge(players[keep], on=["match_id", "id"], how="left")
    r = r[~r["is_bot"].fillna(False).astype(bool)]
    r["final"] = r["team_placement"] if "team_placement" in r else r["placement"]
    wait = r["start_t"] - r["reveal_t"]
    r["type"] = np.select([~r["moving"].astype(bool), wait > 1], TYPES[:2], TYPES[2])

    # Everyone alive and tracked at the reveal: phase context and local density.
    alive = r[((r["first_t"] - r["reveal_t"]) <= 5) & (r["death_t"].isna() | (r["death_t"] >= r["reveal_t"]))].copy()
    alive["neighbours_100m"] = 0
    for _, g in alive.groupby(["match_id", "phase"]):
        alive.loc[g.index, "neighbours_100m"] = _neighbours(g)
    ctxt = alive.groupby(["match_id", "phase"]).agg(
        type=("type", "first"), alive=("id", "size"), cur_r_m=("cur_r_m", "first"), next_r_m=("next_r_m", "first"),
        wait_s=("start_t", "first")).reset_index()
    ctxt["wait_s"] = ctxt["wait_s"] - alive.groupby(["match_id", "phase"])["reveal_t"].first().to_numpy()
    ctxt["per_km2"] = ctxt["alive"] / (np.pi * (ctxt["cur_r_m"] / 1000) ** 2)

    # Rotators: outside the next circle at the reveal and alive when the storm moves.
    rot = alive[alive["d_reveal_m"] > alive["next_r_m"]].copy()
    alive_at_start = rot["death_t"].isna() | (rot["death_t"] >= rot["start_t"])
    dropped = int((~alive_at_start).sum())
    rot = rot[alive_at_start].copy()
    dt = ((rot["last_t"] - rot["first_t"]) / (rot["n"] - 1).clip(lower=1)).clip(upper=5)
    rot["storm_s"] = rot["storm_n"] * dt
    rot["outside_m"] = rot["d_reveal_m"] - rot["next_r_m"]
    rot["arrival_rel_s"] = rot["entry_t"] - rot["start_t"]
    rot["depart_delay_s"] = rot["depart_t"] - rot["reveal_t"]
    rot["died_in_shrink"] = rot["death_t"].between(rot["start_t"], rot["finish_t"] + 1)
    grp = rot.groupby(["match_id", "phase"])
    rot["n_rot"] = grp["id"].transform("size")
    rot["lag_first_s"] = rot["entry_t"] - grp["entry_t"].transform("min")
    rot["first_in"] = rot["lag_first_s"] == 0
    rot["arrival_pct"] = grp["entry_t"].rank(pct=True, na_option="bottom")
    rot["timing_resid_s"] = grp.apply(_resid, include_groups=False).reset_index(level=[0, 1], drop=True)
    pct = rot.groupby(["match_id", "phase"])["timing_resid_s"].rank(pct=True, method="average")
    rot["timing"] = np.where(rot["n_rot"] < MIN_GROUP, None,
                             np.select([pct <= 1 / 3, pct <= 2 / 3], CLASSES[:2], CLASSES[2]))
    return rot.dropna(subset=["final"]), ctxt, dropped


def _tests(r: Result, d: pd.DataFrame, group: str, alpha: float) -> None:
    """Distance-adjusted timing vs placement, and elimination of Behind vs Ahead, per match."""
    c = d.dropna(subset=["timing"])
    rho = c[np.isfinite(c["timing_resid_s"])].groupby(["match_id", "phase"]).apply(
        lambda g: spearman(g["timing_resid_s"], g["final"]) if len(g) >= 4 else np.nan, include_groups=False)
    t = ttest_mean(rho.groupby(level="match_id").mean().dropna(), 0.0)
    if t["n"] >= 3:
        r.test(group, "Later than players at a similar distance, worse placement", t["n"],
               f"mean rho {t['mean']:+.2f}", t["p"], alpha, "Arriving later than comparable players goes with finishing worse")

    def gap(g):
        a, b = g[g["timing"] == "Ahead"], g[g["timing"] == "Behind"]
        return b["died_in_shrink"].mean() - a["died_in_shrink"].mean() if len(a) >= 2 and len(b) >= 2 else np.nan
    gaps = c.groupby("match_id").apply(gap, include_groups=False).dropna()
    t2 = ttest_mean(gaps, 0.0)
    if t2["n"] >= 3:
        rb = c.loc[c["timing"] == "Behind", "died_in_shrink"].mean()
        ra = c.loc[c["timing"] == "Ahead", "died_in_shrink"].mean()
        r.test(group, "Behind eliminated more than Ahead", t2["n"], f"{rb:.0%} of Behind vs {ra:.0%} of Ahead",
               t2["p"], alpha, "Falling behind comparable players is where eliminations happen")
    drho = d.groupby(["match_id", "phase"]).apply(
        lambda g: spearman(g["outside_m"], g["final"]) if len(g) >= 4 else np.nan, include_groups=False)
    t3 = ttest_mean(drho.groupby(level="match_id").mean().dropna(), 0.0)
    if t3["n"] >= 3:
        r.test(group, "Starting farther out, worse placement", t3["n"], f"mean rho {t3['mean']:+.2f}", t3["p"], alpha,
               "Being far from the next circle when it appears goes with finishing worse")


@register("rotation", "Rotation timing",
          "When players reach the next circle compared with the rest of the lobby and with players starting as far "
          "out, by phase type (shrinking, moving, continuous), and how that relates to eliminations and placement.",
          params=[ALPHA_PARAM,
                  Param("type", "Phase type", "select", "all",
                        [{"value": "all", "label": "All phase types"}] + [{"value": t, "label": t} for t in TYPES]),
                  Param("players", "Players", "select", "all",
                        [{"value": "all", "label": "All players"},
                         {"value": "ranked", "label": "Power Rankings top 10,000 only"},
                         {"value": "unranked", "label": "Outside the top 10,000 only"}],
                        "Restrict to one skill band, so timing isn't standing in for skill.")])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    d, ph, dropped = rotations(ctx)
    if d.empty:
        r.headline = "No rotations to analyse in this selection."
        return r
    want = ctx.params.get("type", "all")
    if want in TYPES:
        d, ph = d[d["type"] == want], ph[ph["type"] == want]
    who = ctx.params.get("players", "all")
    if who != "all":
        if "pr_rank" not in d or d["pr_rank"].isna().all():
            r.warnings.append("Power Rankings aren't downloaded, so the Players setting was ignored (data menu option P).")
        else:
            d = d[d["pr_rank"].notna()] if who == "ranked" else d[d["pr_rank"].isna()]
    if d.empty:
        r.headline = "No rotations of this kind in the selection."
        return r

    # ---- tests: overall per match (main claim), then by phase type
    _tests(r, d, "Per match", alpha)
    for t in TYPES:
        sub = d[d["type"] == t]
        if sub["match_id"].nunique() >= 5 and want == "all":
            _tests(r, sub, t, alpha)

    # ---- numbers
    c = d.dropna(subset=["timing"])
    rb = c.loc[c["timing"] == "Behind", "died_in_shrink"].mean()
    ra = c.loc[c["timing"] == "Ahead", "died_in_shrink"].mean()
    r.headline = (f"Across {len(d):,} rotations, players who fell behind others starting as far out were eliminated "
                  f"during the shrink {rb:.0%} of the time, against {ra:.0%} for those ahead.")
    r.metric("Rotations", f"{len(d):,}", "A player outside the next circle when it appeared and alive when the storm "
             "started moving, in one phase")
    r.metric("Eliminated before the shrink", f"{dropped:,}",
             "Rotating players eliminated before the storm moved; not classified")
    r.metric("Median distance outside", f"{d['outside_m'].median():.0f} m")
    r.metric("Median lag behind first in", f"{d['lag_first_s'].median():.0f} s")
    r.metric("Took storm", f"{(d['storm_s'] >= 2).mean():.0%}", "Share of rotations with 2+ seconds in the storm")

    # ---- context table: what each phase looks like
    ctx_t = (ph.groupby("phase").agg(type=("type", lambda s: s.mode().iat[0]), wait=("wait_s", "median"),
                                     radius=("cur_r_m", "median"), alive=("alive", "median"), density=("per_km2", "median"))
             .join(d.groupby("phase").agg(rotating=("id", "size"), outside=("outside_m", "median"),
                                          neighbours=("neighbours_100m", "median"), lag=("lag_first_s", "median"),
                                          storm=("storm_s", "median"), took=("storm_s", lambda s: (s >= 2).mean())))
             .reset_index())
    ctx_t = ctx_t.rename(columns={
        "phase": "Phase", "type": "Type", "wait": "Wait (s)", "radius": "Circle radius (m)", "alive": "Players alive",
        "density": "Players per km²", "rotating": "Rotations", "outside": "Median distance outside (m)",
        "neighbours": "Median players within 100 m", "lag": "Median lag behind first in (s)",
        "storm": "Median storm time (s)", "took": "Took storm"})
    ctx_t["Took storm"] = (ctx_t["Took storm"] * 100).round(0).astype("Int64").astype(str) + "%"
    r.table("Phase by phase: what each phase looks like and how players rotate", ctx_t.round(1))

    # ---- charts
    tier = pd.cut(d["final"], [0, 10, 50, 999], labels=["Top 10", "11th–50th", "51st or lower"])
    g = d.assign(tier=tier).groupby(["phase", "tier"], observed=False)["lag_first_s"]
    lag = g.median().where(g.size() >= MIN_POINT).unstack()
    r.chart("line", "Seconds behind the first player in, by how they finished",
            [dict(name=str(t), x=[int(p) for p in lag.index], y=lag[t].round(0).tolist()) for t in lag.columns],
            x_label="Phase", y_label="Median seconds behind the first arrival")
    g = c.groupby(["phase", "timing"])["died_in_shrink"]
    rate = g.mean().where(g.size() >= MIN_POINT).unstack()
    r.chart("line", "Eliminated during the shrink, by timing vs comparable players",
            [dict(name=k, x=[int(p) for p in rate.index], y=(rate[k] * 100).round(1).tolist()) for k in CLASSES if k in rate],
            x_label="Phase", y_label="% eliminated while the storm closed")
    g = d.assign(tier=tier).groupby(["phase", "tier"], observed=False)["storm_s"]
    st = g.mean().where(g.size() >= MIN_POINT).unstack()
    r.chart("line", "Storm time, by how they finished",
            [dict(name=str(t), x=[int(p) for p in st.index], y=st[t].round(1).tolist()) for t in st.columns],
            x_label="Phase", y_label="Average seconds in the storm")
    place = c.groupby("timing")["final"].mean().reindex(CLASSES)
    r.chart("bar", "Average final placement by timing vs comparable players",
            [dict(name="Average placement", x=CLASSES, y=place.round(1).tolist())], y_label="Average placement (lower is better)")

    if "pr_rank" in d and d["pr_rank"].notna().any():
        band = pd.cut(c["pr_rank"].fillna(10**7), [0, 1000, 10000, 10**8],
                      labels=["PR top 1,000", "PR 1,001–10,000", "Unranked"])
        sk = c.assign(band=band).groupby(["band", "timing"], observed=False)["final"].mean().unstack().reindex(columns=CLASSES)
        sk = sk.round(1).reset_index()
        sk.columns = ["Skill band"] + [f"{k}: avg placement" for k in CLASSES]
        r.table("Skill control: average placement by timing within each Power Rankings band", sk)
        r.notes.insert(0, "Skill control: if Behind players finish worse within each Power Rankings band, timing matters "
                          "beyond skill. If the gap only appears between bands, it's skill showing.")

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=False,
             takeaway_found="Rotation timing, compared with players starting as far out, is linked to outcome: " + "; ".join(
                 t["reading"].lower() for t in r.tests if t["group"] == "Per match" and t["significant"]) + ".",
             takeaway_none="No consistent link between distance-adjusted rotation timing and outcome in this selection.",
             next_found=["Expand each phase type: the effect may hold in shrinking phases but not in continuous moving zones.",
                         "Check the skill-control table before concluding anything about timing itself.",
                         "Use the phase table to see how crowded each phase is when the effect appears."],
             next_none=["Pick one phase type at a time: effects in different kinds of phase can cancel out."])
    r.notes += [
        "Timing is relative: within each match and phase, players are compared with the first arrival and with others "
        "who started a similar distance out. Arriving 'before the storm moves' is impossible in continuous moving zones, "
        "so it isn't used.",
        f"Chart points based on fewer than {MIN_POINT} players are left out; tables show every phase.",
        "Storm time is reconstructed from positions and storm timings (Season 42 replays don't record it), so it's "
        "accurate to a few seconds. It's time in the storm, not damage: replay health data isn't extracted yet.",
        "Associations, not causes: strong players both rotate well and win fights. Use the skill controls.",
    ]
    return r
