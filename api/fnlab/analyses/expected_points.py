"""
Decisions: what is a situation worth in points, and which of two plans is worth more?
Built on ep_model (snapshots every 10 s, gradient-boosted expected points checked on matches it never saw).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import ep_model as ep
from ..conclusion import conclude
from ..result import Result
from ..store import df
from . import Context, Param, register

_CACHE: dict = {}
DEFAULT_PLANS = {
    "a": {"name": "Rotate now", "teams_alive": 20, "zone": 7, "progress": 0.3, "members": 2, "hp": 120, "outside_m": 0, "storm_m": 0,
          "off_centre": 0.6, "height_rank": 0.5, "enemies_50": 0, "enemies_150": 2, "hit_10s": 0, "dealt_rank": 0.5, "kills": 2},
    "b": {"name": "Heal first, rotate late", "teams_alive": 20, "zone": 7, "progress": 0.7, "members": 2, "hp": 200, "outside_m": 60,
          "storm_m": 0, "off_centre": 0.9, "height_rank": 0.5, "enemies_50": 0, "enemies_150": 2, "hit_10s": 0, "dealt_rank": 0.5, "kills": 2},
}


def _load(ctx: Context):
    key = tuple(sorted(df(ctx.con, "SELECT match_id FROM sel")["match_id"]))
    if key in _CACHE:
        return _CACHE[key]
    team = ep.snapshots(ctx.con)
    if team.empty or team["match_id"].nunique() < 6:
        return None
    model, oos, base = ep.fit(team)
    team = team.assign(pred=oos, base=base)
    from ._events_common import has_tables
    expo = ep.exposure(team, ctx.con) if has_tables(ctx.con, "damage") else pd.DataFrame()
    out = dict(team=team, model=model, expo=expo)
    _CACHE.clear()
    _CACHE[key] = out
    return out


def _predict(model, rows: list[dict]) -> np.ndarray:
    return model.predict(pd.DataFrame(rows)[ep.FEATURES].to_numpy(float))


@register("expected_points", "Expected points",
          "What any moment of a match is worth in points (FNCS scoring, including the cliff at 15th), what each change is "
          "worth, the risk of being hit while rotating and of surge, and a comparison of two plans.",
          params=[Param("plans", "Compare two plans", "plans", json.dumps(DEFAULT_PLANS))])
def run(ctx: Context) -> Result:
    r = Result()
    data = _load(ctx)
    if data is None:
        r.headline = "Needs at least 6 matches with zones and player positions in the selection."
        conclude(r, ctx, primary=[], alpha=0.005, recommended=50, descriptive=r.headline)
        return r
    team, model, expo = data["team"], data["model"], data["expo"]
    y, pred, base = team["future_pts"], team["pred"], team["base"]
    ok = pred.notna() & (team["pred"] != 0)
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    r2b = 1 - ((y - base) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    r.metric("Situations analysed", f"{len(team):,}", f"Every living team every {ep.STEP} s, from zone 2 on, in {team['match_id'].nunique()} matches")
    r.metric("Model accuracy", f"{r2:.0%} vs {r2b:.0%}", "Share of the variation in points still to come that the model explains on matches it never "
             "saw, against a baseline that only knows teams left and the zone. The gap is what the situation adds.")

    # ---- the plan comparison
    try:
        plans = json.loads(ctx.params.get("plans") or json.dumps(DEFAULT_PLANS))
    except (TypeError, ValueError):
        plans = DEFAULT_PLANS
    a, b = ({**DEFAULT_PLANS[k], **{kk: v for kk, v in plans.get(k, {}).items() if v is not None}} for k in ("a", "b"))
    pa, pb = _predict(model, [a, b])
    tot_a, tot_b = pa + ep.KILL_POINTS * a["kills"], pb + ep.KILL_POINTS * b["kills"]
    drivers = []
    for f in ep.FEATURES:
        if a[f] != b[f]:
            swapped = dict(a, **{f: b[f]})
            drivers.append((ep.LABELS[f], float(_predict(model, [swapped])[0] - pa), a[f], b[f]))
    drivers.sort(key=lambda d: -abs(d[1]))
    better = a["name"] if tot_a >= tot_b else b["name"]
    r.metric("Plan comparison", f"{a['name']}: {tot_a:.1f} · {b['name']}: {tot_b:.1f}",
             "Expected total points from this moment (eliminations so far plus what's still to come)")
    r.table("Plan comparison", pd.DataFrame(
        [{"What's different": lab, a["name"]: va, b["name"]: vb, f"Points, switching {a['name']} to {b['name']}": f"{dv:+.1f}"}
         for lab, dv, va, vb in drivers] or [{"What's different": "Nothing: the plans are identical"}]))

    # ---- the cliff: points still to come and chance of a top-15 finish, by teams left
    bins = [0, 5, 10, 15, 20, 25, 30, 40, 1000]
    labels = ["1–5", "6–10", "11–15", "16–20", "21–25", "26–30", "31–40", "41+"]
    team["left_bin"] = pd.cut(team["teams_alive"], bins, labels=labels)
    full_hp = float(team["hp"].quantile(0.99)) or 200.0          # 200 with shields; adapts if shields aren't recorded
    hi, lo = round(0.75 * full_hp), round(0.5 * full_hp)
    good = (team["outside_m"] == 0) & (team["hp"] >= hi)
    bad = (team["outside_m"] > 0) & (team["hp"] < lo)
    series, top15 = [], []
    for name, mask in ((f"Inside the next zone, {hi}+ health", good), (f"Outside it, under {lo} health", bad), ("Everyone", pd.Series(True, index=team.index))):
        g = team[mask].groupby("left_bin", observed=False)
        n = g.size()
        series.append(dict(name=name, x=labels[::-1], y=g["future_pts"].mean().where(n >= 30).reindex(labels[::-1]).round(1).tolist()))
        top15.append(dict(name=name, x=labels[::-1], y=(g["placement"].apply(lambda s: (s <= 15).mean()) * 100).where(n >= 30).reindex(labels[::-1]).round(0).tolist()))
    r.chart("line", "Points still to come, by teams left", series, x_label="Teams left", y_label="Average points still to come")
    r.chart("line", "Chance of a top-15 finish (the points cliff), by teams left", top15, x_label="Teams left",
            y_label="Finished top 15 (%)", reference_lines=[dict(axis="y", value=50, label="")])

    # ---- what each change is worth, in the endgame (zones 6-9), from the model
    late = team[team["zone"].between(6, 9)]
    if len(late) >= 200:
        sample = late.sample(min(len(late), 3000), random_state=1)
        rows = sample[ep.FEATURES].copy()
        base_p = model.predict(rows.to_numpy(float))

        def delta(**changes):
            x = rows.copy()
            for k, v in changes.items():
                x[k] = v(x) if callable(v) else v
            return float(np.mean(model.predict(x[ep.FEATURES].to_numpy(float)) - base_p))
        effects = [
            ("+50 health and shield", delta(hp=lambda x: (x["hp"] + 50).clip(upper=200))),
            ("Inside the next zone instead of outside", delta(outside_m=0.0) - delta(outside_m=lambda x: x["outside_m"].clip(lower=100))),
            ("Out of the storm", delta(storm_m=0.0) - delta(storm_m=lambda x: x["storm_m"].clip(lower=50))),
            ("One more enemy team within 50 m", delta(enemies_50=lambda x: x["enemies_50"] + 1)),
            ("Teammate alive (vs not)", delta(members=2) - delta(members=1)) if team["members"].max() >= 2 else ("Teammate alive (vs not)", np.nan),
            ("Top of the lobby in damage dealt this zone (vs bottom) – surge", delta(dealt_rank=1.0) - delta(dealt_rank=0.0)),
            ("High ground (vs low)", delta(height_rank=0.9) - delta(height_rank=0.1)),
            ("Centre of the zone (vs edge)", delta(off_centre=0.2) - delta(off_centre=0.95)),
            ("Just took 50 damage (vs none)", delta(hit_10s=50.0) - delta(hit_10s=0.0)),
        ]
        effects = [(n, v) for n, v in effects if v == v]
        r.chart("bar", "What each change is worth in the endgame (zones 6–9)",
                [dict(name="Expected points", x=[n for n, _ in effects], y=[round(v, 1) for _, v in effects])],
                x_label="Change in expected points", horizontal=True)
        r.table("What each change is worth in the endgame", pd.DataFrame({"Change": [n for n, _ in effects],
                                                                           "Expected points": [f"{v:+.1f}" for _, v in effects]}))
        per_hp = delta(hp=lambda x: (x["hp"] + 10).clip(upper=200)) / 10
    else:
        per_hp = np.nan

    # ---- getting hit while rotating (the player who opens their box as you pass)
    if len(expo):
        expo["zone_group"] = pd.cut(expo["zone"], [0, 4, 6, 8, 99], labels=["Zones 2–4", "Zones 5–6", "Zones 7–8", "Zones 9+"])
        g = expo.groupby(["zone_group", "state"], observed=False)
        p = g["hit"].mean().unstack() * 100
        dm = expo[expo["hit"]].groupby(["zone_group", "state"], observed=False)["dmg"].mean().unstack()
        zg = [str(z) for z in p.index]
        r.chart("bar", "Chance of being hit by a new team within 10 s",
                [dict(name=c, x=zg, y=p[c].round(1).tolist()) for c in p.columns], y_label="% of 10-second windows")
        rows = []
        for z in p.index:
            for c in p.columns:
                ph, d = p.loc[z, c] / 100, dm.loc[z, c] if (z in dm.index and c in dm.columns) else np.nan
                cost = ph * d * per_hp if all(v == v for v in (ph, d, per_hp)) else np.nan
                rows.append({"Zones": str(z), "Situation": c, "Hit within 10 s": f"{ph:.0%}" if ph == ph else "–",
                             "Damage when hit": f"{d:.0f}" if d == d else "–", "Points cost per 10 s": f"{cost:.2f}" if cost == cost else "–"})
        r.table("Getting hit while rotating", pd.DataFrame(rows))
        rot7 = p.loc["Zones 7–8"] if "Zones 7–8" in p.index else None
        if rot7 is not None and rot7.notna().all() and len(rot7) == 2:
            r.metric("Hit within 10 s, zones 7–8", f"{rot7.iloc[1]:.0f}% rotating vs {rot7.iloc[0]:.0f}% holding" if "Rotating" in rot7.index[1] else
                     " vs ".join(f"{v:.0f}% {k.split(' (')[0].lower()}" for k, v in rot7.items()),
                     "The chance a team you weren't fighting hits you in the next 10 seconds")

    # ---- surge
    from .surge import surge_episodes
    from ._events_common import has_tables
    ep_s, per = surge_episodes(ctx) if has_tables(ctx.con, "health", "damage") else (pd.DataFrame(), pd.DataFrame())
    if len(per):
        per["rank"] = per.groupby("episode")["dealt"].rank(pct=True)
        q = per.groupby(pd.cut(per["rank"], [0, 0.25, 0.5, 0.75, 1.0001], labels=["Bottom quarter", "Second", "Third", "Top quarter"]),
                        observed=False)["surged"].mean() * 100
        r.chart("bar", "Chance of being surged, by damage dealt", [dict(name="Surged", x=[str(i) for i in q.index], y=q.round(0).tolist())],
                x_label="Damage dealt before the surge, rank in the lobby", y_label="% surged")
    else:
        r.notes.append("No surge was detected in these matches, so surge risk comes only from the damage-dealt rank in the model.")

    # ---- calibration
    cal = team[ok].assign(bin=pd.qcut(team.loc[ok, "pred"].rank(method="first"), 10, labels=False))
    c = cal.groupby("bin").agg(pred=("pred", "mean"), real=("future_pts", "mean"))
    r.chart("line", "Is the model calibrated?", [dict(name="Real average", x=c["pred"].round(1).tolist(), y=c["real"].round(1).tolist()),
                                                 dict(name="Perfect", x=c["pred"].round(1).tolist(), y=c["pred"].round(1).tolist())],
            x_label="Predicted points still to come", y_label="Real average")

    r.headline = (f"{a['name']}: {tot_a:.1f} expected points; {b['name']}: {tot_b:.1f}. "
                  + (f"Biggest driver: {drivers[0][0].lower()} ({drivers[0][1]:+.1f})." if drivers else ""))
    conclude(r, ctx, primary=[], alpha=0.005, recommended=50,
             descriptive=f"{better} is worth {abs(tot_a - tot_b):.1f} points more in expectation. " + r.headline)
    r.notes += [
        "Points still to come: placement points (FNCS 2026: 65 for 1st down to 22 for 15th, 0 below) plus 4 per future elimination.",
        f"The model learns from every living team every {ep.STEP} s and is checked on matches it never saw. Its expected points are "
        "averages: any single game still turns on fights and luck.",
        "Getting hit: a hit from a team that hadn't hit you in the last 30 s, within the next 10 s; rotating means being outside the next zone.",
        "Train it on the lobbies that match the decision: for a tier-1 duo, select tier-1 duo matches (such as Globals) in the left panel.",
    ]
    return r
