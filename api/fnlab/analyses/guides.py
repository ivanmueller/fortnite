"""
Guide text for every analysis page, keyed by analysis id.

Edit freely: changes show up in the dashboard as soon as you save (the API reloads).
`terms` keys match a metric label, a test name or a test group shown on the page.
`charts` keys match chart titles.
"""
from __future__ import annotations

from ..guide import Guide

P_VALUE = ("Probability of a result at least this extreme if there were no real effect. Smaller is stronger "
           "evidence. Results count as significant only below the threshold chosen at the top of the page.")

GUIDES: dict[str, Guide] = {
    "overview": Guide(
        question="What data is in this selection, and is there enough of it to test anything?",
        method=[
            "Counts the matches in the current selection by week, season and region.",
            "Counts the storm phases recorded per match as a data-quality check. Complete tournament matches "
            "normally record eight or more.",
        ],
        terms={
            "Matches": "Matches in the current selection.",
            "Days covered": "Calendar days from the first to the last match.",
            "Seasons": "Game versions in the selection. Most zone tests should run on one season at a time.",
            "Regions": "Competitive regions in the selection.",
            "Server replays": "Share recorded by Epic's servers. Server replays include every player; replays "
                              "recorded on a player's own machine only include players near the recorder.",
            "Median players": "Median human players per match. Far below the lobby size suggests client replays "
                              "or replays that ended early.",
            "Median lobby strength": "Share of each lobby's players in the top 1,000: by Epic's Power Rankings when "
                                     "downloaded (data menu option P), otherwise by that tournament's own leaderboard. "
                                     "High means an even, high-skill lobby.",
            "Median lobby PR": "Average Power Ranking rating of the ranked players in each lobby. Power Rankings are "
                               "Epic's cross-event skill rating (top 10,000 players).",
        },
        charts={
            "Matches per week": "Bars stacked by season. Gaps are weeks without data; a change of colour marks a new season.",
            "Matches by region": "How the selection splits across regions. Large imbalances mean pooled results mostly describe one region.",
            "Storm phases recorded per match": "Most matches should sit at the same high count. Low counts are replays "
                                               "that stopped early, for example when the recording player was eliminated.",
            "Lobby strength": "How many matches have each level of lobby strength. Early qualifier rounds sit low and "
                              "mixed; later rounds and finals sit high. Uses Power Rankings when downloaded.",
        },
        conclude=[
            "Use this page to choose what to study. Zone tests need roughly 200 or more matches from a single season "
            "to detect modest effects; position and fight pages need about 100.",
            "If server replays are below 100%, expect Position vs placement and High ground to under-count distant players.",
            "For strategy pages (positioning, fights, height), filter to strong lobbies: in mixed lobbies, outcomes "
            "reflect skill gaps as much as decisions. Storm and bus pages can use every lobby.",
        ],
        limits=["Counts only. Nothing on this page is a statistical test."],
    ),

    "zone_randomness": Guide(
        question="Is the storm's next circle placed at random, or does it follow patterns a team could anticipate?",
        method=[
            "Each storm pull is described relative to the circle it shrinks from, so pulls from different matches "
            "and seasons are directly comparable.",
            "Distance: u = (distance moved ÷ maximum allowed distance)². If the next center were placed uniformly at "
            "random inside the area it can occupy, u would be evenly spread between 0 and 1, with a mean of 0.50.",
            "Direction: each pull's compass direction, the same direction measured from the bus heading, and the turn "
            "from the previous pull.",
            "Per-match tests summarise each match once, so linked pulls within a match don't inflate significance. "
            "Per-phase tests use one pull per match.",
            "Late in a match the storm becomes a moving zone: the new circle drifts past the old edge. Distance tests "
            "use shrinking zones only; direction tests use whichever zones the Zones setting picks.",
            "The bus route comes from the bus itself when the replay records it (exact). Otherwise it's fitted from "
            "where players first skydive, and matches with a loose fit are left out of bus tests.",
        ],
        terms={
            "Pull distance": "Tests whether u departs from an even spread: t-test of match averages against 0.50 "
                             "(per match), Kolmogorov–Smirnov test (per phase). Mean u above 0.50 means pulls favour "
                             "the circle's edge; below 0.50, its center.",
            "Compass direction": "Rayleigh test for a preferred map direction. R measures concentration: 0 means no "
                                 "preferred direction, 1 means every pull goes the same way.",
            "Direction vs bus": "Rayleigh test on pull direction measured from the bus heading (0° is the way the "
                                "bus flies). Significant with a mean near 0° means pulls lean the way the bus flies.",
            "Keeps previous direction": "Rayleigh test on the turn between consecutive pulls. A mean near 0° with a "
                                        "small p means each pull tends to continue the last one.",
            "Per match": "One summary per match: the tests that carry the page's main claim.",
            "Storm pulls": "Pulls with a known starting circle in the selected phase range.",
            "Mean u": "Average u over all pulls: 0.50 if random, 1.00 if every pull reaches the edge.",
            "Bus route known": "Share of matches whose bus route is reliable: exact from the bus itself, or a fitted "
                               "line within about 50 m. Matches without one are left out of the bus tests.",
            "Moving zones": "Pulls where the new circle drifts past the old one's edge. They're excluded from the "
                            "distance test, because 'how close to the edge' doesn't apply to them.",
            "p": P_VALUE,
        },
        charts={
            "Pull direction": "Rose chart of pull directions on the map. Random pulls give a roughly round shape; a "
                              "bulge points to a favoured direction.",
            "Pull direction relative to bus heading": "The same, rotated so 0° is the bus heading. A bulge at 0° "
                                                      "means pulls follow the bus.",
            "Turn from previous pull": "How far each pull turned from the one before. A bulge at 0° means pulls keep "
                                       "going the same way.",
            "Pull distance u": "Bars should sit flat at the dashed line if pulls are random. A tall last bar means "
                               "pulls hit the circle's edge; tall first bars mean they stay central.",
        },
        conclude=[
            "Read the per-match tests first; they carry the main claim. Expand the phases to see when in the game an "
            "effect appears.",
            "Treat a pattern as usable only if it holds within one season, at the chosen threshold, and again on a "
            "different season.",
            "Size matters as much as significance: R below about 0.1 is a real but faint lean, too weak to plan "
            "rotations around by itself.",
        ],
        limits=[
            "Island shape (water, map edges) can constrain where circles land, creating patterns that come from the "
            "map rather than the storm system.",
            "Effects that differ between seasons can cancel out when seasons are mixed.",
        ],
    ),

    "zone_geometry": Guide(
        question="How far, how much and where does the storm move at each phase?",
        method=[
            "For each phase: the current circle's radius, the next circle's radius, and how far the center moves.",
            "Ratios are comparable across seasons. Raw map positions are only comparable within one season.",
        ],
        terms={
            "Storm pulls": "Number of circle-to-circle moves in the selection.",
            "Phases": "Distinct storm phases recorded.",
            "Overall median": "Median of the chosen measure across every pull.",
            "Phase by phase": "Type (shrinking or moving), the usual wait before the phase starts (0 = a continuous "
                              "moving zone), median sizes, and how much the distance moved varies between matches.",
            "Distance moved ÷ current radius": "How far the center travels, relative to the circle's size. 0.3 means "
                                               "it moves 30% of the current radius.",
            "Next radius ÷ current radius": "The shrink factor. 0.6 means the next circle is 60% of the current radius.",
            "u (0 = center, 1 = edge)": "Where in the allowed area the next center lands: 0 at the current center, "
                                        "1 touching the edge.",
        },
        charts={
            "Distribution by phase": "Box plots: the line is the median, the box holds the middle half of pulls, the "
                                     "whiskers the typical range. A flat line instead of a box means that phase moves "
                                     "the same amount in every match: set by the game, not random.",
            "Where next circles land": "Each dot is a next-circle center, coloured by phase. Compare positions only "
                                       "within one season; the map changes between seasons.",
        },
        conclude=[
            "Use this page to size an effect found on Is the storm random?, for example how far the circle "
            "typically moves in the phase where a pattern appears.",
            "Narrow, consistent phases are the predictable ones: rotation timing can be planned furthest ahead there.",
        ],
        limits=["Descriptive only: there is no test against randomness on this page."],
    ),

    "compare_periods": Guide(
        question="Did storm behaviour change between two selections, such as two seasons or before and after a patch?",
        method=[
            "Selection A is the main selection. Selection B is set in the second panel.",
            "Each match is summarised once (average u, average pull direction and so on), then A and B are compared.",
            "Distances use a two-sample Kolmogorov–Smirnov test. Directions use a permutation test: the A and B "
            "labels are shuffled 2,000 times to see how often a difference this large appears by chance.",
        ],
        terms={
            "A vs B": "Direct comparisons. Significant means A and B differ on that measure.",
            "Within A": "The randomness tests run on A alone, to show which side carries an effect.",
            "Within B": "The randomness tests run on B alone.",
            "Pull distance (u)": "Compares how far pulls move within their allowed area.",
            "Distance moved ÷ radius": "Compares how far the center moves relative to circle size.",
            "Compass direction": "Compares the preferred map direction, if any.",
            "Direction vs bus": "Compares how pulls relate to the bus heading.",
            "Keeps previous direction": "Compares how strongly pulls continue the previous direction.",
            "Pull distance": "Within one side: does u depart from an even spread?",
            "A matches": "Matches in selection A.",
            "B matches": "Matches in selection B.",
            "Mean u (A / B)": "Average pull distance on each side; 0.50 if random.",
            "p": P_VALUE,
        },
        charts={
            "Pull distance u": "Share of pulls per bin for A and B. Where the bars diverge is where the distributions differ.",
            "Pull direction relative to bus heading": "Outlines for A and B. Different shapes mean a different relationship to the bus.",
            "Pull direction": "Outlines for A and B on the map's compass. Only compare within the same map.",
        },
        conclude=[
            "A significant difference means the storm behaved differently, not which side is correct. The Within rows "
            "show which side carries the pattern.",
            "To check whether a pattern from one season holds, put that season in A and the next in B. No difference, "
            "with the pattern present within both, means it carried over.",
        ],
        limits=[
            "Differences can come from map, playlist or region changes as well as storm logic. Keep every filter "
            "except the period the same in A and B.",
            "Matches in both selections weaken the comparison.",
        ],
    ),

    "positioning": Guide(
        question="Does where a team stands when the storm starts closing relate to how it finishes?",
        method=[
            "At the moment each storm starts closing, every living player's position is taken. Their distance to the "
            "center of the circle the storm is closing to is divided by that circle's radius: below 1 is inside, "
            "above 1 is outside.",
            "A team's value is the average of its living players.",
            "Within each match and phase, a Spearman rank correlation between team distance and final placement is "
            "computed. The average correlation is tested against zero.",
        ],
        terms={
            "Distance vs placement": "Mean Spearman rho between distance from the closing circle and placement. "
                                     "Positive means teams farther out finish worse. As a guide, 0.1 is weak, 0.3 "
                                     "moderate and 0.5 or more strong.",
            "Per match": "One correlation per match, averaged: the page's main claim.",
            "Team snapshots": "One per team per phase, counting only teams with a living player.",
            "Matches": "Matches contributing at least one snapshot.",
            "Mean rho": "Average rank correlation between distance and placement per match.",
            "p": P_VALUE,
        },
        charts={
            "Average placement by distance from the closing circle": "Each bar is the average final placement of "
                "teams at that distance. Rising bars mean teams farther out finish worse. Buckets with too few teams are left blank.",
            "Share of teams inside the closing circle at shrink start": "Lines by finishing tier. The phase where the "
                "lines separate most is when strong and weak finishers stood most differently.",
        },
        conclude=[
            "The phase where the tier lines separate most is the most informative moment for positioning.",
            "A strong correlation shows that good finishers are better positioned. It does not show that moving in "
            "earlier causes a better finish: strong teams also win more fights.",
        ],
        limits=[
            "Positions are taken after the next circle is known, so this page can't be used to predict zones.",
            "Eliminated players aren't counted, so late phases describe survivors only.",
        ],
    ),

    "eliminations": Guide(
        question="Where do eliminations happen relative to the circle the storm is closing to?",
        method=[
            "Each elimination is matched to the storm phase under way when it happened.",
            "Its distance from the center of the circle the storm was closing to is divided by that circle's radius: "
            "below 1 is inside, above 1 outside.",
        ],
        terms={
            "Eliminations": "Eliminations with a known location after the first storm started closing.",
            "Outside closing circle": "Share that happened outside the circle the storm was closing to.",
            "Median distance": "Median distance from the closing circle's center, in radii of that circle.",
        },
        charts={
            "Share of eliminations outside the closing circle, by phase": "High bars mark phases where players caught "
                "outside the next circle are eliminated most often.",
            "Distance from the closing circle's center": "Bars left of the dashed line are inside the closing circle. "
                "A bump just inside the edge points to fights over edge positions.",
            "Elimination locations": "Each dot is an elimination, coloured by phase. Compare only within one season.",
        },
        conclude=[
            "Phases with a high share outside the circle are where rotating late is most costly.",
            "Read alongside Position vs placement: if late rotators finish worse and eliminations cluster outside "
            "the circle in the same phase, rotations are where those teams are lost.",
        ],
        limits=["Descriptive only.", "Storm deaths aren't separated from fights yet."],
    ),

    "rotation": Guide(
        question="When should you rotate? Do players who reach the next circle later than comparable players get "
                 "eliminated more and finish worse, and how does that change from early shrinking circles to "
                 "endgame moving zones?",
        method=[
            "A rotation is one player, in one storm phase, who is outside the next circle when it appears (the moment "
            "the previous shrink finishes) and still alive when the storm starts moving.",
            "Phases are analysed by type. Shrinking: the next circle sits inside the current one. Moving (with wait): "
            "the circle moves past the old edge after a pause. Moving (continuous): it keeps moving with no pause.",
            "Timing is relative, because arriving before the storm moves is impossible in moving zones. Within each match "
            "and phase, each player is compared with the first player to reach the circle, and with players who started "
            "a similar distance out (arrival time fitted against starting distance).",
            "Timing class: Ahead, Typical or Behind = thirds of that distance-adjusted timing within the match and "
            "phase. Players who never reached the circle are Behind.",
            "Storm time is reconstructed second by second, because Season 42 replays don't record it. Density is "
            "players alive per km² of circle, and players within 100 m of each player when the circle appears.",
        ],
        terms={
            "Later than players at a similar distance, worse placement": "Within each match and phase, a Spearman "
                "correlation between distance-adjusted arrival time and final placement, averaged per match and tested "
                "against zero. Positive: arriving later than comparable players goes with finishing worse.",
            "Behind eliminated more than Ahead": "Per match, the share of Behind players eliminated during the shrink "
                                                 "minus the share of Ahead players, tested against zero.",
            "Starting farther out, worse placement": "Spearman correlation between distance outside the next circle "
                                                     "at the reveal and final placement, per match.",
            "Per match": "All phase types together, one summary per match: the page's main claims.",
            "Shrinking": "The same tests within shrinking phases only.",
            "Moving (with wait)": "The same tests within moving phases that start after a pause.",
            "Moving (continuous)": "The same tests within continuous moving zones (late endgame).",
            "Rotations": "Player-phases where a rotation was needed and the player was alive when the storm moved.",
            "Eliminated before the shrink": "Rotating players eliminated before the storm moved. Reported, not classified.",
            "Median distance outside": "Typical distance outside the next circle when it appeared.",
            "Median lag behind first in": "Typical seconds between the first player reaching the circle and each "
                                          "other rotating player reaching it.",
            "Took storm": "Share of rotations with at least 2 seconds in the storm.",
            "Phase by phase: what each phase looks like and how players rotate": "Phase type, wait, circle size, players "
                "alive, density, and how far out, how far behind the first arrival and how long in the storm the typical "
                "rotating player was.",
            "Skill control: average placement by timing within each Power Rankings band": "Placement by timing class "
                "inside each skill band. If Behind players finish worse within a band, timing matters beyond skill.",
            "p": P_VALUE,
        },
        charts={
            "Seconds behind the first player in, by how they finished": "Median lag behind the first arrival per phase, "
                "for top-10, 11th–50th and 51st-or-lower finishers. A gap between lines is how much earlier better "
                "finishers get in.",
            "Eliminated during the shrink, by timing vs comparable players": "One line per class. The gap between Behind "
                "and Ahead in a phase is the cost of falling behind there.",
            "Storm time, by how they finished": "Average storm seconds per phase by finishing tier.",
            "Average final placement by timing vs comparable players": "Lower is better. Read with the skill-control table.",
        },
        conclude=[
            "Compare phase types separately: endgame continuous zones and early shrinking circles reward different "
            "timing, and pooling them can hide both.",
            "The phase table shows how crowded each phase is: a timing effect that appears only in dense phases is "
            "about third-party risk, not the storm.",
            "Check the skill-control table before concluding anything about timing itself.",
        ],
        limits=[
            "Storm time is reconstructed (accurate to a few seconds) and measures time in the storm, not damage taken: "
            "replay health data isn't extracted yet.",
            "Associations, not causes: strong players both rotate well and win fights.",
            "Solo play only so far; duos and trios will need team-level rotations.",
        ],
    ),

    "drops": Guide(
        question="Where should we drop? How much does a contested drop cost, how much does landing far from the first "
                 "circles cost, and which named spots work best for players of a given skill?",
        method=[
            "Landing is detected from movement, because Season 42 replays don't record the skydive: the first moment a "
            "player stays at the same height (under 3 m/s vertically for 3 seconds) after descending at least 50 m "
            "from where they appeared after the bus. The pre-game warm-up island is ignored.",
            "Contest: players from other teams landing within 150 m (contested) and within 300 m. With Power Rankings "
            "downloaded, also how many of those are in the top 1,000.",
            "Context: distance from the exact bus route, and distance outside the first and second storm circles at "
            "landing. Named places come from the current map (data menu option M).",
            "Outcomes: eliminated within 2 minutes of landing (off spawn), eliminations in the first 3 minutes, and final "
            "placement. Tests summarise each match once.",
        ],
        terms={
            "Contested drops eliminated off spawn more": "Per match, the off-spawn elimination rate of contested landings "
                                                         "minus uncontested ones, tested against zero.",
            "More opponents nearby, worse placement": "Spearman correlation per match between opponents within 300 m "
                                                      "and final placement. Positive: more contest, worse finish.",
            "Landing far from zone 2, worse placement": "Spearman correlation per match between distance outside the "
                                                        "second circle at landing and final placement.",
            "Strong opponents nearby, worse placement": "The same, counting only Power Rankings top-1,000 opponents.",
            "Early eliminations, better placement": "Among players who survive the landing, Spearman correlation between "
                                                    "eliminations in the first 3 minutes and placement. Negative: "
                                                    "winning the spawn fight goes with finishing better.",
            "Farther from the bus, less contested": "Spearman correlation per match between distance from the bus line "
                                                    "and opponents within 300 m. Negative: farther drops are quieter.",
            "Per match": "One summary per match: the page's main claims.",
            "Spawn fights": "What happens to players who survive a landing fight.",
            "Bus route": "How the bus line shapes where contest happens.",
            "Landings": "Players whose landing was detected.",
            "Contested": "Another team landed within 150 m.",
            "Eliminated off spawn": "Eliminated within 2 minutes of landing.",
            "Median nearest opponent": "Typical distance to the closest player from another team at landing.",
            "Median glide": "Typical seconds from appearing after the bus to landing.",
            "Landed inside zone 2": "Share of landings already inside the second safe circle.",
            "Drop spots": "One row per named spot with enough landings: contest, off-spawn risk, early eliminations, "
                          "placement, placement compared with what players of the same Power Rankings band usually get "
                          "(negative = better), how often it's in zone 1, distance to zone 2 and to the bus line.",
            "Skill control: placement by contest within each Power Rankings band": "Average placement for contested and "
                "uncontested landings inside each skill band. If contest costs placement within a band, it isn't just "
                "weaker players getting caught in fights.",
            "p": P_VALUE,
        },
        charts={
            "Eliminated off spawn by opponents within 150 m": "How off-spawn risk rises with each extra nearby opponent.",
            "Average placement by opponents within 150 m": "Lower is better.",
            "Average placement by distance outside zone 2 at landing": "Lower is better. 'Zone luck' from the drop.",
            "Where players land": "Every landing, survived or eliminated off spawn, with named places labelled. The map "
                                  "may appear rotated or mirrored compared with the in-game map; the labels show where "
                                  "things are.",
        },
        conclude=[
            "For drop planning, read the Drop spots table: a good spot combines low off-spawn risk, a strong 'vs expected "
            "for skill' and decent zone luck. Contest is the price; zone luck and loot (not measured yet) are the payoff.",
            "Filter to strong lobbies for pro planning: early qualifier drops are less deliberate.",
            "Check the skill-control table before blaming contest itself.",
        ],
        limits=[
            "Loot isn't measured: chests and materials aren't in the extracted data yet.",
            "Contest is proximity, not confirmed fights; teammates aren't counted as opponents.",
            "Spot names come from the current map only; older-season matches show no names.",
        ],
    ),

    "height": Guide(
        question="Does being higher than your opponents win fights and games, and from which storm phase does it start to matter?",
        method=[
            "Height is always relative. Raw elevation mostly reflects terrain, so it's compared with nearby opponents.",
            "Fights: for each knock or elimination, the winner's height at that moment is compared with the victim's. "
            "Fights where the gap is smaller than the 'level' setting are left out; for the rest, the question is how "
            "often the higher player won. 50% means height made no difference.",
            "Standing: when each storm starts closing, the teams in that match are ranked by average height into "
            "thirds (low, mid and high ground) and their final placements compared.",
        ],
        terms={
            "Higher player wins": "Share of fights with a clear height gap won by the higher player, tested per match "
                                  "against 50% (t-test of match shares).",
            "Fights, per match": "One win share per match: the main claim about fights.",
            "Fights by phase": "Binomial test against 50% for fights within each phase. Shows when height starts to "
                               "decide fights.",
            "Higher teams finish better": "Mean Spearman rho between a team's height rank and its placement. "
                                          "Positive means higher teams finish better.",
            "Standing, per match": "One correlation per match: the main claim about standing.",
            "Standing by phase": "The same correlation within each phase. Shows when holding height starts to matter.",
            "Fights matched": "Knocks and eliminations where the winner's position at that moment is known.",
            "Clear height gap": "Fights where the height difference exceeds the 'level' setting.",
            "Median height gap": "Typical height difference between winner and victim, over all matched fights.",
            "Team snapshots": "One per team per phase, counting teams with a living player.",
            "p": P_VALUE,
        },
        charts={
            "Fights won by the higher player, by phase": "Bars above the dashed 50% line mean the higher player won "
                "more often than chance in that phase.",
            "Height of the winner relative to the player they beat": "How fights split by height difference. More "
                "fights on the 'above' side than the 'below' side means winners tend to be higher.",
            "Average placement by height at each storm phase": "One line per height tier. Lower is better. The phase "
                "where the high-ground line drops below the others is when holding height starts to pay.",
        },
        conclude=[
            "The first phase where fights turn significant is when height starts to decide fights.",
            "If fights show an advantage but standing doesn't, height wins individual fights without changing final "
            "placement, or the reverse. Both together are the strongest case.",
        ],
        limits=[
            "Height includes both terrain and builds; this page can't yet tell natural high ground from built height.",
            "Associations, not causes: strong players both take height and win fights.",
        ],
    ),
}
