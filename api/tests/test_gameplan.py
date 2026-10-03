"""
The game plan: built only from matches before the event's first day, every block present on a dataset that has them,
and checked against the event. Uses the demo tables plus a recurring team, named spots and surge events (event_fixture.py).
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(Path(__file__).parent))
DEMO = {"dataset": "demo"}
AS_OF = "2026-08-15"


def _clear_caches():
    from fnlab import engine
    from fnlab.analyses import decides, engine_review, expected_points, playbook, surge_study, zone_forecast
    for c in (surge_study._MEASURED, expected_points._CACHE, engine_review._CACHE, playbook._CACHE, decides._CACHE,
              zone_forecast._CACHE, engine._CUTOFF):
        c.clear()


@pytest.fixture(scope="module")
def plan_data(tmp_path_factory):
    d = tmp_path_factory.mktemp("plan")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                    "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
    from event_fixture import add_events, add_recurring_team
    add_events(d / "tables", rule="team_net", seed=1)
    add_recurring_team(d / "tables")
    return d


@pytest.fixture
def plan_client(client, plan_data, monkeypatch):
    from fnlab import config
    monkeypatch.setitem(config.DATASETS, "demo", plan_data)
    _clear_caches()
    yield client
    _clear_caches()


def _run(c, params, filters=None):
    j = c.post("/api/analyses/gameplan/run", json={"filters": {**DEMO, **(filters or {})}, "params": params}).json()
    assert j["status"] == "ok", j
    return j


def _tables(j):
    return {t["title"]: t for t in j["tables"]}


def test_asks_for_names_and_lists_teams(plan_client):
    j = _run(plan_client, {})
    assert "Type the team's names" in j["headline"]
    assert "Teams in the history" in _tables(j)


def test_builds_every_block_and_checks_the_event(plan_client, plan_data):
    j = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF})
    assert not [w for w in j["warnings"] if w.startswith("Couldn't build")], j["warnings"]
    t = _tables(j)
    blocks = [row[0] for row in t["Plan"]["rows"]]
    assert blocks == ["Drop", "Zones 1–2", "Rotations", "Surge", "Habits", "Points", "Check"], blocks
    m = {x["label"]: x["value"] for x in j["metrics"]}
    dates = pd.read_parquet(plan_data / "tables" / "matches.parquet")["match_date"].astype(str)
    assert m["History"] == f"{int((dates < AS_OF).sum())} matches before {AS_OF}"
    assert m["Team"].startswith("clix")
    check = t["Check: the event, game by game"]["rows"]
    assert len(check) == int((dates >= AS_OF).sum())          # the team plays every event game in this fixture


def test_plan_uses_only_matches_before_the_event(plan_client):
    """Leakage check: the plan is identical whether or not the event's matches are in the selection."""
    with_event = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF})
    day_before = (pd.Timestamp(AS_OF) - pd.Timedelta(days=1)).date().isoformat()
    history_only = _run(plan_client, {"team": "clix, rapid"}, {"date_to": day_before})
    a, b = _tables(with_event), _tables(history_only)
    plan_tables = [k for k in a if not k.startswith("Check") and k != "Plan"]
    assert plan_tables and all(a[k]["rows"] == b[k]["rows"] for k in plan_tables), [k for k in plan_tables if a[k]["rows"] != b[k]["rows"]]
    calls_a = [r for r in a["Plan"]["rows"] if r[0] != "Check"]
    assert calls_a == b["Plan"]["rows"]


def test_small_samples_are_left_out(plan_client):
    j = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF})
    drop = _tables(j)["Drop: how contested your spot is"]
    conf, contested = drop["columns"].index("Confidence"), drop["columns"].index("Contested")
    for row in drop["rows"]:
        if row[conf] == "Too few":
            assert row[contested] == "–"


def test_points_exchange_rate_follows_the_scheme(plan_client):
    j = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF})
    rows = _tables(j)["Points: places against eliminations"]["rows"]
    assert ["6th–25th", "2", "2.0 places"] in rows and ["1st", "9", "0.4 places"] in rows
    solo = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF, "scheme": "fncs_2026_solos_finals"})
    assert ["4th–48th", "1", "4.0 places"] in _tables(solo)["Points: places against eliminations"]["rows"]


def test_unknown_spot_falls_back_with_a_warning(plan_client):
    j = _run(plan_client, {"team": "clix, rapid", "as_of": AS_OF, "spot": "no such place"})
    assert any("No drop spot matches" in w for w in j["warnings"])
    assert {x["label"] for x in j["metrics"]} >= {"Drop spot"}
