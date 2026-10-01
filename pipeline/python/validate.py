"""
validate.py - automated data-quality checks on the flattened tables.

Usage:
    python pipeline/python/validate.py                      # checks data/tables, writes data/reports/validation.md
    python pipeline/python/validate.py --data-dir data_synthetic

This automates everything in the guide's Step 6 except the visual comparison
against the in-game replay viewer, which you still do by hand for ~3 matches.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # repo root

# Thresholds. Tournament (server) replays should comfortably pass these.
MIN_ZONE_PHASES = 6          # BR tournament matches normally have 8+ pulls
MIN_TRACK_COVERAGE = 0.90    # share of human players with a usable movement track
MIN_TRACK_ROWS = 10          # rows for a track to count as "usable"
MAX_MEDIAN_GAP_S = 5.0       # typical gap between position samples
MIN_BUS_POINTS = 10          # skydive starts needed to trust a derived bus line
MAX_BUS_FIT_RMS = 5000       # derived bus line must fit within ~50 m (Unreal units are cm)


def load(tables: Path, name: str) -> pd.DataFrame:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = tables / f"{name}{ext}"
        if p.exists():
            return reader(p)
    return pd.DataFrame()


def check_match(mid, m, players, zones, bus, pos) -> list[tuple[str, str, str]]:
    """Returns a list of (status, check, detail). status in PASS / WARN / FAIL."""
    out = []
    humans = players[~players.is_bot.astype(bool)]

    # Zones
    n_phases = len(zones)
    out.append(("PASS" if n_phases >= MIN_ZONE_PHASES else "WARN", "zone phases",
                f"{n_phases} phases (expected >= {MIN_ZONE_PHASES})"))
    if n_phases:
        bad = zones[(zones.next_r > zones.cur_r) | (zones.next_r <= 0)]
        out.append(("PASS" if bad.empty else "FAIL", "zone radii shrink",
                    "all phases shrink" if bad.empty else f"{len(bad)} phases grow or have zero radius"))
        known = zones.dropna(subset=["cur_x"]).sort_values("phase")
        if not known.empty:
            dist = np.hypot(known.next_x - known.cur_x, known.next_y - known.cur_y)
            moving = (dist > (known.cur_r - known.next_r) * 1.01 + 1).to_numpy()
            first_moving = int(known.phase.to_numpy()[moving.argmax()]) if moving.any() else None
            # Moving zones are normal late in a match. A moving zone followed by a shrinking one is unusual.
            odd = moving.any() and not moving[moving.argmax():].all()
            out.append(("WARN" if odd else "PASS", "shrinking vs moving zones",
                        f"{(~moving).sum()} shrinking, {moving.sum()} moving"
                        + (f" (moving from phase {first_moving})" if first_moving else "")
                        + (": a shrinking zone follows a moving one, worth a look" if odd else "")))

    # Teams and placements
    team_sizes = humans.groupby("team_index").size()
    mode_size = int(team_sizes.mode().iloc[0]) if not team_sizes.empty else 0
    out.append(("PASS", "teams", f"{len(team_sizes)} human teams, typical size {mode_size}"))

    placed = humans.dropna(subset=["placement"])
    missing_place = len(humans) - len(placed)
    inferred = int(humans["placement_inferred"].fillna(False).astype(bool).sum()) if "placement_inferred" in humans else 0
    note = f" (winner's placement filled in from the end of the match)" if inferred else ""
    out.append(("PASS" if missing_place == 0 else "WARN", "placements present",
                ("all humans have a placement" if missing_place == 0 else
                 f"{missing_place} humans missing placement (likely left early)") + note))
    if not placed.empty:
        # Disconnected players keep the placement from when they left, so only
        # connected players are expected to agree.
        connected = placed[placed.disconnected.fillna(False).astype(bool) == False]  # noqa: E712
        per_team = connected.groupby("team_index").placement.nunique()
        inconsistent = int((per_team > 1).sum())
        n_dc = len(placed) - len(connected)
        out.append(("PASS" if inconsistent == 0 else "FAIL", "placement consistent within team",
                    ("ok" if inconsistent == 0 else f"{inconsistent} teams where connected players disagree")
                    + (f" ({n_dc} disconnected players excluded)" if n_dc else "")))
        has_winner = (placed.placement == 1).any()
        out.append(("PASS" if has_winner else "WARN", "a team placed 1st", "yes" if has_winner else "no placement == 1"))

    # Movement tracks
    if pos.empty:
        out.append(("FAIL", "movement tracks", "no position rows (did you run fn-extract with --mode full?)"))
    else:
        hp = pos[pos.id.isin(humans.id)]
        rows_per = hp.groupby("id").size()
        coverage = (rows_per >= MIN_TRACK_ROWS).sum() / max(len(humans), 1)
        status = "PASS" if coverage >= MIN_TRACK_COVERAGE else "WARN"
        out.append((status, "track coverage",
                    f"{coverage:.0%} of humans have >= {MIN_TRACK_ROWS} samples"
                    + ("" if status == "PASS" else " - looks like a client replay (limited relevancy), not a server replay")))
        gaps = hp.sort_values(["id", "t"]).groupby("id").t.diff().dropna()
        if not gaps.empty:
            med, worst = gaps.median(), gaps.max()
            out.append(("PASS" if med <= MAX_MEDIAN_GAP_S else "WARN", "sample gaps",
                        f"median {med:.1f}s, longest {worst:.0f}s"))
        missing = set(humans.id) - set(hp.id)
        out.append(("PASS" if not missing else "WARN", "every human has a track",
                    "yes" if not missing else f"{len(missing)} humans with no track"))

    # Bus
    if bus.empty or bus.bus_source.iloc[0] == "none":
        out.append(("WARN", "bus path", "no bus line (aircraft missing and too few skydive starts)"))
    else:
        b = bus.iloc[0]
        if b.bus_source == "aircraft":
            out.append(("PASS", "bus path", f"exact, from the bus itself: heading {b.bearing_deg:.1f} deg"))
        else:
            ok = b.n_points >= MIN_BUS_POINTS and b.fit_rms <= MAX_BUS_FIT_RMS
            note = "" if b.get("direction_known", True) else ", direction of travel unknown (180 deg ambiguity)"
            out.append(("PASS" if ok else "WARN", "bus path",
                        f"derived from {int(b.n_points)} skydive starts, bearing {b.bearing_deg:.1f} deg, "
                        f"line fit rms {b.fit_rms / 100:.0f} m{note}"
                        + ("" if ok else ": too loose to use; bus-based tests skip this match")))

    # Season tag
    out.append(("PASS" if isinstance(m.season, str) else "WARN", "season tag", str(m.season)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    args = ap.parse_args()
    data = Path(args.data_dir)
    tables, reports = data / "tables", data / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    matches = load(tables, "matches")
    players, zones = load(tables, "players"), load(tables, "zones")
    bus, pos = load(tables, "bus"), load(tables, "positions")
    if matches.empty:
        raise SystemExit(f"No tables in {tables}. Run flatten.py first.")

    lines = ["# Validation report", ""]
    summary = []
    for _, m in matches.iterrows():
        mid = m.match_id
        res = check_match(mid, m,
                          players[players.match_id == mid],
                          zones[zones.match_id == mid] if not zones.empty else zones,
                          bus[bus.match_id == mid],
                          pos[pos.match_id == mid] if not pos.empty else pos)
        worst = "FAIL" if any(s == "FAIL" for s, _, _ in res) else "WARN" if any(s == "WARN" for s, _, _ in res) else "PASS"
        summary.append((mid, worst, m.season, m.playlist))
        lines += [f"## {mid} - {worst}", "", f"Season {m.season}, playlist `{m.playlist}`", "",
                  "| Status | Check | Detail |", "| --- | --- | --- |"]
        lines += [f"| {s} | {c} | {d} |" for s, c, d in res]
        lines.append("")

    n = len(summary)
    counts = pd.Series([s for _, s, _, _ in summary]).value_counts()
    head = [f"{n} matches: {counts.get('PASS', 0)} pass, {counts.get('WARN', 0)} warn, {counts.get('FAIL', 0)} fail.", ""]
    seasons = matches.season.value_counts()
    if len(seasons) > 1:
        head += [f"Multiple seasons present ({', '.join(seasons.index.astype(str))}). Keep the pilot to one season.", ""]
    lines[2:2] = head

    report = reports / "validation.md"
    report.write_text("\n".join(lines))
    for mid, s, season, playlist in summary:
        print(f"{s:4s}  {mid}  ({season}, {playlist})")
    print(f"\n{head[0]}\nFull report: {report}")


if __name__ == "__main__":
    main()
