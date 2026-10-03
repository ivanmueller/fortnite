"""
Game plan: one team's pre-tournament plan, on one page, built only from matches played before the event.

Inputs: the team's names, their drop spot (blank = the spot they used most), the event's scoring scheme, and the
plan's "as of" date (the event's first day). The left panel's selection is the pool: matches before the as-of date
are the history the plan is built from; matches on or after it are the event the plan is checked against. With no
as-of date, the whole selection is history and there's nothing to check.

Blocks, in game order:
  Drop        how often the spot is contested, by how close the bus passes; who lands there and how those
              off-spawns went; the team's own record; backup spots (when the data has named places)
  Zones 1-2   how often the spot is inside zones 1 and 2, how far zone 2 is when it isn't, and which way it pulls
  Rotations   each zone's wait before the storm moves, the measured rotation speed, and the latest time to leave
              from each distance to be inside before it moves; checked against storm taken by players who left later
  Surge       the measured surge rule (Surge study) and the score that kept teams safe at each zone's first check
  Habits      Playbook rules with same-player evidence, ranked by how often this team broke them
  Points      the event's exchange rate between places and eliminations, and what a zero-point game costs this team
  Check       with event matches: what the plan said against what happened, game by game
Every number carries its sample size; numbers from fewer than THIN games are left out rather than shown with a caveat.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from .. import scoring
from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import Context, Param, register
from .audit import _find_team, _team_list

SOLID, THIN = 30, 10            # games behind a number: solid / thin; below THIN the number is left out
SPOT_R_M = 250                  # without named places, landing within this of the spot's centre counts as landing there
BUS_BANDS = [(0, 400, "Bus passes over it (under 400 m)"), (400, 900, "Bus passes near it (400–900 m)"),
             (900, math.inf, "Bus passes far from it (900 m+)")]
DISTANCES_M = [100, 200, 300, 400, 500]
BACKUPS = 5
REGULAR = 3                     # games landed at the spot to count as a regular there
BLOCKS = ["Drop", "Zones 1–2", "Rotations", "Surge", "Habits", "Points", "Check"]


# ---------------------------------------------------------------- helpers

def _conf(n: int) -> str:
    return "Solid" if n >= SOLID else "Thin" if n >= THIN else "Too few"


def _pct(v: float, n: int) -> str:
    return "–" if n < THIN or v != v else f"{v:.0%}"


def _num(v: float, n: int, fmt: str = "{:.0f}") -> str:
    return "–" if n < THIN or v != v else fmt.format(v)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _parse_date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _use(con, table: str) -> int:
    con.execute(f"CREATE OR REPLACE TEMP TABLE sel AS SELECT match_id FROM {table}")
    return con.execute("SELECT count(*) FROM sel").fetchone()[0]


def _split(con, as_of: date | None) -> tuple[int, int]:
    """gp_hist: matches before the as-of date; gp_event: on or after it. Returns (history, event) counts."""
    con.execute("CREATE OR REPLACE TEMP TABLE gp_all AS SELECT match_id FROM sel")
    if as_of is None:
        con.execute("CREATE OR REPLACE TEMP TABLE gp_hist AS SELECT match_id FROM gp_all")
        con.execute("CREATE OR REPLACE TEMP TABLE gp_event AS SELECT match_id FROM gp_all WHERE FALSE")
    else:
        for name, op in (("gp_hist", "<"), ("gp_event", ">=")):
            con.execute(f"""CREATE OR REPLACE TEMP TABLE {name} AS SELECT a.match_id FROM gp_all a JOIN matches m USING (match_id)
                            WHERE TRY_CAST(m.match_date AS DATE) {op} ?""", [as_of])
    count = lambda t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
    return count("gp_hist"), count("gp_event")


def _has(con, table: str) -> bool:
    return table in {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _team_points(con, scheme: scoring.Scheme) -> pd.DataFrame:
    """Placement, eliminations and points for every team-game in the selection."""
    pl = df(con, """SELECT pl.match_id, pl.team_index, coalesce(pl.team_placement, pl.placement) AS placement, pl.kills
                    FROM players pl JOIN sel USING (match_id) WHERE NOT coalesce(pl.is_bot, FALSE)""")
    t = pl.groupby(["match_id", "team_index"]).agg(placement=("placement", "min"), kills=("kills", "sum")).reset_index()
    t["points"] = scheme.total(t["placement"], t["kills"]).to_numpy(float)
    return t


def _landings(con) -> pd.DataFrame:
    if not _has(con, "landings"):
        return pd.DataFrame()
    return df(con, """SELECT l.match_id, l.id, l.team_index, l.land_x, l.land_y, l.poi, l.off_spawn, l.outside_zone1_m,
                             p.name, p.player_id, p.pr_rank
                      FROM landings l JOIN sel USING (match_id)
                      JOIN players p ON p.match_id = l.match_id AND p.id = l.id
                      WHERE NOT coalesce(p.is_bot, FALSE)""").dropna(subset=["land_x", "land_y"])


class Spot:
    """A drop spot: a named place, or (without named places) a circle around where the team usually lands."""

    def __init__(self, name: str, poi: str | None, cx: float, cy: float):
        self.name, self.poi, self.cx, self.cy = name, poi, cx, cy

    def at(self, L: pd.DataFrame) -> pd.Series:
        if self.poi is not None:
            return L["poi"] == self.poi
        return np.hypot(L["land_x"] - self.cx, L["land_y"] - self.cy) <= SPOT_R_M * 100


def _choose_spot(L: pd.DataFrame, mine: pd.DataFrame, typed: str, warnings: list[str]) -> Spot | None:
    has_poi = L["poi"].notna().any()
    if typed and has_poi:
        names = L["poi"].dropna()
        hits = names[names.str.lower().str.contains(typed.lower(), regex=False)]
        if len(hits):
            poi = hits.value_counts().index[0]
            s = L[L["poi"] == poi]
            return Spot(poi, poi, float(s["land_x"].median()), float(s["land_y"].median()))
        warnings.append(f"No drop spot matches '{typed}'. Using the team's usual spot instead.")
    elif typed:
        warnings.append("This data has no named places, so the drop spot is the area where the team usually lands.")
    if mine.empty:
        return None
    if has_poi and mine["poi"].notna().any():
        poi = mine["poi"].mode().iat[0]
        s = L[L["poi"] == poi]
        return Spot(poi, poi, float(s["land_x"].median()), float(s["land_y"].median()))
    cx, cy = float(mine["land_x"].median()), float(mine["land_y"].median())
    return Spot(f"Their usual landing area (x {cx / 100:.0f} m, y {cy / 100:.0f} m)", None, cx, cy)


def _bus_band(con, spot: Spot) -> pd.Series:
    """Per match: how close the bus line passes to the spot, as a band label."""
    if not _has(con, "bus"):
        return pd.Series(dtype=object)
    b = df(con, "SELECT b.match_id, b.start_x, b.start_y, b.end_x, b.end_y FROM bus b JOIN sel USING (match_id)").dropna()
    if b.empty:
        return pd.Series(dtype=object)
    dx, dy = b["end_x"] - b["start_x"], b["end_y"] - b["start_y"]
    length = np.hypot(dx, dy).replace(0, np.nan)
    dist_m = (np.abs((spot.cx - b["start_x"]) * dy - (spot.cy - b["start_y"]) * dx) / length / 100)
    labels = pd.Series(None, index=b.index, dtype=object)
    for lo, hi, lab in BUS_BANDS:
        labels[(dist_m >= lo) & (dist_m < hi)] = lab
    return pd.Series(labels.to_numpy(), index=b["match_id"].to_numpy()).dropna()


def _games_at_spot(L: pd.DataFrame, spot: Spot, team: pd.DataFrame, matches: list[str], points: pd.DataFrame) -> pd.DataFrame:
    """One row per match: other teams landing at the spot, and the team's own landing, off-spawn and points."""
    at = L[spot.at(L)]
    tid = dict(zip(team["match_id"], team["team_index"])) if len(team) else {}
    pts = points.set_index(["match_id", "team_index"])["points"]
    rows = []
    for m in matches:
        a = at[at["match_id"] == m]
        teams_there = set(a["team_index"].dropna())
        me = tid.get(m)
        others = teams_there - ({me} if me is not None else set())
        mine = a[a["team_index"] == me] if me is not None else a.iloc[0:0]
        rows.append(dict(match_id=m, others=len(others), played=me is not None, there=len(mine) > 0,
                         out_off_spawn=bool(len(mine)) and bool(mine["off_spawn"].fillna(False).astype(bool).all()),
                         points=float(pts.get((m, me), np.nan)) if me is not None else np.nan))
    return pd.DataFrame(rows)


def _zone_circles(con, phases=(1, 2)) -> pd.DataFrame:
    z = df(con, f"""SELECT z.match_id, z.phase, z.next_x, z.next_y, z.next_r FROM zones z JOIN sel USING (match_id)
                    WHERE z.phase IN ({', '.join(str(p) for p in phases)})""")
    return z.dropna().drop_duplicates(["match_id", "phase"])


def _spot_vs_zones(con, spot: Spot) -> pd.DataFrame:
    """Per match: is the spot inside zone 1 and zone 2, how far outside zone 2, and which way zone 2 lies."""
    z = _zone_circles(con)
    if z.empty:
        return pd.DataFrame()
    w = z.pivot(index="match_id", columns="phase", values=["next_x", "next_y", "next_r"])
    out = pd.DataFrame(index=w.index)
    for k in (1, 2):
        if ("next_x", k) in w:
            d = np.hypot(w[("next_x", k)] - spot.cx, w[("next_y", k)] - spot.cy)
            out[f"in{k}"] = d <= w[("next_r", k)]
            out[f"out{k}_m"] = ((d - w[("next_r", k)]) / 100).clip(lower=0)
    if ("next_x", 2) in w:
        vx, vy = w[("next_x", 2)] - spot.cx, w[("next_y", 2)] - spot.cy           # toward zone 2
        cx, cy = -spot.cx, -spot.cy                                                # toward the island's centre (0, 0)
        cos = (vx * cx + vy * cy) / (np.hypot(vx, vy) * max(math.hypot(cx, cy), 1e-9)).replace(0, np.nan)
        out["way"] = np.select([cos >= 0.5, cos <= -0.5], ["toward the map centre", "away from the map centre"], "sideways")
    return out.reset_index()


# ---------------------------------------------------------------- blocks

def _drop_block(r, con, L, spot, team, scheme, band, points, n_hist):
    matches = sorted(df(con, "SELECT match_id FROM sel")["match_id"])
    g = _games_at_spot(L, spot, team, matches, points)
    g["band"] = g["match_id"].map(band).fillna("Bus route unknown")
    rows, own = [], []
    for label, q in [("All bus routes", g)] + [(lab, g[g["band"] == lab]) for _, _, lab in BUS_BANDS if (g["band"] == lab).any()]:
        n, mine = len(q), q[q["there"]]
        rows.append({"Bus route": label, "Games": n, "Other teams there": _num(q["others"].mean(), n, "{:.1f}"),
                     "Contested": _pct((q["others"] > 0).mean(), n), "Two or more teams": _pct((q["others"] >= 2).mean(), n),
                     "Confidence": _conf(n)})
        own.append({"Bus route": label, "Your games there": len(mine), "Contested": _pct((mine["others"] > 0).mean(), len(mine)),
                    "Out off spawn": _pct(mine["out_off_spawn"].mean(), len(mine)),
                    "Points per game": _num(mine["points"].mean(), len(mine), "{:.1f}"), "Confidence": _conf(len(mine))})
    r.table("Drop: how contested your spot is", pd.DataFrame(rows))
    if any(o["Your games there"] for o in own):
        r.table("Drop: your record there", pd.DataFrame(own))

    # who lands there, and how off-spawns against them went
    at = L[spot.at(L)]
    tid = dict(zip(team["match_id"], team["team_index"])) if len(team) else {}
    ids = set(zip(team["match_id"], team["id"])) if len(team) else set()
    others = at[[(m, i) not in ids for m, i in zip(at["match_id"], at["id"])]]
    you_out = g.set_index("match_id")["out_off_spawn"]
    there = set(g.loc[g["there"], "match_id"])
    reg = []
    for pid, q in others.groupby("player_id"):
        shared = q[q["match_id"].isin(there)]
        reg.append({"Player": q["name"].dropna().iloc[-1] if q["name"].notna().any() else str(pid)[:8], "Games landed there": q["match_id"].nunique(),
                    "Share of games": f"{q['match_id'].nunique() / max(len(g), 1):.0%}",
                    "Power Rankings": f"{q['pr_rank'].dropna().iloc[-1]:,.0f}" if q["pr_rank"].notna().any() else "Unranked",
                    "Games there with you": shared["match_id"].nunique(),
                    "You out off spawn": int(sum(bool(you_out.get(m, False)) for m in shared["match_id"].unique())),
                    "They out off spawn": int(shared.groupby("match_id")["off_spawn"].apply(lambda s: bool(s.fillna(False).astype(bool).any())).sum())})
    regs = pd.DataFrame(reg)
    if len(regs):
        regs = regs[regs["Games landed there"] >= REGULAR].sort_values(["Games landed there", "Games there with you"], ascending=False).head(8)
    if len(regs):
        r.table("Drop: who lands there", regs)
    else:
        r.notes.append(f"Drop: no other player landed at the spot in {REGULAR}+ of these games, so there are no regulars to plan around.")

    # backups: named places that are less contested and score better
    backup_names = []
    if L["poi"].notna().any():
        tl = L.dropna(subset=["poi"]).groupby(["match_id", "poi", "team_index"]).agg(x=("land_x", "median"), y=("land_y", "median")).reset_index()
        tl["teams_there"] = tl.groupby(["match_id", "poi"])["team_index"].transform("nunique")
        tl = tl.merge(points, on=["match_id", "team_index"], how="left")
        s = tl.groupby("poi").agg(team_games=("team_index", "size"), contested=("teams_there", lambda v: (v > 1).mean()),
                                  points=("points", "mean"), x=("x", "median"), y=("y", "median"))
        s = s[(s["team_games"] >= THIN) & (s.index != spot.poi)]
        mine_c = (g["others"] > 0).mean()
        s = s[s["contested"] < mine_c].sort_values("points", ascending=False).head(BACKUPS)
        if len(s):
            r.table("Drop: backup spots", pd.DataFrame({
                "Spot": s.index, "Team-games": s["team_games"].astype(int), "Contested": [f"{v:.0%}" for v in s["contested"]],
                "Points per game": s["points"].round(1), "From your spot": [f"{math.hypot(x - spot.cx, y - spot.cy) / 100000:.1f} km" for x, y in zip(s["x"], s["y"])],
                "Confidence": [_conf(int(n)) for n in s["team_games"]]}))
            backup_names = list(s.index[:2])

    contested = (g["others"] > 0).mean()
    call = f"{spot.name}: contested in {_pct(contested, len(g))} of {len(g)} games"
    by_band = [(lab, (q["others"] > 0).mean(), len(q)) for _, _, lab in BUS_BANDS for q in [g[g["band"] == lab]] if len(q) >= THIN]
    if len(by_band) >= 2:
        hi = max(by_band, key=lambda t: t[1])
        call += f", most when the {hi[0][0].lower()}{hi[0][1:].split(' (')[0]} ({hi[1]:.0%})"
    if len(regs):
        call += f". Regulars there: {', '.join(f'{p} ({n})' for p, n in zip(regs['Player'].head(2), regs['Games landed there'].head(2)))}"
    if backup_names:
        call += f". Backup: {backup_names[0]}"
    return call + ".", dict(contested=contested, n=len(g))


def _zones_block(r, con, spot):
    z = _spot_vs_zones(con, spot)
    if z.empty or "in1" not in z:
        return None, {}
    n = len(z)
    out2 = z[~z["in2"]] if "in2" in z else z.iloc[0:0]
    rows = [{"": "Spot inside zone 1", "Share of games": _pct(z["in1"].mean(), n), "Games": n, "Confidence": _conf(n)}]
    if "in2" in z:
        rows.append({"": "Spot inside zone 2", "Share of games": _pct(z["in2"].mean(), n), "Games": n, "Confidence": _conf(n)})
        rows.append({"": "Zone 2 distance when outside (median)", "Share of games": _num(out2["out2_m"].median(), len(out2), "{:.0f} m"),
                     "Games": len(out2), "Confidence": _conf(len(out2))})
        if "way" in out2:
            for way in ("toward the map centre", "sideways", "away from the map centre"):
                rows.append({"": f"Zone 2 lies {way}", "Share of games": _pct((out2["way"] == way).mean(), len(out2)),
                             "Games": len(out2), "Confidence": _conf(len(out2))})
    r.table("Zones 1–2 from your spot", pd.DataFrame(rows))
    call = f"Inside zone 1 in {_pct(z['in1'].mean(), n)}"
    if "in2" in z:
        call += f" and zone 2 in {_pct(z['in2'].mean(), n)} of games"
        if len(out2) >= THIN:
            way = out2["way"].mode().iat[0] if "way" in out2 else ""
            call += f"; when outside, zone 2 is a median {out2['out2_m'].median():.0f} m away, most often {way}"
    return call + ".", dict(in1=float(z["in1"].mean()), in2=float(z["in2"].mean()) if "in2" in z else np.nan, n=n)


def _rotation_block(r, ctx, team):
    from .rotation import rotations
    rot, ctxt, _ = rotations(ctx)
    if rot.empty or ctxt.empty:
        return None, {}
    done = rot[rot["entry_t"].notna() & rot["depart_t"].notna() & (rot["entry_t"] > rot["depart_t"] + 1) & (rot["outside_m"] >= 50)]
    if len(done) < THIN:
        return None, {}
    speed = float((done["outside_m"] / (done["entry_t"] - done["depart_t"])).clip(1, 15).median())
    waits = ctxt.groupby("phase").agg(wait=("wait_s", "median"), games=("match_id", "nunique"))
    rows, rules = [], {}
    ids = set(zip(team["match_id"], team["id"])) if len(team) else set()
    mine_late, mine_n = 0, 0
    for k, w in waits.iterrows():
        if w["games"] < THIN:
            continue
        k = int(k)
        if w["wait"] < 5:
            rows.append({"Zone": k, "Storm waits": "Moving: no wait", **{f"{d} m out": "Go now" for d in DISTANCES_M},
                         "Storm if left later": "–", "Storm if left in time": "–", "Confidence": _conf(int(w["games"]))})
            continue
        rules[k] = float(w["wait"])
        q = rot[(rot["phase"] == k) & rot["depart_delay_s"].notna()]
        late = q["depart_delay_s"] > (w["wait"] - q["outside_m"] / speed)
        storm = q["storm_s"].fillna(0) > 0
        mine = [(m, i) in ids for m, i in zip(q["match_id"], q["id"])]
        mine_late += int(late[mine].sum())
        mine_n += int(sum(mine))
        cell = {}
        for d in DISTANCES_M:
            t = w["wait"] - d / speed
            cell[f"{d} m out"] = f"{t:.0f} s" if t >= 0 else "Go now"
        rows.append({"Zone": k, "Storm waits": f"{w['wait']:.0f} s", **cell,
                     "Storm if left later": _pct(storm[late].mean(), int(late.sum())),
                     "Storm if left in time": _pct(storm[~late].mean(), int((~late).sum())),
                     "Confidence": _conf(int(w["games"]))})
    if not rows:
        return None, {}
    r.table("Rotations: latest time to leave", pd.DataFrame(rows))
    r.notes.append(f"Rotations: the latest time to leave, in seconds after the zone appears, to be inside before the storm starts "
                   f"moving, at the measured rotation speed of {speed:.1f} m/s (players who rotated in, including stops and building). "
                   "'Go now' means you'd already be late.")
    ref = 300
    k0 = next((k for k in sorted(rules) if k >= 3), next(iter(sorted(rules)), None))
    call = f"Rotating at {speed:.1f} m/s"
    if k0 is not None:
        t = rules[k0] - ref / speed
        call += f", leave zone {k0} by {t:.0f} s after it appears from {ref} m out" if t >= 0 else f", from {ref} m out in zone {k0} leave as soon as it appears"
    if mine_n >= THIN:
        call += f". You left later than this in {mine_late / mine_n:.0%} of {mine_n} rotations"
    return call + ".", dict(speed=speed, waits=rules, late_share=mine_late / mine_n if mine_n else np.nan, late_n=mine_n)


def _surge_block(r, ctx, team):
    from .surge_study import measured
    m = measured(ctx)
    if m is None or not len(m["per"]):
        return None, {}
    ep, per = m["ep"].dropna(subset=["cutoff"]), m["per"]
    first = ep.sort_values("t0").drop_duplicates(["match_id", "zone"])
    tid = dict(zip(team["match_id"], team["team_index"])) if len(team) else {}
    rows, lines = [], {}
    for k, q in first.groupby("zone"):
        n = len(q)
        pz = per[per["episode"].isin(q["episode"])]
        mine = pz[[tid.get(mm) == ti for mm, ti in zip(pz["match_id"], pz["team_index"])]]
        mine_eps = mine.groupby("episode")["surged"].any()
        p80 = float(q["cutoff"].quantile(0.8))
        if n >= THIN:
            lines[int(k)] = (p80, float(q["alive"].median()))
        rows.append({"Zone": int(k), "Players alive": _num(q["alive"].median(), n),
                     "Safe half the time above": _num(q["cutoff"].median(), n), "Safe 80% of the time above": _num(p80, n),
                     "You were surged": f"{int(mine_eps.sum())} of {len(mine_eps)}" if len(mine_eps) else "–",
                     "Games": n, "Confidence": _conf(n)})
    if not rows:
        return None, {}
    r.table("Surge: the line to stay above", pd.DataFrame(rows))
    rule = m["label"]
    r.notes.append(f"Surge: the score is {rule}, the rule the Surge study measured. 'Safe 80% of the time above' is the score that "
                   "kept teams safe at the first surge check of that zone in 80% of games.")
    call = f"Surge counts {rule}"
    if lines:
        k = min(lines)
        call += f". By the first check in zone {k} (about {lines[k][1]:.0f} players alive), stay above {lines[k][0]:.0f}".replace("-", "−")
    return call + ".", dict(lines=lines, rule=rule)


def _habits_block(r, ctx, team):
    from . import playbook as pb
    key = ("pb",) + tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"]))
    a = pb._CACHE.get(key)
    if a is None:
        a = pb.applications(ctx)
        pb._CACHE.clear()
        pb._CACHE[key] = a
    if a.empty:
        return None, {}
    ids = set(zip(team["match_id"], team["id"])) if len(team) else set()
    rows = []
    for rule, (label, when) in pb.RULES.items():
        x = a[a["rule"] == rule]
        if len(x) < SOLID or x["followed"].all() or not x["followed"].any():
            continue
        within = x.groupby("id").apply(lambda q: q.loc[q["followed"], "ahead"].mean() - q.loc[~q["followed"], "ahead"].mean()
                                       if q["followed"].sum() >= 2 and (~q["followed"]).sum() >= 2 else np.nan, include_groups=False).dropna()
        tw = ttest_mean(within, 0.0)
        gain = -tw["mean"] * 100 if tw["n"] >= 5 else np.nan             # rank points: positive = finished ahead of more rivals
        backed = tw["n"] >= 5 and tw["p"] < 0.05 and gain > 0
        mine = x[[(m, i) in ids for m, i in zip(x["match_id"], x["id"])]]
        broke = 1 - mine["followed"].mean() if len(mine) else np.nan
        rows.append(dict(rule=f"{label} ({when.lower()})", gain=gain, p=tw["p"], backed=backed, broke=broke, n_mine=len(mine), n=int(tw["n"])))
    if not rows:
        return None, {}
    h = pd.DataFrame(rows)
    h["priority"] = np.where(h["backed"], h["gain"] * h["broke"].fillna(0), -1)
    h = h.sort_values(["priority", "gain"], ascending=False)
    r.table("Habits: what to fix first", pd.DataFrame({
        "Habit": h["rule"], "Evidence": ["Backed by the data" if b else "Not proven" for b in h["backed"]],
        "Worth, same players": [f"+{g:.0f} rank points" if g == g and g > 0 else (f"{g:.0f} rank points" if g == g else "–") for g in h["gain"]],
        "You broke it": [_pct(b, n) for b, n in zip(h["broke"], h["n_mine"])], "Your moments": h["n_mine"].astype(int),
        "Players compared": h["n"].astype(int)}))
    r.notes.append("Habits: 'Worth' is how many more rivals (out of 100) the same players finished ahead of when they followed the rule "
                   "than when they didn't, so it isn't explained by who the players are. Only rules backed by the data are ranked; the "
                   "first is the one this team breaks most, weighted by what it's worth.")
    top = h[h["backed"] & (h["n_mine"] >= THIN)]
    if top.empty:
        return "No habit is both backed by the data and broken often enough by this team to rank yet.", {}
    t = top.iloc[0]
    return f"Fix first: {t['rule']}. You broke it in {t['broke']:.0%} of {int(t['n_mine'])} moments; worth +{t['gain']:.0f} rank points.", {}


def _points_block(r, scheme: scoring.Scheme, team_pts: pd.Series):
    """The exchange rate: for each run of places, how much one place up is worth, and one elimination in places."""
    tbl = scheme.table
    segs: list[list] = []                       # [first place, last place, points over the place below]
    for p in range(1, scheme.paid_places + 1):
        step = tbl.get(p, 0) - tbl.get(p + 1, 0)
        if segs and segs[-1][2] == step:
            segs[-1][1] = p
        else:
            segs.append([p, p, step])
    rows = [{"Places": _ordinal(a) if a == b else f"{_ordinal(a)}–{_ordinal(b)}",
             "Each is worth over the place below": f"{s_:g}",
             "One elimination equals": f"{scheme.elimination / s_:.1f} places"} for a, b, s_ in segs if s_ > 0]
    rows.append({"Places": f"Below {_ordinal(scheme.paid_places)}", "Each is worth over the place below": "0",
                 "One elimination equals": "Eliminations are the only points"})
    r.table("Points: places against eliminations", pd.DataFrame(rows))
    a, b, s_ = max((sg for sg in segs if sg[2] > 0), key=lambda sg: sg[1] - sg[0])
    call = (f"From {_ordinal(a)} to {_ordinal(b)} each place is worth {s_:g}, so one elimination equals "
            f"{scheme.elimination / s_:.1f} places; below {_ordinal(scheme.paid_places)} only eliminations score")
    if len(team_pts) >= THIN:
        zeros = int((team_pts == 0).sum())
        call += (f". A zero-point game costs your average of {team_pts.mean():.1f} points; "
                 + (f"you had {zeros} in {len(team_pts)} games" if zeros else f"you had none in {len(team_pts)} games"))
    return call + "."


def _check_block(r, ctx, con, spot, team_e, scheme, plan):
    """The event, game by game, against what the plan said."""
    L = _landings(con)
    matches = sorted(df(con, "SELECT match_id FROM sel")["match_id"])
    points = _team_points(con, scheme)
    g = _games_at_spot(L, spot, team_e, matches, points) if len(L) else pd.DataFrame({"match_id": matches})
    z = _spot_vs_zones(con, spot)
    g = g.merge(z, on="match_id", how="left") if len(z) else g
    order = df(con, "SELECT m.match_id, m.replay_timestamp, m.match_date FROM matches m JOIN sel USING (match_id)")
    order["start"] = pd.to_datetime(order["replay_timestamp"].astype(str), errors="coerce").fillna(pd.to_datetime(order["match_date"].astype(str), errors="coerce"))
    gnum = dict(zip(order.sort_values(["start", "match_id"])["match_id"], range(1, len(order) + 1)))
    tid = dict(zip(team_e["match_id"], team_e["team_index"]))
    played = g[g["match_id"].isin(tid)].copy()
    # surge at first checks, scored on the plan's line
    surged = {}
    sp = plan.get("surge", {})
    if sp.get("lines"):
        from .surge_study import measured
        m = measured(ctx)
        if m is not None and len(m["per"]):
            ep = m["ep"].sort_values("t0").drop_duplicates(["match_id", "zone"])
            per = m["per"][m["per"]["episode"].isin(ep["episode"])]
            for mm, q in per.groupby("match_id"):
                q = q[q["team_index"] == tid.get(mm)]
                if len(q):
                    surged[mm] = int(q.groupby("episode")["surged"].any().sum())
    # rotations: left later than the plan's latest time
    late = {}
    rp = plan.get("rotations", {})
    if rp.get("speed"):
        from .rotation import rotations
        rot, _, _ = rotations(ctx)
        ids = set(zip(team_e["match_id"], team_e["id"]))
        if len(rot):
            keep = np.array([(mm, i) in ids for mm, i in zip(rot["match_id"], rot["id"])], bool) & rot["phase"].isin(list(rp["waits"])).to_numpy()
            rot = rot[keep]
        for mm, q in (rot.groupby("match_id") if len(rot) else []):
            lim = q["phase"].map(rp["waits"]) - q["outside_m"] / rp["speed"]
            late[mm] = (int((q["depart_delay_s"] > lim).sum()), int(q["storm_s"].fillna(0).gt(0).sum()), len(q))
    rows = []
    for _, q in played.sort_values("match_id", key=lambda s: s.map(gnum)).iterrows():
        lt = late.get(q["match_id"])
        rows.append({"Game": gnum.get(q["match_id"]), "At the spot": "Yes" if q.get("there") else "No",
                     "Contested": ("Yes" if q.get("others", 0) > 0 else "No") if q.get("there") else "–",
                     "Spot in zone 1": "Yes" if q.get("in1") is True else ("No" if q.get("in1") is False else "–"),
                     "Spot in zone 2": "Yes" if q.get("in2") is True else ("No" if q.get("in2") is False else "–"),
                     "Surged": str(surged.get(q["match_id"], 0)) if surged else "–",
                     "Left late (took storm)": f"{lt[0]} ({lt[1]})" if lt else "–",
                     "Points": _num(q.get("points", np.nan), THIN, "{:.0f}")})
    r.table("Check: the event, game by game", pd.DataFrame(rows))
    dp, zp = plan.get("drop", {}), plan.get("zones", {})
    th = played[played["there"].astype(bool)] if "there" in played else played.iloc[0:0]
    cmp = []
    if dp:
        cmp.append({"": "Spot contested when you landed there", "Plan said": f"{dp['contested']:.0%}",
                    "Event": f"{(th['others'] > 0).mean():.0%} of {len(th)}" if len(th) else "Didn't land there"})
    if zp and "in1" in played:
        cmp.append({"": "Spot inside zone 1", "Plan said": f"{zp['in1']:.0%}", "Event": f"{played['in1'].mean():.0%} of {len(played)}"})
        if "in2" in played and zp.get("in2") == zp.get("in2"):
            cmp.append({"": "Spot inside zone 2", "Plan said": f"{zp['in2']:.0%}", "Event": f"{played['in2'].mean():.0%} of {len(played)}"})
    if rp.get("late_n"):
        tot, n_rot = sum(v[0] for v in late.values()), sum(v[2] for v in late.values())
        cmp.append({"": "Left after the latest time", "Plan said": f"{rp['late_share']:.0%} of {rp['late_n']} rotations before",
                    "Event": f"{tot / n_rot:.0%} of {n_rot} rotations" if n_rot else "–"})
    if len(played):
        cmp.append({"": "Points per game", "Plan said": f"{plan['history_ppg']:.1f} before" if plan.get("history_ppg") == plan.get("history_ppg") else "–",
                    "Event": f"{played['points'].mean():.1f}"})
    if cmp:
        r.table("Check: what the plan said against the event", pd.DataFrame(cmp))
    return f"{len(played)} event games checked against the plan."


# ---------------------------------------------------------------- the page

def _scheme_options() -> list[dict]:
    try:
        return [{"value": s.id, "label": s.label} for s in scoring.schemes().values()]
    except Exception:  # noqa: BLE001 - a broken scoring.json shows up on every page that scores; don't break registration
        return []


@register("gameplan", "Game plan",
          "One team's pre-tournament plan on one page: drop, zones 1–2, rotation timing, the surge line, the habits to fix and the "
          "event's points maths, built only from matches before the event, then checked against it.",
          params=[Param("team", "Players (comma-separated)", "text", ""),
                  Param("spot", "Drop spot (blank = their usual)", "field", ""),
                  Param("as_of", "Plan as of (the event's first day)", "date", ""),
                  Param("scheme", "Event scoring", "choice", None, _scheme_options())])
def run(ctx: Context) -> Result:
    r, con = Result(), ctx.con
    scheme = scoring.get(ctx.params.get("scheme") or None)
    as_of = _parse_date(ctx.params.get("as_of"))
    text = str(ctx.params.get("team") or "").strip()
    n_hist, n_event = _split(con, as_of)
    _use(con, "gp_hist")
    blocks: list[dict] = []
    plan: dict = {}

    def block(name, call, tables):
        if call:
            blocks.append({"Block": name, "The call": call, "Tables": "|".join(tables)})

    try:
        if n_hist < 6:
            r.headline = (f"Only {n_hist} matches before {as_of}." if as_of else f"Only {n_hist} matches selected.") + \
                         " A plan needs history: widen the selection in the left panel or move the as-of date later."
            conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
            return r
        team = _find_team(con, text) if text else pd.DataFrame()
        if not text:
            tl = _team_list(con)
            if len(tl):
                r.table("Teams in the history", tl.head(60))
            r.headline = "Type the team's names above (find them in the table below by their results), and set the event's first day."
            conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
            return r
        L = _landings(con)
        mine = L.merge(team[["match_id", "id"]], on=["match_id", "id"]) if len(team) and len(L) else pd.DataFrame(columns=L.columns)
        spot = _choose_spot(L, mine, str(ctx.params.get("spot") or "").strip(), r.warnings) if len(L) else None
        names = " & ".join(sorted(team["name"].dropna().unique())[:3]) if len(team) else text
        games_h = team["match_id"].nunique() if len(team) else 0
        r.metric("Team", names, f"Found in {games_h} of {n_hist} history matches" if games_h else "Not found in the history")
        r.metric("History", f"{n_hist} matches" + (f" before {as_of}" if as_of else ""), "The plan uses only these matches")
        r.metric("Scoring", scheme.label, scheme.describe())
        if games_h == 0:
            r.warnings.append(f"No games for '{text}' before {as_of or 'the end of the selection'}. The plan still describes the spot, "
                              "zones, rotations and surge, but not this team's own record. Check the names in Team audit.")
        if spot is None:
            r.headline = f"{names}: no drop spot. Type one above, or select matches where the team played so their usual spot is known."
            conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
            return r
        r.metric("Drop spot", spot.name)
        points = _team_points(con, scheme)
        tp = points.merge(team[["match_id", "team_index"]].drop_duplicates(), on=["match_id", "team_index"]) if len(team) else points.iloc[0:0]
        plan["history_ppg"] = float(tp["points"].mean()) if len(tp) else np.nan

        def run_block(name, fn, tables, key=None):
            try:
                call, info = fn()
            except Exception as e:  # noqa: BLE001 - one block failing shouldn't lose the rest of the plan
                r.warnings.append(f"Couldn't build the {name.lower()} block: {type(e).__name__}: {e}")
                return
            if key and info:
                plan[key] = info
            block(name, call, tables)

        band = _bus_band(con, spot)
        run_block("Drop", lambda: _drop_block(r, con, L, spot, team, scheme, band, points, n_hist),
                  ["Drop: how contested your spot is", "Drop: your record there", "Drop: who lands there", "Drop: backup spots"], "drop")
        run_block("Zones 1–2", lambda: _zones_block(r, con, spot), ["Zones 1–2 from your spot"], "zones")
        run_block("Rotations", lambda: _rotation_block(r, ctx, team), ["Rotations: latest time to leave"], "rotations")
        run_block("Surge", lambda: _surge_block(r, ctx, team), ["Surge: the line to stay above"], "surge")
        if not any(b["Block"] == "Surge" for b in blocks):
            r.notes.append("Surge: no surge in the history's in-match data. Collect later rounds with health and damage (data options T and 7).")
        run_block("Habits", lambda: _habits_block(r, ctx, team), ["Habits: what to fix first"])
        block("Points", _points_block(r, scheme, tp["points"]), ["Points: places against eliminations"])

        if n_event:
            _use(con, "gp_event")
            team_e = _find_team(con, text)
            if len(team_e):
                run_block("Check", lambda: (_check_block(r, ctx, con, spot, team_e, scheme, plan), {}),
                          ["Check: what the plan said against the event", "Check: the event, game by game"])
            else:
                r.warnings.append(f"No event games for '{text}' on or after {as_of}. At a LAN, players use event accounts: add those names "
                                  "too (comma-separated), so the team is found in both the history and the event.")
            _use(con, "gp_hist")

        r.table("Plan", pd.DataFrame(blocks, columns=["Block", "The call", "Tables"]))
        drop_call = next((b["The call"] for b in blocks if b["Block"] == "Drop"), "")
        r.headline = f"{names}: plan from {n_hist} matches" + (f" before {as_of}" if as_of else "") + (f". {drop_call}" if drop_call else ".")
        conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
        r.notes += [
            f"Built only from the {n_hist} selected matches" + (f" before {as_of}; the {n_event} on or after it are the event the "
                                                                "plan is checked against." if as_of else "."),
            f"Numbers from fewer than {THIN} games are left out (–). 'Thin' means {THIN}–{SOLID - 1} games; 'Solid' means {SOLID}+.",
            f"Drop spot: {'the named place' if spot.poi else f'landings within {SPOT_R_M} m of where the team usually lands'}. "
            "Bus route: how close the bus line passes to the spot.",
            f"Scoring: {scheme.describe()} {scheme.source}",
        ]
        return r
    finally:
        _use(con, "gp_all")
