"""
Zone forecast: where will the next zone go?

1. Playable map. The map is divided into CELL_M squares; a square is playable if players stood on the
   ground there (after landing) in the selected matches of one season. Small gaps are filled in. Each
   square also gets a ground height (a low percentile of the heights seen there).
2. Random zones that follow the game's known rules, many per real zone change: a shrinking zone's next
   circle lands anywhere it fits inside the current one; 50/50, shifted and moving zones move their
   fixed distance (the same for a given zone number) in a random direction.
3. Real vs random. For each measure (centre on land, share of the circle on land, pull toward the
   island's centre, ground height, distance toward the edge, direction vs the previous pull) we take
   where the real zone falls among its random alternatives (0 = lowest, 1 = highest). With no rule,
   that's 0.5 on average; a consistent lean is a rule of the game.
4. Forecast. Each possible next position is weighted by how much more often real zones show its
   measures than random ones do (learned from other matches only), and checked against where the zone
   really went: how often it lands in the forecast's most likely quarter, against 25% by chance.
5. Repeated zones: identical zone positions across matches.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import ndimage

from ..conclusion import conclude
from ..result import Result
from ..stats import ttest_mean
from ..store import df
from . import ALPHA_PARAM, Context, Param, register

CELL = 2500                 # 25 m squares (Unreal units are cm)
N_DISK = 300                # random alternatives for a shrinking zone
N_RING = 72                 # directions for a zone that moves a fixed distance
TOP = 0.25                  # "most likely quarter"
FOLDS = 5
RNG = np.random.default_rng(7)
# Fixed sample points inside a unit disk (sunflower pattern) to measure how much of a circle covers land.
_k = np.arange(48) + 0.5
DISK_PTS = np.c_[np.sqrt(_k / 48) * np.cos(np.pi * (3 - np.sqrt(5)) * _k), np.sqrt(_k / 48) * np.sin(np.pi * (3 - np.sqrt(5)) * _k)]
TYPES = ["shrinking", "50/50", "shifted", "moving"]
MEASURES = {
    "on_land": "Centre on playable ground",
    "land_share": "Share of the zone on playable ground",
    "inward": "Pull toward the island's centre",
    "height": "Ground height inside the zone",
    "edge": "Distance toward the current zone's edge",
    "turn": "Keeps the previous pull's direction",
}


class LandMap:
    def __init__(self, cells: pd.DataFrame):
        self.x0, self.y0 = int(cells["cx"].min()) - 2, int(cells["cy"].min()) - 2
        w, h = int(cells["cx"].max()) - self.x0 + 3, int(cells["cy"].max()) - self.y0 + 3
        grid = np.zeros((w, h), bool)
        grid[cells["cx"] - self.x0, cells["cy"] - self.y0] = True
        self.land = ndimage.binary_closing(grid, structure=np.ones((3, 3)), iterations=2) | grid
        hgt = np.full((w, h), np.nan)
        hgt[cells["cx"] - self.x0, cells["cy"] - self.y0] = cells["ground"]
        # Fill heights of filled-in squares from their nearest measured neighbour.
        idx = ndimage.distance_transform_edt(np.isnan(hgt), return_distances=False, return_indices=True)
        self.height = hgt[tuple(idx)]
        ii, jj = np.nonzero(self.land)
        self.cx, self.cy = (ii.mean() + self.x0 + 0.5) * CELL, (jj.mean() + self.y0 + 0.5) * CELL

    def lookup(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        i = np.floor(np.asarray(x) / CELL).astype(int) - self.x0
        j = np.floor(np.asarray(y) / CELL).astype(int) - self.y0
        ok = (i >= 0) & (j >= 0) & (i < self.land.shape[0]) & (j < self.land.shape[1])
        land = np.zeros(i.shape, bool)
        h = np.full(i.shape, np.nan)
        land[ok] = self.land[i[ok], j[ok]]
        h[ok] = self.height[i[ok], j[ok]]
        return land, h


def _land_map(ctx: Context, season: str) -> LandMap | None:
    cells = df(ctx.con, f"""
        SELECT floor(p.x / {CELL})::INT AS cx, floor(p.y / {CELL})::INT AS cy, count(*) AS n, quantile_cont(p.z, 0.2) AS ground
        FROM positions p JOIN sel USING (match_id)
        JOIN matches m USING (match_id)
        JOIN landings l ON l.match_id = p.match_id AND l.id = p.id
        WHERE m.season = '{season}' AND p.t >= l.land_t AND abs(coalesce(p.vz, 0)) < 300
        GROUP BY 1, 2 HAVING count(*) >= 2
    """)
    return LandMap(cells) if len(cells) >= 50 else None


def _features(land: LandMap, cur: tuple, nxt_x: np.ndarray, nxt_y: np.ndarray, next_r: float, prev_angle: float | None) -> dict:
    """Measures for candidate next centres (arrays) given the current circle (x, y, r)."""
    cx, cy, cr = cur
    on, h_c = land.lookup(nxt_x, nxt_y)
    px = nxt_x[:, None] + DISK_PTS[None, :, 0] * next_r
    py = nxt_y[:, None] + DISK_PTS[None, :, 1] * next_r
    lp, hp = land.lookup(px, py)
    share = lp.mean(axis=1)
    with np.errstate(invalid="ignore"):
        height = np.nanmean(np.where(lp, hp, np.nan), axis=1) / 100
    vx, vy = nxt_x - cx, nxt_y - cy
    tx, ty = land.cx - cx, land.cy - cy
    norm = np.hypot(vx, vy) * np.hypot(tx, ty)
    inward = np.where(norm > 0, (vx * tx + vy * ty) / np.where(norm > 0, norm, 1), 0.0)
    allowed = max(cr - next_r, 1.0)
    edge = np.clip(np.hypot(vx, vy) / allowed, 0, 1)
    turn = np.full(len(nxt_x), np.nan) if prev_angle is None else \
        np.cos(np.arctan2(vy, vx) - np.radians(prev_angle))
    return dict(on_land=on.astype(float), land_share=share, inward=inward, height=height, edge=edge, turn=turn)


def _candidates(row: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    cx, cy, cr, nr = row["cur_x"], row["cur_y"], row["cur_r"], row["next_r"]
    if row["zone_type"] == "shrinking":
        rho = (cr - nr) * np.sqrt(RNG.random(N_DISK))
        th = RNG.random(N_DISK) * 2 * np.pi
    else:
        rho = np.full(N_RING, np.hypot(row["next_x"] - cx, row["next_y"] - cy))
        th = (np.arange(N_RING) + RNG.random()) * 2 * np.pi / N_RING
    return cx + rho * np.cos(th), cy + rho * np.sin(th)


def _bins(m: str) -> np.ndarray:
    return {"on_land": np.array([-0.5, 0.5, 1.5]), "land_share": np.linspace(0, 1, 6), "inward": np.linspace(-1, 1, 6),
            "edge": np.linspace(0, 1, 6), "turn": np.linspace(-1, 1, 7)}.get(m, np.array([]))


def _measure_list(zone_type: str) -> list[str]:
    return ["on_land", "land_share", "inward", "edge", "turn"] if zone_type == "shrinking" else ["on_land", "land_share", "inward", "turn"]


def _fit(train: list[dict]) -> dict:
    """Density ratios (real / random) per zone type, measure and bin, with light smoothing."""
    model = {}
    for zt in TYPES:
        rows = [t for t in train if t["type"] == zt]
        if len(rows) < 8:
            continue
        for m in _measure_list(zt):
            b = _bins(m)
            real = np.zeros(len(b) - 1)
            rand = np.zeros(len(b) - 1)
            for t in rows:
                v = t["actual"][m]
                if np.isnan(v):
                    continue
                real += np.histogram([v], b)[0]
                cv = t["cand"][m]
                cv = cv[~np.isnan(cv)]
                if len(cv):
                    rand += np.histogram(cv, b)[0] / len(cv)
            if real.sum() >= 8:
                model[(zt, m)] = (b, (real + 1) / (rand + 1))
    return model


def _weights(model: dict, zt: str, feats: dict, n: int) -> np.ndarray:
    w = np.ones(n)
    for m in _measure_list(zt):
        key = (zt, m)
        if key not in model:
            continue
        b, ratio = model[key]
        v = np.atleast_1d(feats[m])
        idx = np.clip(np.searchsorted(b, v, side="right") - 1, 0, len(ratio) - 1)
        w = w * np.where(np.isnan(v), 1.0, ratio[idx])
    return w


def _percentile(value: float, cands: np.ndarray) -> float:
    c = cands[~np.isnan(cands)]
    if np.isnan(value) or not len(c):
        return np.nan
    return float(((c < value).sum() + 0.5 * (c == value).sum()) / len(c))


warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)


@register("zone_forecast", "Zone forecast",
          "Where will the next zone go? Real zones compared with random zones that follow the game's rules on a map of "
          "the playable area, the rules that come out of it, and a forecast tested on matches it never saw.",
          params=[ALPHA_PARAM, Param("match", "Match to forecast", "match", ""),
                  Param("zone", "Zone to forecast", "zone", 6, [{"value": z, "label": f"Zone {z}"} for z in range(3, 12)])])
def run(ctx: Context) -> Result:
    alpha = float(ctx.params.get("alpha", 0.005))
    r, con = Result(), ctx.con
    z = df(con, """SELECT z.match_id, z.phase, z.cur_x, z.cur_y, z.cur_r, z.next_x, z.next_y, z.next_r, z.angle_deg, z.zone_type,
                          m.season, m.match_date
                   FROM zone_offsets z JOIN sel USING (match_id) JOIN matches m USING (match_id)
                   WHERE z.cur_x IS NOT NULL ORDER BY z.match_id, z.phase""")
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if z.empty or "landings" not in have or "zone_type" not in z:
        r.headline = "No zone changes with known circles in this selection (rebuild tables after updating)."
        conclude(r, ctx, primary=[], alpha=alpha, recommended=50, descriptive=r.headline)
        return r
    season = z["season"].mode().iat[0]
    z = z[z["season"] == season].copy()
    land = _land_map(ctx, season)
    if land is None:
        r.headline = "Not enough player positions to map the playable area."
        conclude(r, ctx, primary=[], alpha=alpha, recommended=50, descriptive=r.headline)
        return r
    z["prev_angle"] = z.groupby("match_id")["angle_deg"].shift(1)

    # ---- real zone vs its random alternatives, for every zone change
    trans = []
    for _, row in z.iterrows():
        cur = (row["cur_x"], row["cur_y"], row["cur_r"])
        prev = None if pd.isna(row["prev_angle"]) else float(row["prev_angle"])
        cx, cy = _candidates(row)
        cand = _features(land, cur, cx, cy, row["next_r"], prev)
        act = _features(land, cur, np.array([row["next_x"]]), np.array([row["next_y"]]), row["next_r"], prev)
        act = {k: float(v[0]) for k, v in act.items()}
        trans.append(dict(match_id=row["match_id"], phase=int(row["phase"]), type=row["zone_type"], cur=cur,
                          next=(row["next_x"], row["next_y"], row["next_r"]), cx=cx, cy=cy, cand=cand, actual=act, prev=prev))

    # ---- rules: where real zones fall among random ones, one summary per match
    rows = []
    for zt in TYPES:
        sub = [t for t in trans if t["type"] == zt]
        if len(sub) < 5:
            continue
        for m in _measure_list(zt) + ["height"]:
            pct = pd.DataFrame([(t["match_id"], _percentile(t["actual"][m], t["cand"][m])) for t in sub], columns=["match_id", "p"]).dropna()
            if len(pct) < 5:
                continue
            per = pct.groupby("match_id")["p"].mean()
            tt = ttest_mean(per, 0.5) if len(per) >= 3 else {"n": len(per), "p": np.nan, "mean": per.mean()}
            real = np.nanmean([t["actual"][m] for t in sub])
            rand = np.nanmean([np.nanmean(t["cand"][m]) for t in sub])
            label = MEASURES[m]
            reading = {
                "on_land": ("Zones put their centre on playable ground more often than random placement would",
                            "Zones put their centre on playable ground less often than random placement would"),
                "land_share": ("Zones cover more playable ground than random placement would",
                               "Zones cover less playable ground than random placement would"),
                "inward": ("Zones pull toward the island's centre more than chance", "Zones pull away from the island's centre more than chance"),
                "height": ("Zones land on higher ground than random placement would", "Zones land on lower ground than random placement would"),
                "edge": ("Zones land closer to the current zone's edge than chance", "Zones land closer to the current zone's centre than chance"),
                "turn": ("Zones keep going in the previous pull's direction more than chance", "Zones reverse the previous pull's direction more than chance"),
            }[m]
            if tt["n"] >= 3:
                r.test(zt.capitalize() if zt != "50/50" else "50/50", label, int(tt["n"]),
                       f"real {real:.2f} vs random {rand:.2f}", tt["p"], alpha, reading, direction=tt.get("mean"), null=0.5)
            fmt = (lambda v: f"{v:.0%}") if m in ("on_land", "land_share", "edge") else (lambda v: f"{v:+.2f}") if m in ("inward", "turn") else (lambda v: f"{v:.0f} m")
            rows.append({"Zone type": zt, "Rule": label, "Real zones": fmt(real), "Random zones": fmt(rand),
                         "Zone changes": len(sub), "_p": tt["p"], "_dir": (tt.get("mean") or 0.5) - 0.5})
    rules = pd.DataFrame(rows)
    if len(rules):
        rules["Verdict"] = [("Strong evidence" if p < alpha else "Some evidence" if p < 0.05 else "No difference") if p == p else "No difference"
                            for p in rules["_p"]]
        r.table("Rules: real zones vs random placement", rules.drop(columns=["_p", "_dir"]))

    # ---- repeated zones: identical positions across matches
    key = z.assign(kx=(z["next_x"] / 100).round(0), ky=(z["next_y"] / 100).round(0))
    reps = key.groupby(["phase", "kx", "ky"])["match_id"].nunique()
    reps = reps[reps > 1]
    r.metric("Repeated zone positions", f"{len(reps)}" if len(reps) else "None",
             "Zone positions that appear identically (to the metre) in more than one match")
    if len(reps):
        r.table("Repeated zone positions", reps.reset_index().rename(columns={"phase": "Zone", "kx": "Centre X (m)", "ky": "Centre Y (m)",
                                                                               "match_id": "Matches"}))

    # ---- forecast, backtested: each match forecast by a model trained on the other matches
    matches = sorted({t["match_id"] for t in trans})
    folds = {m: i % FOLDS for i, m in enumerate(RNG.permutation(matches))}
    scored = []
    for f in range(FOLDS):
        model = _fit([t for t in trans if folds[t["match_id"]] != f])
        for t in (t for t in trans if folds[t["match_id"]] == f):
            w = _weights(model, t["type"], t["cand"], len(t["cx"]))
            wa = _weights(model, t["type"], {k: np.array([v]) for k, v in t["actual"].items()}, 1)[0]
            rank = float(((w < wa).sum() + 0.5 * (w == wa).sum()) / len(w))
            scored.append(dict(match_id=t["match_id"], phase=t["phase"], type=t["type"], rank=rank, hit=rank >= 1 - TOP))
    sc = pd.DataFrame(scored)
    if len(sc):
        per = sc.groupby("match_id")["rank"].mean()
        tt = ttest_mean(per, 0.5)
        if tt["n"] >= 3:
            r.test("Forecast", "Forecast beats random guessing", int(tt["n"]),
                   f"real zone in the forecast's top quarter {sc['hit'].mean():.0%} of the time (25% by chance)", tt["p"], alpha,
                   ("The forecast places the real next zone better than random guessing", "The forecast does no better than random guessing"),
                   direction=tt.get("mean"), null=0.5)
        r.metric("Forecast hit rate", f"{sc['hit'].mean():.0%}", "How often the real next zone landed in the forecast's most likely "
                 "quarter of the possible area, in matches the forecast never saw. 25% is random guessing.")
        by = sc.groupby("phase").agg(hit=("hit", "mean"), n=("hit", "size"))
        by = by[by["n"] >= 5]
        r.chart("bar", "Forecast hit rate by zone",
                [dict(name="Next zone in the forecast's top quarter", x=[f"Zone {int(p)}" for p in by.index], y=(by["hit"] * 100).round(0).tolist())],
                y_label="% of zones forecast correctly", reference_lines=[dict(axis="y", value=25, label="Random guess")])
    r.metric("Zone changes analysed", f"{len(trans):,}", f"From {len(matches)} matches of {season}")

    # ---- the playable map with every real zone centre
    ii, jj = np.nonzero(land.land)
    series = [dict(name="Playable ground", x=((ii + land.x0 + 0.5) * CELL).round(0).tolist(), y=((jj + land.y0 + 0.5) * CELL).round(0).tolist(),
                   color="#C9D3DD", size=3, opacity=0.6)]
    for zt in TYPES:
        s = z[z["zone_type"] == zt]
        if len(s):
            series.append(dict(name=f"{zt.capitalize() if zt != '50/50' else '50/50'} zone centres", x=s["next_x"].round(0).tolist(), y=s["next_y"].round(0).tolist()))
    if "pois" in have:
        pois = df(con, "SELECT * FROM pois WHERE kind = 'poi'")
        if len(pois):
            series.append(dict(name="Named places", x=pois["x"].tolist(), y=pois["y"].tolist(), text=pois["name"].tolist()))
    r.chart("map_points", "Where zones land on the playable map", series, x_label="Map X", y_label="Map Y", marker_size=5)

    # ---- one forecast drawn: the chosen match and zone
    want_zone = int(ctx.params.get("zone") or 6)
    mid = ctx.params.get("match") or (matches[-1] if matches else None)
    pick = next((t for t in trans if t["match_id"] == mid and t["phase"] == want_zone), None) or \
        next((t for t in trans if t["match_id"] == mid), None)
    if pick is not None:
        model = _fit([t for t in trans if t["match_id"] != pick["match_id"]])
        w = _weights(model, pick["type"], pick["cand"], len(pick["cx"]))
        order = np.argsort(-w)
        top = np.zeros(len(w), bool)
        top[order[: max(1, int(len(w) * TOP))]] = True
        mid_band = np.zeros(len(w), bool)
        mid_band[order[int(len(w) * TOP): int(len(w) * 0.5)]] = True
        cx, cy, cr = pick["cur"]
        nx, ny, nr = pick["next"]
        fs = [dict(name="Most likely quarter", x=pick["cx"][top].round(0).tolist(), y=pick["cy"][top].round(0).tolist(), color="#0F766E", size=7),
              dict(name="Next quarter", x=pick["cx"][mid_band].round(0).tolist(), y=pick["cy"][mid_band].round(0).tolist(), color="#7FC8C0", size=6),
              dict(name="Less likely", x=pick["cx"][~top & ~mid_band].round(0).tolist(), y=pick["cy"][~top & ~mid_band].round(0).tolist(),
                   color="#C9D3DD", size=5),
              dict(name="Where it really went", x=[round(nx)], y=[round(ny)], color="#D08A12", size=14)]
        pad = max(cr, np.hypot(nx - cx, ny - cy) + nr) * 1.25
        r.chart("map_points", "Forecast for the chosen zone", fs, x_label="Map X", y_label="Map Y",
                circles=[dict(x=cx, y=cy, r=cr, label=f"Zone {pick['phase'] - 1}"), dict(x=nx, y=ny, r=nr, label=f"Zone {pick['phase']}")],
                zone_circles=True, range_x=[cx - pad, cx + pad], range_y=[cy - pad, cy + pad])
        r.notes.insert(0, f"Forecast map: match {pick['match_id']}, zone {pick['phase']} ({pick['type']}). The forecast was trained on "
                          "the other matches only. Dots are possible centres for the next zone.")

    hit = sc["hit"].mean() if len(sc) else np.nan
    # Plain takeaway: the forecast's accuracy, then the three strongest distinct rules with their zone type.
    sig = sorted([x for x in r.tests if x["significant"] and x["group"] != "Forecast"], key=lambda x: x["p"])
    seen, top_rules = set(), []
    for x in sig:
        if x["reading"] not in seen:
            seen.add(x["reading"])
            top_rules.append(f"{x['reading'].lower()} ({x['group'].lower()} zones)")
        if len(top_rules) == 3:
            break
    lead = (f"The forecast put the next zone in its most likely quarter {hit:.0%} of the time, against 25% by chance. "
            if hit == hit else "")
    strong = rules[rules["Verdict"] == "Strong evidence"] if len(rules) else rules
    r.headline = (f"Across {len(trans):,} zone changes, the forecast put the real next zone in its most likely quarter "
                  f"{hit:.0%} of the time, against 25% by chance." if hit == hit else f"{len(trans):,} zone changes analysed.") + \
        (f" {len(strong)} rule{'s' if len(strong) != 1 else ''} found." if len(strong) else "")
    conclude(r, ctx, primary=["Forecast"] + [zt.capitalize() if zt != "50/50" else "50/50" for zt in TYPES], alpha=alpha,
             recommended=100, single_season=True,
             takeaway_found=lead + ("Strongest rules: " + "; ".join(top_rules) + "." if top_rules else ""),
             takeaway_none=lead + "No rule beyond the game's fixed sizes and distances in this selection yet.",
             next_found=["Use the rules table to rule out directions mid-game (for example, toward the coast).",
                         "Watch the hit rate by zone: zones where the forecast is strong are where pre-rotating pays off."],
             next_none=["Add matches: rules about land and the coast need many zone changes near the coast."])
    r.notes += [
        f"Playable ground: {CELL // 100} m squares where players stood on the ground after landing in {season} matches, with small "
        "gaps filled in. Lakes players swim through count as playable.",
        "Random zones follow the game's fixed rules: a shrinking zone lands anywhere it fits inside the current one; other zones "
        "move their fixed distance in a random direction.",
        "Rules compare where each real zone falls among its random alternatives; 'Real zones' and 'Random zones' are averages.",
        f"The forecast is checked with {FOLDS}-fold cross-validation: every match is forecast by a model trained on the other matches.",
    ]
    return r
