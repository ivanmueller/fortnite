"""Statistics used by the analyses. Circular tests treat angles in degrees."""
from __future__ import annotations

import numpy as np
from scipy import stats as sps


def rayleigh(deg, axial: bool = False) -> dict:
    """Rayleigh test for a preferred direction. axial=True treats theta and theta+180 as equal."""
    th = np.radians(np.asarray(deg, float))
    th = th[np.isfinite(th)]
    n = len(th)
    if n < 2:
        return dict(n=n, R=np.nan, mean_deg=np.nan, p=np.nan)
    if axial:
        th = 2 * th
    C, S = np.cos(th).mean(), np.sin(th).mean()
    R = float(np.hypot(C, S))
    mean = float(np.degrees(np.arctan2(S, C)) % 360)
    if axial:
        mean = (mean / 2) % 180
    Z = n * R * R
    p = np.exp(-Z) * (1 + (2 * Z - Z**2) / (4 * n) - (24 * Z - 132 * Z**2 + 76 * Z**3 - 9 * Z**4) / (288 * n**2))
    return dict(n=n, R=R, mean_deg=mean, p=float(min(max(p, 0.0), 1.0)))


def circ_mean(deg) -> float:
    a = np.radians(np.asarray(deg, float))
    a = a[np.isfinite(a)]
    if not len(a):
        return np.nan
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) % 360)


def ks_uniform(u) -> dict:
    u = np.clip(np.asarray(u, float), 0, 1)
    u = u[np.isfinite(u)]
    if len(u) < 2:
        return dict(n=len(u), D=np.nan, p=np.nan)
    r = sps.kstest(u, "uniform")
    return dict(n=len(u), D=float(r.statistic), p=float(r.pvalue))


def ttest_mean(x, mu: float) -> dict:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 3 or np.std(x) == 0:
        return dict(n=len(x), mean=float(np.mean(x)) if len(x) else np.nan,
                    p=0.0 if len(x) >= 3 and np.mean(x) != mu else np.nan)
    r = sps.ttest_1samp(x, mu)
    return dict(n=len(x), mean=float(x.mean()), t=float(r.statistic), p=float(r.pvalue))


def ks_2samp(a, b) -> dict:
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    b = np.asarray(b, float); b = b[np.isfinite(b)]
    if len(a) < 2 or len(b) < 2:
        return dict(n_a=len(a), n_b=len(b), D=np.nan, p=np.nan)
    r = sps.ks_2samp(a, b)
    return dict(n_a=len(a), n_b=len(b), D=float(r.statistic), p=float(r.pvalue))


def circular_permutation(a_deg, b_deg, n_perm: int = 2000, seed: int = 0) -> dict:
    """
    Do two groups of angles have different direction distributions?
    Statistic: distance between the groups' mean resultant vectors. p from label permutations.
    Pass one angle per match so observations are independent.
    """
    a = np.radians(np.asarray(a_deg, float)); a = a[np.isfinite(a)]
    b = np.radians(np.asarray(b_deg, float)); b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return dict(n_a=len(a), n_b=len(b), stat=np.nan, p=np.nan)
    va = np.column_stack([np.cos(a), np.sin(a)])
    vb = np.column_stack([np.cos(b), np.sin(b)])
    obs = float(np.linalg.norm(va.mean(0) - vb.mean(0)))
    pooled = np.vstack([va, vb])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        idx = rng.permutation(len(pooled))
        d = np.linalg.norm(pooled[idx[: len(a)]].mean(0) - pooled[idx[len(a):]].mean(0))
        hits += d >= obs
    return dict(n_a=len(a), n_b=len(b), stat=obs, p=(hits + 1) / (n_perm + 1))


def spearman(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 4 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return np.nan
    return float(sps.spearmanr(x[ok], y[ok]).statistic)
