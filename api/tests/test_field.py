"""
Field and top teams: picking top teams, the same-team comparison, the study end to end on demo tables where every team
recurs across matches, and the crowd (field and top teams in similar spots) in the match room's decisions.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(Path(__file__).parent))
DEMO = {"dataset": "demo"}


def test_top_teams_by_points_per_game_with_enough_games():
    from fnlab.analyses.field_study import MIN_GAMES, top_teams
    teams = pd.DataFrame({"games": [MIN_GAMES] * 30 + [2], "ppg": list(range(30)) + [99.0]}, index=[f"t{i}" for i in range(31)])
    top, need = top_teams(teams)
    assert need == MIN_GAMES and list(top) == ["t29", "t28", "t27"]          # top 10% of 30 = 3; the 2-game team can't qualify
    few = pd.DataFrame({"games": [3] * 12, "ppg": range(12)}, index=[f"s{i}" for i in range(12)])
    top2, need2 = top_teams(few)
    assert need2 == 3 and len(top2) == 3                                     # steps the minimum down when too few have 8+


def test_same_team_compares_a_team_with_itself():
    from fnlab.analyses.field_study import same_team
    rows = []
    for k in range(8):                                   # every team scores 10 more in games where it stayed back
        for g in range(6):
            stayed = float(g % 2)
            rows.append(dict(key=f"k{k}", stayed_13=stayed, points=5 * k + 10 * stayed + (g % 3)))
    res = same_team(pd.DataFrame(rows), "stayed_13", [f"k{k}" for k in range(8)])
    assert res["n"] == 8 and np.isclose(res["mean"], 10, atol=1)


@pytest.fixture(scope="module")
def field_data(tmp_path_factory):
    d = tmp_path_factory.mktemp("field")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                    "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
    from event_fixture import add_events, add_recurring_field, add_recurring_team
    add_events(d / "tables", rule="team_net", seed=1)
    add_recurring_field(d / "tables")
    add_recurring_team(d / "tables")
    return d


def _clear():
    from fnlab import engine
    from fnlab.analyses import decides, engine_review, expected_points, field_study, playbook, surge_study, zone_forecast
    for c in (surge_study._MEASURED, expected_points._CACHE, engine_review._CACHE, playbook._CACHE, decides._CACHE, zone_forecast._CACHE,
              engine._CUTOFF, field_study._SNAP):
        c.clear()


@pytest.fixture(scope="module")
def runs(client, field_data):
    from fnlab import config
    old = config.DATASETS["demo"]
    config.DATASETS["demo"] = field_data
    _clear()
    try:
        field = client.post("/api/analyses/field/run", json={"filters": DEMO, "params": {"team": "clix, rapid"}}).json()
        room = client.post("/api/analyses/match_room/run", json={"filters": DEMO, "params": {"team": "clix, rapid"}}).json()
    finally:
        config.DATASETS["demo"] = old
        _clear()
    return field, room


def test_field_study_end_to_end(runs):
    j, _ = runs
    assert j["status"] == "ok" and not j["warnings"]
    m = {x["label"]: x["value"] for x in j["metrics"]}
    assert m["Teams in the field"] == "33" and int(m["Top teams"]) >= 3 and m["Your games"] == "24"
    t = {x["title"]: x for x in j["tables"]}
    assert {"Field, top teams and you", "Top teams", "Top 3 in this selection", "Where you differ most from the top teams"} <= set(t)
    main = t["Field, top teams and you"]
    cols = main["columns"]
    assert cols[:5] == ["Behaviour", "Field", "Top teams", "Top 3 in selection", "You"]
    ok = ("Copy this: do ", "Top teams' habit: ", "The field underuses it", "The field overuses it", "Helps the same team",
          "Hurts the same team", "Mixed", "No clear difference")
    assert all(any(str(r[cols.index("Verdict")]).startswith(v) for v in ok) for r in main["rows"])
    labels = [r[0] for r in main["rows"]]
    assert "Surged at a surge check (share of the checks alive for)" in labels           # a rate, not 'ever surged'
    assert any(lab.startswith("Eliminations per minute alive") for lab in labels)          # per minute alive, not a count


def test_match_room_decisions_carry_the_crowd(runs):
    _, room = runs
    ds = next(c for c in room["charts"] if c["kind"] == "match_replay")["options"]["decisions"]
    withc = [d for d in ds if d["crowd"]]
    assert withc
    for d in withc:
        c = d["crowd"]
        assert c["n_field"] >= 20 and abs(sum(c["field"].values()) - 1) < 0.05
        assert c["top"] is None or abs(sum(c["top"].values()) - 1) < 0.05
        assert isinstance(c["second_look"], bool)
