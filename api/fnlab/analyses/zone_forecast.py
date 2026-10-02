"""
Zone forecast page. The engine (zone_model.py) compares models of increasing richness on matches they never
saw, keeps a richer model only if it does better out of sample, checks its gain with a shuffle control and a
fresh-matches test, and forecasts both the next zone and the final zone. Results are cached per selection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import zone_model as zm
from ..locks import serialized
from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

RUNGS = [("one_step", "One zone back", "Where the current zone sits, the previous pull, land and height"),
         ("history", "Whole zone history", "Plus the pull two zones back, the drift since zone 1, and returning toward zone 1"),
         ("memory", "History + map memory", "Plus where this zone number has landed in other matches this season")]
RULE_MEASURES = {"on_land": "Centre on playable ground", "land": "Share of the zone on playable ground",
                 "inward": "Pull toward the island's centre", "height": "Ground height inside the zone",
                 "edge": "Distance toward the current zone's edge", "turn1": "Keeps the previous pull's direction",
                 "drift": "Follows the overall drift since zone 1", "home": "Moves away from zone 1's centre"}
READINGS = {
    "on_land": ("Zones put their centre on playable ground more often than random placement would", "Zones put their centre off playable ground more often than random placement would"),
    "land": ("Zones cover more playable ground than random placement would", "Zones cover less playable ground than random placement would"),
    "inward": ("Zones pull toward the island's centre more than chance", "Zones pull away from the island's centre more than chance"),
    "height": ("Zones land on higher ground than random placement would", "Zones land on lower ground than random placement would"),
    "edge": ("Zones land closer to the current zone's edge than chance", "Zones land closer to the current zone's centre than chance"),
    "turn1": ("Zones keep going in the previous pull's direction more than chance", "Zones reverse the previous pull's direction more than chance"),
    "drift": ("Zones keep following the overall drift since zone 1", "Zones turn back against the overall drift since zone 1"),
    "home": ("Zones move away from zone 1's centre more than chance", "Zones come back toward zone 1's centre more than chance"),
}
_CACHE: dict = {}


@serialized
def _load(ctx: Context):
    con = ctx.con
    z = df(con, """SELECT z.match_id, z.phase, z.next_x, z.next_y, z.next_r, o.zone_type, m.season, m.match_date
                   FROM zones z JOIN sel USING (match_id) JOIN matches m USING (match_id)
                   LEFT JOIN zone_offsets o ON o.match_id = z.match_id AND o.phase = z.phase""")
    if z.empty:
        return None
    season = z["season"].mode().iat[0]
    z = z[z["season"] == season].copy()
    z["zone_type"] = z["zone_type"].fillna("shrinking")
    key = (season, tuple(sorted(z["match_id"].unique())), round(float(z["next_x"].sum()), 0))
    if key in _CACHE:
        return _CACHE[key]
    cells = df(con, zm.land_cells_sql(season))
    if len(cells) < 50:
        return None
    land = zm.LandMap(cells)
    seqs = zm.sequences(z)
    if len(seqs) < 10:
        return None
    dates = z.groupby("match_id")["match_date"].first().astype(str).to_dict()
    lad = zm.ladder(land, seqs, dates)
    end = zm.endgame(land, seqs, lad["folds"])
    out = dict(season=season, z=z, land=land, seqs=seqs, lad=lad, end=end)
    if len(_CACHE) > 4:
        _CACHE.clear()
    _CACHE[key] = out
    return out


@register("zone_forecast", "Zone forecast",
          "Where will the next zone go, and where will the game end? Models of increasing richness, from one zone back to "
          "the whole zone history and map memory, each tested on matches it never saw.",
          params=[ALPHA_PARAM, Param("match", "Match to forecast", "match", ""),
                  Param("zone", "Zone to forecast", "zone", 6, [{"value": z, "label": f"Zone {z}"} for z in range(3, 12)])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r = Result()
    have = {t for (t,) in ctx.con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    data = _load(ctx) if {"landings", "zone_offsets"} <= have else None
    if data is None:
        r.headline = "Not enough matches with zones and player positions to forecast (at least 10 from one season)."
        conclude(r, ctx, primary=[], alpha=alpha, recommended=100, descriptive=r.headline)
        return r
    land, seqs, lad, end, season = data["land"], data["seqs"], data["lad"], data["end"], data["season"]
    res, best = lad["results"], lad["best"]
    states = lad["states"]

    # ---- the ladder: each model on matches it never saw
    rows = [{"Model": "Game rules only", "Uses": "Fixed sizes and move distances", "Next zone in top quarter": "25%",
             "Gain over the rules": "–", "Status": ""}]
    for key, name, uses in RUNGS:
        d = res[key]
        rows.append({"Model": name, "Uses": uses, "Next zone in top quarter": f"{d['hit'].mean():.0%}",
                     "Gain over the rules": f"{d['bits'].mean():+.2f} bits", "Status": "Chosen" if key == best else ""})
    sh = res["shuffled"]
    rows.append({"Model": "Shuffle control", "Uses": "Whole zone history, but taken from other matches",
                 "Next zone in top quarter": f"{sh['hit'].mean():.0%}", "Gain over the rules": f"{sh['bits'].mean():+.2f} bits",
                 "Status": "Should be no better than One zone back"})
    r.table("Forecast models compared", pd.DataFrame(rows))
    b = res[best]
    per = b.groupby("match_id")["bits"].mean()
    t = ttest_mean(per, 0.0)
    if t["n"] >= 3:
        r.test("Forecast", "The chosen forecast beats the game's rules", int(t["n"]), f"{b['hit'].mean():.0%} in the top quarter",
               t["p"], alpha, ("The forecast places the next zone better than the game's rules alone",
                               "The forecast does no better than the game's rules alone"), direction=t["mean"])
    hist_gain = res["history"].groupby("match_id")["bits"].mean() - res["one_step"].groupby("match_id")["bits"].mean()
    tg = ttest_mean(hist_gain.dropna(), 0.0)
    if tg["n"] >= 3:
        r.test("Forecast", "Zone history adds to one zone back", int(tg["n"]), f"{tg['mean']:+.2f} bits per zone", tg["p"], alpha,
               ("Earlier zones help predict the next one beyond the previous zone",
                "Earlier zones add nothing beyond the previous zone"), direction=tg["mean"])

    fresh = lad["fresh"]
    r.metric("Next zone forecast", f"{b['hit'].mean():.0%}", "How often the real next zone was in the forecast's most likely quarter of "
             "the possible area, in matches the forecast never saw. 25% is the game's rules alone.")
    if len(fresh):
        r.metric("On the newest matches", f"{fresh['hit'].mean():.0%}", "Trained only on older matches, tested on the newest 20%: "
                 "how it will do on matches that haven't happened yet.")

    # ---- endgame
    er = end["results"]
    use_full = end.get("chosen") == "full"
    if len(er):
        er = er.assign(use_hit=er["hit"] if use_full else er["rules_hit"])
        r.metric("Endgame forecast", f"{er['use_hit'].mean():.0%}", "How often the real final zone was in the forecast's most likely quarter, "
                 "forecast from earlier zones. " + ("Uses zone history and the map: they beat the rules alone out of sample." if use_full else
                 "Uses the game's rules alone: zone history and the map didn't improve it out of sample yet."))
        r.metric("Endgame from the rules alone", f"{er['rules_hit'].mean():.0%}", "The same, using only what the fixed move distances imply.")
        r.metric("Best-guess distance to the final zone", f"{er['err_m'].median():.0f} m",
                 f"Median distance between the forecast's single best spot and the real final zone (a random guess in the same area: {er['err_random_m'].median():.0f} m)")
        eg = er.groupby("match_id").apply(lambda g: (g["bits"] - g["rules_bits"]).mean(), include_groups=False)
        te = ttest_mean(eg.dropna(), 0.0)
        if te["n"] >= 3:
            r.test("Endgame", "Zone history and the map improve the endgame forecast", int(te["n"]),
                   f"{er['hit'].mean():.0%} vs {er['rules_hit'].mean():.0%} from the rules alone", te["p"], alpha,
                   ("Earlier zones and the map tell you where the game will end, beyond the fixed move distances",
                    "Earlier zones and the map add nothing to the endgame forecast beyond the fixed move distances"), direction=te["mean"])
        bz = er.groupby("zone_now").agg(full=("hit", "mean"), rules=("rules_hit", "mean"), n=("hit", "size"))
        bz = bz[bz["n"] >= 5]
        r.chart("bar", "Endgame forecast accuracy, by the zone you're in",
                [dict(name="Forecast", x=[f"Zone {int(k)}" for k in bz.index], y=(bz["full"] * 100).round(0).tolist()),
                 dict(name="Game rules alone", x=[f"Zone {int(k)}" for k in bz.index], y=(bz["rules"] * 100).round(0).tolist())],
                y_label="Final zone in the forecast's top quarter (%)", reference_lines=[dict(axis="y", value=25, label="Random")])

    by = b.groupby("zone").agg(hit=("hit", "mean"), n=("hit", "size"))
    by = by[by["n"] >= 5]
    r.chart("bar", "Next zone forecast accuracy by zone",
            [dict(name="Next zone in the top quarter", x=[f"Zone {int(k)}" for k in by.index], y=(by["hit"] * 100).round(0).tolist())],
            y_label="% forecast correctly", reference_lines=[dict(axis="y", value=25, label="Game rules alone")])

    # ---- rules: where real zones fall among the game-allowed alternatives
    rule_rows = []
    for zt in ["shrinking", "50/50", "shifted", "moving"]:
        sub = [s for s in states if s["type"] == zt]
        if len(sub) < 5:
            continue
        for m, label in RULE_MEASURES.items():
            if m == "edge" and zt != "shrinking":
                continue
            vals = []
            for s in sub:
                c, tv = s["base_c"][m], float(s["base_t"][m][0])
                if np.allclose(c, c[0]):
                    continue
                vals.append((s["match_id"], ((c < tv).sum() + 0.5 * (c == tv).sum()) / len(c), tv, float(np.mean(c))))
            if len(vals) < 5:
                continue
            v = pd.DataFrame(vals, columns=["match_id", "pct", "real", "rand"])
            pm = v.groupby("match_id")["pct"].mean()
            tt = ttest_mean(pm, 0.5) if len(pm) >= 3 else {"n": len(pm), "p": np.nan, "mean": pm.mean()}
            grp = "50/50" if zt == "50/50" else zt.capitalize()
            if tt["n"] >= 3:
                r.test(grp, label, int(tt["n"]), f"real {v['real'].mean():.2f} vs random {v['rand'].mean():.2f}", tt["p"], alpha,
                       READINGS[m], direction=tt.get("mean"), null=0.5)
            fmt = (lambda x: f"{x:.0%}") if m in ("on_land", "land", "edge") else (lambda x: f"{x:+.2f}")
            p = tt["p"]
            rule_rows.append({"Zone type": zt, "Rule": label, "Real zones": fmt(v["real"].mean()), "Random zones": fmt(v["rand"].mean()),
                              "Verdict": ("Strong evidence" if p < alpha else "Some evidence" if p < 0.05 else "No difference") if p == p else "No difference"})
    if rule_rows:
        r.table("Rules: real zones vs random placement", pd.DataFrame(rule_rows))

    # ---- repeated zone positions
    z = data["z"]
    key = z.assign(kx=(z["next_x"] / 100).round(0), ky=(z["next_y"] / 100).round(0))
    reps = key.groupby(["phase", "kx", "ky"])["match_id"].nunique()
    reps = reps[reps > 1]
    r.metric("Repeated zone positions", f"{len(reps)}" if len(reps) else "None",
             "Zone positions that appear identically (to the metre) in more than one match")
    if len(reps):
        r.table("Repeated zone positions", reps.reset_index().rename(columns={"phase": "Zone", "kx": "Centre X (m)", "ky": "Centre Y (m)", "match_id": "Matches"}))

    # ---- the two forecast maps for the chosen match and zone (models that never saw this match)
    want = int(ctx.params.get("zone") or 6)
    mid = ctx.params.get("match") or sorted(seqs, key=lambda m: str(z.loc[z["match_id"] == m, "match_date"].iat[0]))[-1]
    if mid not in seqs:
        mid = next(iter(seqs))
    st = next((s for s in states if s["match_id"] == mid and s["zone"] == want), None) or next((s for s in states if s["match_id"] == mid), None)
    if st is not None:
        f = lad["folds"][mid]
        fm = lad["fold_models"][f]
        t_f, c_f = lad["with_prior"](st, fm["prior"].get(st["zone"]))
        sc = fm["models"][best].scores(c_f, st["type"])
        r.chart("map_points", "Next zone forecast", _bands(st["cand_x"], st["cand_y"], sc, (st["true_x"], st["true_y"]), "Where it really went"),
                x_label="Map X", y_label="Map Y", **_frame(st["h"], st["true_x"], st["true_y"], st["next_r"], st["zone"]))
        eb = next((bb for bb in end.get("base", []) if bb["match_id"] == mid and bb["zone_now"] == st["zone"] - 1), None)
        efm = end["fold_models"].get(f)
        if eb is not None and efm is not None:
            t_e, c_e = end["rows_for"](eb, efm["prior"])
            esc = (efm["model"] if use_full else efm["rules"]).scores(c_e, "end")
            fin = seqs[mid].iloc[-1]
            circles = [dict(x=eb["h"]["cx"], y=eb["h"]["cy"], r=eb["h"]["cr"], label=f"Zone {st['zone'] - 1} (now)"),
                       dict(x=float(fin["x"]), y=float(fin["y"]), r=float(fin["r"]), label=f"Final zone ({int(fin['phase'])})")]
            pad = eb["R"] * 1.1
            r.chart("map_points", "Endgame forecast", _bands(eb["px"], eb["py"], esc, (eb["true_x"], eb["true_y"]), "Where the game really ended"),
                    x_label="Map X", y_label="Map Y", circles=circles, zone_circles=True,
                    range_x=[eb["h"]["cx"] - pad, eb["h"]["cx"] + pad], range_y=[eb["h"]["cy"] - pad, eb["h"]["cy"] + pad])
        r.notes.insert(0, f"Maps: match {mid}, standing in zone {st['zone'] - 1}. Both forecasts come from models trained without this match.")

    # ---- playable map with real zone centres
    ii, jj = np.nonzero(land.land)
    series = [dict(name="Playable ground", x=((ii + land.x0 + 0.5) * zm.CELL).round(0).tolist(), y=((jj + land.y0 + 0.5) * zm.CELL).round(0).tolist(),
                   color="#C9D3DD", size=3, opacity=0.6)]
    for zt in ["shrinking", "50/50", "shifted", "moving"]:
        s = z[z["zone_type"] == zt]
        if len(s):
            series.append(dict(name=f"{'50/50' if zt == '50/50' else zt.capitalize()} zone centres", x=s["next_x"].round(0).tolist(), y=s["next_y"].round(0).tolist()))
    r.chart("map_points", "Where zones land on the playable map", series, x_label="Map X", y_label="Map Y", marker_size=5)

    # ---- headline and conclusion
    chosen = dict((k, n) for k, n, _ in RUNGS)[best]
    r.headline = (f"Next zone: {b['hit'].mean():.0%} in the forecast's most likely quarter (25% from the game's rules alone), using "
                  f"{chosen.lower()}." + (f" Endgame: {er['use_hit'].mean():.0%} (rules alone {er['rules_hit'].mean():.0%})." if len(er) else ""))
    sig = sorted([x for x in r.tests if x["significant"] and x["group"] not in ("Forecast", "Endgame")], key=lambda x: x["p"])
    seen, top = set(), []
    for x in sig:
        if x["reading"] not in seen:
            seen.add(x["reading"])
            top.append(f"{x['reading'].lower()} ({x['group'].lower()} zones)")
        if len(top) == 3:
            break
    conclude(r, ctx, primary=["Forecast", "Endgame"], alpha=alpha, recommended=100, single_season=True,
             takeaway_found=("Zones follow patterns a team can plan around: " + "; ".join(top) + "." if top else
                             "The forecast pinpoints the next zone better than the game's rules alone."),
             takeaway_none="Zones don't yet follow patterns beyond the game's fixed rules in this selection; plan rotations to stay flexible.",
             next_found=["Check 'On the newest matches': it's the honest estimate for upcoming matches.",
                         "Use the endgame accuracy by zone to decide from which zone to commit to an endgame side."],
             next_none=["Add matches: patterns across whole zone sequences need many matches to show."])
    r.notes += [
        f"Season {season}: {len(seqs)} matches, {len(states):,} zone changes. Playable ground is mapped from positions before the storm "
        "first moves (drop and loot), so it isn't shaped by where zones went.",
        "Every model is scored on matches it never saw (5-fold, grouped by match). A richer model is chosen only if it does better.",
        "Shuffle control: the whole-history model with each match's history swapped for another match's. If it still did as well, "
        "the history gain would be fake; it should fall back toward the one-zone-back score.",
        "Endgame: the final zone's centre forecast from each earlier zone, against what the fixed move distances alone imply.",
    ]
    return r


def _bands(px, py, sc, real, real_name):
    order = np.argsort(-sc)
    n = len(sc)
    top = order[: max(1, int(n * zm.TOP))]
    mid = order[int(n * zm.TOP): int(n * 0.5)]
    rest = order[int(n * 0.5):]

    def pick(idx):
        return np.asarray(px)[idx].round(0).tolist(), np.asarray(py)[idx].round(0).tolist()
    return [dict(name="Most likely quarter", x=pick(top)[0], y=pick(top)[1], color="#0F766E", size=7),
            dict(name="Next quarter", x=pick(mid)[0], y=pick(mid)[1], color="#7FC8C0", size=6),
            dict(name="Less likely", x=pick(rest)[0], y=pick(rest)[1], color="#C9D3DD", size=5),
            dict(name=real_name, x=[round(real[0])], y=[round(real[1])], color="#D08A12", size=14)]


def _frame(h, tx, ty, nr, zone):
    pad = max(h["cr"], np.hypot(tx - h["cx"], ty - h["cy"]) + nr) * 1.25
    return dict(circles=[dict(x=h["cx"], y=h["cy"], r=h["cr"], label=f"Zone {zone - 1}"), dict(x=tx, y=ty, r=nr, label=f"Zone {zone}")],
                zone_circles=True, range_x=[h["cx"] - pad, h["cx"] + pad], range_y=[h["cy"] - pad, h["cy"] + pad])
