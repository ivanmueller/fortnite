"""
The three pitch blockers from the audit:
  1. one scoring table, in scoring.json, used everywhere (FNCS 2026 Duos pays down to 25th, not 15th);
  2. surge measured as candidate rules, recovering team net damage when that's what the matches follow;
  3. team and lobby skill as inputs to the points model, and in the baseline it must beat.
"""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))          # so fnlab imports work even when no test has started the API yet
DEMO = {"dataset": "demo"}


# ---------------------------------------------------------------- 1. scoring

def test_duos_finals_table_pays_down_to_25th():
    from fnlab import scoring
    s = scoring.get("fncs_2026_duos_finals")
    assert s.paid_places == 25
    assert [s.points(p) for p in (1, 2, 3, 4, 5)] == [65, 56, 52, 48, 44]
    assert s.points(15) == 22 and s.points(16) == 20 and s.points(25) == 2 and s.points(26) == 0
    assert all(s.points(p) - s.points(p + 1) == 2 for p in range(6, 25))      # 2 points a place from 6th to 25th
    assert s.elimination == 4


def test_every_scheme_is_valid_and_solos_pays_to_49th():
    from fnlab import scoring
    all_ = scoring.schemes()
    assert scoring.DEFAULT in all_
    for s in all_.values():
        assert not scoring.validate(s.table), s.id
    solo = all_["fncs_2026_solos_finals"]
    assert solo.points(1) == 60 and solo.points(5) == 46 and solo.points(49) == 2 and solo.points(50) == 0
    assert all_["fncs_2026_solos_qualifiers"].elimination == 2


def test_validation_rejects_broken_tables():
    from fnlab import scoring
    assert scoring.validate({1: 65, 3: 52})                 # gap
    assert scoring.validate({1: 50, 2: 56})                 # rises
    assert scoring.validate({2: 56})                        # doesn't start at 1st
    assert not scoring.validate({1: 65, 2: 56, 3: 0})


def test_scheme_totals_and_override(monkeypatch):
    from fnlab import scoring
    s = scoring.get("fncs_2026_duos_finals")
    assert s.total(pd.Series([1, 20, 30]), pd.Series([3, 0, 2])).tolist() == [77.0, 12.0, 8.0]
    monkeypatch.setenv("FN_SCORING", "fncs_2026_solos_finals")
    assert scoring.active().id == "fncs_2026_solos_finals"
    monkeypatch.setenv("FN_SCORING", "no_such_scheme")
    with pytest.raises(ValueError):
        scoring.active()


def test_no_scoring_table_is_hardcoded_anywhere():
    """Points come from scoring.json only: no placement table or per-elimination constant in the code."""
    offenders = []
    for p in (REPO / "api" / "fnlab").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if re.search(r"PLACEMENT_POINTS|KILL_POINTS|\{\s*1\s*:\s*65\s*,", text):
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, offenders


# ---------------------------------------------------------------- 2. surge rule

def test_surge_rule_scores():
    from fnlab import surge_rule as sr
    hits = pd.DataFrame({"attacker_id": [1, 1, 3], "target_id": [3, 4, 1], "attacker_team": [10, 10, 20],
                         "target_team": [20, 20, 10], "amount": [50.0, 30.0, 20.0]})
    s = sr.scores(hits, ids=[1, 2, 3, 4], teams=[10, 10, 20, 20])
    assert s[("dealt", "player")].tolist() == [80, 0, 20, 0]
    assert s[("net", "player")].tolist() == [60, 0, -30, -30]
    assert s[("dealt", "team")].tolist() == [80, 80, 20, 20]
    assert s[("net", "team")].tolist() == [60, 60, -60, -60]
    empty = sr.scores(hits.iloc[0:0], ids=[1, 2], teams=[10, 10])
    assert all(v.tolist() == [0.0, 0.0] for v in empty.values())


def test_surge_rule_choice_prefers_epics_rule_only_on_near_ties():
    from fnlab import surge_rule as sr
    near = {("zone", "net", "team"): 0.90, ("zone", "dealt", "player"): 0.91}
    assert sr.choose(near) == ("zone", "net", "team")
    clear = {("zone", "net", "team"): 0.80, ("zone", "dealt", "player"): 0.95}
    assert sr.choose(clear) == ("zone", "dealt", "player")
    assert sr.choose({}) == ("zone", "net", "team")


def test_teammates_agree():
    from fnlab import surge_rule as sr
    per = pd.DataFrame({"episode": [1, 1, 1, 1, 1], "team_index": [1, 1, 2, 2, 3], "surged": [True, True, True, False, False]})
    assert sr.teammates_agree(per) == 0.5            # team 1 agrees, team 2 doesn't, team 3 has one player


@pytest.fixture
def event_data(tmp_path, monkeypatch):
    """The demo tables plus health and damage events with planted surge, swapped in for the demo dataset."""
    def build(rule: str) -> None:
        d = tmp_path / rule
        py = sys.executable
        subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                        "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
        subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
        sys.path.insert(0, str(Path(__file__).parent))
        from event_fixture import add_events
        add_events(d / "tables", rule=rule, seed=1)
        from fnlab import config
        monkeypatch.setitem(config.DATASETS, "demo", d)
        _clear_caches()
    yield build
    _clear_caches()


def _clear_caches():
    from fnlab import engine
    from fnlab.analyses import decides, engine_review, expected_points, playbook, surge_study, zone_forecast
    for c in (surge_study._MEASURED, expected_points._CACHE, engine_review._CACHE, playbook._CACHE, decides._CACHE,
              zone_forecast._CACHE, engine._CUTOFF):
        c.clear()


def _rule(client):
    j = client.post("/api/analyses/surge_study/run", json={"filters": DEMO}).json()
    metrics = {m["label"]: m["value"] for m in j["metrics"]}
    return metrics["Damage surge counts"].lower(), float(metrics["Teammates surged together"].rstrip("%")) / 100


def test_surge_study_recovers_team_net_damage(client, event_data):
    event_data("team_net")
    rule, together = _rule(client)
    assert rule.startswith("team net damage"), rule
    assert together >= 0.9
    j = client.post("/api/analyses/surge/run", json={"filters": DEMO}).json()
    assert "team net damage" in j["headline"]


def test_surge_study_recovers_the_old_rule_too(client, event_data):
    """The study isn't just defaulting to Epic's rule: when matches follow player damage dealt, it says so."""
    event_data("player_dealt")
    rule, together = _rule(client)
    assert rule.startswith("player damage dealt"), rule
    assert together < 0.9


# ---------------------------------------------------------------- 3. skill

def test_points_model_has_skill_and_net_damage_inputs():
    from fnlab import ep_model as ep
    from fnlab import engine
    assert {"team_pr_rank", "lobby_pr_rank", "surge_rank"} <= set(ep.FEATURES)
    assert "dealt_rank" not in ep.FEATURES
    assert {"team_pr_rank", "lobby_pr_rank"} <= set(engine.LIVE)
    assert ep.MONOTONE.get("team_pr_rank") == -1


def test_expected_points_reports_skill_scoring_and_paid_places(client):
    _clear_caches()
    j = client.post("/api/analyses/expected_points/run", json={"filters": DEMO}).json()
    metrics = {m["label"]: m["value"] for m in j["metrics"]}
    assert metrics["Scoring"] == "FNCS 2026 Duos, online finals"
    assert metrics["Skill input"].startswith("Power Rankings for")
    assert "Chance of finishing in the points, by teams left" in {c["title"] for c in j["charts"]}
    from fnlab.analyses import expected_points
    data = next(iter(expected_points._CACHE.values()))
    team = data["team"]
    assert team["team_pr_rank"].nunique() > 5 and team["lobby_pr_rank"].nunique() > 1     # demo players have Power Rankings
    r25 = team[(team["placement"] == 25) & (team["kills_total"] == team["kills"])]
    assert len(r25) and np.allclose(r25["future_pts"], 2.0)                                 # 25th scores 2 (it scored 0 before)
    _clear_caches()


def test_skill_is_geometric_mean_of_the_team_and_median_of_the_lobby():
    import duckdb
    from fnlab import ep_model as ep
    con = duckdb.connect()
    con.register("pl", pd.DataFrame({"match_id": ["m"] * 4, "team_index": [1, 1, 2, 2], "pr_rank": [100.0, 10000.0, None, 400.0],
                                     "is_bot": [False] * 4}))
    con.execute("CREATE VIEW players AS SELECT * FROM pl")
    con.execute("CREATE TEMP TABLE sel AS SELECT 'm' AS match_id")
    sk = ep.skill(con).set_index("team_index")
    assert np.isclose(sk.loc[1, "team_pr_rank"], 1000.0)                          # sqrt(100 * 10,000)
    assert np.isclose(sk.loc[2, "team_pr_rank"], np.sqrt(ep.UNRANKED * 400.0))    # unranked counts as UNRANKED
    assert np.isclose(sk.loc[1, "lobby_pr_rank"], np.median([100, 10000, ep.UNRANKED, 400]))
    assert np.isclose(ep.skill_coverage(con), 0.75)
