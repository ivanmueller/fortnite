"""
The dashboard's pages. Each page groups existing analyses into sections; each section shows a
plain-language takeaway, a few key numbers, and only its most useful charts and table up front.
Everything else an analysis produces stays available under "Details" in the dashboard.
"""
from __future__ import annotations


def section(analysis: str, title: str, question: str, charts: list[str] | None = None,
            table: str | None = None, columns: list[str] | None = None, metrics: list[str] | None = None) -> dict:
    return dict(analysis=analysis, title=title, question=question, charts=charts or [],
                table=dict(title=table, columns=columns) if table else None, metrics=metrics or [])


PAGES = [
    dict(id="overview", title="Overview", question="What data is in the selection?", sections=[
        section("overview", "The data", "How many matches, from when, and how strong were the lobbies?",
                charts=["Matches per week", "Lobby strength"],
                metrics=["Matches", "Days covered", "Server replays", "Median lobby strength"]),
    ]),
    dict(id="storm", title="Storm", question="How do zones behave, and where will the next one go?", sections=[
        section("zone_geometry", "Zone rules", "What kind of zone is each one, and how does it move?",
                charts=["Where the next zone goes", "Where endgames land"], table="Zone by zone",
                columns=["Zone", "Type", "Type in % of matches", "Next zone inside current", "Wait before (s)"]),
        section("zone_randomness", "Is zone placement random?", "Do zones favour the edge, a direction, or the bus route?",
                charts=["How far toward the edge each zone lands", "Turn from previous pull"],
                metrics=["Matches", "Storm pulls"]),
        section("zone_forecast", "Zone forecast", "Where will the next zone go, and where will the game end?",
                charts=["Next zone forecast", "Endgame forecast"], table="Forecast models compared",
                metrics=["Next zone forecast", "On the newest matches", "Endgame forecast", "Endgame from the rules alone"]),
        section("zone_check", "Zone accuracy", "Are the zones in the data right? Checked against the storm damage players took.",
                charts=["Zone replay map", "Endgame zones close-up"], table="Zone timeline",
                metrics=["Zone-to-zone continuity", "Storm damage where our storm says outside", "Clearly inside but damaged",
                         "Winner at the final zone"]),
    ]),
    dict(id="drops", title="Drops and loot", question="Where should we land?", sections=[
        section("drops", "Drop spots", "How contested is each spot, and how do players who land there finish?",
                charts=["Where players land"], table="Drop spots",
                columns=["Drop spot", "Players per match", "Contested", "Eliminated off spawn", "Placement vs similar players",
                         "In zone 1"],
                metrics=["Contested", "Eliminated off spawn", "Landed inside zone 2"]),
        section("loot", "Loot", "How fast and how well do players loot, and does it matter?",
                charts=["Average placement by chests opened in 3 min"], table="Loot by drop spot",
                columns=["Drop spot", "Time to first chest (s)", "Chests in 3 min", "Rare+ weapon by 3 min", "AR and shotgun by 3 min"],
                metrics=["Time to first chest", "Chests in 3 min", "AR and shotgun by 3 min"]),
    ]),
    dict(id="rotations", title="Rotations", question="When and where should we move?", sections=[
        section("rotation", "Rotation timing", "Does arriving later than comparable players cost eliminations and placement?",
                charts=["Eliminated during the shrink, by timing vs comparable players",
                        "Seconds behind the first player in, by how they finished"],
                metrics=["Median lag behind first in", "Took storm"]),
        section("endgame_height", "Endgame height", "In the endgame, does rotating through high ground or low ground pay off?",
                charts=["How teams finished, by endgame height path", "How each height finished, zone by zone"],
                metrics=["Teams tracked through the endgame", "Held high ground", "Stayed low", "Typical height gap, high vs low"]),
        section("positioning", "Positioning", "Do teams closer to the next zone finish better?",
                charts=["How teams finished, by distance from the closing zone"]),
    ]),
    dict(id="fights", title="Fights", question="Which fights should we take?", sections=[
        section("fights", "Fight outcomes", "How much do health, the first shot and third parties decide fights?",
                charts=["Win rate by health advantage going in", "Third-party rate by zone"],
                metrics=["Fights", "Median fight length", "Third-partied"]),
        section("eliminations", "Where eliminations happen", "Do eliminations happen inside the next zone or on the way to it?",
                charts=["Share of eliminations outside the closing circle, by zone"], metrics=["Outside closing circle"]),
    ]),
    dict(id="surge", title="Surge", question="How much damage keeps a player safe from surge?", sections=[
        section("surge", "Surge", "When does surge trigger, who does it hit, and what damage is enough?",
                charts=["Chance of being surged by damage dealt beforehand"], table="Surge by zone",
                metrics=["Surge episodes", "Players alive at surge", "Damage per tick"]),
        section("height_damage", "Getting damage from height", "Which height over opponents deals the most damage for the least taken back?",
                charts=["Damage dealt for every 1 taken back, by height and range", "Damage dealt and taken back, by height over the opponent"],
                metrics=["Trade from 15 m+ above", "Trade from 5–15 m above", "One-sided tags from 15 m+ above"]),
    ]),
    dict(id="compare", title="Compare", question="How does group A differ from group B?", needs_compare=True, sections=[
        section("divergence", "A vs B", "Which measures differ between the two groups?",
                charts=["Largest differences (effect size, A vs B)"], table="All measures side by side",
                columns=["Section", "Measure", "A", "B", "Verdict"],
                metrics=["Matches in A", "Matches in B", "Players per team"]),
    ]),
]
