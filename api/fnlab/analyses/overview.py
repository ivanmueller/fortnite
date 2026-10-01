from __future__ import annotations

import pandas as pd

from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, register


@register("overview", "Dataset overview",
          "What's in the current selection: matches over time, seasons, regions and data quality.")
def run(ctx: Context) -> Result:
    r = Result()
    m = df(ctx.con, """
        SELECT m.*, TRY_CAST(match_date AS DATE) AS d FROM matches m JOIN sel USING (match_id)
    """)
    if m.empty:
        r.headline = "No matches in this selection."
        return r

    d = pd.to_datetime(m["d"])
    span_days = (d.max() - d.min()).days + 1 if d.notna().any() else 0
    r.headline = (f"{len(m):,} matches from {d.min():%b %d, %Y} to {d.max():%b %d, %Y}"
                  if d.notna().any() else f"{len(m):,} matches")
    r.metric("Matches", f"{len(m):,}")
    r.metric("Days covered", f"{span_days:,}")
    r.metric("Seasons", m["season"].nunique())
    r.metric("Regions", m["region"].nunique())
    if "is_server_replay" in m:
        known = pd.to_numeric(m["is_server_replay"].astype("object"), errors="coerce").dropna()
        r.metric("Server replays", f"{known.astype(bool).mean():.0%}" if len(known) else "Unknown",
                 "Client replays only include nearby players" if len(known) else
                 "Not recorded for these matches (they weren't in data/match_ids.csv when processed)")
    r.metric("Median players", f"{m['n_humans'].median():.0f}")

    m["week"] = d.dt.to_period("W-SUN").dt.start_time
    weekly = m.groupby(["week", "season"]).size().unstack(fill_value=0).sort_index()
    r.chart("stacked_bar", "Matches per week",
            [dict(name=str(s), x=[w.date().isoformat() for w in weekly.index], y=weekly[s].tolist())
             for s in weekly.columns],
            x_label="Week starting", y_label="Matches")

    reg = m.groupby("region").size().sort_values(ascending=False)
    r.chart("bar", "Matches by region", [dict(name="Matches", x=reg.index.astype(str).tolist(), y=reg.tolist())],
            y_label="Matches")

    zph = df(ctx.con, "SELECT match_id, count(*) n FROM zones JOIN sel USING (match_id) GROUP BY 1")
    if not zph.empty:
        counts = zph["n"].value_counts().sort_index()
        r.chart("bar", "Storm phases recorded per match",
                [dict(name="Matches", x=counts.index.astype(str).tolist(), y=counts.tolist())],
                x_label="Phases", y_label="Matches")

    by = (m.groupby(["season", "region"]).size().unstack(fill_value=0)
          .reset_index().rename(columns={"season": "Season"}))
    r.table("Matches by season and region", by)
    per_season = m.groupby("season").size().sort_values(ascending=False)
    top, top_n = (per_season.index[0], int(per_season.iloc[0])) if len(per_season) else ("–", 0)
    ready = (per_season >= 200).sum()
    conclude(r, ctx, primary=[], alpha=0.005, recommended=200, single_season=False,
             descriptive=(f"The largest season, {top}, has {top_n:,} matches. "
                          + (f"{ready} season(s) reach the 200 matches recommended for zone tests."
                             if ready else "No season yet reaches the 200 matches recommended for zone tests; "
                             "treat test results as early signals.")),
             next_none=[f"Filter to {top} and open Is the storm random?" if top_n else "Add matches with the pipeline.",
                        "Check Storm phases recorded per match: complete matches should all show the same high count."])
    if len(m["season"].dropna().unique()) > 1:
        r.notes.append("This selection mixes seasons. Map changes between seasons can create or hide patterns, "
                       "so run zone analyses one season at a time before comparing.")
    return r
