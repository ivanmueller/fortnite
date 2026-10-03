"""
Storm hunt or rotate first: the like-for-like comparison, the count of teams set up at a rotating team's way in, the engine's
rotation risk split by them, and the study end to end on demo tables with surge events.
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


def test_like_for_like_compares_only_within_groups():
    from fnlab.analyses.storm_hunt import ROTATED, STAYED, like_for_like
    rows = []
    # three comparable groups: staying back scores 4 fewer points in each, whatever the group's level
    for g, base in enumerate((40, 20, 5)):
        rows += [dict(match_id=f"m{g}", zone=4, dist_band="Under 300 m", surge_half="Bottom half", approach=ROTATED, points=base),
                 dict(match_id=f"m{g}", zone=4, dist_band="Under 300 m", surge_half="Bottom half", approach=STAYED, points=base - 4)]
    # a group with only one approach can't be compared and must be ignored
    rows.append(dict(match_id="m9", zone=4, dist_band="Under 300 m", surge_half="Top half", approach=STAYED, points=60))
    res = like_for_like(pd.DataFrame(rows), "points")
    assert res["n"] == 3 and np.isclose(res["mean"], -4)


def test_lookers_count_enemy_teams_inside_the_zone_nearby():
    from fnlab.ep_model import lookers
    team = pd.DataFrame({"match_id": ["m"] * 4, "t": [100.0] * 4, "team_index": [1, 2, 3, 4],
                         "x": [0.0, 8_000.0, 10_000.0, 90_000.0], "y": [0.0] * 4, "outside_m": [60.0, 0.0, 0.0, 0.0]})
    # team 1 is outside; teams 2 and 3 are inside within 120 m; team 4 is inside but 900 m away
    assert lookers(team).tolist() == [2, 1, 1, 0]


def test_engine_rotation_risk_is_split_by_lookers():
    from fnlab import engine
    rng = np.random.default_rng(0)
    n = 400
    lk = rng.integers(0, 4, n)
    expo = pd.DataFrame({"zone": 6, "outside_m": 80.0, "lookers": lk, "hit": rng.random(n) < 0.1 + 0.15 * np.minimum(lk, 2), "dmg": 40.0})
    risk = engine.hit_risk(expo)
    assert risk[("mid", "rotate", 2)][0] > risk[("mid", "rotate", 0)][0]


def test_every_zone_with_a_wait_is_included_from_zone_1():
    """Zone 1 appears at the safe-zones start; zones past 7 count too; a zone with no wait (moving) doesn't."""
    import duckdb
    from fnlab.analyses.storm_hunt import _windows, stage
    con = duckdb.connect()
    con.register("m_", pd.DataFrame({"match_id": ["m"], "safe_zones_start_t": [100.0]}))
    con.execute("CREATE VIEW matches AS SELECT * FROM m_")
    phases = list(range(1, 10))
    start = [160.0 + 90 * (k - 1) for k in phases]
    finish = [s_ + 45 for s_ in start]
    finish[7] = start[8]                                   # zone 9 appears as zone 8 finishes and starts moving at once
    con.register("z_", pd.DataFrame({"match_id": "m", "phase": phases, "next_x": 0.0, "next_y": 0.0, "next_r": 50_000.0,
                                     "start_shrink_t": start, "finish_shrink_t": finish}))
    con.execute("CREATE VIEW zones AS SELECT * FROM z_")
    con.execute("CREATE TEMP TABLE sel AS SELECT 'm' AS match_id")
    w = _windows(con)
    assert w["zone"].tolist() == [1, 2, 3, 4, 5, 6, 7, 8]
    assert w.loc[w["zone"] == 1, "reveal"].iat[0] == 100.0 and w.loc[w["zone"] == 1, "prev"].iat[0] == 0.0
    assert [stage(k) for k in (1, 3, 4, 5, 6, 9)] == ["Zones 1–3", "Zones 1–3", "Zones 4–5", "Zones 4–5", "Zones 6+", "Zones 6+"]


@pytest.fixture(scope="module")
def hunt(client, tmp_path_factory):
    d = tmp_path_factory.mktemp("hunt")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                    "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
    from event_fixture import add_events
    add_events(d / "tables", rule="team_net", seed=1)
    from fnlab import config
    from fnlab.analyses import surge_study
    old = config.DATASETS["demo"]
    config.DATASETS["demo"] = d
    surge_study._MEASURED.clear()
    j = client.post("/api/analyses/storm_hunt/run", json={"filters": DEMO}).json()
    yield j
    config.DATASETS["demo"] = old
    surge_study._MEASURED.clear()


def test_study_runs_and_explains_itself(hunt):
    assert hunt["status"] == "ok" and not hunt["warnings"]
    titles = {t["title"] for t in hunt["tables"]}
    assert {"Stay back or rotate first: like for like", "Who it pays off for", "Staying back: in the storm or behind it",
            "Getting sprayed on the way in", "By zone: where staying back stops paying"} <= titles
    bz = next(t for t in hunt["tables"] if t["title"] == "By zone: where staying back stops paying")
    zones = [r[0] for r in bz["rows"]]
    assert zones == sorted(zones)
    for r in bz["rows"]:                                   # no verdict on fewer than 10 teams that stayed back
        stayed = int(str(r[bz["columns"].index("Stayed back")]).split(" ")[0])
        if stayed < 10:
            assert r[bz["columns"].index("Verdict")] == "Too few to judge"
    who = next(t for t in hunt["tables"] if t["title"] == "Who it pays off for")
    assert who["columns"][:2] == ["Stage", "Surge standing"]
    assert "Staying back is common through" in {x["label"] for x in hunt["metrics"]}
    m = {x["label"]: x["value"] for x in hunt["metrics"]}
    rot, stay = (int(v) for v in m["Rotated first / stayed back"].split(" / "))
    assert int(m["Team-zones compared"].replace(",", "")) == rot + stay and rot and stay
    lfl = next(t for t in hunt["tables"] if t["title"] == "Stay back or rotate first: like for like")
    verdicts = [r[lfl["columns"].index("Verdict")] for r in lfl["rows"]]
    assert all(v.split(":")[0] in {"Too few", "Strong evidence", "Some evidence", "No clear difference"} for v in verdicts)
    assert not [v for v in verdicts if v.startswith("No clear difference:")]      # no "better" claim without evidence
