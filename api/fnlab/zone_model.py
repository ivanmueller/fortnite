"""
zone_model.py - the zone forecasting engine behind the Zone forecast section.

Data
  Each match is a sequence of zone circles C1, C2, ... (zone 1 is the first circle with a known centre).
  Predicting zone k uses only what's known when zone k-1 is the current zone: the circles so far.

Candidates (the game's rules, nothing else)
  Shrinking zone: anywhere its circle fits inside the current one (spread evenly over that area).
  50/50, shifted, moving zone: the real move distance in any direction (the game fixes the distance).
  The real next centre is scored against these alternatives.

Features (per candidate, relative to the current zone)
  edge, edge^2      how far toward the current zone's edge (shrinking zones only)
  turn1, turn1^2    direction vs the previous pull (cos; ^2 = along that axis either way)
  turn2             direction vs the pull two zones back
  drift             direction vs the overall drift since zone 1
  home              moving back toward zone 1's centre (change in distance, in current-zone widths)
  inward, centre    toward the island's centre (direction; change in distance)
  on_land, land     centre on playable ground; share of the circle on playable ground
  height            ground height vs the other candidates (per 10 m)
  prior             map memory: log density of this zone number's centres in other matches (25 m grid, 150 m blur)

Model
  Conditional logit: P(candidate) proportional to exp(beta . features), separate weights for shrinking and
  moving-type zones, L2 penalty, features standardised on the training matches. Fitted by L-BFGS.

Validation
  Grouped K-fold by match; a fresh-matches test (train on older matches, test on the newest); a shuffle
  control (zone histories swapped between matches). Scores: how often the real zone is in the forecast's
  most likely quarter (25% by chance) and the information gain over the game's rules, in bits.

Endgame
  The final zone's centre predicted directly from the state at each zone k, with candidates spread evenly
  over a disc that holds the real final centre in 97.5% of training cases.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.optimize import minimize
from scipy.special import logsumexp

warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)

CELL = 2500
N_DISK, N_RING, N_END = 200, 72, 250
TOP = 0.25
FOLDS = 5
_k = np.arange(32) + 0.5
DISK = np.c_[np.sqrt(_k / 32) * np.cos(np.pi * (3 - np.sqrt(5)) * _k), np.sqrt(_k / 32) * np.sin(np.pi * (3 - np.sqrt(5)) * _k)]

STEP_SETS = {
    "rules": [],
    "one_step": ["edge", "edge2", "turn1", "turn1sq", "inward", "centre", "on_land", "land", "height"],
    "history": ["edge", "edge2", "turn1", "turn1sq", "turn2", "drift", "home", "inward", "centre", "on_land", "land", "height"],
    "memory": ["edge", "edge2", "turn1", "turn1sq", "turn2", "drift", "home", "inward", "centre", "on_land", "land", "height", "prior"],
}
END_FEATURES = ["dist", "dist2", "turn1", "drift", "home", "inward", "centre", "on_land", "land", "height", "prior"]
END_RULES = ["dist", "dist2"]   # what the game's fixed move distances alone imply


# --------------------------------------------------------------------------- playable map
class LandMap:
    def __init__(self, cells: pd.DataFrame):
        self.x0, self.y0 = int(cells["cx"].min()) - 2, int(cells["cy"].min()) - 2
        w, h = int(cells["cx"].max()) - self.x0 + 3, int(cells["cy"].max()) - self.y0 + 3
        grid = np.zeros((w, h), bool)
        grid[cells["cx"] - self.x0, cells["cy"] - self.y0] = True
        self.land = ndimage.binary_closing(grid, structure=np.ones((3, 3)), iterations=2) | grid
        hgt = np.full((w, h), np.nan)
        hgt[cells["cx"] - self.x0, cells["cy"] - self.y0] = cells["ground"]
        idx = ndimage.distance_transform_edt(np.isnan(hgt), return_distances=False, return_indices=True)
        self.height = hgt[tuple(idx)]
        ii, jj = np.nonzero(self.land)
        self.cx, self.cy = (ii.mean() + self.x0 + 0.5) * CELL, (jj.mean() + self.y0 + 0.5) * CELL

    def lookup(self, x, y):
        i = np.floor(np.asarray(x, float) / CELL).astype(int) - self.x0
        j = np.floor(np.asarray(y, float) / CELL).astype(int) - self.y0
        ok = (i >= 0) & (j >= 0) & (i < self.land.shape[0]) & (j < self.land.shape[1])
        land = np.zeros(i.shape, bool)
        h = np.full(i.shape, np.nan)
        land[ok] = self.land[i[ok], j[ok]]
        h[ok] = self.height[i[ok], j[ok]]
        return land, h

    def density(self, xs: np.ndarray, ys: np.ndarray, sigma_cells: float = 6) -> np.ndarray:
        """Blurred density grid (log scale) of points, for map memory."""
        g = np.zeros(self.land.shape)
        i = np.floor(xs / CELL).astype(int) - self.x0
        j = np.floor(ys / CELL).astype(int) - self.y0
        ok = (i >= 0) & (j >= 0) & (i < g.shape[0]) & (j < g.shape[1])
        np.add.at(g, (i[ok], j[ok]), 1.0)
        g = ndimage.gaussian_filter(g, sigma_cells)
        g = g / max(g.sum(), 1e-9)
        return np.log(g + 1e-7)


def land_cells_sql(season: str) -> str:
    """Ground positions before the storm first moves (drop and loot): spread across the island, not herded by zones."""
    return f"""
        WITH first_shrink AS (SELECT z.match_id, min(z.start_shrink_t) AS t1 FROM zones z JOIN sel USING (match_id) GROUP BY 1)
        SELECT floor(p.x / {CELL})::INT AS cx, floor(p.y / {CELL})::INT AS cy, count(*) AS n, quantile_cont(p.z, 0.2) AS ground
        FROM positions p JOIN sel USING (match_id) JOIN matches m USING (match_id)
        JOIN first_shrink f ON f.match_id = p.match_id
        JOIN landings l ON l.match_id = p.match_id AND l.id = p.id
        WHERE m.season = '{season}' AND p.t >= l.land_t AND p.t < f.t1 AND abs(coalesce(p.vz, 0)) < 300
        GROUP BY 1, 2 HAVING count(*) >= 2
    """


# --------------------------------------------------------------------------- sequences and candidates
def sequences(z: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """match_id -> its zone circles in order (phase, x, y, r, zone_type of the change into that zone)."""
    out = {}
    for mid, g in z.sort_values("phase").groupby("match_id"):
        g = g.dropna(subset=["next_x", "next_y", "next_r"])
        if len(g) >= 3:
            out[mid] = g[["phase", "next_x", "next_y", "next_r", "zone_type"]].rename(
                columns={"next_x": "x", "next_y": "y", "next_r": "r"}).reset_index(drop=True)
    return out


def _angle(dx, dy):
    return np.arctan2(dy, dx)


def step_states(seqs: dict[str, pd.DataFrame], rng: np.random.Generator) -> list[dict]:
    """One state per zone change: everything known before the next zone appears, plus its candidates."""
    states = []
    for mid, s in seqs.items():
        for k in range(1, len(s)):
            cur, nxt = s.iloc[k - 1], s.iloc[k]
            zt = nxt["zone_type"] if isinstance(nxt["zone_type"], str) else "shrinking"
            if zt == "shrinking":
                rho = max(cur["r"] - nxt["r"], 1.0) * np.sqrt(rng.random(N_DISK))
                th = rng.random(N_DISK) * 2 * np.pi
            else:
                d = float(np.hypot(nxt["x"] - cur["x"], nxt["y"] - cur["y"]))
                rho = np.full(N_RING, d)
                th = (np.arange(N_RING) + rng.random()) * 2 * np.pi / N_RING
            states.append(dict(match_id=mid, zone=int(nxt["phase"]), type=zt, k=k, seq=s,
                               cand_x=cur["x"] + rho * np.cos(th), cand_y=cur["y"] + rho * np.sin(th),
                               true_x=float(nxt["x"]), true_y=float(nxt["y"]), next_r=float(nxt["r"])))
    return states


def _history(s: pd.DataFrame, k: int) -> dict:
    """What's known when zone k-1 (index k-1) is current: previous pulls and the drift since zone 1."""
    cur = s.iloc[k - 1]
    h = dict(cx=cur["x"], cy=cur["y"], cr=cur["r"], first_x=s.iloc[0]["x"], first_y=s.iloc[0]["y"])
    h["p1"] = _angle(cur["x"] - s.iloc[k - 2]["x"], cur["y"] - s.iloc[k - 2]["y"]) if k >= 2 else None
    h["p2"] = _angle(s.iloc[k - 2]["x"] - s.iloc[k - 3]["x"], s.iloc[k - 2]["y"] - s.iloc[k - 3]["y"]) if k >= 3 else None
    h["drift"] = _angle(cur["x"] - h["first_x"], cur["y"] - h["first_y"]) if k >= 2 else None
    return h


def land_features(land: LandMap, h: dict, px: np.ndarray, py: np.ndarray, r_new: float, zt: str,
                  end_radius: float | None = None) -> dict[str, np.ndarray]:
    """The expensive part (map lookups): depends only on the current zone and the candidate, so it's computed once."""
    cx, cy, cr = h["cx"], h["cy"], h["cr"]
    vx, vy = px - cx, py - cy
    dist = np.hypot(vx, vy)
    f: dict[str, np.ndarray] = {}
    if end_radius:
        f["dist"] = dist / end_radius
        f["dist2"] = f["dist"] ** 2
    else:
        allowed = max(cr - r_new, 1.0)
        f["edge"] = np.clip(dist / allowed, 0, 1.5) if zt == "shrinking" else np.zeros_like(dist)
        f["edge2"] = f["edge"] ** 2
    tx, ty = land.cx - cx, land.cy - cy
    ang = _angle(vx, vy)
    f["inward"] = np.cos(ang - _angle(tx, ty)) if np.hypot(tx, ty) > 0 else np.zeros_like(dist)
    f["centre"] = (np.hypot(px - land.cx, py - land.cy) - np.hypot(tx, ty)) / max(cr, 1.0)
    on, _ = land.lookup(px, py)
    f["on_land"] = on.astype(float)
    qx = px[:, None] + DISK[None, :, 0] * r_new
    qy = py[:, None] + DISK[None, :, 1] * r_new
    lp, hp = land.lookup(qx, qy)
    f["land"] = lp.mean(axis=1)
    hgt = np.nanmean(np.where(lp, hp, np.nan), axis=1) / 1000      # per 10 m
    f["height"] = np.nan_to_num(hgt - np.nanmean(hgt)) if np.isfinite(hgt).any() else np.zeros_like(dist)
    return f


def history_features(h: dict, px: np.ndarray, py: np.ndarray) -> dict[str, np.ndarray]:
    """The cheap part: directions relative to the zone history (recomputed for the shuffle control)."""
    vx, vy = px - h["cx"], py - h["cy"]
    ang = _angle(vx, vy)
    z = np.zeros(len(px))
    cos1 = np.cos(ang - h["p1"]) if h["p1"] is not None else z
    d_first_now = np.hypot(h["cx"] - h["first_x"], h["cy"] - h["first_y"])
    return dict(turn1=cos1, turn1sq=cos1 ** 2 if h["p1"] is not None else z,
                turn2=np.cos(ang - h["p2"]) if h["p2"] is not None else z,
                drift=np.cos(ang - h["drift"]) if h["drift"] is not None else z,
                home=(np.hypot(px - h["first_x"], py - h["first_y"]) - d_first_now) / max(h["cr"], 1.0))


def prior_feature(land: LandMap, grid: np.ndarray | None, px: np.ndarray, py: np.ndarray, center: bool = True) -> np.ndarray:
    if grid is None:
        return np.zeros(len(px))
    i = np.clip(np.floor(px / CELL).astype(int) - land.x0, 0, grid.shape[0] - 1)
    j = np.clip(np.floor(py / CELL).astype(int) - land.y0, 0, grid.shape[1] - 1)
    return grid[i, j]


def features(land: LandMap, h: dict, px: np.ndarray, py: np.ndarray, r_new: float, zt: str,
             prior: np.ndarray | None = None, end_radius: float | None = None) -> dict[str, np.ndarray]:
    f = land_features(land, h, px, py, r_new, zt, end_radius)
    f.update(history_features(h, px, py))
    f["prior"] = prior_feature(land, prior, px, py)
    return f


# --------------------------------------------------------------------------- conditional logit
class Logit:
    def __init__(self, names: list[str], lam: float = 1.0, split_types: bool = True):
        self.names, self.lam, self.split = names, lam, split_types
        self.beta = None
        self.mu = self.sd = None

    def _design(self, feats: dict, zt: str) -> np.ndarray:
        x = np.column_stack([feats[n] for n in self.names]) if self.names else np.zeros((len(next(iter(feats.values()))), 0))
        if self.mu is not None and x.shape[1]:
            x = (x - self.mu) / self.sd
        if self.split:
            shr = 1.0 if zt == "shrinking" else 0.0
            x = np.hstack([x * shr, x * (1 - shr)])
        return x

    def fit(self, rows: list[tuple[dict, dict, str]], sample: int = 64, seed: int = 3) -> "Logit":
        """rows: (true features, candidate features, zone type). Training uses a random sample of the alternatives
        per zone change (unbiased for this model; much faster); scoring always uses all of them."""
        if not self.names:
            return self
        rng = np.random.default_rng(seed)
        slim = []
        for t, c, zt in rows:
            n = len(next(iter(c.values())))
            if n > sample:
                idx = rng.choice(n, sample, replace=False)
                c = {k: v[idx] for k, v in c.items()}
            slim.append((t, c, zt))
        rows = slim
        allx = np.vstack([np.column_stack([c[n] for n in self.names]) for _, c, _ in rows])
        self.mu, self.sd = allx.mean(0), allx.std(0) + 1e-6
        Xs = [np.vstack([self._design(t, zt), self._design(c, zt)]) for t, c, zt in rows]
        n_max = max(len(x) for x in Xs)
        F = Xs[0].shape[1]
        X = np.zeros((len(Xs), n_max, F))
        mask = np.zeros((len(Xs), n_max), bool)
        for i, x in enumerate(Xs):
            X[i, : len(x)] = x
            mask[i, : len(x)] = True

        def nll(b):
            s = np.where(mask, X @ b, -np.inf)
            lse = logsumexp(s, axis=1)
            p = np.where(mask, np.exp(s - lse[:, None]), 0.0)
            val = (lse - s[:, 0]).sum() + self.lam * b @ b
            grad = (p[:, :, None] * X).sum((0, 1)) - X[:, 0, :].sum(0) + 2 * self.lam * b
            return val, grad
        self.beta = minimize(nll, np.zeros(F), jac=True, method="L-BFGS-B").x
        return self

    def scores(self, feats: dict, zt: str) -> np.ndarray:
        n = len(next(iter(feats.values())))
        if not self.names or self.beta is None:
            return np.zeros(n)
        return self._design(feats, zt) @ self.beta


def evaluate(model: Logit, true_f: dict, cand_f: dict, zt: str) -> dict:
    s_t = model.scores(true_f, zt)[0]
    s_c = model.scores(cand_f, zt)
    rank = float(((s_c < s_t).sum() + 0.5 * (s_c == s_t).sum()) / len(s_c))
    allsc = np.append(s_c, s_t)
    logp = s_t - logsumexp(allsc)
    return dict(rank=rank, hit=rank >= 1 - TOP, bits=float((logp + np.log(len(allsc))) / np.log(2)))


def folds_for(matches: list[str], rng: np.random.Generator) -> dict[str, int]:
    return {m: i % FOLDS for i, m in enumerate(rng.permutation(matches))}


def priors_for(land: LandMap, seqs: dict[str, pd.DataFrame], train: set[str], final: bool = False) -> dict:
    """Map memory from training matches only: density of each zone number's centres (or of final centres)."""
    if final:
        pts = np.array([[s.iloc[-1]["x"], s.iloc[-1]["y"]] for m, s in seqs.items() if m in train])
        return {"final": land.density(pts[:, 0], pts[:, 1])} if len(pts) else {}
    out = {}
    by_zone: dict[int, list] = {}
    for m, s in seqs.items():
        if m in train:
            for _, row in s.iterrows():
                by_zone.setdefault(int(row["phase"]), []).append((row["x"], row["y"]))
    for zone, pts in by_zone.items():
        a = np.array(pts)
        out[zone] = land.density(a[:, 0], a[:, 1])
    return out


# --------------------------------------------------------------------------- validation harness
def _prior_rows(land, seqs, states, train_ids, rng, final=False):
    """Map-memory feature values: test rows use priors from all training matches; training rows use priors from
    the other half of the training matches (so a match never informs its own map memory)."""
    ids = sorted(train_ids)
    half = {m: i % 2 for i, m in enumerate(rng.permutation(ids))}
    full = priors_for(land, seqs, set(ids), final)
    halves = [priors_for(land, seqs, {m for m in ids if half[m] != h}, final) for h in (0, 1)]
    return full, halves, half


def _prior_grid(priors: dict, st: dict, final: bool):
    return priors.get("final") if final else priors.get(st["zone"])


def ladder(land: LandMap, seqs: dict[str, pd.DataFrame], dates: dict[str, str], seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    states = step_states(seqs, rng)
    for st in states:
        st["h"] = _history(st["seq"], st["k"])
        tx, ty = np.array([st["true_x"]]), np.array([st["true_y"]])
        st["land_c"] = land_features(land, st["h"], st["cand_x"], st["cand_y"], st["next_r"], st["type"])
        st["land_t"] = land_features(land, st["h"], tx, ty, st["next_r"], st["type"])
        st["base_c"] = {**st["land_c"], **history_features(st["h"], st["cand_x"], st["cand_y"])}
        st["base_t"] = {**st["land_t"], **history_features(st["h"], tx, ty)}
    matches = sorted(seqs)
    folds = folds_for(matches, rng)

    def with_prior(st, grid):
        c, t = dict(st["base_c"]), dict(st["base_t"])
        pc = prior_feature(land, grid, st["cand_x"], st["cand_y"])
        pt = prior_feature(land, grid, np.array([st["true_x"]]), np.array([st["true_y"]]))
        c["prior"], t["prior"] = pc - pc.mean(), pt - pc.mean()
        return t, c

    rungs = ["one_step", "history", "memory"]
    results = {r: [] for r in rungs + ["shuffled"]}
    fold_models: dict[int, dict] = {}
    for f in range(FOLDS):
        train = [s for s in states if folds[s["match_id"]] != f]
        test = [s for s in states if folds[s["match_id"]] == f]
        train_ids = {s["match_id"] for s in train}
        full, halves, half = _prior_rows(land, seqs, train, train_ids, rng)
        tr_rows = {r: [] for r in rungs}
        for s in train:
            t, c = with_prior(s, _prior_grid(halves[1 - half[s["match_id"]]], s, False))
            for r in rungs:
                tr_rows[r].append((t, c, s["type"]))
        models = {r: Logit(STEP_SETS[r]).fit(tr_rows[r]) for r in rungs}
        fold_models[f] = dict(models=models, prior=full)
        for s in test:
            t, c = with_prior(s, _prior_grid(full, s, False))
            for r in rungs:
                ev = evaluate(models[r], t, c, s["type"])
                results[r].append(dict(match_id=s["match_id"], zone=s["zone"], type=s["type"], **ev))
        # Shuffle control: the same history model, but each state's history taken from another match.
        shuf_train, shuf_test = [], []
        pool = {}
        for s in states:
            pool.setdefault(s["k"], []).append(s)
        for group, out in ((train, shuf_train), (test, shuf_test)):
            for s in group:
                donor = pool[s["k"]][rng.integers(len(pool[s["k"]]))]
                h = dict(s["h"], p1=donor["h"]["p1"], p2=donor["h"]["p2"], drift=donor["h"]["drift"],
                         first_x=donor["h"]["first_x"] - donor["h"]["cx"] + s["h"]["cx"], first_y=donor["h"]["first_y"] - donor["h"]["cy"] + s["h"]["cy"])
                c = {**s["land_c"], **history_features(h, s["cand_x"], s["cand_y"])}
                t = {**s["land_t"], **history_features(h, np.array([s["true_x"]]), np.array([s["true_y"]]))}
                out.append((t, c, s["type"], s))
        m_sh = Logit(STEP_SETS["history"]).fit([(t, c, zt) for t, c, zt, _ in shuf_train])
        for t, c, zt, s in shuf_test:
            results["shuffled"].append(dict(match_id=s["match_id"], zone=s["zone"], type=zt, **evaluate(m_sh, t, c, zt)))

    res = {r: pd.DataFrame(v) for r, v in results.items()}
    # Choose the rung with the best out-of-sample information gain (a richer model must earn its place).
    best = max(rungs, key=lambda r: res[r]["bits"].mean() if len(res[r]) else -1e9)

    # Fresh-matches test: train on the older 80% of matches, test on the newest 20%.
    order = sorted(matches, key=lambda m: (dates.get(m) or "", m))
    cut = max(1, int(len(order) * 0.8))
    old, new = set(order[:cut]), set(order[cut:])
    fresh = []
    if new and len(old) >= 5:
        full, halves, half = _prior_rows(land, seqs, None, old, rng)
        rows = []
        for s in (s for s in states if s["match_id"] in old):
            t, c = with_prior(s, _prior_grid(halves[1 - half[s["match_id"]]], s, False))
            rows.append((t, c, s["type"]))
        m = Logit(STEP_SETS[best]).fit(rows)
        for s in (s for s in states if s["match_id"] in new):
            t, c = with_prior(s, _prior_grid(full, s, False))
            fresh.append(dict(match_id=s["match_id"], zone=s["zone"], **evaluate(m, t, c, s["type"])))

    return dict(states=states, folds=folds, results=res, best=best, fresh=pd.DataFrame(fresh),
                fold_models=fold_models, with_prior=with_prior)


def endgame(land: LandMap, seqs: dict[str, pd.DataFrame], folds: dict[str, int], seed: int = 11) -> dict:
    """Predict the final zone's centre directly from each earlier zone."""
    rng = np.random.default_rng(seed)
    base = []
    for mid, s in seqs.items():
        last = s.iloc[-1]
        for k in range(2, len(s)):          # current zone = s.iloc[k-1]; must be before the final zone
            h = _history(s, k)
            base.append(dict(match_id=mid, zone_now=int(s.iloc[k - 1]["phase"]), h=h, true_x=float(last["x"]), true_y=float(last["y"]),
                             r_final=float(last["r"]), dist=float(np.hypot(last["x"] - h["cx"], last["y"] - h["cy"]))))
    if len(base) < 20:
        return dict(results=pd.DataFrame(), fold_models={}, base=base)
    # The search area (where to look, not what's likely) holds the real final centre in 97.5% of cases per zone.
    bdf = pd.DataFrame(base)
    radius = (bdf.groupby("zone_now")["dist"].quantile(0.975) * 1.05).to_dict()
    glob = float(bdf["dist"].quantile(0.975) * 1.05)
    for b in base:
        R = float(radius.get(b["zone_now"], glob))
        rho = R * np.sqrt(rng.random(N_END))
        th = rng.random(N_END) * 2 * np.pi
        b.update(R=R, px=b["h"]["cx"] + rho * np.cos(th), py=b["h"]["cy"] + rho * np.sin(th))
        tx, ty = np.array([b["true_x"]]), np.array([b["true_y"]])
        b["c"] = {**land_features(land, b["h"], b["px"], b["py"], b["r_final"], "end", R), **history_features(b["h"], b["px"], b["py"])}
        b["t"] = {**land_features(land, b["h"], tx, ty, b["r_final"], "end", R), **history_features(b["h"], tx, ty)}

    def rows_for(b, grid):
        c, t = dict(b["c"]), dict(b["t"])
        pc = prior_feature(land, grid, b["px"], b["py"])
        pt = prior_feature(land, grid, np.array([b["true_x"]]), np.array([b["true_y"]]))
        c["prior"], t["prior"] = pc - pc.mean(), pt - pc.mean()
        return t, c
    out, fold_models = [], {}
    for f in range(FOLDS):
        train = [b for b in base if folds.get(b["match_id"], -1) != f]
        test = [b for b in base if folds.get(b["match_id"], -1) == f]
        if len(train) < 10 or not test:
            continue
        train_ids = {b["match_id"] for b in train}
        full, halves, half = _prior_rows(land, seqs, None, train_ids, rng, final=True)
        train_rows = [(*rows_for(b, halves[1 - half[b["match_id"]]].get("final")), "end") for b in train]
        model = Logit(END_FEATURES, split_types=False).fit(train_rows)
        rules = Logit(END_RULES, split_types=False).fit(train_rows)
        fold_models[f] = dict(model=model, rules=rules, prior=full.get("final"))
        for b in test:
            t, c = rows_for(b, full.get("final"))
            ev = evaluate(model, t, c, "end")
            ev_rules = evaluate(rules, t, c, "end")
            sc = model.scores(c, "end")
            bi = int(np.argmax(sc))
            ri = int(rng.integers(len(b["px"])))
            out.append(dict(match_id=b["match_id"], zone_now=b["zone_now"], **ev, rules_hit=ev_rules["hit"], rules_bits=ev_rules["bits"],
                            err_m=float(np.hypot(b["px"][bi] - b["true_x"], b["py"][bi] - b["true_y"]) / 100),
                            err_random_m=float(np.hypot(b["px"][ri] - b["true_x"], b["py"][ri] - b["true_y"]) / 100),
                            radius_m=b["R"] / 100, in_reach=b["dist"] <= b["R"]))
    res = pd.DataFrame(out)
    # Same principle as the next-zone ladder: the fuller endgame model is used only if it beats the rules out of sample.
    chosen = "full" if len(res) and res["bits"].mean() > res["rules_bits"].mean() else "rules"
    return dict(results=res, fold_models=fold_models, base=base, rows_for=rows_for, chosen=chosen)
