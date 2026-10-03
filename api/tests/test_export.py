"""
The findings export: which studies it runs and in what order, the Markdown / CSV formats, and one end-to-end export through the
API with a team and an event date (every study must run).
"""
import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(Path(__file__).parent))


def test_order_puts_team_studies_first_and_skips_viewers():
    from fnlab import export
    plain = [s["id"] for s in export._order("")]
    assert not set(plain) & (export.EXCLUDE | export.TEAM_ONLY)
    assert "storm_hunt" in plain and "surge_study" in plain and len(plain) == len(set(plain))
    team = [s["id"] for s in export._order("clix, rapid")]
    assert team[:5] == export.TEAM_ORDER and "gameplan" in team and not set(team) & export.EXCLUDE


def _sample():
    rows = [[i, f"spot {i} | east", "40%"] for i in range(30)]
    return dict(meta=dict(generated="2026-10-03T12:00:00", dataset="real", selection="NAC; 2026-08-01 to …", filters={}, matches=120,
                          first_date="2026-08-01", last_date="2026-09-20", seasons=["v41.10"], regions=["NAC"], median_lobby_strength=0.61,
                          team="clix, rapid", event_first_day="2026-09-26",
                          scoring=dict(id="x", label="FNCS", description="FNCS: 65 for a win.", source="Rules."), synthetic=False,
                          surge_rule="Team net damage (dealt − taken), since the zone appeared"),
                studies=[dict(id="gameplan", title="Game plan", page="Your team", section="Game plan", question="What should we do?",
                              status="ok", headline="Plan from 120 matches.", conclusion=dict(status="descriptive", summary="Plan from 120 matches."),
                              metrics=[dict(label="Drop spot", value="Spot A | B", detail=None)], tests=[], notes=["A note."], warnings=[],
                              tables=[dict(title="Drop", columns=["#", "Spot", "Contested"], rows=rows, total_rows=30)]),
                         dict(id="storm_hunt", title="Storm hunt", page="Surge", section="Storm hunt or rotate first?", question="Stay or go?",
                              status="ok", headline="h", conclusion=dict(status="found", summary="Staying back finished with fewer points.",
                                                                         evidence=[dict(label="Points", detail="-2.1.", strength="strong", n=40, p=0.001)],
                                                                         reliability=[dict(label="Sample size", ok=False, detail="Few.")]),
                              metrics=[], tests=[dict(group="Like for like", name="Points", value="-2.1", reading="r", significant=True, n=40, p=0.001)],
                              notes=[], warnings=[], tables=[]),
                         dict(id="loot", title="Loot", page="Drops", section="Loot", question="", status="error", error="ValueError: boom")])


def test_markdown_reads_well_for_an_ai():
    from fnlab.export import to_markdown
    md = to_markdown(_sample(), max_rows=10)
    for part in ("# Vantage findings", "## The data", "## How to read this file", "## Known limits of the data", "## Suggested prompt",
                 "## Your team: clix, rapid", "### Your team: Game plan", "### Surge: Storm hunt or rotate first?",
                 "**Answer (pattern found):** Staying back finished with fewer points.", "(strong evidence; n=40, p=0.001)",
                 "**Reliability warnings:**", "_20 more rows not shown", "## Studies that couldn't run", "Team net damage"):
        assert part in md, part
    assert "spot 0 / east" in md and "spot 0 | east" not in md   # a pipe inside a cell can't break the table
    assert "synthetic demo data" not in md
    assert md.index("## Your team") < md.index("## Studies")


def test_csv_is_one_flat_sheet():
    from fnlab.export import CSV_COLUMNS, to_csv
    rows = list(csv.DictReader(io.StringIO(to_csv(_sample()))))
    assert list(rows[0]) == CSV_COLUMNS
    kinds = {r["kind"] for r in rows}
    assert {"answer", "evidence", "test", "metric", "table row", "error"} <= kinds
    assert sum(1 for r in rows if r["kind"] == "table row") == 30


@pytest.fixture(scope="module")
def team_data(tmp_path_factory):
    d = tmp_path_factory.mktemp("export")
    py = sys.executable
    subprocess.run([py, str(REPO / "pipeline/python/make_synthetic.py"), "--preset", "demo", "--per-season", "8",
                    "--sample-dt", "6", "--data-dir", str(d)], check=True, capture_output=True)
    subprocess.run([py, str(REPO / "pipeline/python/flatten.py"), "--data-dir", str(d)], check=True, capture_output=True)
    from event_fixture import add_events, add_loadouts, add_recurring_team
    add_events(d / "tables", rule="team_net", seed=1)
    add_recurring_team(d / "tables")
    add_loadouts(d / "tables")
    return d


def test_export_through_the_api_runs_every_study(client, team_data):
    from fnlab import config
    from fnlab.analyses import decides, engine_review, expected_points, playbook, surge_study, zone_forecast
    old = config.DATASETS["demo"]
    config.DATASETS["demo"] = team_data
    caches = (surge_study._MEASURED, expected_points._CACHE, engine_review._CACHE, playbook._CACHE, decides._CACHE, zone_forecast._CACHE)
    try:
        for c in caches:
            c.clear()
        res = client.post("/api/export", json={"filters": {"dataset": "demo"}, "team": "clix, rapid", "as_of": "2026-08-15"})
    finally:
        config.DATASETS["demo"] = old
        for c in caches:
            c.clear()
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert set(z.namelist()) == {"findings.md", "findings.json", "findings.csv"}
    data = json.loads(z.read("findings.json"))
    failed = [(s["section"], s.get("error")) for s in data["studies"] if s["status"] != "ok"]
    assert not failed, failed
    assert [s["id"] for s in data["studies"][:5]] == ["gameplan", "audit", "audit_zones", "review", "engine_review"]
    md = z.read("findings.md").decode()
    assert "synthetic demo data" in md and "## Your team: clix, rapid" in md and "event's first day: 2026-08-15" in md
    assert len(md) < 400_000                                   # stays readable in one go by an AI
