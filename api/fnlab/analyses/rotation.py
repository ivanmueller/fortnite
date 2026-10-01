"""
Rotation timing: when players leave for the next circle, when they arrive, how long
they spend in the storm, and how that relates to being eliminated and to placement.

For each storm phase and each living player outside the next circle when it's revealed:
  reveal       the previous shrink finishing (that's when the next circle appears)
  departure    first moment they've closed a meaningful part of the gap to the next circle
  arrival      first moment they're inside the next circle, relative to the storm starting
               to close (negative = arrived before the storm moved)
  storm time   seconds outside the storm circle, which is reconstructed second by second:
               the current circle until the shrink starts, then moving and shrinking
               linearly to the next circle (Season 42 replays don't record storm status)
  timing class early (inside before the shrink starts), with the storm (arrived during the
               shrink with under 5 s in the storm), late (5 s+ in the storm or never arrived)
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

CLASSES = ["Early", "With the storm", "Late"]
STORM_LATE_S = 5.0
PHASE_GROUPS = {"all": None, "early": (2, 4), "mid": (5, 7), "late": (8, 99)}


def _rotations(ctx: Context, phase_sql: str) -> pd.DataFrame:
    """One row per (match, phase, player) with timing measures. Positions are cm; output in m and s."""
    r = df(ctx.con, f"""
        WITH z AS (
            SELECT z.match_id, z.phase, z.cur_x, z.cur_y, z.cur_r, z.next_x, z.next_y, z.next_r,
                   z.start_shrink_t, z.finish_shrink_t,
                   lag(z.finish_shrink_t) OVER (PARTITION BY z.match_id ORDER BY z.phase) AS reveal_t
            FROM zones z JOIN sel USING (match_id)
        ),
        zz AS (
            SELECT * FROM z
            WHERE reveal_t IS NOT NULL AND cur_x IS NOT NULL AND start_shrink_t >= reveal_t
              AND finish_shrink_t > start_shrink_t {phase_sql}
        ),
        p AS (SELECT p.match_id, p.id, p.t, p.x, p.y FROM positions p JOIN sel USING (match_id)),
        s AS (
            SELECT zz.match_id, zz.phase, p.id, p.t, zz.next_r, zz.reveal_t, zz.start_shrink_t, zz.finish_shrink_t,
                   sqrt(power(p.x - zz.next_x, 2) + power(p.y - zz.next_y, 2)) AS d_next,
                   greatest(0, least(1, (p.t - zz.start_shrink_t) / (zz.finish_shrink_t - zz.start_shrink_t))) AS f,
                   p.x, p.y, zz.cur_x, zz.cur_y, zz.cur_r, zz.next_x, zz.next_y
            FROM zz JOIN p ON p.match_id = zz.match_id AND p.t >= zz.reveal_t AND p.t <= zz.finish_shrink_t
        ),
        s2 AS (
            SELECT match_id, phase, id, t, d_next, next_r, reveal_t, start_shrink_t, finish_shrink_t,
                   sqrt(power(x - (cur_x + f * (next_x - cur_x)), 2) + power(y - (cur_y + f * (next_y - cur_y)), 2))
                       > (cur_r + f * (next_r - cur_r)) AS in_storm,
                   first_value(d_next) OVER (PARTITION BY match_id, phase, id ORDER BY t) AS d_reveal
            FROM s
        )
        SELECT match_id, phase, id,
               min(t) AS first_t, max(t) AS last_t, count(*) AS n,
               any_value(d_reveal) / 100 AS d_reveal_m, any_value(next_r) / 100 AS next_r_m,
               any_value(reveal_t) AS reveal_t, any_value(start_shrink_t) AS start_t, any_value(finish_shrink_t) AS finish_t,
               min(t) FILTER (WHERE d_next <= next_r) AS entry_t,
               min(t) FILTER (WHERE d_next <= d_reveal - greatest(5000, 0.25 * (d_reveal - next_r))) AS depart_t,
               count(*) FILTER (WHERE in_storm) AS storm_n
        FROM s2 GROUP BY 1, 2, 3
    """)
    if r.empty:
        return r
    players = df(ctx.con, "SELECT * FROM players JOIN sel USING (match_id)")
    keep = ["match_id", "id", "is_bot", "death_t", "placement"] + [c for c in ("team_placement", "pr_rank") if c in players]
    r = r.merge(players[keep], on=["match_id", "id"], how="left")
    r = r[~r["is_bot"].fillna(False).astype(bool)]
    r["final"] = r["team_placement"] if "team_placement" in r else r["placement"]
    r = r[(r["first_t"] - r["reveal_t"]) <= 5]                      # alive and tracked at the reveal
    r = r[r["d_reveal_m"] > r["next_r_m"]].copy()                   # had to rotate
    dt = ((r["last_t"] - r["first_t"]) / (r["n"] - 1).clip(lower=1)).clip(upper=5)
    r["storm_s"] = r["storm_n"] * dt
    r["arrival_rel_s"] = r["entry_t"] - r["start_t"]
    r["depart_delay_s"] = r["depart_t"] - r["reveal_t"]
    # Compare like with like: only players still alive when the storm starts moving are classified,
    # and "eliminated" means during the shrink, for every class. Otherwise anyone eliminated
    # before the shrink would count as "never arrived" = late, biasing the comparison.
    alive_at_start = r["death_t"].isna() | (r["death_t"] >= r["start_t"])
    r.attrs["dropped_before_shrink"] = int((~alive_at_start).sum())
    r = r[alive_at_start].copy()
    r["died_in_phase"] = r["death_t"].between(r["start_t"], r["finish_t"] + 1)
    early = r["entry_t"] < r["start_t"]
    with_storm = (r["entry_t"] >= r["start_t"]) & (r["storm_s"] < STORM_LATE_S)
    r["timing"] = np.select([early, with_storm], CLASSES[:2], CLASSES[2])
    r["outside_m"] = r["d_reveal_m"] - r["next_r_m"]
    return r


@register("rotation", "Rotation timing",
          "When players leave for the next circle, when they arrive, how long they spend in the storm, "
          "and how that relates to being eliminated and to final placement.",
          params=[ALPHA_PARAM,
                  Param("phases", "Phases", "select", "all",
                        [{"value": "all", "label": "All phases"}, {"value": "early", "label": "Early (2–4)"},
                         {"value": "mid", "label": "Mid (5–7)"}, {"value": "late", "label": "Late (8+)"}]),
                  Param("players", "Players", "select", "all",
                        [{"value": "all", "label": "All players"},
                         {"value": "ranked", "label": "Power Rankings top 10,000 only"},
                         {"value": "unranked", "label": "Outside the top 10,000 only"}],
                        "Restrict to one skill band, so timing isn't standing in for skill.")])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    rng = PHASE_GROUPS.get(ctx.params.get("phases", "all"))
    phase_sql = "" if rng is None else f"AND phase BETWEEN {rng[0]} AND {rng[1]}"
    d = _rotations(ctx, phase_sql)
    who = ctx.params.get("players", "all")
    if not d.empty and who != "all":
        if "pr_rank" not in d or d["pr_rank"].isna().all():
            r.warnings.append("Power Rankings aren't downloaded, so the Players setting was ignored (data menu option P).")
        else:
            d = d[d["pr_rank"].notna()] if who == "ranked" else d[d["pr_rank"].isna()]
    if d.empty:
        r.headline = "No rotations to analyse in this selection."
        return r
    dropped = d.attrs.get("dropped_before_shrink", 0)
    d = d.dropna(subset=["final"])

    # ---- main tests (per match, so linked rotations within a match don't inflate significance)
    arrived = d.dropna(subset=["arrival_rel_s"])
    rhos = arrived.groupby(["match_id", "phase"]).apply(
        lambda g: spearman(g["arrival_rel_s"], g["final"]) if len(g) >= 4 else np.nan, include_groups=False)
    t_arr = ttest_mean(rhos.groupby(level="match_id").mean().dropna(), 0.0)
    r.test("Per match", "Later arrival, worse placement", t_arr["n"], f"mean rho {t_arr.get('mean', np.nan):+.2f}",
           t_arr["p"], alpha, "Players who reach the next circle later finish worse")

    def rate_gap(g):
        e, l = g[g["timing"] == "Early"], g[g["timing"] == "Late"]
        return l["died_in_phase"].mean() - e["died_in_phase"].mean() if len(e) >= 3 and len(l) >= 3 else np.nan
    gaps = d.groupby("match_id").apply(rate_gap, include_groups=False).dropna()
    t_gap = ttest_mean(gaps, 0.0)
    late_rate = d.loc[d["timing"] == "Late", "died_in_phase"].mean()
    early_rate = d.loc[d["timing"] == "Early", "died_in_phase"].mean()
    r.test("Per match", "Late rotators eliminated more", t_gap["n"],
           f"{late_rate:.0%} of late vs {early_rate:.0%} of early rotators eliminated during the shrink",
           t_gap["p"], alpha, "Late rotations are where players get eliminated")

    # ---- per phase
    for ph, rh in rhos.groupby(level="phase"):
        t = ttest_mean(rh.dropna(), 0.0)
        if t["n"] >= 5:
            r.test("Arrival vs placement by phase", f"Phase {int(ph)}", t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha)
    for ph, g in d.groupby("phase"):
        e, l = g[g["timing"] == "Early"], g[g["timing"] == "Late"]
        if len(e) >= 10 and len(l) >= 10:
            table = [[int(l["died_in_phase"].sum()), int((~l["died_in_phase"]).sum())],
                     [int(e["died_in_phase"].sum()), int((~e["died_in_phase"]).sum())]]
            p = sps.fisher_exact(table).pvalue
            r.test("Eliminated during the shrink: late vs early", f"Phase {int(ph)}", len(e) + len(l),
                   f"late {l['died_in_phase'].mean():.0%} vs early {e['died_in_phase'].mean():.0%}", p, alpha)

    # ---- headline and numbers
    share = d["timing"].value_counts(normalize=True)
    r.headline = (f"Across {len(d):,} rotations, {share.get('Late', 0):.0%} were late; late rotators were eliminated "
                  f"during the shrink {late_rate:.0%} of the time, against {early_rate:.0%} for early ones.")
    r.metric("Rotations", f"{len(d):,}", "A player outside the next circle when it was revealed and still alive when "
             "the storm started moving, in one phase")
    r.metric("Eliminated before the shrink", f"{dropped:,}", "Rotating players eliminated before the storm moved; not "
             "classified, because their timing never played out")
    r.metric("Early / with storm / late", " / ".join(f"{share.get(c, 0):.0%}" for c in CLASSES))
    r.metric("Median departure", f"{d['depart_delay_s'].median():.0f} s after reveal")
    r.metric("Median arrival", f"{arrived['arrival_rel_s'].median():+.0f} s vs shrink start",
             "Negative: arrived before the storm started moving")
    r.metric("Median storm time (late)", f"{d.loc[d['timing'] == 'Late', 'storm_s'].median():.0f} s")

    # ---- charts
    MIN_POINT = 10  # chart points based on fewer players than this are left out
    grp = d.groupby(["phase", "timing"])["died_in_phase"]
    rate = grp.mean().where(grp.size() >= MIN_POINT).unstack()
    r.chart("line", "Eliminated during the shrink, by rotation timing",
            [dict(name=c, x=[int(p) for p in rate.index], y=(rate[c] * 100).round(1).tolist()) for c in CLASSES if c in rate],
            x_label="Phase", y_label="% eliminated while the storm closed")
    place = d.groupby("timing")["final"].mean().reindex(CLASSES)
    r.chart("bar", "Average final placement by rotation timing",
            [dict(name="Average placement", x=CLASSES, y=place.round(1).tolist())], y_label="Average placement (lower is better)")
    arrived = arrived.assign(tier=pd.cut(arrived["final"], [0, 10, 50, 999], labels=["Top 10", "11th–50th", "51st or lower"]))
    ga = arrived.groupby(["phase", "tier"], observed=False)["arrival_rel_s"]
    med = ga.median().where(ga.size() >= MIN_POINT).unstack()
    r.chart("line", "When players arrive, by how they finished",
            [dict(name=str(t), x=[int(p) for p in med.index], y=med[t].round(0).tolist()) for t in med.columns],
            x_label="Phase", y_label="Median arrival vs shrink start (s)")
    r.chart("histogram", "Arrival relative to the shrink starting",
            [dict(name="Rotations", values=arrived["arrival_rel_s"].clip(-120, 120).tolist())], bins=48, range=[-120, 120],
            x_label="Seconds from the storm starting to close (negative = earlier)",
            y_label="Rotations", reference_lines=[dict(axis="x", value=0, label="Shrink starts")])

    # ---- tables
    by_phase = d.groupby("phase").agg(
        rotations=("id", "size"), outside_m=("outside_m", "median"), depart=("depart_delay_s", "median"),
        arrival=("arrival_rel_s", "median"), storm=("storm_s", "median"),
        late=("timing", lambda s: (s == "Late").mean()))
    by_phase = by_phase.reset_index().rename(columns={
        "phase": "Phase", "rotations": "Rotations", "outside_m": "Median distance outside (m)",
        "depart": "Median departure (s after reveal)", "arrival": "Median arrival (s vs shrink start)",
        "storm": "Median storm time (s)", "late": "Late share"})
    by_phase["Late share"] = (by_phase["Late share"] * 100).round(0).astype(int).astype(str) + "%"
    r.table("Phase by phase", by_phase.round(0))
    if "pr_rank" in d and d["pr_rank"].notna().any():
        band = pd.cut(d["pr_rank"].fillna(10**7), [0, 1000, 10000, 10**8],
                      labels=["PR top 1,000", "PR 1,001–10,000", "Unranked"])
        sk = d.assign(band=band).groupby(["band", "timing"], observed=False)["final"].mean().unstack().reindex(columns=CLASSES)
        sk = sk.round(1).reset_index().rename(columns={"band": "Skill band"})
        sk.columns = ["Skill band"] + [f"{c}: avg placement" for c in CLASSES]
        r.table("Skill control: average placement by timing within each Power Rankings band", sk)
        r.notes.insert(0, "Skill control: if late rotators finish worse within each Power Rankings band, timing matters "
                          "beyond skill. If the gap only appears between bands, it's skill showing.")

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=False,
             takeaway_found="Rotation timing is linked to outcome: " + "; ".join(
                 t["reading"].lower() for t in r.tests if t["group"] == "Per match" and t["significant"]) + ".",
             takeaway_none="No consistent link between rotation timing and outcome in this selection.",
             next_found=["Check the skill-control table: the effect should hold within each Power Rankings band.",
                         "Find the phases where the late/early elimination gap is widest: that's where to rotate first.",
                         "Use the Players setting to repeat the tests within one skill band."],
             next_none=["Try one phase group at a time: an effect confined to mid-game phases can be diluted."])
    r.notes += [
        f"Chart points based on fewer than {MIN_POINT} players are left out; the tables show every phase.",
        "Storm status is reconstructed from positions and storm timings, because Season 42 replays don't record it. "
        "Treat storm time as accurate to a few seconds.",
        "Only players outside the next circle when it was revealed count as rotating; players already inside are left out.",
        "Associations, not causes: strong players both rotate well and win fights. Use the skill controls.",
    ]
    return r
