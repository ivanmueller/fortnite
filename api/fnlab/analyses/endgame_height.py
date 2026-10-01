"""
Endgame height: in the endgame (zone 6 on: 50/50s, shifted and moving zones), do teams that rotate
through high ground finish better than teams that rotate low?

Snapshot: each team's average height when each zone starts closing, ranked against the other teams
alive at that moment (low / mid / high ground = bottom, middle, top third). Outcomes compare each team
only with rivals alive at the same moment, so late-game survivors don't flatter any tier.

Path: a team's tiers across the endgame zones it was alive for:
  Held high ground   mostly top third
  Stayed low         mostly bottom third
  Climbed            ended at least one tier higher than it started
  Dropped            ended at least one tier lower than it started
  Mixed              anything else
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..stats import spearman, ttest_mean
from . import ALPHA_PARAM, Context, register
from .height import TIERS, _snapshots

ENDGAME_FROM = 6
PATHS = ["Held high ground", "Climbed", "Mixed", "Dropped", "Stayed low"]


def _path(levels: pd.Series) -> str:
    v = levels.to_numpy(float)
    if v.mean() >= 1.5:
        return "Held high ground"
    if v.mean() <= 0.5:
        return "Stayed low"
    if v[-1] - v[0] >= 1:
        return "Climbed"
    if v[0] - v[-1] >= 1:
        return "Dropped"
    return "Mixed"


@register("endgame_height", "Endgame height",
          "In the endgame (zone 6 on), do teams that rotate through high ground finish better than teams that rotate low?",
          params=[ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    team = _snapshots(ctx)
    if not team.empty:
        team = team[team["phase"] >= ENDGAME_FROM].copy()
    if team.empty:
        r.headline = f"No endgame snapshots (zone {ENDGAME_FROM} on) in this selection."
        conclude(r, ctx, primary=["Per match"], alpha=alpha, recommended=100, takeaway_none="No endgame data to test yet.")
        return r
    low, mid, high = TIERS
    team["level"] = team["tier"].map({low: 0, mid: 1, high: 2}).astype(float)
    # Rivals alive at the same moment who finished ahead (0 = best of those still alive, 1 = worst).
    team["rivals_ahead"] = team.groupby(["match_id", "phase"])["placement"].rank(pct=True, method="average")

    # ---- tests: height vs placement within each endgame snapshot, one summary per match
    rhos = team.groupby(["match_id", "phase"]).apply(
        lambda g: spearman(g["height_pct"], g["placement"]) if len(g) >= 4 else np.nan, include_groups=False)
    t = ttest_mean(rhos.groupby(level="match_id").mean().dropna(), 0.0)
    if t["n"] >= 3:
        r.test("Per match", "Higher teams finish better in the endgame", t["n"], f"mean rho {t['mean']:+.2f}", t["p"], alpha,
               ("Holding higher ground in the endgame goes with finishing worse",
                "Holding higher ground in the endgame goes with finishing better"), direction=t["mean"])
    for ph, rh in rhos.groupby(level="phase"):
        tt = ttest_mean(rh.dropna(), 0.0)
        if tt["n"] >= 5:
            r.test("By zone", f"Zone {int(ph)}", tt["n"], f"mean rho {tt['mean']:+.2f}", tt["p"], alpha,
                   ("Higher teams finish worse", "Higher teams finish better"), direction=tt["mean"])

    # ---- paths through the endgame
    seq = team.sort_values("phase").groupby(["match_id", "team_index"])
    paths = seq.agg(snaps=("level", "size"), placement=("placement", "first"))
    paths["path"] = seq["level"].apply(_path)
    paths = paths[paths["snaps"] >= 2].reset_index()
    paths["finish"] = paths.groupby("match_id")["placement"].rank(pct=True, method="average")
    gap = paths.groupby("match_id").apply(
        lambda g: g.loc[g["path"] == "Held high ground", "finish"].mean() - g.loc[g["path"] == "Stayed low", "finish"].mean()
        if (g["path"] == "Held high ground").sum() >= 1 and (g["path"] == "Stayed low").sum() >= 1 else np.nan,
        include_groups=False).dropna()
    tg = ttest_mean(gap, 0.0)
    if tg["n"] >= 3:
        r.test("Per match", "Holding high beats staying low", tg["n"], f"{tg['mean'] * 100:+.0f} points of finishing rank",
               tg["p"], alpha, ("Teams that held high ground finished behind teams that stayed low",
                                "Teams that held high ground finished ahead of teams that stayed low"), direction=tg["mean"])

    # ---- numbers
    by_tier = team.groupby("tier", observed=True)["rivals_ahead"].mean()
    r.headline = (f"In zones {ENDGAME_FROM}+, high-ground teams finished ahead of {1 - by_tier.get(high, np.nan):.0%} of the rivals "
                  f"alive at the same moment, against {1 - by_tier.get(low, np.nan):.0%} for low-ground teams.")
    share = paths["path"].value_counts(normalize=True)
    r.metric("Teams tracked through the endgame", f"{len(paths):,}", f"Teams alive for at least two zones from zone {ENDGAME_FROM}")
    r.metric("Held high ground", f"{share.get('Held high ground', 0):.0%}")
    r.metric("Stayed low", f"{share.get('Stayed low', 0):.0%}")
    gaps = team.groupby(["match_id", "phase"]).apply(
        lambda g: (g.loc[g["tier"] == high, "z"].mean() - g.loc[g["tier"] == low, "z"].mean()) / 100, include_groups=False)
    r.metric("Typical height gap, high vs low", f"{gaps.median():.0f} m", "Median difference in average height between the two tiers")

    # ---- charts
    line = team.groupby(["phase", "tier"], observed=True)["rivals_ahead"].mean().unstack()
    r.chart("line", "How each height finished, zone by zone",
            [dict(name=str(c), x=[int(p) for p in line.index], y=((1 - line[c]) * 100).round(0).tolist()) for c in line.columns],
            x_label="Zone", y_label="Finished ahead of rivals (%)",
            reference_lines=[dict(axis="y", value=50, label="Average")])
    pg = paths.groupby("path").agg(teams=("finish", "size"), finish=("finish", "mean")).reindex(PATHS).dropna(subset=["teams"])
    r.chart("bar", "How teams finished, by endgame height path",
            [dict(name="Finished ahead of", x=list(pg.index), y=((1 - pg["finish"]) * 100).round(0).tolist())],
            x_label="Height path through the endgame", y_label="Finished ahead of (%)",
            reference_lines=[dict(axis="y", value=50, label="Average")])
    # Survival: present at this zone's snapshot but not the next one = eliminated during the zone.
    nxt = team[["match_id", "phase", "team_index"]].assign(phase=lambda d: d["phase"] - 1, survived=True)
    surv = team.merge(nxt, on=["match_id", "phase", "team_index"], how="left")
    last = team.groupby("match_id")["phase"].transform("max")
    surv = surv[surv["phase"] < last.reindex(surv.index).fillna(surv["phase"]).to_numpy()]
    surv["survived"] = surv["survived"].fillna(False).astype(bool)
    sv = surv.groupby(["phase", "tier"], observed=True)["survived"].mean().unstack()
    if not sv.empty:
        r.chart("line", "Teams surviving each zone, by height",
                [dict(name=str(c), x=[int(p) for p in sv.index], y=(sv[c] * 100).round(0).tolist()) for c in sv.columns],
                x_label="Zone", y_label="Still alive at next zone (%)")
    tbl = pg.reset_index().rename(columns={"path": "Endgame path", "teams": "Teams"})
    tbl["Finished ahead of"] = ((1 - tbl.pop("finish")) * 100).round(0).astype(int).astype(str) + "%"
    tbl["Share of teams"] = (tbl["Teams"] / tbl["Teams"].sum() * 100).round(0).astype(int).astype(str) + "%"
    r.table("Endgame height paths", tbl)

    conclude(r, ctx, strategy=True, primary=["Per match"], alpha=alpha, recommended=100, single_season=False,
             takeaway_found="Endgame height matters: " + "; ".join(
                 x["reading"].lower() for x in r.tests if x["group"] == "Per match" and x["significant"]) + ".",
             takeaway_none="No consistent advantage for high or low ground in the endgame in this selection.",
             next_found=["Open Details: the zone-by-zone results show where height starts to pay off.",
                         "Compare the paths: whether climbing late works as well as holding height from the start."],
             next_none=["Use the Lobby strength filter: endgame height choices are most deliberate in strong lobbies."])
    r.notes += [
        f"Endgame means zone {ENDGAME_FROM} on (50/50s, shifted and moving zones). Early-game height is left out on purpose: "
        "it mostly reflects where players landed, not a positioning choice.",
        "Height is each team's average height when the zone starts closing, ranked against the teams alive then.",
        "Associations, not causes: stronger teams may both take height and win more.",
    ]
    return r
