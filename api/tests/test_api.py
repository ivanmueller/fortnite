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


ANALYSES = ["overview", "zone_randomness", "zone_geometry", "positioning", "eliminations"]


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
