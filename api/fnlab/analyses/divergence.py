"""
Divergence report: where does selection A (for example the FNCS Global Championship)
differ from selection B (for example everything else)?

Each measure is computed with the same code as its own page, once per match, for both
groups; the groups are then compared with a Mann-Whitney U test on the per-match values.
B automatically excludes matches that are also in A.

Measures that depend on team play (rotation, drops, fights, eliminations) are flagged
when the groups' team sizes differ (for example Duos vs Solos): a divergence there may be
the mode, not the players.
"""
from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import ALPHA_PARAM, Context, register
from .height import _fights
from .rotation import TYPES, rotations
from .zone_randomness import load_pulls

SECTIONS = ["Lobby", "Storm", "Rotation", "Drops", "Fights and eliminations"]
MODE_SENSITIVE = {"Rotation", "Drops", "Fights and eliminations"}


def _tables(con) -> set[str]:
    return {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


@contextmanager
def _as_selection(con, cohort: str):
    """Point the shared 'sel' table at one group, so each page's own code computes its measures."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE sel AS SELECT match_id FROM {cohort}")
    try:
        yield
    finally:
        con.execute("CREATE OR REPLACE TEMP TABLE sel AS SELECT match_id FROM coh_a")


def _measures(ctx: Context) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    """Per-match measures for the current 'sel'. Returns (frame indexed by match_id, {column: (section, unit)})."""
    con, cols, parts = ctx.con, {}, []

    def add(frame: pd.DataFrame | pd.Series, section: str, unit: str, name: str | None = None):
        frame = frame.to_frame(name) if isinstance(frame, pd.Series) else frame
        for c in frame.columns:
            cols[c] = (section, unit)
        parts.append(frame)

    m = df(con, "SELECT m.* FROM matches m JOIN sel USING (match_id)").set_index("match_id")
    pl = df(con, "SELECT p.match_id, p.team_index, p.is_bot FROM players p JOIN sel USING (match_id)")
    humans = pl[~pl["is_bot"].astype(bool)]
    add(humans.groupby(["match_id", "team_index"]).size().groupby("match_id").median(), "Lobby", "n", "Players per team")
    add(m["n_humans"], "Lobby", "n", "Players per match")
    if "lobby_strength" in m and m["lobby_strength"].notna().any():
        add(m["lobby_strength"], "Lobby", "%", "Lobby strength (top-1,000 share)")

    z = load_pulls(con, "sel")
    if not z.empty:
        for ph in (2, 3, 4):
            s = z[(z["phase"] == ph) & z["u"].notna()].groupby("match_id")["u"].mean()
            if len(s):
                add(s, "Storm", "u", f"Zone {ph} pull distance (u)")
        add(z.groupby("match_id")["kind"].apply(lambda k: (k == "moving").sum()), "Storm", "n", "Moving phases")

    d, _, _ = rotations(ctx)
    if not d.empty:
        for t in TYPES:
            g = d[d["type"] == t].groupby("match_id")
            if not len(g):
                continue
            add(g["lag_first_s"].median(), "Rotation", "s", f"{t}: lag behind first in")
            add(g["storm_s"].mean(), "Rotation", "s", f"{t}: storm time")
            add(g["storm_s"].apply(lambda s: (s >= 2).mean()), "Rotation", "%", f"{t}: took storm")
            add(g["outside_m"].median(), "Rotation", "m", f"{t}: distance outside at reveal")
            add(g["depart_delay_s"].median(), "Rotation", "s", f"{t}: departure after reveal")

    if "landings" in _tables(con):
        L = df(con, "SELECT l.* FROM landings l JOIN sel USING (match_id)")
        if len(L):
            g = L.groupby("match_id")
            add(g["opp_150m"].apply(lambda s: (s > 0).mean()), "Drops", "%", "Contested landings")
            add(g["off_spawn"].mean(), "Drops", "%", "Eliminated off spawn")
            add(g["nearest_opp_m"].median(), "Drops", "m", "Nearest opponent at landing")
            add(g["early_kills"].mean(), "Drops", "n", "Early eliminations per player")
            add(g["glide_s"].median(), "Drops", "s", "Glide time")
            if L["outside_zone2_m"].notna().any():
                add(g["outside_zone2_m"].apply(lambda s: (s == 0).mean()), "Drops", "%", "Landed inside zone 2")
            if L["bus_offset_m"].notna().any():
                add(g["bus_offset_m"].median(), "Drops", "m", "Distance from bus line")

    f = _fights(ctx, True, 150)
    if not f.empty:
        decided = f[f["dz_m"].abs() > 3]
        if len(decided):
            add(decided.groupby("match_id")["dz_m"].apply(lambda s: (s > 0).mean()), "Fights and eliminations", "%",
                "Higher player wins")
        add(f.groupby("match_id")["dist_m"].median(), "Fights and eliminations", "m", "Fight distance")
    k = df(con, """
        WITH k AS (SELECT k.match_id, k.t, k.x, k.y FROM kills k JOIN sel USING (match_id)
                   WHERE k.t > 0 AND k.x IS NOT NULL AND NOT coalesce(k.downed, FALSE)),
             z AS (SELECT z.* FROM zones z JOIN sel USING (match_id) WHERE z.start_shrink_t IS NOT NULL)
        SELECT k.match_id, sqrt(power(k.x - z.next_x, 2) + power(k.y - z.next_y, 2)) / z.next_r AS d
        FROM k ASOF JOIN z ON k.match_id = z.match_id AND k.t >= z.start_shrink_t""")
    if len(k):
        add(k.groupby("match_id")["d"].apply(lambda s: (s > 1).mean()), "Fights and eliminations", "%",
            "Eliminations outside the closing circle")

    out = pd.concat(parts, axis=1) if parts else pd.DataFrame()
    return out, cols


def _fmt(v: float, unit: str) -> str:
    if v is None or not np.isfinite(v):
        return "–"
    return {"%": f"{v:.0%}", "s": f"{v:.0f} s", "m": f"{v:.0f} m", "u": f"{v:.2f}"}.get(unit, f"{v:.1f}")


@register("divergence", "Divergence report",
          "Where does selection A (for example the Global Championship) differ from selection B (for example "
          "everything else)? Every page's key measures, side by side, with a test for each.",
          params=[ALPHA_PARAM], needs_compare=True, min_matches=3)
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    con.execute("CREATE OR REPLACE TEMP TABLE coh_a AS SELECT match_id FROM sel")
    con.execute("CREATE OR REPLACE TEMP TABLE coh_b AS SELECT match_id FROM sel_b EXCEPT SELECT match_id FROM sel")
    n_a = con.execute("SELECT count(*) FROM coh_a").fetchone()[0]
    n_b = con.execute("SELECT count(*) FROM coh_b").fetchone()[0]
    overlap = con.execute("SELECT count(*) FROM sel_b JOIN sel USING (match_id)").fetchone()[0]
    if overlap:
        r.notes.append(f"{overlap} matches were in both selections; they count for A only.")
    if n_a < 3 or n_b < 3:
        r.headline = f"Each group needs at least 3 matches (A has {n_a}, B has {n_b} after removing overlap)."
        return r

    with _as_selection(con, "coh_a"):
        A, cols = _measures(ctx)
    with _as_selection(con, "coh_b"):
        B, cols_b = _measures(ctx)
    cols.update(cols_b)

    mode_differs = False
    if "Players per team" in A and "Players per team" in B:
        ta, tb = A["Players per team"].median(), B["Players per team"].median()
        mode_differs = abs(ta - tb) >= 0.5
        if mode_differs:
            r.warnings.append(f"The groups play different modes ({ta:.0f} vs {tb:.0f} players per team). Rotation, drop "
                              "and fight differences may come from the mode, not the players; storm measures don't.")

    rows, effects = [], []
    for col, (section, unit) in cols.items():
        a = A[col].dropna() if col in A else pd.Series(dtype=float)
        b = B[col].dropna() if col in B else pd.Series(dtype=float)
        if len(a) < 3 or len(b) < 3:
            continue
        u = sps.mannwhitneyu(a, b, alternative="two-sided")
        eff = 1 - 2 * u.statistic / (len(a) * len(b))      # rank-biserial: +1 = every A below every B
        eff = -eff                                           # report as +1 = A higher
        ma, mb = a.median(), b.median()
        caution = mode_differs and section in MODE_SENSITIVE
        verdict = "Divergent" if u.pvalue < alpha else ("Possible" if u.pvalue < 0.05 else "Similar")
        direction = "higher" if ma > mb else "lower"
        if section != "Lobby":
            r.test(section, col, len(a) + len(b), f"A {_fmt(ma, unit)} vs B {_fmt(mb, unit)}", u.pvalue, alpha,
                   f"A is {direction} than B" + (" (mode differs)" if caution else ""))
        rows.append(dict(Section=section, Measure=col, A=_fmt(ma, unit), B=_fmt(mb, unit),
                         **{"Effect size": round(eff, 2), "p": float(u.pvalue), "Verdict": verdict,
                            "Mode may explain it": "yes" if caution else ""}))
        if section != "Lobby":
            effects.append((col, eff, verdict))

    if not rows:
        r.headline = "Not enough matching measures in both groups to compare."
        return r
    tbl = pd.DataFrame(rows)
    tbl["Section"] = pd.Categorical(tbl["Section"], SECTIONS, ordered=True)
    tbl = tbl.sort_values(["Section", "p"])
    tbl["p"] = tbl["p"].map(lambda p: "<0.001" if p < 0.001 else f"{p:.3f}")
    r.table("All measures side by side", tbl)

    storm_div = tbl[(tbl["Section"] == "Storm") & (tbl["Verdict"] == "Divergent")]
    if len(storm_div):
        r.warnings.append("The storm itself behaves differently between the groups. Rotation and drop differences can "
                          "follow from that (for example, farther pulls mean longer rotations and more storm time) "
                          "rather than from how players play.")
    div = [e for e in effects if e[2] == "Divergent"]
    pos = [e for e in effects if e[2] == "Possible"]
    r.headline = (f"{len(div)} of {len(effects)} measures diverge clearly between A ({n_a} matches) and B ({n_b}); "
                  f"{len(pos)} more are possible differences.")
    r.metric("Matches in A", f"{n_a:,}")
    r.metric("Matches in B", f"{n_b:,}", "After removing any overlap with A")
    if "Players per team" in A and "Players per team" in B:
        r.metric("Players per team", f"A {A['Players per team'].median():.0f} · B {B['Players per team'].median():.0f}")
    if "Lobby strength (top-1,000 share)" in A and "Lobby strength (top-1,000 share)" in B:
        r.metric("Lobby strength", f"A {A['Lobby strength (top-1,000 share)'].median():.0%} · "
                                   f"B {B['Lobby strength (top-1,000 share)'].median():.0%}",
                 "Reads low for LAN events whose players used event accounts")

    top = sorted(effects, key=lambda e: -abs(e[1]))[:15]
    if top:
        r.chart("bar", "Largest differences (effect size, A vs B)",
                [dict(name="Effect size", x=[e[0] for e in top][::-1], y=[round(e[1], 2) for e in top][::-1])],
                x_label="Effect size: +1 = A always higher, −1 = A always lower", y_label="",
                horizontal=True)

    conclude(r, ctx, primary=SECTIONS[1:], alpha=alpha, recommended=10, single_season=False,
             takeaway_found=f"{len(div)} measures diverge clearly: " + "; ".join(e[0] for e in div[:6]) + ".",
             takeaway_none="No measure diverges clearly at this threshold.",
             next_found=["Open the pages behind the divergent measures and filter to each group to see the full picture.",
                         "Where 'Mode may explain it' is marked, confirm with a same-mode comparison before acting on it.",
                         "A divergence in a small group is a lead: confirm it when more matches are available."],
             next_none=["Small groups only reveal large differences: add matches to A, or relax the threshold to look for leads."])
    r.notes += [
        "Every measure is computed by the same code as its own page, once per match, then compared with a "
        "Mann-Whitney U test on the per-match values (robust to outliers and different group sizes).",
        "Effect size is the rank-biserial correlation: +1 means every A match is higher than every B match, 0 means no "
        "difference, −1 means every A match is lower.",
        "Verdicts: Divergent (p below the threshold), Possible (p < 0.05), Similar. With dozens of measures, expect a "
        "few 'Possible' results by chance.",
    ]
    return r
