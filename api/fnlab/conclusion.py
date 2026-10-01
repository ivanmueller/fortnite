"""
Turns an analysis result into a conclusion: what was found, how strong the
evidence is, whether the data behind it can be trusted, and what to do next.

Strength of evidence for a test at threshold alpha:
    strong    p < alpha / 10
    clear     p < alpha
    weak      alpha <= p < 0.05 (would pass a lone test, not this many)
    none      p >= 0.05
"""
from __future__ import annotations

import numpy as np

from .result import Result
from .store import df

STRENGTH_ORDER = ["strong", "clear", "weak", "none"]


def strength(p, alpha: float) -> str:
    if p is None or not np.isfinite(p):
        return "none"
    if p < alpha / 10:
        return "strong"
    if p < alpha:
        return "clear"
    if p < 0.05:
        return "weak"
    return "none"


def reliability(ctx, *, recommended: int, single_season: bool = True, sel: str = "sel", strategy: bool = False) -> list[dict]:
    """Checks on the data behind a result. Each item: {label, ok, detail}."""
    m = df(ctx.con, f"""
        SELECT count(*) AS n, count(DISTINCT season) AS seasons,
               avg(CASE WHEN is_server_replay THEN 1.0 WHEN is_server_replay IS NULL THEN NULL ELSE 0.0 END) AS server
        FROM matches JOIN {sel} USING (match_id)
    """).iloc[0]
    n, seasons, server = int(m["n"]), int(m["seasons"]), m["server"]
    checks = [dict(label="Sample size", ok=n >= recommended,
                   detail=f"{n:,} matches; {recommended:,}+ recommended for this page." if n < recommended
                   else f"{n:,} matches, above the {recommended:,} recommended.")]
    if single_season:
        checks.append(dict(label="One season", ok=seasons <= 1,
                           detail="Single season, so map changes can't blur the result." if seasons <= 1 else
                           f"{seasons} seasons mixed. Map changes between seasons can create or hide patterns; "
                           "filter to one season before drawing conclusions."))
    if server is None or not np.isfinite(server):
        checks.append(dict(label="Replay source", ok=False,
                           detail="Unknown whether these are server replays. Client replays miss distant players."))
    else:
        checks.append(dict(label="Replay source", ok=server >= 0.95,
                           detail=f"{server:.0%} server replays." + ("" if server >= 0.95 else
                           " Client replays only include players near the recorder, which biases position and fight data.")))
    if strategy:
        # Strategy conclusions (positioning, fights) only transfer from even, high-skill lobbies.
        has = "lobby_strength" in set(ctx.con.execute("SELECT * FROM matches LIMIT 0").df().columns)
        med = ctx.con.execute(f"SELECT median(lobby_strength), count(lobby_strength) FROM matches JOIN {sel} USING (match_id)").fetchone() if has else (None, 0)
        if not has or not med[1]:
            checks.append(dict(label="Lobby strength", ok=False, detail="Unknown for these matches. Re-run option 4 on their "
                               "tournament windows to record leaderboard ranks, then filter to strong lobbies."))
        else:
            ok = med[0] >= 0.5
            checks.append(dict(label="Lobby strength", ok=ok,
                               detail=f"Median lobby has {med[0]:.0%} of its players in the top 1,000." + (
                                   "" if ok else " Mixed-skill lobbies: outcomes may reflect skill gaps more than decisions. "
                                   "Use the Lobby strength filter or later-round windows.")))
    if ctx.filters.dataset == "demo":
        checks.append(dict(label="Real data", ok=False,
                           detail="Demo data with planted effects. Use it to learn the page, not to draw conclusions."))
    return checks


def conclude(r: Result, ctx, *, primary: list[str], alpha: float, recommended: int,
             takeaway_found: str = "", takeaway_none: str = "", next_found: list[str] | None = None,
             next_none: list[str] | None = None, single_season: bool = True, descriptive: str | None = None,
             sel: str = "sel", strategy: bool = False) -> None:
    """Fill r.conclusion. `primary` names the test groups that carry the main claim."""
    checks = reliability(ctx, recommended=recommended, single_season=single_season, sel=sel, strategy=strategy)
    n_ok = all(c["ok"] for c in checks if c["label"] == "Sample size")

    if descriptive is not None:
        status, title, summary = "descriptive", "Description, not a test", descriptive
        evidence = []
    else:
        tests = [t for t in r.tests if t["group"] in primary]
        evidence = []
        for t in tests:
            s = strength(t["p"], alpha)
            evidence.append(dict(label=f"{t['name']}" + ("" if len(primary) == 1 else f" ({t['group']})"),
                                 detail=t["value"] + (f". {t['reading']}." if s in ("strong", "clear") and t.get("reading") else "."),
                                 strength=s, p=t["p"], n=t["n"]))
        evidence.sort(key=lambda e: STRENGTH_ORDER.index(e["strength"]))
        found = [e for e in evidence if e["strength"] in ("strong", "clear")]
        weak = [e for e in evidence if e["strength"] == "weak"]
        if found:
            status = "found"
            title = "Pattern found" if n_ok else "Pattern found, on a small sample"
            summary = takeaway_found or "Results differ from what chance would produce."
        elif not evidence:
            status, title, summary = "insufficient", "Not enough data to test", \
                "No test could run on this selection. Widen the filters or add matches."
        elif not n_ok:
            status = "insufficient"
            title = "Not enough data yet"
            summary = ("Nothing crossed the threshold, but this sample is too small to rule an effect out."
                       + (f" {len(weak)} result(s) fall between p = 0.05 and the threshold." if weak else ""))
        else:
            status = "none"
            title = "No pattern detected"
            summary = takeaway_none or "Results are consistent with chance at this sample size."
            if weak:
                summary += f" {len(weak)} result(s) fall between p = 0.05 and the threshold: worth re-checking with more data, not worth acting on."

    steps = list((next_found if status == "found" else next_none) or [])
    if not n_ok:
        steps.insert(0, "Add more matches before acting on this page; small samples swing easily.")
    if any(c["label"] == "One season" and not c["ok"] for c in checks):
        steps.insert(0, "Filter to a single season, then repeat.")
    if status == "found" and ctx.filters.dataset != "demo":
        steps.append("Confirm on a different season or period (Compare page) before treating it as a rule.")
    r.conclusion = dict(status=status, title=title, summary=summary, evidence=evidence,
                        reliability=checks, next_steps=steps)
