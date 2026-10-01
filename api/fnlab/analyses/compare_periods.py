from __future__ import annotations

import numpy as np

from ..conclusion import conclude
from ..result import Result
from ..stats import circ_mean, circular_permutation, ks_2samp, rayleigh
from . import ALPHA_PARAM, Context, register
from .zone_randomness import load_pulls, per_match


@register("compare_periods", "Compare storm between periods",
          "Did storm behaviour change? Compares the main selection (A) with a second selection (B), "
          "for example two seasons or before and after a patch.",
          params=[ALPHA_PARAM], needs_compare=True, min_matches=3)
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    za, zb = load_pulls(ctx.con, "sel"), load_pulls(ctx.con, "sel_b")
    if za.empty or zb.empty:
        r.headline = "Both selections need storm pulls. Adjust selection A or B."
        return r
    overlap = ctx.con.execute("SELECT count(*) FROM sel JOIN sel_b USING (match_id)").fetchone()[0]
    if overlap:
        r.warnings.append(f"{overlap} matches are in both A and B. Comparisons are cleaner with no overlap.")
    pa, pb = per_match(za), per_match(zb)

    tests = [
        ("Pull distance (u)", ks_2samp(pa["u"], pb["u"]), f"A {pa['u'].mean():.2f} vs B {pb['u'].mean():.2f}"),
        ("Distance moved ÷ radius", ks_2samp(pa["offset_ratio"], pb["offset_ratio"]),
         f"A {pa['offset_ratio'].mean():.2f} vs B {pb['offset_ratio'].mean():.2f}"),
    ]
    for name, res, val in tests:
        r.test("A vs B", name, res["n_a"] + res["n_b"], val, res["p"], alpha)
    for name, col in (("Compass direction", "angle"), ("Direction vs bus", "rel_bus"), ("Keeps previous direction", "turn")):
        res = circular_permutation(pa[col], pb[col])
        ra, rb = rayleigh(pa[col]), rayleigh(pb[col])
        r.test("A vs B", name, res["n_a"] + res["n_b"],
               f"A {circ_mean(pa[col]):.0f}° (R {ra['R']:.2f}) vs B {circ_mean(pb[col]):.0f}° (R {rb['R']:.2f})",
               res["p"], alpha)
    for label, pm in (("A", pa), ("B", pb)):
        rb = rayleigh(pm["rel_bus"])
        r.test(f"Within {label}", "Direction vs bus", rb["n"], f"{rb['mean_deg']:.0f}°, R {rb['R']:.2f}", rb["p"], alpha)
        r.test(f"Within {label}", "Pull distance", len(pm), f"mean u {pm['u'].mean():.2f}",
               _t(pm["u"]), alpha)

    changed = [t["name"].lower() for t in r.tests if t["group"] == "A vs B" and t["significant"]]
    r.headline = (f"Storm behaviour differs between A and B: {', '.join(changed)}." if changed else
                  f"No significant difference between A ({len(pa)} matches) and B ({len(pb)} matches) at p < {alpha}.")
    r.metric("A matches", f"{len(pa):,}", ctx.filters.describe())
    r.metric("B matches", f"{len(pb):,}", ctx.compare.describe() if ctx.compare else "")
    r.metric("Mean u (A / B)", f"{za['u'].mean():.2f} / {zb['u'].mean():.2f}")

    conclude(r, ctx, primary=["A vs B"], alpha=alpha, recommended=100, single_season=False,
             takeaway_found="Storm behaviour differs between A and B in: " + ", ".join(changed) + ".",
             takeaway_none="No measurable change in storm behaviour between A and B.",
             next_found=["Check the Within A and Within B rows to see which side carries the pattern.",
                         "Make sure only the period differs between A and B; region or playlist differences also change results."],
             next_none=["If a pattern was found in A, check Within B: present in both with no difference means it carried over."])
    r.conclusion["reliability"].insert(1, dict(label="Sample size (B)", ok=len(pb) >= 100,
        detail=f"{len(pb):,} matches in B; 100+ recommended." if len(pb) < 100 else f"{len(pb):,} matches in B."))
    r.chart("histogram", "Pull distance u", [dict(name="A", values=za["u"].tolist()), dict(name="B", values=zb["u"].tolist())],
            bins=10, range=[0, 1], normalize=True, x_label="u", y_label="Share of pulls")
    r.chart("polar_histogram", "Pull direction relative to bus heading",
            [dict(name="A", theta=za["rel_bus_deg"].dropna().tolist()), dict(name="B", theta=zb["rel_bus_deg"].dropna().tolist())],
            bins=24, normalize=True, zero_label="Bus heading")
    r.chart("polar_histogram", "Pull direction",
            [dict(name="A", theta=za["angle_deg"].tolist()), dict(name="B", theta=zb["angle_deg"].tolist())],
            bins=24, normalize=True)
    r.notes.append("All comparisons use one summary per match (KS tests for distances, permutation tests for "
                   "directions), so linked pulls within a match don't inflate significance.")
    return r


def _t(x):
    from ..stats import ttest_mean
    return ttest_mean(x, 0.5)["p"]
