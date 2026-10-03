"""
surge_rule.py - which damage competitive surge counts, as candidate rules the data can test.

Epic changed tournament surge in October 2025 from damage dealt to net damage (damage dealt minus damage taken,
counting only damage between players), and surges the teams with the lowest net damage. Older seasons used
damage dealt. Rather than assume either, every candidate is scored on every detected surge:

  measure   dealt   damage dealt to players on other teams
            net     damage dealt minus damage taken from players on other teams
  level     player  each player's own damage
            team    the team's combined damage, shared by its members (dead teammates' damage still counts)
  window    see surge_study.WINDOWS (whole match, since the zone appeared, ...)

The candidate whose ranking best separates surged from safe players wins. Epic's announced rule (team net
damage) wins ties within TIE_MARGIN, so noise in a small sample can't override the published rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MEASURES = {"dealt": "damage dealt", "net": "net damage (dealt − taken)"}
LEVELS = {"player": "player", "team": "team"}
OFFICIAL = ("net", "team")        # Epic, Oct 2025: net damage, lowest teams surged
TIE_MARGIN = 0.02                 # on the 0.5–1.0 separation score


def scores(hits: pd.DataFrame, ids, teams) -> dict[tuple[str, str], np.ndarray]:
    """
    Every measure and level for the given players, from one match's hits inside one window.
    hits: attacker_id, target_id, attacker_team, target_team, amount (player-vs-player, other teams only).
    ids, teams: the players to score and their team index, in the same order. Returns arrays in that order.
    """
    ids = pd.Series(list(ids))
    teams = pd.Series(list(teams))
    if hits is None or hits.empty:
        z = np.zeros(len(ids))
        return {(m, lv): z.copy() for m in MEASURES for lv in LEVELS}
    dealt_p = hits.groupby("attacker_id")["amount"].sum()
    taken_p = hits.groupby("target_id")["amount"].sum()
    dealt_t = hits.groupby("attacker_team")["amount"].sum()
    taken_t = hits.groupby("target_team")["amount"].sum()
    dp = ids.map(dealt_p).fillna(0.0).to_numpy(float)
    tp = ids.map(taken_p).fillna(0.0).to_numpy(float)
    dt = teams.map(dealt_t).fillna(0.0).to_numpy(float)
    tt = teams.map(taken_t).fillna(0.0).to_numpy(float)
    return {("dealt", "player"): dp, ("net", "player"): dp - tp, ("dealt", "team"): dt, ("net", "team"): dt - tt}


def choose(mean_auc: dict[tuple[str, str, str], float], default_window: str = "zone") -> tuple[str, str, str]:
    """The (window, measure, level) that best separates surged from safe players; Epic's rule wins near-ties."""
    valid = {k: v for k, v in mean_auc.items() if v == v}
    if not valid:
        return (default_window, *OFFICIAL)
    best = max(valid, key=valid.get)
    official = [k for k in valid if k[1:] == OFFICIAL]
    if official:
        ob = max(official, key=valid.get)
        if valid[ob] >= valid[best] - TIE_MARGIN:
            return ob
    return best


def label(window_label: str, measure: str, level: str) -> str:
    """'team net damage (dealt − taken), since the zone appeared'"""
    return f"{LEVELS[level]} {MEASURES[measure]}, {window_label[0].lower()}{window_label[1:]}"


def teammates_agree(per: pd.DataFrame) -> float:
    """
    Of the teams with two or more members alive at a surge, the share where every member had the same outcome
    (all surged or all safe). Near 100% means surge picks teams, not players.
    per: episode, team_index, surged (one row per living player per episode).
    """
    if per is None or per.empty or "team_index" not in per:
        return np.nan
    g = per.dropna(subset=["team_index"]).groupby(["episode", "team_index"])["surged"]
    n, k = g.size(), g.sum()
    multi = n >= 2
    if not multi.any():
        return np.nan
    return float(((k[multi] == 0) | (k[multi] == n[multi])).mean())
