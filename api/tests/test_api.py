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


ANALYSES = ["overview", "zone_randomness", "zone_geometry", "positioning", "eliminations", "height", "rotation", "drops", "loot", "fights", "surge"]


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
