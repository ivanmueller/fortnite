"""
The match room: one team's game with its HUD over time and every decision priced. Uses the demo tables plus a recurring
team, surge events, weapons, pickups and builds (event_fixture.py).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(Path(__file__).parent))
DEMO = {"dataset": "demo"}


def _clear_caches():
    from fnlab import engine
    from fnlab.analyses import decides, engine_review, expected_points, playbook, surge_study, zone_forecast
    for c in (surge_study._MEASURED, expected_points._CACHE, engine_review._CACHE, playbook._CACHE, decides._CACHE,
              zone_forecast._CACHE, engine._CUTOFF):
        c.clear()


@pytest.fixture(scope="module")
def room_data(tmp_path_factory):
    d = tmp_path_factory.mktemp("room")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                    "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
    from event_fixture import add_events, add_loadouts, add_recurring_team
    add_events(d / "tables", rule="team_net", seed=1)
    add_recurring_team(d / "tables")
    add_loadouts(d / "tables")
    return d


@pytest.fixture(scope="module")
def room(client, room_data):
    from fnlab import config
    old = config.DATASETS["demo"]
    config.DATASETS["demo"] = room_data
    _clear_caches()
    j = client.post("/api/analyses/match_room/run", json={"filters": DEMO, "params": {"team": "clix, rapid"}}).json()
    yield j
    config.DATASETS["demo"] = old
    _clear_caches()


def test_asks_for_names_without_a_team(client):
    j = client.post("/api/analyses/match_room/run", json={"filters": DEMO}).json()
    assert j["status"] == "ok" and "Type the team's names" in j["headline"]
    assert not [c for c in j["charts"] if c["kind"] == "match_replay"]


def test_room_has_the_replay_hud_and_games(room):
    assert room["status"] == "ok" and not room["warnings"], room.get("warnings")
    chart = next(c for c in room["charts"] if c["kind"] == "match_replay")
    assert chart["title"] == "Match room"
    o = chart["options"]
    assert len(o["games"]) == 24 and o["match"] in {g["match"] for g in o["games"]}
    hud = o["hud"]
    assert {p["name"] for p in hud["players"]} == {"clix", "rapid", "third"}
    assert hud["has"] == {"health": True, "weapons": True, "pickups": True, "builds": True}
    for p in hud["players"]:
        assert p["hp"] and len(p["hp"]["t"]) == len(p["hp"]["health"]) == len(p["hp"]["shield"])
        assert p["weapons"] and all(w["name"] and w["rarity"] for w in p["weapons"])
        assert any(x["category"] == "materials" and x["count"] == 30 for x in p["pickups"])
    assert o["tracks"] and o["storm"]                      # the match map's replay is still there


def test_surge_series_is_team_net_damage(room):
    s = next(c for c in room["charts"] if c["kind"] == "match_replay")["options"]["surge"]
    assert s and len(s["t"]) == len(s["net"]) == len(s["zone"]) == len(s["dealt"]) == len(s["taken"])
    assert all(n == d - k for n, d, k in zip(s["net"], s["dealt"], s["taken"]))
    assert s["rule"].startswith("team net damage") and s["lines"]


def test_every_decision_is_consistent(room):
    ds = next(c for c in room["charts"] if c["kind"] == "match_replay")["options"]["decisions"]
    assert ds
    for d in ds:
        evs = [x["ev"] for x in d["options"]]
        assert evs == sorted(evs, reverse=True) and d["options"][0]["key"] == d["engine"]
        assert d["stake"] >= -0.05
        assert d["followed"] == (d["engine"] == d["actual"] or d["stake"] < 0.5)
        if d["fight"]:
            assert 0 <= d["fight"]["p_win"] <= 1 and d["fight"]["if_won"] >= d["fight"]["if_lost"] - 1e-6
    assert any(d["fight"] for d in ds)
    assert all(d["engine"] in {"hold", "rotate", "rotate_alt", "engage"} and "heal" not in {x["key"] for x in d["options"]} for d in ds)


def test_repeated_calls_form_one_stretch_and_damage_has_causes(room):
    o = next(c for c in room["charts"] if c["kind"] == "match_replay")["options"]
    ds, st = o["decisions"], o["stretches"]
    assert st and len(st) <= len(ds) and sum(x["checks"] for x in st) == len(ds)
    for a, b in zip(ds, ds[1:]):
        if a["stretch"] == b["stretch"]:
            assert (a["engine"], a["actual"], a["followed"]) == (b["engine"], b["actual"], b["followed"]) and b["t"] - a["t"] <= 25
    for p in o["hud"]["players"]:
        assert "killed_by" in p and all(e["cause"] in {"hit", "storm", "surge", "other"} and e["amount"] >= 0 for e in p["damage"])
    assert any(e["cause"] == "hit" and e["by"] for p in o["hud"]["players"] for e in p["damage"])
