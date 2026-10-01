"""
zone_analysis.py - first research experiment: is the storm's next circle random?

Usage:
    python pipeline/python/zone_analysis.py                          # all matches in data
    python pipeline/python/zone_analysis.py --season v32.00          # one season only (recommended)
    python pipeline/python/zone_analysis.py --data-dir data_synthetic

Four questions, each with a statistical test against a "pure randomness" null:

1. DISTANCE  Is the next center uniformly placed inside the area it is allowed
             to occupy?  If so, u = (dist / (R - r))^2 ~ Uniform(0,1).
             Test: Kolmogorov-Smirnov.  Mean u near 1 = pulls hug the edge.
2. DIRECTION Do pulls favour a compass direction?  Test: Rayleigh.
3. BUS       Do pulls favour a direction relative to the bus heading?
             Test: Rayleigh on (pull angle - bus bearing).
4. PERSISTENCE  Does each pull go the same way as the previous pull?
             Test: Rayleigh on the turn angle between consecutive pulls.

A small p-value says "not random in this respect". It does not prove the effect
is exploitable or generalizes: confirm on a held-out season before trusting it.
Map geometry (water, island edge) can also create non-randomness by itself.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]  # repo root
MIN_N = 8  # smallest group we report a test for


def load(tables: Path, name: str) -> pd.DataFrame:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = tables / f"{name}{ext}"
        if p.exists():
            return reader(p)
    return pd.DataFrame()


def rayleigh(deg: np.ndarray, axial: bool = False) -> tuple[float, float, float]:
    """Returns (mean resultant length R, mean direction deg, p-value). axial=True treats theta and theta+180 as the same."""
    th = np.radians(np.asarray(deg, float))
    if axial:
        th = 2 * th
    n = len(th)
    if n == 0:
        return np.nan, np.nan, np.nan
    C, S = np.cos(th).mean(), np.sin(th).mean()
    R = float(np.hypot(C, S))
    mean_dir = float(np.degrees(np.arctan2(S, C)) % 360)
    if axial:
        mean_dir = (mean_dir / 2) % 180
    Z = n * R * R
    p = np.exp(-Z) * (1 + (2 * Z - Z ** 2) / (4 * n) - (24 * Z - 132 * Z ** 2 + 76 * Z ** 3 - 9 * Z ** 4) / (288 * n ** 2))
    return R, mean_dir, float(min(max(p, 0.0), 1.0))


def fmt_p(p: float) -> str:
    if not np.isfinite(p):
        return "-"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--season", help="only use matches from this season label, e.g. v32.00")
    ap.add_argument("--playlist-contains", help="only use playlists containing this text")
    args = ap.parse_args()

    data = Path(args.data_dir)
    tables, reports = data / "tables", data / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    z = load(tables, "zone_offsets")
    matches, bus = load(tables, "matches"), load(tables, "bus")
    if z.empty:
        raise SystemExit("zone_offsets is empty. Run flatten.py first.")

    keep = matches.copy()
    if args.season:
        keep = keep[keep.season == args.season]
    if args.playlist_contains:
        keep = keep[keep.playlist.fillna("").str.contains(args.playlist_contains, case=False)]
    z = z[z.match_id.isin(keep.match_id)].copy()
    z["u"] = z.u.clip(0, 1)
    z = z.merge(bus[["match_id", "bearing_deg", "direction_known", "bus_source"]], on="match_id", how="left")
    z["rel_bus_deg"] = (z.angle_deg - z.bearing_deg) % 360
    z = z.sort_values(["match_id", "phase"])
    z["turn_deg"] = (z.groupby("match_id").angle_deg.diff()) % 360

    n_matches = z.match_id.nunique()
    seasons = ", ".join(sorted(keep.season.dropna().astype(str).unique()))
    lines = ["# Zone analysis", "",
             f"{n_matches} matches, {len(z)} zone pulls with a known starting circle. Seasons: {seasons or 'n/a'}.", ""]
    if keep.season.nunique() > 1:
        lines += ["**Warning:** more than one season mixed. Map changes between seasons can create or hide patterns; "
                  "rerun with `--season`.", ""]
    if n_matches < 30:
        lines += [f"**Warning:** only {n_matches} matches. Treat every result below as a smoke test, not evidence. "
                  "Aim for 200+ matches from one season.", ""]

    # ---- per-phase table
    rows = []
    groups = [(f"phase {int(ph)}", g) for ph, g in z.groupby("phase")] + [("all pulls pooled*", z)]
    for label, g in groups:
        if len(g) < MIN_N and not label.startswith("all"):
            rows.append([label, len(g)] + ["-"] * 9)
            continue
        ks = stats.kstest(g.u.dropna(), "uniform") if g.u.notna().sum() >= 2 else None
        R_dir, mu_dir, p_dir = rayleigh(g.angle_deg.dropna())
        known = g[g.direction_known.fillna(False).astype(bool) & g.rel_bus_deg.notna()]
        unknown = g[~g.direction_known.fillna(False).astype(bool) & g.rel_bus_deg.notna()]
        # Bus direction known -> ordinary test; unknown -> axial test (pull along vs across the bus line).
        R_bus, mu_bus, p_bus = rayleigh(known.rel_bus_deg) if len(known) >= len(unknown) else rayleigh(unknown.rel_bus_deg, axial=True)
        R_turn, mu_turn, p_turn = rayleigh(g.turn_deg.dropna())
        rows.append([label, len(g),
                     f"{g.u.mean():.2f}", fmt_p(ks.pvalue) if ks else "-",
                     f"{mu_dir:.0f}", f"{R_dir:.2f}", fmt_p(p_dir),
                     f"{mu_bus:.0f}", fmt_p(p_bus),
                     f"{mu_turn:.0f}" if np.isfinite(mu_turn) else "-", fmt_p(p_turn)])

    # ---- match-level row: one summary per match, so observations are independent.
    def circ_mean(a):
        a = np.radians(a.dropna())
        return np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) % 360 if len(a) else np.nan
    per = z.groupby("match_id").agg(u=("u", "mean"), n_pulls=("u", "size"),
                                     ang=("angle_deg", circ_mean), rel=("rel_bus_deg", circ_mean),
                                     turn=("turn_deg", circ_mean))
    if len(per) >= 2:
        t_u = stats.ttest_1samp(per.u.dropna(), 0.5) if per.u.notna().sum() >= 2 else None
        R_d, mu_d, p_d = rayleigh(per.ang.dropna())
        R_b, mu_b, p_b = rayleigh(per.rel.dropna())
        R_t, mu_t, p_t = rayleigh(per.turn.dropna())
        rows.append(["per match (independent)", len(per), f"{per.u.mean():.2f}",
                     fmt_p(t_u.pvalue) if t_u is not None else "-", f"{mu_d:.0f}", f"{R_d:.2f}", fmt_p(p_d),
                     f"{mu_b:.0f}", fmt_p(p_b), f"{mu_t:.0f}" if np.isfinite(mu_t) else "-", fmt_p(p_t)])

    cols = ["group", "n", "mean u", "p (distance)", "mean dir", "R", "p (direction)",
            "dir vs bus", "p (bus)", "mean turn", "p (persistence)"]
    table = pd.DataFrame(rows, columns=cols)
    lines += ["Under pure randomness: mean u = 0.50, R near 0, and every p-value is spread evenly between 0 and 1.",
              "With many phases tested, only treat p < 0.005 as interesting (Bonferroni-style caution).", "",
              "| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    lines += ["", "Column guide: *mean u* near 1 means pulls hug the edge of the current circle; "
              "*R* is direction concentration (0 = none, 1 = always the same way); "
              "*dir vs bus* is the average pull direction measured from the bus heading; "
              "*mean turn* near 0 means consecutive pulls keep going the same way.", "",
              "*Pooled p-values treat every pull as independent, but pulls in the same match are linked, "
              "so they overstate significance. Trust the per-phase rows and the per-match row "
              "(one summary per match; distance uses a t-test of match-average u against 0.5).", ""]

    # ---- plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(13, 4.2))
        bins = np.linspace(0, 2 * np.pi, 25)
        for i, (col, title) in enumerate([("angle_deg", "Pull direction"),
                                          ("rel_bus_deg", "Pull vs bus heading"),
                                          ("turn_deg", "Turn vs previous pull")]):
            ax = fig.add_subplot(1, 4, i + 1, projection="polar")
            vals = np.radians(z[col].dropna())
            ax.hist(vals, bins=bins, color="#3b6ea5", edgecolor="white")
            ax.set_title(f"{title} (n={len(vals)})", fontsize=9, pad=18)
            ax.set_yticklabels([])
        ax = fig.add_subplot(1, 4, 4)
        ax.hist(z.u.dropna(), bins=np.linspace(0, 1, 11), color="#3b6ea5", edgecolor="white")
        ax.axhline(z.u.notna().sum() / 10, color="#c0392b", ls="--", lw=1, label="random")
        ax.set_title("Pull distance u (flat = random)", fontsize=9)
        ax.set_xlabel("u = (dist / allowed)^2")
        ax.legend(fontsize=8)
        fig.tight_layout()
        png = reports / "zone_analysis.png"
        fig.savefig(png, dpi=130)
        lines += [f"![zone analysis]({png.name})", ""]
    except Exception as e:  # plotting is optional
        lines += [f"(plots skipped: {e})", ""]

    out = reports / "zone_analysis.md"
    out.write_text("\n".join(lines))
    print("\n".join(lines[:6]))
    print(table.to_string(index=False))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
