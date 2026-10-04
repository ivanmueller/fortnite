"""
Field and top teams: what the whole field does, what the best teams do differently, and what your team does.

The field's behaviour is information (like the betting public's odds in Bill Benter's horse-racing model): most teams in
strong lobbies already know a lot. The edge is where the best teams consistently depart from the field, and where the
field's common choice doesn't pay.

Team: the same set of player accounts, followed across all its games. Each team counts once in every comparison, however
many games it played.
Top teams: the top TOP_SHARE by points per game among teams with MIN_GAMES+ games in the selection (the minimum steps down,
with a note, when too few teams have that many). "Top 3 in this selection" is the three teams with the most total points:
select one event to see its podium (a small sample, shown separately).

The fingerprint, per team-game (BEHAVIOURS below): contested drop, lost a player off spawn, stayed back when outside the
next zone (zones 1-3, 4-5) and sat in the storm (Storm hunt study's classes), when it left for the zone against the field
(Rotations), surged (Surge study), distance from the zone's centre in zones 4-6 and height in zones 6+ (points model
snapshots), eliminations per minute alive early (to zone 3 closing) and late (from zone 6 appearing).
Anything that only happens to teams still alive is measured over the time they were alive (a share of the surge checks they
faced; eliminations per minute alive), so it doesn't simply reward surviving.

Two checks per behaviour:
  Top vs field   top teams' values against every other team's (rank test, each team one value)
  Same team      for teams with MIN_GAMES+ games: their points per game when they did it (or did more of it, above their own
                 median) against when they didn't, averaged across teams and tested. This compares a team with itself, so
                 it isn't explained by who the team is.
Verdict: Copy this (top teams differ from the field and it helps the same team, in the same direction); Top teams' habit
(they differ, no sign it helps others: maybe skill); The field underuses / overuses it (it helps or hurts the same team and
most of the field does the opposite); No clear difference.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sps

from .. import scoring
from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register
from ._events_common import has_tables

TOP_SHARE = 0.10
MIN_GAMES = 8
MIN_TOP = 3
CLEAR = 0.05
SPOT_M = 250
# column -> (label, kind: rate | num, format, unit for differences)
BEHAVIOURS = {
    "contested": ("Landed with another team", "rate", "{:.0%}", ""),
    "off_spawn": ("Lost a player off spawn", "rate", "{:.0%}", ""),
    "stayed_13": ("Stayed back when outside the zone, zones 1–3", "rate", "{:.0%}", ""),
    "stayed_45": ("Stayed back when outside the zone, zones 4–5", "rate", "{:.0%}", ""),
    "in_storm": ("Sat in the storm when staying back, zones 1–5", "rate", "{:.0%}", ""),
    "rot_vs_field": ("Left for the zone vs the field (s; negative = earlier)", "num", "{:+.0f} s", " s"),
    "surged": ("Surged at a surge check (share of the checks alive for)", "num", "{:.0%}", ""),
    "off_centre": ("Distance from the zone's centre, zones 4–6 (zone widths)", "num", "{:.2f}", ""),
    "height": ("Height rank, zones 6+ (0 low – 1 high)", "num", "{:.2f}", ""),
    "elims_early": ("Eliminations per minute alive, to zone 3 closing", "num", "{:.2f}", ""),
    "elims_late": ("Eliminations per minute alive, from zone 6", "num", "{:.2f}", ""),
}
_SNAP: dict = {}


def _team_games(con, scheme: scoring.Scheme) -> pd.DataFrame:
    """One row per team per game: the team's identity (its accounts), names, placement and points."""
    pl = df(con, """SELECT p.match_id, p.team_index, coalesce(p.player_id, p.name) AS acct, p.name,
                           coalesce(p.team_placement, p.placement) AS placement, p.kills
                    FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE)""")
    g = pl.groupby(["match_id", "team_index"]).agg(key=("acct", lambda s: "|".join(sorted(map(str, s)))),
                                                    names=("name", lambda s: " & ".join(sorted(map(str, s)))),
                                                    placement=("placement", "min"), kills=("kills", "sum")).reset_index()
    g["points"] = scheme.total(g["placement"], g["kills"]).to_numpy(float)
    return g


def _drops(con) -> pd.DataFrame:
    if not has_tables(con, "landings"):
        return pd.DataFrame(columns=["match_id", "team_index", "contested", "off_spawn"])
    L = df(con, """SELECT l.match_id, l.team_index, l.land_x, l.land_y, l.off_spawn FROM landings l JOIN sel USING (match_id)
                   JOIN players p ON p.match_id = l.match_id AND p.id = l.id WHERE NOT coalesce(p.is_bot, FALSE)""").dropna(subset=["land_x"])
    t = L.groupby(["match_id", "team_index"]).agg(x=("land_x", "mean"), y=("land_y", "mean"),
                                                   off_spawn=("off_spawn", lambda s: float(s.fillna(False).astype(bool).any()))).reset_index()
    t["contested"] = 0.0
    for _, idx in t.groupby("match_id").indices.items():
        x, y = t["x"].to_numpy()[idx], t["y"].to_numpy()[idx]
        d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :]) / 100
        np.fill_diagonal(d, np.inf)
        t.loc[t.index[idx], "contested"] = (d.min(1) <= SPOT_M).astype(float)
    return t[["match_id", "team_index", "contested", "off_spawn"]]


def _zones_play(ctx) -> pd.DataFrame:
    from .storm_hunt import BETWEEN, STAYED, STORM, team_zones
    t = team_zones(ctx) if has_tables(ctx.con, "damage", "health") else pd.DataFrame()
    if t.empty:
        return pd.DataFrame(columns=["match_id", "team_index", "stayed_13", "stayed_45", "in_storm"])
    t = t[t["approach"] != BETWEEN]
    out = t.groupby(["match_id", "team_index"]).apply(lambda g: pd.Series({
        "stayed_13": (g.loc[g["zone"] <= 3, "approach"] == STAYED).mean() if (g["zone"] <= 3).any() else np.nan,
        "stayed_45": (g.loc[g["zone"].between(4, 5), "approach"] == STAYED).mean() if g["zone"].between(4, 5).any() else np.nan,
        "in_storm": (g.loc[g["zone"] <= 5, "detail"] == STORM).mean() if (g["zone"] <= 5).any() else np.nan,
    }), include_groups=False).reset_index()
    return out


def _rotation_timing(ctx, con) -> pd.DataFrame:
    from .rotation import rotations
    rot, _, _ = rotations(ctx)
    if rot.empty:
        return pd.DataFrame(columns=["match_id", "team_index", "rot_vs_field"])
    rot = rot[rot["phase"].between(3, 6) & rot["depart_delay_s"].notna()].copy()
    rot["vs"] = rot["depart_delay_s"] - rot.groupby(["match_id", "phase"])["depart_delay_s"].transform("median")
    ti = df(con, "SELECT p.match_id, p.id, p.team_index FROM players p JOIN sel USING (match_id)")
    rot = rot.merge(ti, on=["match_id", "id"], how="left")
    return rot.groupby(["match_id", "team_index"])["vs"].mean().rename("rot_vs_field").reset_index()


def _surged(ctx) -> pd.DataFrame:
    """Share of the surge checks a team was alive for where it was surged. A share, not 'ever surged': teams that last longer
    face more checks, so 'ever surged' would partly measure surviving."""
    from .surge_study import measured
    m = measured(ctx) if has_tables(ctx.con, "damage", "health") else None
    if m is None or not len(m["per"]):
        return pd.DataFrame(columns=["match_id", "team_index", "surged"])
    eps = m["per"].groupby(["match_id", "team_index", "episode"])["surged"].any()
    return eps.groupby(["match_id", "team_index"]).mean().astype(float).rename("surged").reset_index()


def _positions(con) -> pd.DataFrame:
    from .. import ep_model as ep
    key = tuple(sorted(df(con, "SELECT match_id FROM sel")["match_id"]))
    if key not in _SNAP:
        _SNAP.clear()
        _SNAP[key] = ep.snapshots(con)
    s = _SNAP[key]
    if s.empty:
        return pd.DataFrame(columns=["match_id", "team_index", "off_centre", "height"])
    a = s[s["zone"].between(4, 6)].groupby(["match_id", "team_index"])["off_centre"].mean()
    b = s[s["zone"] >= 6].groupby(["match_id", "team_index"])["height_rank"].mean().rename("height")
    return pd.concat([a, b], axis=1).reset_index()


def _eliminations(con) -> pd.DataFrame:
    """Eliminations per minute the team was alive, to zone 3 closing and from zone 6 appearing. Per minute alive, not a count:
    a team eliminated early has less time to get any, so a count would partly measure surviving. NaN under a minute alive."""
    if not has_tables(con, "kills"):
        return pd.DataFrame(columns=["match_id", "team_index", "elims_early", "elims_late"])
    k = df(con, """SELECT k.match_id, k.t, p.team_index FROM kills k JOIN sel USING (match_id)
                   JOIN players p ON p.match_id = k.match_id AND p.id = k.finisher_id
                   WHERE NOT coalesce(k.downed, FALSE)""")
    z = df(con, "SELECT z.match_id, z.phase, z.finish_shrink_t FROM zones z JOIN sel USING (match_id)")
    fin = {(m, int(p)): t for m, p, t in zip(z["match_id"], z["phase"], z["finish_shrink_t"])}
    end = z.groupby("match_id")["finish_shrink_t"].max().to_dict()
    teams = df(con, """SELECT p.match_id, p.team_index, max(coalesce(p.death_t, 1e9)) AS out_t, min(p.death_t IS NULL) AS any_alive_flag
                       FROM players p JOIN sel USING (match_id) WHERE NOT coalesce(p.is_bot, FALSE) GROUP BY 1, 2""")
    rows = []
    for _, q in teams.iterrows():
        m = q["match_id"]
        out_t = min(float(q["out_t"]), float(end.get(m, q["out_t"])))
        windows = {"elims_early": (0.0, fin.get((m, 3), np.nan)), "elims_late": (fin.get((m, 5), np.nan), float(end.get(m, np.nan)))}
        kk = k[(k["match_id"] == m) & (k["team_index"] == q["team_index"])]
        rec = dict(match_id=m, team_index=q["team_index"])
        for c, (a, b) in windows.items():
            alive_s = min(out_t, b) - a if a == a and b == b else np.nan
            rec[c] = (float(kk["t"].between(a, min(out_t, b)).sum()) / (alive_s / 60)) if alive_s == alive_s and alive_s >= 60 else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def fingerprints(ctx: Context, scheme: scoring.Scheme) -> pd.DataFrame:
    """One row per team-game: identity, points and every behaviour in BEHAVIOURS (NaN where it doesn't apply)."""
    con = ctx.con
    tg = _team_games(con, scheme)
    for part in (_drops(con), _zones_play(ctx), _rotation_timing(ctx, con), _surged(ctx), _positions(con), _eliminations(con)):
        if len(part):
            part = part.assign(team_index=part["team_index"].astype(tg["team_index"].dtype))
            tg = tg.merge(part, on=["match_id", "team_index"], how="left")
    for c in BEHAVIOURS:
        if c not in tg:
            tg[c] = np.nan
    return tg


def top_teams(teams: pd.DataFrame) -> tuple[pd.Index, int]:
    """The top TOP_SHARE by points per game among teams with enough games; returns (their keys, the minimum games used)."""
    need = MIN_GAMES
    while need > 2 and (teams["games"] >= need).sum() < 10:
        need -= 1
    elig = teams[teams["games"] >= need].sort_values("ppg", ascending=False)
    k = max(MIN_TOP, math.ceil(len(elig) * TOP_SHARE)) if len(elig) else 0
    return elig.index[:k], need


def same_team(tg: pd.DataFrame, col: str, keys) -> dict:
    """Points per game when a team did it (or did more of it than its own median) minus when it didn't, across teams."""
    kind = BEHAVIOURS[col][1]
    diffs = []
    for _, g in tg[tg["key"].isin(keys)].groupby("key"):
        v = g[[col, "points"]].dropna()
        if len(v) < 4:
            continue
        hi = v[col] > 0.5 if kind == "rate" else v[col] > v[col].median()
        if hi.sum() >= 2 and (~hi).sum() >= 2:
            diffs.append(v.loc[hi, "points"].mean() - v.loc[~hi, "points"].mean())
    return ttest_mean(pd.Series(diffs, dtype=float), 0.0)


def _fmt(col: str, v) -> str:
    return "–" if v is None or v != v else BEHAVIOURS[col][2].format(v)


@register("field", "Field and top teams",
          "What the whole field does, what the best teams do differently, and what your team does: drop, staying back or rotating "
          "first, surge, rotation timing, position and fights, each team counted once.",
          params=[Param("team", "Players (comma-separated)", "text", ""), ALPHA_PARAM])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    scheme = scoring.active()
    tg = fingerprints(ctx, scheme)
    teams = tg.groupby("key").agg(names=("names", "last"), games=("match_id", "nunique"), ppg=("points", "mean"),
                                  total=("points", "sum"), placement=("placement", "mean"),
                                  **{c: (c, "mean") for c in BEHAVIOURS})
    top, need = top_teams(teams)
    if len(top) < MIN_TOP:
        r.headline = (f"Too few teams with repeated games to pick top teams ({int((teams['games'] >= 3).sum())} teams have 3+ games). "
                      "Select more matches from the same events or season.")
        conclude(r, ctx, primary=[], alpha=alpha, recommended=200, descriptive=r.headline)
        return r
    podium = teams.sort_values("total", ascending=False).index[:3]
    text = str(ctx.params.get("team") or "").strip()
    you = pd.DataFrame()
    if text:
        from .audit import _find_team
        found = _find_team(con, text)
        if len(found):
            mine = set(zip(found["match_id"], found["team_index"]))
            you = tg[[(m, t) in mine for m, t in zip(tg["match_id"], tg["team_index"])]]
        else:
            r.warnings.append(f"No games for '{text}' in this selection. Check the names in Team audit.")
    elig = teams[teams["games"] >= need]
    rest = elig.index.difference(top)

    rows, gaps, chart = [], [], []
    for col, (label, kind, _fmt_, _) in BEHAVIOURS.items():
        vals = teams[col].dropna()
        if vals.empty:
            continue
        field_v, top_v = vals.mean(), teams.loc[top, col].dropna().mean()
        top3_v = teams.loc[podium, col].dropna().mean()
        you_v = you[col].dropna().mean() if len(you) else np.nan
        a, b = teams.loc[top, col].dropna(), teams.loc[rest, col].dropna()
        p_tf = float(sps.mannwhitneyu(a, b).pvalue) if len(a) >= MIN_TOP and len(b) >= 5 and pd.concat([a, b]).nunique() > 1 else np.nan
        st = same_team(tg, col, elig.index)
        p_st = st["p"] if st["p"] == st["p"] else np.nan
        top_diff = top_v - b.mean() if len(b) else np.nan
        helps = st["mean"] if st["n"] >= 5 else np.nan
        if p_tf == p_tf:
            r.test("Top vs field", f"{label}: top teams vs the rest", len(a) + len(b), f"top {_fmt(col, top_v)} vs rest {_fmt(col, b.mean())}",
                   p_tf, alpha, (f"Top teams do more of this: {label.lower()}", f"Top teams do less of this: {label.lower()}"), direction=top_diff)
        if st["n"] >= 5:
            r.test("Same team", f"{label}: points per game when the same team does it", int(st["n"]), f"{st['mean']:+.1f} points per game",
                   p_st, alpha, (f"Teams score more in games where they do this: {label.lower()}",
                                 f"Teams score less in games where they do this: {label.lower()}"), direction=st["mean"])
        tf_clear = p_tf == p_tf and p_tf < CLEAR
        st_clear = p_st == p_st and p_st < CLEAR and st["n"] >= 5
        more = "more" if top_diff > 0 else "less"
        if tf_clear and st_clear:
            verdict = f"Copy this: do {more}" if np.sign(top_diff) == np.sign(helps) else "Mixed: top teams differ the other way from what helps"
        elif tf_clear:
            verdict = f"Top teams' habit: {more} (no sign it helps others; maybe skill)"
        elif st_clear and kind == "rate" and ((helps > 0 and field_v < 0.5) or (helps < 0 and field_v > 0.5)):
            verdict = "The field underuses it" if helps > 0 else "The field overuses it"
        elif st_clear:
            verdict = "Helps the same team" if helps > 0 else "Hurts the same team"
        else:
            verdict = "No clear difference"
        rows.append({"Behaviour": label, "Field": _fmt(col, field_v), "Top teams": _fmt(col, top_v), "Top 3 in selection": _fmt(col, top3_v),
                     "You": _fmt(col, you_v) if len(you) else "–",
                     "Top vs field": "–" if p_tf != p_tf else f"p = {p_tf:.3g}",
                     "Same team, did it": "–" if st["n"] < 5 else f"{st['mean']:+.1f} pts per game (p = {p_st:.3g}, {int(st['n'])} teams)",
                     "Verdict": verdict})
        if len(you) and you_v == you_v and top_v == top_v:
            spread = vals.std() or 1.0
            gaps.append((abs(you_v - top_v) / spread, label, col, you_v, top_v, verdict))
        if kind == "rate":
            chart.append((label, field_v, top_v, you_v))
    r.table("Field, top teams and you", pd.DataFrame(rows))

    tt = teams.loc[top].sort_values("ppg", ascending=False)
    r.table("Top teams", pd.DataFrame({"Team": tt["names"], "Games": tt["games"].astype(int), "Points per game": tt["ppg"].round(1),
                                       "Average placement": tt["placement"].round(1)}))
    pd_ = teams.loc[podium]
    r.table("Top 3 in this selection", pd.DataFrame({"Team": pd_["names"], "Games": pd_["games"].astype(int), "Total points": pd_["total"].round(0),
                                                     "Points per game": pd_["ppg"].round(1)}))
    if gaps:
        gaps.sort(reverse=True)
        r.table("Where you differ most from the top teams", pd.DataFrame(
            [{"Behaviour": lab, "You": _fmt(c, yv), "Top teams": _fmt(c, tv), "Verdict": v} for _, lab, c, yv, tv, v in gaps[:6]]))
    if chart:
        names = [c[0].split(",")[0] if len(c[0]) > 40 else c[0] for c in chart]
        series = [dict(name="Field", x=names, y=[None if v != v else round(v * 100) for _, v, _, _ in chart]),
                  dict(name="Top teams", x=names, y=[None if v != v else round(v * 100) for _, _, v, _ in chart])]
        if len(you):
            series.append(dict(name="You", x=names, y=[None if v != v else round(v * 100) for _, _, _, v in chart]))
        r.chart("bar", "How often the field, the top teams and you do each", series, y_label="% of games")

    copy = [f"{x['Behaviour']} ({x['Verdict'].split(': ')[1]})" for x in rows if x["Verdict"].startswith("Copy this")]
    r.metric("Teams in the field", f"{len(teams):,}", f"Teams (the same accounts) in the selection; {len(elig)} with {need}+ games")
    r.metric("Top teams", f"{len(top)}", f"The top {TOP_SHARE:.0%} by points per game among teams with {need}+ games")
    r.metric("Copy this", f"{len(copy)}", "; ".join(copy) or "No behaviour where top teams differ and it helps the same team")
    if len(you):
        r.metric("Your games", f"{you['match_id'].nunique()}", f"{you['points'].mean():.1f} points per game")
    if need < MIN_GAMES:
        r.notes.append(f"Too few teams had {MIN_GAMES}+ games, so top teams are picked from teams with {need}+ games: noisier.")
    lead = f"Top teams ({len(top)}, by points per game over {need}+ games)"
    diff = [f"{x['Verdict'].split(': ')[1].split(' (')[0]}: {x['Behaviour'].lower()}" for x in rows
            if x["Verdict"].startswith("Copy this") or x["Verdict"].startswith("Top teams")][:3]
    r.headline = (f"{lead} differ from the field on: {'; '.join(diff)}." if diff else f"{lead} don't clearly differ from the field yet.") + \
        (f" You differ most from them on: {gaps[0][1].lower()}." if gaps else "")
    conclude(r, ctx, strategy=True, primary=["Same team", "Top vs field"], alpha=alpha, recommended=200,
             takeaway_found="; ".join(x["reading"] for x in r.tests if x["significant"]) + ".",
             takeaway_none=r.headline,
             next_found=["Start with 'Copy this': top teams differ from the field there, and the same team scores more when it does it.",
                         "'Top teams' habit' rows may be skill rather than choice: test them in scrims before making them rules."],
             next_none=["Select more matches from the same season and strong lobbies, so more teams have repeated games."])
    r.notes += [
        "Each team (the same player accounts) counts once: its value is the average over its games, so teams that played more don't "
        "outweigh the rest.",
        "Top vs field: top teams' values against every other team with enough games (rank test). Same team: a team's points per game "
        "in games where it did it (or did more of it than its own median) against games where it didn't, averaged across teams.",
        "Top 3 in this selection: the three teams with the most total points; select one event to see its podium. Three teams is a "
        "small sample: use it as a picture, not evidence.",
        "Staying back, sitting in the storm and rotation timing come from the Storm hunt and Rotations studies; surge from the Surge "
        "study; position and height from the points model's snapshots.",
    ]
    return r
