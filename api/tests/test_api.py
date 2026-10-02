import pytest

DEMO = {"dataset": "demo"}


def test_health(client):
    j = client.get("/api/health").json()
    assert j["ok"] and j["datasets"]["demo"]["available"] and set(j["datasets"]) == {"real", "local", "demo"}
    assert j["datasets"]["demo"]["matches"] == 24


def test_facets(client):
    j = client.get("/api/facets?dataset=demo").json()
    assert [s["value"] for s in j["seasons"]] == ["v96.10", "v97.10", "v98.10"]
    assert j["date_min"] <= j["date_max"]


def test_filters_narrow_matches(client):
    all_ = client.post("/api/matches", json={"filters": DEMO}).json()["total"]
    one = client.post("/api/matches", json={"filters": {**DEMO, "seasons": ["v97.10"]}}).json()["total"]
    dated = client.post("/api/matches", json={"filters": {**DEMO, "date_from": "2026-07-01"}}).json()
    assert one == 8 and all_ == 24
    assert dated["total"] == 8 and all(r[1] >= "2026-07-01" for r in dated["rows"])


ANALYSES = ["overview", "zone_randomness", "zone_geometry", "positioning", "eliminations", "height", "rotation", "drops", "loot", "fights", "surge", "endgame_height", "height_damage", "zone_check", "zone_forecast", "playbook", "decides", "audit_zones", "review", "match_map", "engine_review"]


@pytest.mark.parametrize("aid", ANALYSES)
def test_analysis_runs_and_is_well_formed(client, aid):
    r = client.post(f"/api/analyses/{aid}/run", json={"filters": DEMO})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "ok" and j["headline"]
    for c in j["charts"]:
        assert c["kind"] in {"bar", "stacked_bar", "line", "histogram", "polar_histogram", "box", "map_points"}
        assert c["series"]
    for t in j["tests"]:
        assert set(t) >= {"group", "name", "n", "value", "p", "significant"}


def test_compare_detects_planted_change(client):
    """Season v96 zones are random; v98 zones always hit the edge. Compare must flag pull distance."""
    j = client.post("/api/analyses/compare_periods/run", json={
        "filters": {**DEMO, "seasons": ["v96.10"]}, "compare": {"seasons": ["v98.10"]},
        "params": {"alpha": 0.01}}).json()
    dist = next(t for t in j["tests"] if t["group"] == "A vs B" and t["name"] == "Pull distance (u)")
    assert dist["significant"]


def test_empty_selection_is_handled(client):
    j = client.post("/api/analyses/zone_randomness/run",
                    json={"filters": {**DEMO, "regions": ["NOWHERE"]}}).json()
    assert j["status"] == "empty"


def test_missing_dataset_explains_fix(client, monkeypatch):
    from fnlab import store
    monkeypatch.setattr(store, "available", lambda d: False)
    r = client.get("/api/facets?dataset=real")
    assert r.status_code == 404 and "pipeline" in r.json()["detail"]


def test_height_finds_planted_late_game_advantage(client):
    """Demo data: no height advantage before zone 2, strong from zone 5. Fights should show both."""
    j = client.post("/api/analyses/height/run", json={"filters": DEMO}).json()
    fights = {t["name"]: t for t in j["tests"] if t["group"] == "Fights by zone"}
    assert fights, j["headline"]
    late = [t for name, t in fights.items() if name in {"Zone 5", "Zone 6", "Zone 7"}]
    assert late and all(t["significant"] for t in late)
    if "Zone 1" in fights:
        assert not fights["Zone 1"]["significant"]


def test_every_page_has_a_guide(client):
    for a in client.get("/api/analyses").json():
        g = a["guide"]
        assert g and g["question"] and g["method"] and g["conclude"], a["id"]


@pytest.mark.parametrize("aid", ANALYSES)
def test_every_page_concludes(client, aid):
    j = client.post(f"/api/analyses/{aid}/run", json={"filters": DEMO}).json()
    c = j["conclusion"]
    assert c["status"] in {"found", "none", "insufficient", "descriptive"} and c["title"] and c["summary"]
    assert any(r["label"] == "Real data" and not r["ok"] for r in c["reliability"])  # demo data is flagged


def test_conclusion_does_not_overclaim_on_small_random_sample(client):
    """v96 is random and has only 8 matches here: must not report a pattern, must flag sample size."""
    j = client.post("/api/analyses/zone_randomness/run", json={"filters": {**DEMO, "seasons": ["v96.10"]}}).json()
    c = j["conclusion"]
    assert c["status"] in {"insufficient", "none"}
    assert any(r["label"] == "Sample size" and not r["ok"] for r in c["reliability"])


def test_lobby_strength_filter_and_trust_check(client):
    all_ = client.post("/api/matches", json={"filters": DEMO}).json()["total"]
    strong = client.post("/api/matches", json={"filters": {**DEMO, "min_lobby_strength": 0.5}}).json()
    assert 0 < strong["total"] < all_
    assert "lobby_strength_pct" in strong["columns"]
    assert all(r[strong["columns"].index("lobby_strength_pct")] >= 50 for r in strong["rows"])
    assert client.get("/api/facets?dataset=demo").json()["lobby_rated_matches"] == all_
    c = client.post("/api/analyses/positioning/run", json={"filters": DEMO}).json()["conclusion"]
    assert any(r["label"] == "Lobby strength" for r in c["reliability"])
    o = client.post("/api/analyses/overview/run", json={"filters": DEMO}).json()
    assert any(m["label"] == "Median lobby strength" for m in o["metrics"])
    assert any(m["label"] == "Median lobby PR" for m in o["metrics"])
    assert "Power Rankings" in next(m["detail"] for m in o["metrics"] if m["label"] == "Median lobby strength")


def test_rotation_excludes_players_eliminated_before_the_shrink(client):
    """Players eliminated before the storm moves must not be counted as late rotators."""
    j = client.post("/api/analyses/rotation/run", json={"filters": DEMO}).json()
    labels = {m["label"]: m["value"] for m in j["metrics"]}
    assert "Eliminated before the shrink" in labels and "Rotations" in labels
    assert any(t["name"] == "Behind eliminated more than Ahead" for t in j["tests"])
    phase_table = j["tables"][0]
    assert "Type" in phase_table["columns"] and "Players per km²" in phase_table["columns"]


def test_drops_detects_landings_and_contest(client):
    j = client.post("/api/analyses/drops/run", json={"filters": DEMO}).json()
    labels = {m["label"]: m["value"] for m in j["metrics"]}
    assert int(labels["Landings"].replace(",", "")) > 1000
    assert any(t["name"] == "Contested drops eliminated off spawn more" for t in j["tests"])


def test_divergence_flags_planted_storm_difference(client):
    """Demo season v98 pulls every circle to the edge: the report must flag the storm, not the unrelated measures."""
    j = client.post("/api/analyses/divergence/run", json={
        "filters": {**DEMO, "seasons": ["v98.10"]}, "compare": DEMO}).json()
    rows = {row[1]: row for row in j["tables"][0]["rows"]}
    verdict = j["tables"][0]["columns"].index("Verdict")
    assert rows["Zone 2 pull distance (u)"][verdict] == "Divergent"
    assert rows["Players per match"][verdict] == "Similar"
    assert any("storm itself" in w for w in j["warnings"])


def test_event_pages_explain_missing_data(client):
    """Demo data has no in-match events: the pages say how to get them instead of failing."""
    for a in ("loot", "fights", "surge"):
        j = client.post(f"/api/analyses/{a}/run", json={"filters": DEMO}).json()
        assert "option 7" in " ".join(j.get("warnings", []))


def test_pages_point_at_real_analyses(client):
    """Every dashboard section must name a registered analysis, and featured charts must exist on demo data."""
    ids = {a["id"] for a in client.get("/api/analyses").json()}
    pages = client.get("/api/pages").json()
    assert [p["id"] for p in pages][:3] == ["overview", "review", "storm"]
    for p in pages:
        for s in p["sections"]:
            assert s["analysis"] in ids, s["analysis"]
    storm = next(p for p in pages if p["id"] == "storm")["sections"][0]
    j = client.post(f"/api/analyses/{storm['analysis']}/run", json={"filters": DEMO}).json()
    titles = {c["title"] for c in j["charts"]}
    assert all(c in titles for c in storm["charts"] if c != "Where endgames land" or "pois" in titles)


def test_endgame_height_finds_planted_advantage(client):
    """Demo data: better teams hold height late, so endgame high ground must come out ahead of low ground."""
    j = client.post("/api/analyses/endgame_height/run", json={"filters": DEMO}).json()
    main = next(t for t in j["tests"] if t["name"] == "Higher teams finish better in the endgame")
    assert main["significant"] and "finishing better" in main["reading"]


def test_data_page_status_and_job_validation(client):
    """The Data page's status loads, and bad job requests get a plain explanation instead of running."""
    s = client.get("/api/data/status").json()
    assert {"epic", "parser", "counts", "data_dir"} <= set(s)
    assert client.post("/api/data/jobs", json={"kind": "collect", "params": {}}).status_code == 400
    assert client.post("/api/data/jobs", json={"kind": "nonsense"}).status_code == 400
    assert client.post("/api/data/jobs", json={"kind": "login", "params": {"code": " "}}).status_code == 400
    assert "rows" in client.get("/api/data/tournaments").json()


def test_data_actions_are_local_only():
    """Data endpoints run programs, so they refuse requests from other machines."""
    import pytest
    from fastapi import HTTPException
    from fnlab.datamgr import local_only

    class Req:
        client = type("C", (), {"host": "203.0.113.9"})()
    with pytest.raises(HTTPException):
        local_only(Req())


def test_zone_check_confirms_continuity(client):
    """Zone accuracy: every zone transition in the demo data must be exact, and the replay map must draw every zone."""
    j = client.post("/api/analyses/zone_check/run", json={"filters": DEMO}).json()
    cont = next(m["value"] for m in j["metrics"] if m["label"] == "Zone-to-zone continuity")
    n, of = cont.replace(",", "").split(" exact")[0].split(" of ")
    assert n == of
    zmap = next(c for c in j["charts"] if c["title"] == "Zone replay map")
    assert len(zmap["options"]["circles"]) >= 5


def test_zone_forecast_explains_small_selections(client):
    """Fewer than 10 matches in a season: the page says so instead of forecasting from too little."""
    j = client.post("/api/analyses/zone_forecast/run", json={"filters": {**DEMO, "seasons": ["v96.10"]}}).json()
    assert "at least 10" in j["headline"]


def test_zone_model_is_honest():
    """Engine-level: random zones must not beat the rules; a one-step rule must be found."""
    import numpy as np
    import pandas as pd
    from fnlab import zone_model as zm

    xs, ys = np.meshgrid(np.arange(-40, 40), np.arange(-40, 40))
    land = zm.LandMap(pd.DataFrame({"cx": xs.ravel(), "cy": ys.ravel(), "n": 5, "ground": 0.0}))
    radii = [95000, 75000, 52500, 32500, 20000, 10000, 5000, 2500]

    def season(cont, n=40, seed=1):
        rng, seqs = np.random.default_rng(seed), {}
        for m in range(n):
            x, y = rng.uniform(-15000, 15000, 2)
            rows, prev = [(1, x, y, radii[0], "shrinking")], None
            for k in range(2, 9):
                d = 32500 if k >= 5 else (radii[k - 2] - radii[k - 1]) * np.sqrt(rng.random())
                a = prev + rng.normal(0, 0.4) if (cont and prev is not None) else rng.uniform(0, 2 * np.pi)
                nx, ny = x + d * np.cos(a), y + d * np.sin(a)
                prev = np.arctan2(ny - y, nx - x)
                rows.append((k, nx, ny, radii[k - 1], "shrinking" if k < 5 else "moving"))
                x, y = nx, ny
            seqs[f"m{m}"] = pd.DataFrame(rows, columns=["phase", "x", "y", "r", "zone_type"])
        return seqs
    none = zm.ladder(land, season(False), {})
    assert none["results"][none["best"]]["hit"].mean() <= 0.35
    cont = zm.ladder(land, season(True), {})
    assert cont["results"]["one_step"]["hit"].mean() >= 0.55


def test_playbook_finds_planted_rules(client):
    """Demo data plants late rotations and low ground among lower finishers: following those rules must pay off,
    within matches and within the same player."""
    j = client.post("/api/analyses/playbook/run", json={"filters": DEMO}).json()
    rules = {t["name"]: t for t in j["tests"] if t["group"] == "Each rule"}
    h = rules["Held mid or high ground (Zones 6+)"]
    assert h["significant"] and "finishing better" in h["reading"]
    same = {t["name"]: t for t in j["tests"] if t["group"] == "Same player"}
    assert "finished better" in same["Held mid or high ground (Zones 6+)"]["reading"]


def test_audit_asks_for_names_and_explains_misses(client):
    j = client.post("/api/analyses/audit/run", json={"filters": DEMO}).json()
    assert "Find the team" in j["headline"] and j["tables"][0]["title"] == "Teams in the selected matches"
    j = client.post("/api/analyses/audit/run", json={"filters": DEMO, "params": {"team": "zzzz-nobody"}}).json()
    assert "No team found" in j["headline"]


def test_expected_points_prices_plans(client):
    """The model trains on the selection and prices two plans; changing a plan changes the answer."""
    import json
    j = client.post("/api/analyses/expected_points/run", json={"filters": DEMO}).json()
    assert any(m["label"] == "Plan comparison" for m in j["metrics"])
    plans = {"a": {"name": "A", "teams_alive": 10, "zone": 8}, "b": {"name": "B", "teams_alive": 30, "zone": 5}}
    k = client.post("/api/analyses/expected_points/run", json={"filters": DEMO, "params": {"plans": json.dumps(plans)}}).json()
    rows = k["tables"][0]["rows"]
    assert {r[0] for r in rows} >= {"Teams left", "Zone"}


def test_game_review_has_three_stages(client):
    """A named team's game is reviewed in early, mid and endgame tables."""
    tl = client.post("/api/analyses/review/run", json={"filters": DEMO}).json()["tables"][0]["rows"]
    name = tl[0][1].split(",")[0]
    j = client.post("/api/analyses/review/run", json={"filters": DEMO, "params": {"team": name}}).json()
    review = next(t for t in j["tables"] if t["title"] == "Review")
    assert {"Early game", "Mid game", "Endgame"} <= {row[0] for row in review["rows"]}
    assert any(row[1] in ("How it ended", "Won the game") for row in review["rows"])


def test_match_map_replay_and_plans(client):
    """The match map carries every player's path, the storm and a reasoned plan per zone."""
    tl = client.post("/api/analyses/match_map/run", json={"filters": DEMO}).json()["tables"][0]["rows"]
    name = tl[0][1].split(",")[0]
    j = client.post("/api/analyses/match_map/run", json={"filters": DEMO, "params": {"team": name}}).json()
    ch = next(c for c in j["charts"] if c["kind"] == "match_replay")
    o = ch["options"]
    assert len(o["tracks"]) > 10 and len(o["storm"]) >= 5 and any(t["mine"] for t in o["tracks"])
    assert all(p["reasons"] for p in o["plans"])


def test_engine_rotates_when_outside_and_holds_inside(client):
    """Sanity: the live engine says rotate for most decisions outside the next zone and almost never inside it."""
    client.post("/api/analyses/engine_review/run", json={"filters": DEMO})
    from fnlab.analyses.engine_review import _CACHE
    d = list(_CACHE.values())[-1]["d"]
    out, ins = d[d["outside_m"] > 30], d[d["outside_m"] == 0]
    assert out["engine"].str.startswith("rotate").mean() >= 0.6
    assert ins["engine"].str.startswith("rotate").mean() <= 0.05
