"""
Findings export: every study run on one selection, written for an AI (and a spreadsheet) to read.

  findings.md    for an AI: what the data is, how to read evidence, the known limits, a suggested prompt, the team's own
                 studies (game plan, team audit, zone positions, game review, engine) when a team is given, then every study
                 in dashboard order: question, answer, evidence with strength / n / p, key numbers, reliability warnings,
                 tables (first max_rows rows) and notes
  findings.json  the same, complete (tables up to JSON_ROWS rows), for programs
  findings.csv   one flat sheet: a row per answer, piece of evidence, key number and table row

Studies are run exactly as the dashboard runs them (same code, same selection). Replay viewers and A/B comparisons are left
out; the team studies run only when a team is given.

Command line (from the repo root):
  node scripts/py.mjs scripts/export_findings.py --dataset real --team "clix, rapid" --as-of 2026-09-26 --out findings.zip
"""
from __future__ import annotations

import csv
import io
import json
import time
import traceback
import zipfile
from datetime import datetime

from . import scoring, store
from .analyses import REGISTRY, Context
from .filters import Filters

EXCLUDE = {"match_map", "match_room", "compare_periods", "divergence"}     # replay viewers and A/B comparisons
TEAM_ONLY = {"gameplan", "audit", "audit_zones", "review"}                 # need a team; engine_review works without one too
TEAM_ORDER = ["gameplan", "audit", "audit_zones", "review", "engine_review"]
JSON_ROWS = 200
NOTES_MD = 6
STRENGTH = {"strong": "strong evidence", "clear": "clear evidence", "weak": "weak evidence (a lead, not a rule)", "none": "no evidence"}

LIMITS = [
    "Material and item counts aren't in the replay data: loadouts are weapons seen in hand; build pieces are per team.",
    "Fights are judged by health only: numbers (2v1), height and third parties aren't in the engine's fight odds yet.",
    "Cover, builds and line of sight aren't in the data: 'teams nearby' means near, not with a clear shot.",
    "Surge is detected from health drops, not recorded: inside the zone, unexplained, in surge's rhythm or by 3+ players at once.",
    "Expected points are averages over many situations; a single game still turns on fights and luck.",
    "Each study's 'Reliability' lists what's weak about its data (sample size, mixed seasons, non-server replays).",
]

HOW_TO_READ = [
    "Each study answers one question. Its 'Answer' is the conclusion; 'Evidence' lists the tests behind it with their strength.",
    "Strength: strong = p < 0.005 (many tests run at once, so this is the bar for a rule); clear = p < 0.05; weak = suggestive; "
    "none = no difference found. n is how many independent comparisons (matches, groups or players) a test rests on.",
    "Prefer like-for-like and same-team/same-player results over raw averages: stronger teams choose some options more often, "
    "so raw averages mix skill with the decision.",
    "'–' in a table means too few games to give a number; 'Too few' / 'Too few to judge' means no verdict was given on purpose.",
    "Points use the scoring scheme below. Elimination points and placement points are both included where a study says 'points'.",
]


def _order(team: str) -> list[dict]:
    """Studies in dashboard order (team studies first when a team is given), then any not on a page."""
    from .pages import PAGES
    out, seen = [], set()
    if team:
        for aid in TEAM_ORDER:
            if aid in REGISTRY:
                out.append(dict(id=aid, page="Your team", section=REGISTRY[aid].title, question=""))
                seen.add(aid)
    for p in PAGES:
        for s in p["sections"]:
            aid = s["analysis"]
            if aid in seen or aid in EXCLUDE or aid not in REGISTRY or (aid in TEAM_ONLY and not team):
                continue
            out.append(dict(id=aid, page=p["title"], section=s["title"], question=s.get("question", "")))
            seen.add(aid)
    for aid in REGISTRY:
        if aid not in seen and aid not in EXCLUDE and not (aid in TEAM_ONLY and not team):
            out.append(dict(id=aid, page="Other studies", section=REGISTRY[aid].title, question=""))
    return out


def _meta(con, f: Filters, n: int, team: str, as_of: str, scheme: scoring.Scheme) -> dict:
    cols = set(con.execute("SELECT * FROM matches LIMIT 0").df().columns)
    q = lambda sql: con.execute(sql).fetchone()  # noqa: E731
    lo, hi = q("SELECT min(match_date), max(match_date) FROM matches JOIN sel USING (match_id)")
    seasons = [r[0] for r in con.execute("SELECT DISTINCT season FROM matches JOIN sel USING (match_id) ORDER BY 1").fetchall() if r[0]]
    regions = [r[0] for r in con.execute("SELECT DISTINCT region FROM matches JOIN sel USING (match_id) ORDER BY 1").fetchall() if r[0]] \
        if "region" in cols else []
    lobby = q("SELECT median(lobby_strength) FROM matches JOIN sel USING (match_id)")[0] if "lobby_strength" in cols else None
    return dict(generated=datetime.now().isoformat(timespec="seconds"), dataset=f.dataset, selection=f.describe(),
                filters=json.loads(f.model_dump_json()), matches=n, first_date=str(lo) if lo else None, last_date=str(hi) if hi else None,
                seasons=seasons, regions=regions, median_lobby_strength=None if lobby is None else round(float(lobby), 2),
                team=team or None, event_first_day=as_of or None,
                scoring=dict(id=scheme.id, label=scheme.label, description=scheme.describe(), source=scheme.source),
                synthetic=f.dataset == "demo")


def run_all(filters: Filters, team: str = "", as_of: str = "", scheme_id: str = "", progress=None) -> dict:
    """Run every study on the selection. progress(i, total, title) is called before each one."""
    scheme = scoring.get(scheme_id or None)
    team = (team or "").strip()
    studies = _order(team)
    out = dict(meta=None, studies=[])
    with store.connect(filters.dataset) as con:
        n = store.select(con, filters, "sel")
        out["meta"] = _meta(con, filters, n, team, as_of, scheme)
        for i, s in enumerate(studies):
            a = REGISTRY[s["id"]]
            if progress:
                progress(i, len(studies), a.title)
            params = {p.name: p.default for p in a.params}
            if team and "team" in params:
                params["team"] = team
            if s["id"] == "gameplan":
                params.update(as_of=as_of or "", scheme=scheme.id)
            store.select(con, filters, "sel")                # every study starts from the same selection
            entry = dict(id=s["id"], title=a.title, page=s["page"], section=s["section"],
                         question=(a.guide.question if a.guide else "") or s["question"], summary=a.summary)
            t0 = time.perf_counter()
            try:
                if n < a.min_matches:
                    raise ValueError(f"needs at least {a.min_matches} matches; the selection has {n}")
                res = a.run(Context(con=con, filters=filters, n_matches=n, params=params)).to_dict()
                entry.update(status="ok", headline=res.get("headline", ""), conclusion=res.get("conclusion"),
                             metrics=res.get("metrics", []), tests=res.get("tests", []), notes=res.get("notes", []),
                             warnings=res.get("warnings", []),
                             tables=[dict(title=t["title"], columns=t["columns"], rows=t["rows"][:JSON_ROWS], total_rows=len(t["rows"]))
                                     for t in res.get("tables", [])])
            except Exception as e:  # noqa: BLE001 - one study failing shouldn't lose the rest of the export
                traceback.print_exc()
                entry.update(status="error", error=f"{type(e).__name__}: {e}")
            entry["seconds"] = round(time.perf_counter() - t0, 1)
            out["studies"].append(entry)
    if out["meta"]:
        rule = next((m["value"] for s in out["studies"] if s["id"] == "surge_study" for m in s.get("metrics", [])
                     if m["label"] == "Damage surge counts"), None)
        out["meta"]["surge_rule"] = rule
    return out


# ---------------------------------------------------------------- Markdown, for an AI

def _cell(v) -> str:
    s = "–" if v is None else str(v)
    return s.replace("|", "/").replace("\n", " ")


def _md_table(t: dict, max_rows: int) -> list[str]:
    rows = t["rows"][:max_rows]
    lines = [f"#### {t['title']}", "", "| " + " | ".join(_cell(c) or " " for c in t["columns"]) + " |",
             "|" + "---|" * len(t["columns"])]
    lines += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in rows]
    more = t.get("total_rows", len(t["rows"])) - len(rows)
    if more > 0:
        lines.append(f"\n_{more} more rows not shown (all rows are in findings.json)._")
    return lines + [""]


def _md_study(s: dict, max_rows: int) -> list[str]:
    L = [f"### {s['page']}: {s['section']}", ""]
    if s.get("question"):
        L.append(f"**Question:** {s['question']}")
    if s["status"] != "ok":
        return L + [f"**Not available:** {s.get('error')}", ""]
    c = s.get("conclusion") or {}
    status = {"found": "pattern found", "none": "no clear pattern", "insufficient": "not enough data", "descriptive": "description"}.get(
        c.get("status"), c.get("status") or "")
    answer = c.get("summary") if c.get("status") not in (None, "descriptive") else s.get("headline")
    L.append(f"**Answer ({status}):** {answer}" if status else f"**Answer:** {answer}")
    if c.get("status") not in (None, "descriptive") and s.get("headline") and s["headline"] != answer:
        L.append(f"**Headline:** {s['headline']}")
    if c.get("evidence"):
        L += ["", "**Evidence:**"] + [f"- {e['label']}: {e['detail']} ({STRENGTH.get(e['strength'], e['strength'])}; n={e['n']}"
                                      + (f", p={e['p']:.3g}" if isinstance(e.get('p'), (int, float)) else "") + ")" for e in c["evidence"]]
    other = [t for t in s.get("tests", []) if not any(t["name"] in e["label"] for e in c.get("evidence") or [])]
    if other:
        L += ["", "**Other tests:**"] + [f"- {t['group']}, {t['name']}: {t['value']}"
                                         + (f". {t['reading']}" if t.get("significant") and t.get("reading") else "")
                                         + f" (n={t['n']}" + (f", p={t['p']:.3g}" if isinstance(t.get('p'), (int, float)) else "")
                                         + (", significant" if t.get("significant") else "") + ")" for t in other]
    if s.get("metrics"):
        L += ["", "**Key numbers:**"] + [f"- {m['label']}: {m['value']}" + (f" ({m['detail']})" if m.get("detail") else "") for m in s["metrics"]]
    weak = [r for r in c.get("reliability") or [] if not r.get("ok")]
    if weak:
        L += ["", "**Reliability warnings:**"] + [f"- {r['label']}: {r.get('detail', '')}" for r in weak]
    if s.get("warnings"):
        L += ["", "**Warnings:**"] + [f"- {w}" for w in s["warnings"]]
    if s.get("tables"):
        L += ["", "**Tables:**", ""]
        for t in s["tables"]:
            L += _md_table(t, max_rows)
    if s.get("notes"):
        L += ["**Notes:**"] + [f"- {n_}" for n_ in s["notes"][:NOTES_MD]]
    return L + [""]


def to_markdown(data: dict, max_rows: int = 25) -> str:
    m = data["meta"]
    team = m.get("team")
    L = ["# Vantage findings", "",
         "Competitive Fortnite analytics from tournament replays: every study in the system, run on one selection of matches. "
         "Written to be read by an AI building a game plan, and by a person checking it.", ""]
    if m.get("synthetic"):
        L += ["> **This export is from synthetic demo data. Its findings mean nothing about real Fortnite; use it only to test the "
              "workflow.**", ""]
    L += ["## The data", "",
          f"- Selection: {m['selection']} ({m['dataset']} dataset)",
          f"- Matches: {m['matches']}" + (f", {m['first_date']} to {m['last_date']}" if m.get("first_date") else ""),
          f"- Seasons: {', '.join(m['seasons']) or '–'}; regions: {', '.join(m['regions']) or '–'}",
          f"- Median lobby strength: {m['median_lobby_strength']} (share of the lobby in the top 1,000)" if m.get("median_lobby_strength") is not None
          else "- Lobby strength: not available",
          f"- Scoring: {m['scoring']['description']} {m['scoring']['source']}",
          f"- Surge rule (measured): {m.get('surge_rule') or 'not measured in this selection (no surge detected or no in-match data)'}",
          ]
    if team:
        L.append(f"- Team: {team}" + (f"; event's first day: {m['event_first_day']} (the game plan uses only matches before it)"
                                     if m.get("event_first_day") else ""))
    L += ["", "## How to read this file", ""] + [f"- {x}" for x in HOW_TO_READ] + ["", "## Known limits of the data", ""] + \
         [f"- {x}" for x in LIMITS] + [""]
    who = team or "the team"
    L += ["## Suggested prompt", "", "```text",
          f"You are a competitive Fortnite strategy analyst. Using only the findings in this file, build a game plan for {who} "
          "for their next event: where to land and the backup, how to play zones 1-3, when to rotate in each zone and by which "
          "side, how to manage surge in each zone, the endgame defaults, and the five habits to fix first. For every rule, cite "
          "the study it comes from and its evidence strength. Use only strong or clear evidence as rules; list weak evidence "
          "separately as things to test in scrims. Say where the data is too thin to advise. Don't invent numbers that aren't in "
          "the file.", "```", ""]
    studies = data["studies"]
    mine = [s for s in studies if s["page"] == "Your team"]
    if mine:
        L += [f"## Your team: {team}", ""]
        for s in mine:
            L += _md_study(s, max_rows)
    L += ["## Studies", ""]
    for s in studies:
        if s["page"] != "Your team":
            L += _md_study(s, max_rows)
    errors = [s for s in studies if s["status"] != "ok"]
    if errors:
        L += ["## Studies that couldn't run", ""] + [f"- {s['section']}: {s.get('error')}" for s in errors] + [""]
    return "\n".join(L)


# ---------------------------------------------------------------- one flat sheet

CSV_COLUMNS = ["page", "study", "kind", "label", "value", "detail", "strength", "n", "p", "table", "row"]


def to_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for s in data["studies"]:
        base = dict(page=s["page"], study=s["section"])
        if s["status"] != "ok":
            w.writerow(dict(base, kind="error", label="Not available", value=s.get("error")))
            continue
        c = s.get("conclusion") or {}
        w.writerow(dict(base, kind="answer", label=c.get("status") or "headline",
                        value=c.get("summary") if c.get("status") not in (None, "descriptive") else s.get("headline")))
        for e in c.get("evidence") or []:
            w.writerow(dict(base, kind="evidence", label=e["label"], value=e["detail"], strength=e["strength"], n=e["n"], p=e.get("p")))
        for t in s.get("tests", []):
            w.writerow(dict(base, kind="test", label=f"{t['group']}: {t['name']}", value=t["value"], detail=t.get("reading"),
                            strength="significant" if t.get("significant") else "not significant", n=t["n"], p=t.get("p")))
        for m in s.get("metrics", []):
            w.writerow(dict(base, kind="metric", label=m["label"], value=m["value"], detail=m.get("detail")))
        for t in s.get("tables", []):
            for i, r in enumerate(t["rows"]):
                w.writerow(dict(base, kind="table row", table=t["title"], row=i + 1, label=_cell(r[0]) if r else "",
                                value="; ".join(f"{c_}: {_cell(v)}" for c_, v in zip(t["columns"], r))))
    return buf.getvalue()


def bundle(data: dict, max_rows: int = 25) -> bytes:
    """findings.md, findings.json and findings.csv in one zip."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("findings.md", to_markdown(data, max_rows))
        z.writestr("findings.json", json.dumps(data, indent=1, default=str))
        z.writestr("findings.csv", to_csv(data))
    return out.getvalue()


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Export every study's findings for an AI to read.")
    ap.add_argument("--dataset", default="real", choices=["real", "local", "demo"])
    ap.add_argument("--team", default="", help="players, comma-separated (adds the team's own studies)")
    ap.add_argument("--as-of", default="", help="the event's first day (YYYY-MM-DD) for the game plan")
    ap.add_argument("--scheme", default="", help="scoring scheme id from scoring.json (default: the active one)")
    ap.add_argument("--from-date", default=None)
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--region", action="append", default=[])
    ap.add_argument("--min-lobby-strength", type=float, default=None)
    ap.add_argument("--rows", type=int, default=25, help="table rows per study in findings.md")
    ap.add_argument("--out", default="vantage-findings.zip")
    a = ap.parse_args(argv)
    f = Filters(dataset=a.dataset, date_from=a.from_date, date_to=a.to_date, regions=a.region, min_lobby_strength=a.min_lobby_strength)
    data = run_all(f, a.team, a.as_of, a.scheme, progress=lambda i, n, t: print(f"[{i + 1}/{n}] {t}", flush=True))
    with open(a.out, "wb") as fh:
        fh.write(bundle(data, a.rows))
    ok = sum(1 for s in data["studies"] if s["status"] == "ok")
    print(f"wrote {a.out}: {ok} of {len(data['studies'])} studies, {data['meta']['matches']} matches")


if __name__ == "__main__":
    main()
