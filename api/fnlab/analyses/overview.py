from __future__ import annotations

import pandas as pd

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
        share = m["is_server_replay"].fillna(False).astype(bool).mean()
        r.metric("Server replays", f"{share:.0%}", "Client replays only include nearby players")
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
    if len(m["season"].dropna().unique()) > 1:
        r.notes.append("This selection mixes seasons. Map changes between seasons can create or hide patterns, "
                       "so run zone analyses one season at a time before comparing.")
    return r
