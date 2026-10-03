"""
Guide text for every analysis page, keyed by analysis id.

Edit freely: changes show up in the dashboard as soon as you save (the API reloads).
`terms` keys match a metric label, a test name or a test group shown on the page.
`charts` keys match chart titles.
"""
from __future__ import annotations

from ..guide import Guide

P_VALUE = ("Probability of a result at least this extreme if there were no real effect. Smaller is stronger "
           "evidence. Results count only below a strict bar of 0.005, because each page tests many things at once.")

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
            "use shrinking zones only; direction tests use all zones.",
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
            "How far toward the edge each zone lands": "For shrinking zones: how far the next zone's centre sits from the "
                "middle of the current zone toward its edge, as a percentage of the room it has. 50% is what random "
                "placement would give; higher means zones hug the edge.",
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
            "Zones": "Distinct storm zones recorded.",
            "Type": "Shrinking: the next zone sits fully inside the current one. 50/50: it partly overlaps the current "
                    "one. Shifted: it lies entirely outside, after a wait. Moving: the zone keeps moving with no wait.",
            "Type in % of matches": "How often this zone has its most common type across the selected matches. Near "
                                    "100% means the game sets it; lower means it varies by match or playlist.",
            "Next zone inside current": "Median share of the next zone's area that lies inside the current zone: 100% "
                                        "for a shrinking zone, around 50% for a true 50/50, 0% for a shifted zone.",
            "Overall median": "Median of the chosen measure across every pull.",
            "Zone by zone": "Type (shrinking or moving), the usual wait before the phase starts (0 = a continuous "
                              "moving zone), median sizes, and how much the distance moved varies between matches.",
            "Distance moved ÷ current radius": "How far the center travels, relative to the circle's size. 0.3 means "
                                               "it moves 30% of the current radius.",
            "Next radius ÷ current radius": "The shrink factor. 0.6 means the next circle is 60% of the current radius.",
            "u (0 = center, 1 = edge)": "Where in the allowed area the next center lands: 0 at the current center, "
                                        "1 touching the edge.",
        },
        charts={
            "Distribution by zone": "Box plots: the line is the median, the box holds the middle half of pulls, the "
                                     "whiskers the typical range. A flat line instead of a box means that phase moves "
                                     "the same amount in every match: set by the game, not random.",
            "Where the next zone goes": "Each dot is one zone's next position, drawn as if the current zone were the dotted "
                "circle and the previous pull pointed straight up. Dots near the top edge: the zone kept going the same way "
                "toward the edge. Dots below the circle: the zone reversed. Dots outside the circle: it moved beyond the old "
                "edge (50/50s, shifted and moving zones).",
            "Where endgames land": "The final zone of each match on the map, with named places. Clusters show areas where "
                                   "endgames tend to end up this season.",
            "Where next circles land": "Each dot is a next-circle center, coloured by zone. Compare positions only "
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
            "Share of eliminations outside the closing circle, by zone": "High bars mark phases where players caught "
                "outside the next circle are eliminated most often.",
            "Distance from the closing circle's center": "Bars left of the dashed line are inside the closing circle. "
                "A bump just inside the edge points to fights over edge positions.",
            "Elimination locations": "Each dot is an elimination, coloured by zone. Compare only within one season.",
        },
        conclude=[
            "Zones with a high share outside the circle are where rotating late is most costly.",
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
            "Zones are analysed by type, in pros' vocabulary. Shrinking: the next zone sits fully inside the current one. "
            "50/50: it partly overlaps the current one (half in, half out). Shifted: it lies entirely outside, after a "
            "wait. Moving: the zone keeps moving with no wait (late endgame).",
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
            "Per match": "All zone types together, one summary per match: the page's main claims.",
            "Shrinking": "The same tests within shrinking zones only.",
            "50/50": "The same tests within 50/50 zones (the next zone partly overlaps the current one).",
            "Shifted": "The same tests within shifted zones (the next zone entirely outside, after a wait).",
            "Moving": "The same tests within continuously moving zones (late endgame).",
            "Rotations": "Player-phases where a rotation was needed and the player was alive when the storm moved.",
            "Eliminated before the shrink": "Rotating players eliminated before the storm moved. Reported, not classified.",
            "Median distance outside": "Typical distance outside the next circle when it appeared.",
            "Median lag behind first in": "Typical seconds between the first player reaching the circle and each "
                                          "other rotating player reaching it.",
            "Took storm": "Share of rotations with at least 2 seconds in the storm.",
            "Zone by zone: what each zone looks like and how players rotate": "Zone type, wait, circle size, players "
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
            "Compare zone types separately: moving endgame zones, 50/50s and early shrinking zones reward different "
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

    "divergence": Guide(
        question="Where does one group of matches (for example the Global Championship) play out differently from "
                 "another (for example everything else)?",
        method=[
            "Pick the group to study as Selection A (for example the Globals event windows) and the comparison as "
            "Selection B (leave it open for 'everything else'). Matches in both count for A only.",
            "Each measure is computed by the same code as its own page, once per match, for both groups: storm pulls, "
            "rotation by zone type, drops, fights and eliminations.",
            "The groups are compared with a Mann-Whitney U test on the per-match values, with an effect size showing how "
            "consistently A sits above or below B.",
        ],
        terms={
            "All measures side by side": "Every measure with A and B medians, effect size, p-value and verdict. "
                                         "'Mode may explain it' marks team-play measures when the groups play "
                                         "different modes (for example Duos vs Solos).",
            "Effect size": "Rank-biserial correlation: +1 means every A match is higher than every B match, −1 every A "
                           "match lower, 0 no difference. Around ±0.3 is a moderate difference, ±0.5 or more is large.",
            "Divergent": "p below the threshold: a clear difference.",
            "Possible": "p below 0.05 but above the threshold: a lead worth checking, not a finding.",
            "Matches in A": "Matches in the group being studied.",
            "Matches in B": "Matches in the comparison group, after removing any overlap with A.",
            "Players per team": "Median team size in each group: 1 for Solos, 2 for Duos.",
            "Lobby strength": "Median share of each lobby in the Power Rankings top 1,000. LAN events where players used "
                              "event accounts read near zero.",
            "Storm": "Storm measures don't depend on how players play, so differences there are about the game's "
                     "storm settings.",
            "Rotation": "Rotation measures by zone type, as on the Rotation timing page.",
            "Drops": "Landing measures, as on the Drop spots page.",
            "Fights and eliminations": "Fight and elimination measures, as on the High ground and Eliminations pages.",
            "p": P_VALUE,
        },
        charts={
            "Largest differences (effect size, A vs B)": "The fifteen measures where the groups differ most. Bars to the "
                "right: A higher; to the left: A lower.",
        },
        conclude=[
            "A Divergent storm measure means the storm itself differed (settings or patch). Rotation and drop differences "
            "can then follow from it, so read those with the storm in mind.",
            "When the groups play different modes, team-play divergences need a same-mode comparison before you act on "
            "them. The October FNCS Solos finals will give elite solo matches to compare like with like.",
            "Small groups reveal only large differences. A 'Possible' in a 12-match group is a lead, not evidence.",
        ],
        limits=[
            "With dozens of measures tested, a few 'Possible' results are expected by chance.",
            "Groups can differ in patch, lobby size and mode as well as skill. The Lobby rows show what differs.",
        ],
    ),

    "loot": Guide(
        question="How fast and how well do players loot after landing, which drop spots pay off, and does early loot "
                 "predict placement?",
        method=[
            "For each player, over a window after landing (3 minutes by default): time to the first chest, chests opened, "
            "items taken, weapons and heals taken, the best weapon rarity held, and whether they held both an assault "
            "rifle and a shotgun.",
            "Chest openers and item takers come straight from the replay; when it didn't record one, the nearest player "
            "within 3 m is used.",
            "Tests use only players still alive at the end of the window, so players eliminated off spawn don't create a "
            "fake 'little loot, bad finish' link. Each match is summarised once.",
        ],
        terms={
            "Faster first chest, better placement": "Spearman correlation per match between seconds to the first chest "
                                                    "and placement. Positive: faster chest, better finish.",
            "More chests in 3 min, better placement": "Correlation per match between chests opened and placement. "
                                                      "Negative: more chests, better finish.",
            "More items in 3 min, better placement": "The same for items picked up.",
            "Better weapon rarity at 3 min, better placement": "The same for the best weapon rarity held (common = 1 "
                                                              "to legendary = 5). Negative: better weapons, better finish.",
            "AR and shotgun by 3 min, better placement": "Per match, the average placement of players holding both an "
                                                         "assault rifle and a shotgun minus everyone else's. Negative: "
                                                         "the pair finishes better.",
            "Landings": "Players whose landing was detected.",
            "Time to first chest": "Median seconds from landing to opening the first chest.",
            "Chests in 3 min": "Median chests opened in the window, by players who survived it.",
            "Items taken in 3 min": "Median items picked up in the window.",
            "AR and shotgun by 3 min": "Share holding both by the end of the window.",
            "Rare or better weapon by 3 min": "Share holding a rare, epic or legendary weapon by the end of the window.",
            "Loot by drop spot": "Per named spot: contest, time to first chest, chests, weapon quality, survival of the "
                                 "window, and placement compared with players of the same Power Rankings band.",
            "Skill control: placement by early chests within each Power Rankings band": "If more chests go with better "
                "placement inside each band, looting matters beyond skill.",
            "Per match": "One summary per match: the page's main claims.",
            "p": P_VALUE,
        },
        charts={
            "Time from landing to first chest": "How quickly players reach a chest.",
            "Average placement by chests opened in 3 min": "Lower is better.",
            "Average placement by best weapon rarity at 3 min": "Lower is better.",
        },
        conclude=[
            "Use 'Loot by drop spot' to weigh what a spot gives (chests, weapon quality) against what it costs (contest, "
            "survival) and how players of your skill do there.",
            "A link between loot and placement can also mean strong players loot efficiently: check the skill table.",
        ],
        limits=[
            "Inventories aren't in replays; weapons are measured by what players hold in hand.",
            "Floor loot that's never touched isn't counted against a spot.",
        ],
    ),

    "fights": Guide(
        question="Which fights should we take? How much do health going in, shooting first and third parties decide "
                 "fights, and how much storm damage do players take?",
        method=[
            "A fight is a run of hits between two teams with gaps under 15 s, with at least 3 hits or ending in an "
            "elimination. A side loses when a player is eliminated during it (or within 5 s after).",
            "Health going in is health plus shield 1 s before the first hit, averaged over the side's players.",
            "A fight is third-partied when another team hits either side during it. Afterwards we check whether the "
            "winner was eliminated within 60 s.",
            "Storm damage is health or shield lost outside the storm circle with no player hit nearby.",
        ],
        terms={
            "More health going in wins": "Per match, the share of decided fights with a clear health gap won by the side "
                                         "with more, tested against 50%.",
            "First to shoot wins": "Per match, the share of decided fights won by the side that landed the first hit.",
            "Third-partied fights cost the winner": "Per match, how much more often a winner is eliminated within 60 s "
                                                    "when a third team joined the fight.",
            "Storm damage in zones 2–4, worse placement": "Among players who reach zone 5, the correlation between storm "
                                                           "damage taken in phases 2–4 and placement.",
            "Fights": "Fights detected from damage.",
            "Decided": "Share of fights where one side lost a player.",
            "Median fight length": "Seconds from first to last hit.",
            "Third-partied": "Share of fights another team joined.",
            "Median health going in": "Health plus shield 1 s before the first hit.",
            "Storm damage per player": "Median total storm damage, players who took any.",
            "Fights by zone": "Fights, typical length and third-party rate in each storm phase (0 = before the first storm).",
            "Per match": "One summary per match: the page's main claims.",
            "p": P_VALUE,
        },
        charts={
            "Win rate by health advantage going in": "How often a side wins at each health gap. The dashed line is 50%.",
            "Third-party rate by zone": "When fights draw a third team.",
            "Fight length": "How long fights last.",
            "Storm damage taken, by zone": "Total storm damage across all players in each phase.",
        },
        conclude=[
            "The health-advantage chart gives a 'take or skip' rule: the gap at which the win rate clearly passes 50%.",
            "Zones with high third-party rates reward short fights and disengaging early.",
        ],
        limits=[
            "Health updates can lag a hit slightly, so health going in is read 1 s early.",
            "Fights are inferred from damage; a fight with no damage landed isn't seen.",
        ],
    ),

    "surge": Guide(
        question="When does competitive storm surge trigger, who does it hit, and what surge score keeps a team safe?",
        method=[
            "Replays don't record surge directly. It appears as health lost inside the safe zone with no player hitting them, by 3 or "
            "more players in the same second, or repeating every 3–7 s for one player (a surge on a single duo or a lone player; the "
            "storm ticks every second and fall damage doesn't repeat). Without the second rule, a surge on one duo would be missed. "
            "Ticks within 10 s form one episode.",
            "For each episode: players alive, players hit, damage per tick, and each alive player's surge score before it started.",
            "Surge score is the rule the Surge study measured. Since October 2025, tournaments surge the teams with the lowest net "
            "damage (damage dealt minus damage taken, between players only); older matches used damage dealt. Comparing surged and "
            "safe players' scores estimates the threshold to stay safe.",
        ],
        terms={
            "Surge hits players with a lower surge score": "Per episode, a rank test of surge scores of surged vs safe "
                                                           "players, combined across episodes.",
            "Surged players finish worse": "Per match, surged players' average placement minus safe players'.",
            "Matches with in-match data": "Matches that include health and damage.",
            "Surge episodes": "Surge episodes detected.",
            "Matches with surge": "Matches with at least one episode.",
            "Players alive at surge": "Median players alive when an episode starts.",
            "Players hit per episode": "Median players surged per episode.",
            "Damage per tick": "Median health or shield lost per surge tick.",
            "Surge by zone": "Per phase: episodes, players alive and hit, damage per tick, and the highest surge score of a "
                              "surged player and the lowest of a safe player.",
            "Per episode": "One test per surge episode, combined.",
            "Per match": "One summary per match.",
            "p": P_VALUE,
        },
        charts={
            "Surge episodes by zone": "Which phases trigger surge.",
            "Chance of being surged, by damage rank within the same surge": "Players ranked against the others alive at the same "
                "surge, by the surge score the Surge study measured. The share surged should fall from the "
                "bottom quarter to the top. Ranking within each surge avoids mixing early surges with late ones, when a smaller "
                "lobby means a bigger share is hit.",
        },
        conclude=[
            "The 'Highest surge score of a surged player' column is a practical target: above it, nobody was surged in "
            "that phase. Under net damage, damage you take lowers it, so avoiding chip damage counts as much as tagging.",
            "Surge is round-specific: early qualifier rounds may never trigger it. Collect later rounds with option T.",
        ],
        limits=[
            "Detection is inferred from damage patterns; very small surges (fewer than 3 players) aren't caught.",
            "Surge score counts damage between players on different teams only (no storm, fall or self damage).",
        ],
    ),

    "endgame_height": Guide(
        question="In the endgame, does rotating through high ground or low ground pay off?",
        method=[
            "Endgame means zone 6 on: the 50/50s, shifted and moving zones. Early-game height is left out on purpose: it "
            "mostly reflects where players landed, not a positioning choice.",
            "When each zone starts closing, every team's average height is ranked against the other teams alive at that "
            "moment: low ground (bottom third), mid ground, high ground (top third).",
            "A team's path is its tiers across the endgame zones it was alive for: held high ground, climbed, mixed, "
            "dropped, or stayed low.",
            "Outcomes compare each team only with rivals alive at the same moment, so late-game survivors don't flatter "
            "any tier.",
        ],
        terms={
            "Higher teams finish better in the endgame": "Within each endgame zone, the link between a team's height rank "
                                                         "and its final placement, summarised per match.",
            "Holding high beats staying low": "Per match, how much better (or worse) teams that held high ground finished "
                                              "than teams that stayed low, in points of finishing rank.",
            "Teams tracked through the endgame": "Teams alive for at least two endgame zones.",
            "Held high ground": "Share of those teams that spent most of the endgame in the top third by height.",
            "Stayed low": "Share that spent most of the endgame in the bottom third.",
            "Typical height gap, high vs low": "Median difference in average height between high- and low-ground teams.",
            "Endgame height paths": "Each path: how many teams took it and how they finished against other endgame teams.",
            "Per match": "One summary per match: the page's main claims.",
            "By zone": "The same link, zone by zone.",
            "p": P_VALUE,
        },
        charts={
            "How teams finished, by endgame height path": "Each bar: the share of other endgame teams that teams on this "
                "path finished ahead of. Above the dashed 50% line means better than average.",
            "How each height finished, zone by zone": "For each zone, how low, mid and high-ground teams finished against "
                "the rivals alive then. Lines above 50% did better than average.",
            "Teams surviving each zone, by height": "Share of teams in each tier still alive when the next zone closes.",
        },
        conclude=[
            "If held high and climbed both beat stayed low, the endgame rewards getting height, even late.",
            "If the zone-by-zone lines only separate in moving zones, height matters most once the storm keeps moving.",
        ],
        limits=[
            "Height is measured at each zone's start; a team that rotates low and builds up mid-zone counts as low.",
            "Associations, not causes: stronger teams may both take height and win more.",
        ],
    ),

    "height_damage": Guide(
        question="When we need damage (surge), which height over the opponent deals the most damage for the least taken back?",
        method=[
            "Every hit between players is paired with both players' positions at that moment: the attacker's height over "
            "the target, and the range.",
            "Hits between two teams no more than 15 s apart form one exchange. For each side: damage dealt, damage taken "
            "back, its average height over the other side, and the typical range.",
            "From zone 4 on, when surge becomes relevant; earlier fights mostly reflect the landing.",
        ],
        terms={
            "Higher side wins the damage trade": "Per match, the link between a side's height over its opponent and its net "
                                                 "damage (dealt minus taken back) in each exchange.",
            "By range": "The same link at close, mid and long range.",
            "Exchanges": "Runs of hits between two teams from zone 4 on.",
            "Trade from 15 m+ above": "Damage dealt for every 1 taken back when 15 m or more above the opponent.",
            "Trade from 5–15 m above": "The same for a smaller height edge.",
            "One-sided tags from 15 m+ above": "Share of exchanges from 15 m+ above where the higher side took no damage back.",
            "Damage trade by range and height": "Every range and height band: exchanges, damage dealt and taken back, the "
                                                "trade, and how often it was one-sided.",
            "Per match": "One summary per match: the page's main claim.",
            "p": P_VALUE,
        },
        charts={
            "Damage dealt for every 1 taken back, by height and range": "One line per range. Above the dashed line at 1, "
                "that height deals more than it takes back. Level ground is always about 1 by definition.",
            "Damage dealt and taken back, by height over the opponent": "Average damage dealt and taken back per exchange. "
                "The gap between the bars is the height advantage.",
            "One-sided tags, by height over the opponent": "How often each height dealt damage and took none back: the "
                                                           "safe tags a surge tower is for.",
        },
        conclude=[
            "The height at which the range lines rise clearly above 1 is where tagging becomes cheap damage.",
            "A high one-sided share from far above is the case for building a tower before a surge check.",
        ],
        limits=[
            "Damage counts hits on players only; it doesn't see damage absorbed by builds.",
            "Bands with fewer than 15 exchanges are left blank.",
        ],
    ),

    "audit_zones": Guide(
        question="Where did the team set up each early zone, was a stronger surge base available given where other teams were "
                 "rotating, and how did their surge go?",
        method=[
            "Surge bases are scored in zones 2–5, where traditional surge bases are built. Their base for each zone is their median "
            "position while the next zone was showing and before it started closing.",
            "Every spot in the next zone is scored on tag opportunities: enemies who are rotating (running, and outside the next "
            "zone or heading into it), within tagging range, on the outward side of the spot (looking toward the storm with the "
            "zone at your back), counted double when the spot's natural ground is 15 m or more above them. Players holding still "
            "don't count: they're other bases, not tags.",
            "Tagging range is learned from the data: the middle 60% of the distances at which one-sided tags actually happened in "
            "zones 2–5 (never closer than 20 m).",
            "Danger is enemy player-seconds within 30 m. Their base is compared with the best spot that had no more danger.",
            "Validation: for every team's base in every hold, how well the score predicts the damage that team actually dealt, "
            "compared with a simpler score (just being near enemies).",
            "Surge: each detected surge episode, whether they were hit, and their surge score (the rule the Surge study measured: "
            "team net damage since October 2025) against the lowest score that stayed safe. Loadouts: the weapons each of them held "
            "up to each zone.",
        ],
        terms={
            "Zones with a stronger base anywhere": "Zone holds where some spot in the zone offered at least twice their base's "
                                                   "tag opportunities with no more danger.",
            "Zones with a stronger base nearby": "The same, for spots within 150 m of their base: a small move away.",
            "Their bases vs the zone": "Tag opportunities at their bases compared with a typical spot in the same zone.",
            "Surged": "Surge episodes in their games where at least one of them took surge damage.",
            "Their tags": "Exchanges where they dealt damage and took none back.",
            "Where they set up each zone": "Each zone hold: how long it lasted, where their base was, its tag opportunities and "
                                           "danger, and the best spot's tag opportunities, distance and height.",
            "Which scoring predicts real damage": "Within each zone hold, how well each scoring ranks teams' bases by the damage "
                                                  "they actually dealt (+1 perfect, 0 no link). The higher one is the better way to "
                                                  "judge a surge base.",
            "Loadouts during each hold": "The latest weapons each player held up to each zone, with rarity.",
            "Surge episodes in their games": "Each detected surge: whether they were hit, their surge score, the lowest score that "
                                             "stayed safe, and the margin between them (negative = inside the surged group).",
            "Their damage trade by height": "Their exchanges grouped by how far above or below the opponent they were.",
            "Teams in the selected matches": "Every team with its results, to identify a team at a LAN.",
        },
        charts={
            "Their base vs stronger surge bases": "One early zone of one game: red is other players rotating, grey players holding, "
                "teal the spots with the most tag opportunities, amber their base and navy the best spot with no more danger. "
                "Choose the game and zone above.",
            "Tag opportunities: their base vs the best spot nearby": "By zone, averaged over games: their base, the best spot with no "
                "more danger, and a typical spot.",
        },
        conclude=[
            "If the best spots keep sitting on the side where rotations arrive (often the side facing the previous zone or the bulk "
            "of the lobby), that's a rule they can use every game: build the surge base facing incoming rotations.",
            "If they were surged close to the safe threshold, they need to tag earlier in that zone, or take less chip damage: "
            "under net damage, damage taken counts against them.",
        ],
        limits=[
            "Tag opportunities use where other teams actually went in that game: they show what a base would have offered, not what "
            "was knowable beforehand. Look for patterns across many zones.",
            "Line of sight, cover and builds aren't in the score; natural height is.",
        ],
    ),

    "expected_points": Guide(
        question="What is any moment of a match worth in points, and which of two plans is worth more?",
        method=[
            "Every 10 seconds from zone 2 on, every living team's situation is recorded: teams left, the zone and how far through "
            "it, teammates alive, health and shield, distance outside the next zone and into the storm, distance from the centre, "
            "height rank, enemy teams within 50 m and 150 m, damage taken in the last 10 seconds, net damage this zone (dealt minus "
            "taken, ranked against the lobby: what tournament surge counts), eliminations so far, and both teams' skill (the team's "
            "Power Rankings rank and the lobby's median).",
            "The model learns what each situation turned into: placement points on the active scoring scheme (api/fnlab/scoring.json; "
            "FNCS 2026 Duos finals by default: 65 for 1st down to 2 for 25th, 0 below) plus elimination points for every elimination "
            "still to come. It's checked on matches it never saw.",
            "Skill is an input, and the baseline knows it too, so the model can't credit whatever strong teams happen to do: the gap "
            "between the model and the baseline is what the situation adds beyond who the teams are.",
            "Getting hit while rotating: the chance that a team you weren't fighting (no hits between you in the last 30 seconds) "
            "hits you in the next 10 seconds, outside the next zone against inside it, and what that costs in points.",
            "Plan comparison: two situations priced by the model, and which differences drive the gap.",
        ],
        terms={
            "Plan comparison": "Expected total points from this moment for each plan: eliminations so far plus what's still to come.",
            "Model accuracy": "How much of the variation in points still to come the model explains on matches it never saw, "
                              "against a baseline that only knows teams left, the zone and both teams' skill. The gap is what the "
                              "situation adds beyond who the teams are.",
            "Skill input": "The share of players with a Power Rankings rank. Low coverage means the model can't separate strong teams "
                           "from good situations.",
            "Scoring": "The scoring scheme every number on this page uses, from api/fnlab/scoring.json.",
            "Hit within 10 s, zones 7–8": "The chance a team you weren't fighting hits you in the next 10 seconds, rotating against holding.",
            "Situations analysed": "Living teams, every 10 seconds, across the selected matches.",
            "What each change is worth in the endgame": "The model's average change in expected points for each change, over real "
                                                         "situations in zones 6–9.",
            "Getting hit while rotating": "By zone group, rotating against holding: the chance of a hit from a new team within "
                                          "10 seconds, the damage when it happens, and its average cost in points per 10 seconds.",
        },
        charts={
            "What each change is worth in the endgame (zones 6–9)": "Each bar is a change, in expected points. Longer bars are the "
                "decisions that matter most in the endgame.",
            "Chance of finishing in the points, by teams left": "How often teams in each situation finish inside the paid places "
                "(top 25 in FNCS 2026 Duos), where placement points start. The gap between the lines is the price of being outside "
                "and damaged.",
            "Points still to come, by teams left": "Average points still to come for each situation as the lobby shrinks.",
            "Chance of being hit by a new team within 10 s": "The risk of someone you weren't fighting hitting you, rotating against "
                                                             "holding, by zone group.",
            "Chance of being surged, by surge score": "When surge triggered: how often players in each quarter of the measured surge "
                                                      "score were surged.",
            "Is the model calibrated?": "Predicted against real points still to come. Close to the 'Perfect' line means the "
                                        "model's numbers can be taken at face value.",
        },
        conclude=[
            "Use the plan comparison for real decisions from a match: describe what the team did and what it could have done.",
            "The biggest bars in 'What each change is worth' are where a team's decisions earn or lose the most points.",
            "Train it on matching lobbies: tier-1 duo decisions from tier-1 duo matches.",
        ],
        limits=[
            "Expected points are averages over many situations; a single game still turns on fights and luck.",
            "With few matches (Globals has 12), check 'Model accuracy' and calibration before trusting small differences.",
            "The model can't value situations it never saw, such as health above what was recorded.",
            "The first calculation on a selection takes up to a minute; comparing plans afterwards is instant.",
        ],
    ),

    "engine_review": Guide(
        question="If the team had played by a decision engine that only knows what a player knows live, what would it have "
                 "said at each moment, and do teams that play that way actually finish better?",
        method=[
            "What the engine knows (live only): its own position, health and shield, teammates alive, eliminations, the current "
            "and next zone and their timers, distance outside the next zone and into the storm, teams left, damage just taken, "
            "surge as the HUD shows it (team net damage above or below the cut-off), enemy teams within the perception radius (how "
            "many, how close, how far above), the natural ground under it, and both teams' Power Rankings. Never far enemies' "
            "positions or anyone else's health.",
            "A points model trained on those live inputs only (the active scoring scheme, paying down to 25th in FNCS 2026 Duos) prices "
            "every situation. It's told "
            "the directions the game fixes (more health never hurts, more storm never helps, fewer teams left never hurts a team "
            "that's alive) and learns how much each matters. Each game is priced by a model that never saw it.",
            "Every 20 seconds the engine compares four options 20 seconds ahead: hold (and heal if it's safe: no enemy within 50 m and "
            "no damage in the last 10 s; healing is maintenance, never advice on its own), rotate by the direct route, rotate by a "
            "less crowded entry (perceived enemies only), and engage a visible enemy (the measured "
            "win rate for its health; a loss means being placed now, at the points for the teams left).",
            "What the team actually did over the same 20 seconds is read from the data (held, rotated by which route, healed, "
            "engaged) and priced the same way: the difference is the points at stake.",
            "The real test: across every team in the selected matches, teams that happened to follow the engine at most key "
            "decisions against teams that didn't, within the same match and for the same team across games.",
        ],
        terms={
            "Engine knowledge": "How much of the points still to come the live-only model explains on matches it never saw, "
                                "against the full-information model. The gap is the price of the fog of war.",
            "Followed 90%+ vs under 60%": "Average points per game of teams that followed the engine at 90%+ of key decisions, "
                                          "against teams under 60%. Across teams, so stronger teams following more can explain it.",
            "Same team: following vs finishing": "The same team across games: positive means it finished better in the games it "
                                                 "followed the engine more. The result to trust, because team strength can't explain it.",
            "Decisions followed": "Key decisions (an option beat holding by half a point, or the team did something else) where the "
                                  "team did what the engine recommends, within half a point.",
            "Disagreements worth 1+ point": "Decisions where the engine's call was worth at least one expected point more.",
            "Following the engine vs results": "Team-games grouped by how often they followed the engine: average points and how "
                                               "many teams they finished ahead of.",
            "Decisions where the engine disagreed": "Each disagreement: the time and zone, what the team knew, the engine's call, "
                                                    "what they did, the points at stake, and every option's expected points.",
            "Across teams": "Following the engine against finishing, within the same match.",
            "Same team": "The same team across games: did it finish better in the games where it followed the engine more?",
            "Teams in the selected matches": "Every team with its results, to identify a team at a LAN.",
        },
        charts={
            "Points per game, by how often teams followed the engine": "Every team-game in the selection by how often it followed "
                "the engine at key decisions. A rising chart means playing the engine's way goes with more points.",
            "Points at stake by zone": "Where in the game this team's disagreements with the engine were worth the most.",
        },
        conclude=[
            "If the same team finishes better in the games it follows the engine more, the engine is worth teaching, not just a "
            "description of better teams.",
            "The biggest disagreements are the moments to review on video and to drill.",
        ],
        limits=[
            "The engine looks 20 seconds ahead with a simple movement model; it can't see builds, cover or fights in detail.",
            "Healing assumes healing items are available; materials and inventory aren't in replays.",
            "Teams that follow the engine may also be stronger teams; the same-team result is the one that rules that out.",
            "The first calculation on a large selection takes a few minutes; it's cached afterwards. Changing the perception "
            "radius recalculates.",
        ],
    ),

    "match_map": Guide(
        question="How did the game unfold for the team, and what was the reasoning behind each rotation?",
        method=[
            "Every player's path every 2 seconds, the storm as it moved, eliminations and the team's health, replayed on the map "
            "with the analysed team highlighted.",
            "At every zone: the time budget (when it appeared, when it closes, distance to its safe edge), travel time at the speed "
            "players actually move in that match, when players who arrived ahead of comparable players set off (same zone, "
            "similar distance, selected matches), a leave-by time, and when the team actually left.",
            "Other teams: those within 60 m of the straight route in or near the entry point, a less crowded entry point when one "
            "exists (up to 120 m longer), and the routes teams outside the zone must take (dashed).",
            "Zones 3–4: the surge base facing those routes, as in the Game review.",
            "Zone 1's starting circle: the replay records its size but not its centre, so the centre is estimated from the storm "
            "damage players took during zone 1's shrink (the centre whose shrinking circle best separates damaged players outside "
            "from undamaged players inside). The status line says so while zone 1 closes.",
            "The real Fortnite map image can sit underneath after a calibration: choose any named places, landmarks or zone "
            "centres from a real game, click each on the zoomable image, and the fit updates live with every point's error in "
            "metres. At least 3 points, spread out; 6 or more is better.",
        ],
        terms={
            "Rotation plan": "Each zone: when it appeared and closed, distance to its safe edge, travel time, leave-by time, when "
                             "they left, teams on their route, a better entry point, and the surge base.",
            "Rotations left late": "Zones where they left more than 10 seconds after the leave-by time.",
            "Teams in the selected matches": "Every team with its results, to identify a team at a LAN.",
        },
        charts={
            "Match map": "Play the game, drag the timeline, or click a zone number. Scroll to zoom, drag to pan, and hover a dot for "
                         "the player. The panel shows the rotation plan for the current zone; on the map, dashed red lines are the "
                         "routes teams outside the zone must take, the dot is your entry point, the ring a less crowded entry, and the "
                         "diamond the surge base.",
        },
        conclude=[
            "Zones where they left well after the leave-by time, with teams on their route, are the rotations to review on video.",
            "If a less crowded entry keeps appearing on the same side, plan rotations around that side.",
        ],
        limits=[
            "Travel time uses running speed; mobility items, vehicles and launch pads make real rotations faster.",
            "Routes in are straight lines; real routes bend around terrain, builds and fights.",
            "The map image is only as accurate as the three clicks: zoom the browser in for precise clicks.",
        ],
    ),

    "review": Guide(
        question="For one team in one game: what did they do in each stage, what would the system have recommended knowing only "
                 "what they knew then, why, and what was it worth?",
        method=[
            "Early game: the drop (contested or not), time to two rare-or-better weapons against top-10 finishers, and where they "
            "were when zone 2 appeared against the spot the zone forecast favoured. The forecast never saw this game.",
            "Mid game, zones 3–6: rotation timing against comparable teams. In shrinking zones (3–4), a recommended surge base facing "
            "the routes teams outside the zone must take, worked out from where those teams were when the zone appeared (not where "
            "they later went), at 20–80 m, from natural height where possible, with no enemy within 30 m. In 50/50 zones (5–6), "
            "the inner half of the zone.",
            "Endgame, zone 7 on: inside or outside, distance from the centre, height rank, teammates' distance apart, and how the "
            "game ended, with evidence from the selected matches on staying together and pushing up onto a higher team.",
            "Worth: the expected-points model's value of their real situation against the recommended one, at that moment, priced "
            "by a model that never saw this game, and only shown when the model clearly beats its baseline.",
        ],
        terms={
            "Result": "Placement, eliminations and points (FNCS scoring) in this game.",
            "Biggest single decision": "The largest gain in expected points from following one recommendation.",
            "Review": "The whole game, stage by stage: early game (drop, loot, first rotation), mid game (zones 3–6) and endgame "
                      "(zone 7 on). For each moment: what they did, the recommendation, the evidence, and its worth in expected points.",
            "Evidence: staying together": "Duos in zones 7+ by how far apart the teammates were, and how they finished against the "
                                          "teams alive then.",
            "Evidence: pushing a higher team": "Endgame fights by the attacker's height against the defender's, and how often the "
                                               "attacker won.",
            "Teams in the selected matches": "Every team with its results, to identify a team at a LAN.",
        },
        charts={
            "Early game map": "Their path to zone 2, where they were when it appeared (amber), and the forecast's recommended spot (navy).",
            "Mid game map": "Their path through zones 3–6, the teams outside the zone when it appeared (red), and the recommended surge base.",
            "Endgame map": "Their path from zone 7 on, over the late zones.",
        },
        conclude=[
            "Rows with the largest Worth are the decisions to review on video first.",
            "Recommendations that repeat across games (for example, always outside when zone 5 appears) are habits; one-offs are moments.",
        ],
        limits=[
            "Worth values stand alone: one changed decision changes everything after it, so they don't add up.",
            "Surge bases use predicted routes (straight lines in); real routes bend around terrain and fights.",
            "The evidence and the models come from the matches selected in the left panel: select lobbies that match the team.",
        ],
    ),

    "audit": Guide(
        question="For one team: where did their points come from, game by game, and which risks cost them?",
        method=[
            "Type the players' names (a name matches if it contains what you type; separate players with commas) and select the "
            "tournament's matches in the left panel, for example with the event window filter.",
            "Games are numbered by when each replay started. Points use the active scoring scheme in api/fnlab/scoring.json (FNCS "
            "2026 Duos finals by default: 65, 56, 52, 48... down to 2 for 25th) plus elimination points.",
            "For every zone: whether the team was already inside the next zone when it appeared, whether they rotated behind "
            "comparable teams, whether they were at the edge of the zone in zones 5+, storm damage, and height rank in zones 6+.",
            "How each game ended: the zone, the team that eliminated them and where it finished, health going into that last "
            "fight, whether a third team joined, and whether they were outside the zone.",
        ],
        terms={
            "Teams in the selected matches": "Every team, followed across the selected games by its accounts, ranked by computed "
                                             "points: games, which games it won, average placement and usual drop. At a LAN, where "
                                             "players use event accounts, this is how to find a team: by its results.",
            "Games found": "Matches in the selection where the team was found, and the account names it was found as.",
            "Points (computed)": "Placement points plus elimination points on the active scoring scheme. Compare with the official "
                                 "total to check the data.",
            "Average placement": "The team's average placement, and its wins.",
            "Game by game": "One row per game: placement, eliminations, points, drop, zones where they were already inside the next "
                            "zone, rotations where they fell behind, how the game ended, and the risks they took.",
            "Compared with the top teams": "The team's averages against the teams that finished top 5 in each game, and everyone.",
        },
        charts={
            "Points per game": "Placement points and elimination points for every game.",
            "Their path vs the winner's": "The team's path and the game winner's, over that game's zones. Choose the game above the takeaway.",
            "Risks taken most often": "How often each kind of risk appears across the tournament.",
        },
        conclude=[
            "Look for risks that appear in the games where they lost the most points: those are the habits worth changing.",
            "Compare with the top teams: a measure where this team differs most from the top 5 is the likeliest edge to gain.",
        ],
        limits=[
            "Risks aren't mistakes on their own; winners take risks too. The point is which ones cost points over a tournament.",
            "LAN accounts can have event names; check the names it was found as.",
        ],
    ),

    "match_room": Guide(
        question="In this game, what did the team have at each moment, what did they decide, and what was each option worth?",
        method=[
            "Type the team's names and pick a game. The replay plays in the centre: every player's path, the storm and the next zone, "
            "eliminations, with the team highlighted. Press play, drag the timeline, or click a zone number or a red decision marker.",
            "Beside the map, each player at the current moment: shield and health, knocked or eliminated, the weapons seen in their "
            "hand (rarity colours; the one in hand is outlined), and what they've picked up. For the team: net damage since the zone "
            "appeared (dealt minus taken, between players) against the line that kept teams safe in that zone across the selected "
            "matches, surge checks as they happen, and build pieces placed.",
            "Below the map, the decision at this moment: what the team did, the best option by expected points, every option's value, "
            "and for a fight its parts: the chance to win at their health, the points if they win (with the elimination) and if they "
            "lose (placed now, at the points for the teams left). That's the trade behind forcing a refresh: a coin flip for four "
            "points against the placement points of rotating safely.",
            "Decisions come from the engine on the Game review page: it knows only what a player knows live, and each game is priced by "
            "a points model that never saw it.",
        ],
        terms={
            "Decisions worth 1+ point": "Decision points where the best option beat what the team did by at least one expected point.",
            "Match room": "The replay with the team's HUD and decisions.",
        },
        conclude=[
            "Start with 'Biggest calls this game': each jumps the replay to the moment, so you can watch what happened before and after.",
            "A fight's chance to win comes from fights in the selected matches at that health; select matching lobbies for it to mean much.",
        ],
        limits=[
            "Material and item counts aren't in the replay data the pipeline reads, so the HUD can't show a player's materials; the "
            "engine can't yet weigh them in a refresh. The extractor's season survey shows whether the replays carry the inventory.",
            "Weapons are the ones seen in hand, not the full inventory; build pieces are per team, not per player.",
            "The engine looks 20 seconds ahead and judges fights by health only, not numbers, height or a third team arriving.",
        ],
    ),

    "storm_hunt": Guide(
        question="When your team is outside the next zone as it appears, does it pay to go in first and look for surge inside, or to "
                 "stay back in or behind the storm and tag the teams rotating late?",
        method=[
            "Every team outside the next zone by 50 m+ when it appears (zones 2–7 with a wait of 20 s+ before the storm moves) is "
            "classed by what it did halfway through the wait: inside the zone or half the distance covered = rotated first; under a "
            "fifth covered = stayed back (in the storm if it took storm damage that zone, else behind it). In between is left out.",
            "The class comes from movement, not from who got in, so teams sprayed and eliminated on the way in still count as rotating "
            "first. Judging by who arrived would hide exactly that cost.",
            "Like for like: teams are only compared with others in the same match and zone, the same distance band (under or over "
            "300 m) and the same surge standing (net damage over the previous zone, at or below the lobby's median or above it). "
            "Each group with both approaches gives one difference; the differences are averaged and tested.",
            "Outcomes over the zone, from appearing to closing: net damage gained, damage taken from players, storm damage, surged at "
            "any check, eliminated, safe from surge and alive, and the team's placement points in the end.",
            "Getting sprayed: damage taken from players during the wait, by how many enemy teams were already set up inside the zone "
            "within 150 m of the team's way in (the nearest point of the edge) when it appeared.",
        ],
        terms={
            "Team-zones compared": "Teams outside the next zone that rotated first or stayed back, each zone counted once.",
            "Rotated first / stayed back": "How many team-zones took each approach.",
            "Staying back, like for like": "Placement points, staying back minus rotating first, within comparable groups.",
            "Like for like": "Each outcome: staying back against rotating first within groups of the same match, zone, distance and "
                             "surge standing.",
            "Stay back or rotate first: like for like": "Each outcome's average for both approaches, the like-for-like difference, "
                                                        "how many groups it rests on, and the verdict.",
            "Who it pays off for": "The same comparison split by surge standing and distance: it can pay for teams low on surge or "
                                   "far out and not for others.",
            "Staying back: in the storm or behind it": "Teams that stayed back, split by whether they sat in the storm.",
            "Getting sprayed on the way in": "Damage taken during the wait by how many teams were set up at the team's way in.",
            "p": "The chance of a difference this large if both approaches were really equal. Small means a real difference.",
        },
        charts={
            "Safe from surge and alive through the zone": "For each surge standing, the share of teams neither surged nor eliminated, "
                                                          "rotating first against staying back (raw, not like for like).",
            "Damage taken while rotating first, by teams set up at your way in": "If this rises with the number of teams set up, "
                                                                                 "rotating first into a lined-up edge gets you sprayed.",
        },
        conclude=[
            "Trust the like-for-like column over the raw averages: stronger teams may choose one approach more often.",
            "Read 'Who it pays off for' before making it a rule: staying back may pay for teams below the surge line and cost the rest.",
            "If the spray table rises steeply, the decision engine already prices it: it counts the teams it can see set up ahead.",
        ],
        limits=[
            "Needs in-match health and damage, and zones where surge was live; early qualifier rounds may have no surge.",
            "Cover, builds and line of sight aren't in the data: 'set up at your way in' counts teams near it, not teams with a shot.",
            "Groups need both approaches in the same match and zone; with few matches, the comparison rests on few groups.",
        ],
    ),

    "gameplan": Guide(
        question="Before an event, what should this team do: where to land, when to rotate, what surge score to hold, which habits to "
                 "fix, and what the scoring rewards?",
        method=[
            "Select the matches to learn from in the left panel (for example, this season's cash cups and FNCS rounds in strong "
            "lobbies), type the team's names, and set the event's first day. Matches before that day are the history; matches on or "
            "after it are the event. Nothing from the event is used to build the plan.",
            "Drop: the team's usual spot (a named place, or the area they usually land when the data has no names), how often another "
            "team lands there, split by how close the bus line passes, the players who land there regularly and how off-spawn fights "
            "with them went, and backup spots that are less contested and score better.",
            "Zones 1–2: how often the spot is inside zones 1 and 2, and when zone 2 misses it, how far away it is and whether it pulls "
            "toward the map centre, sideways or away.",
            "Rotations: each zone's wait before the storm moves, the rotation speed players actually managed (including stops and "
            "building), and from that the latest time to leave from 100–500 m out to be inside before the storm moves. Checked against "
            "storm damage taken by players who left later.",
            "Surge: the rule the Surge study measured (team net damage since October 2025) and, for each zone's first surge check, the "
            "score that kept teams safe in half and in 80% of games.",
            "Habits: Playbook rules that the same players did better with when they followed them, ranked by how often this team "
            "broke them, weighted by what they're worth.",
            "Points: the event's scoring as an exchange rate between places and eliminations, and what a zero-point game costs this team.",
            "Check: with event matches in the selection, each event game against what the plan said.",
        ],
        terms={
            "Team": "The players found, and in how many history matches.",
            "Drop spot": "The spot the plan is built around: typed, or where the team landed most.",
            "History": "The matches the plan is built from: the selection before the event's first day.",
            "Scoring": "The scoring scheme for the points block, from api/fnlab/scoring.json.",
            "Plan": "Each block's one-line call and the tables behind it.",
            "Confidence": "Solid: 30+ games. Thin: 10–29. Too few: under 10, and the number is left out.",
            "Drop: how contested your spot is": "By how close the bus line passes: games, other teams landing there, and how often "
                                               "one, or two or more, other teams were there.",
            "Drop: your record there": "The team's own games at the spot, by bus route: how often they were contested, went out "
                                       "off spawn, and their points per game.",
            "Drop: who lands there": "Players who landed at the spot in 3+ games, and how off-spawns went when the team was there too.",
            "Drop: backup spots": "Named places less contested than the team's spot, best points first.",
            "Zones 1–2 from your spot": "How often the spot is inside zones 1 and 2, and where zone 2 is when it isn't.",
            "Rotations: latest time to leave": "Seconds after each zone appears to leave from each distance, to be inside before the "
                                               "storm moves; and how often players who left later took storm damage.",
            "Surge: the line to stay above": "For each zone's first surge check: players alive, the score that kept teams safe in half "
                                             "and in 80% of games, and how often this team was surged.",
            "Habits: what to fix first": "Playbook rules, what following them was worth for the same players, and how often this team "
                                         "broke them.",
            "Points: places against eliminations": "How much one place up is worth in each range, and one elimination in places.",
            "Check: what the plan said against the event": "The plan's numbers against what happened at the event.",
            "Check: the event, game by game": "Each event game: landed at the spot, contested, spot in zones 1 and 2, surges, rotations "
                                              "left after the latest time, and points.",
        },
        conclude=[
            "Read the calls first: each one is the block's decision in one line. The tables are the evidence; 'Too few' means the plan "
            "has nothing reliable to say there yet.",
            "Backtest before trusting it: set the first day of a past event the team played, include that event's matches in the "
            "selection, and read 'How the plan held up'.",
            "Early in a season, most blocks will be thin: add the previous season's matches only for blocks that don't depend on the "
            "map (surge, points), and expect the drop and zone blocks to firm up after a couple of weeks of cash cups.",
        ],
        limits=[
            "The plan describes what happened to teams in similar spots and moments; fights, comms and mechanics aren't in it.",
            "At a LAN, players use event accounts: type both their online and event names so they're found in the history and the event.",
            "Drop spots without named places are circles around where the team usually lands, so nearby spots can merge.",
            "Rotation timing assumes the measured average speed; a team with mobility items can leave later.",
        ],
    ),

    "surge_study": Guide(
        question="How does surge work in these matches, and what actually works against it: sitting in zone hunting, sitting "
                 "passively, rotating early and building a surge base, or rotating late, and at what base height?",
        method=[
            "Surge is detected as health lost inside the zone with no player hitting them: 3+ players in the same second, or one "
            "player's drops repeating every 3–7 s (a surge on a single duo or a lone player).",
            "Which damage surge counts is measured: for each surge, players are ranked by every candidate rule, damage dealt or net "
            "damage (dealt minus taken), per player or per team, over several windows (whole match, since the zone appeared, since "
            "the previous surge check, the last 60, 120 and 180 seconds). The rule that best separates surged from safe players is "
            "used everywhere: the Surge page, the team audit and the points model. Epic's announced rule since October 2025 (team "
            "net damage) wins near-ties, so a small sample can't override it.",
            "Every player alive when a surge zone appeared is followed to its first surge check and classed by what they did: held "
            "and hunted (moved under 80 m, dealt 25+ damage), held passively (moved under 80 m, dealt less), rotated early then held "
            "(inside the new zone at least 20 s before the check), or rotated late (still moving at the check).",
            "Base height is their height above the natural ground at the check (the ground is mapped from where players stood "
            "before the storm moved), so it measures how tall they had built.",
            "Success is measured on what surge is about: safe from that surge, eliminated before the next zone, and both together "
            "(safe and alive), plus damage gathered per minute. The comparisons only use players at risk when the zone appeared, "
            "within the same zone of the same match.",
        ],
        terms={
            "Best approach (safe and alive)": "The approach and base height with the highest share of players safe from surge and "
                                              "still alive at the next zone (at least 10 players).",
            "Damage surge counts": "The rule (measure, team or player, and window) that best explains who gets surged.",
            "Teammates surged together": "Of the teams with two or more players alive at a surge, the share where all of them were "
                                         "surged or none were. Near 100% confirms surge picks teams, not players.",
            "Typical cut-off": "The median surge score, on that rule, that kept players safe.",
            "Surge episodes": "Detected surges across the selected matches.",
            "Which damage surge counts": "Every candidate rule (measure, team or player, window), best first, and how well it separates "
                                         "surged from safe players (1.00 = perfectly, 0.50 = no better than chance).",
            "How surge works, by zone": "For each zone: how often surge hit, how long after the zone appeared, relative to the shrink "
                                        "starting, players alive and hit, damage per tick and per player hit, and the cut-off.",
            "Approach and base height: what worked": "Every approach at every base height: players, safe from surge, eliminated "
                                                     "before the next zone, safe and alive, and damage per minute.",
            "Who captures the most surge damage: the top 10% vs everyone": "Where the players who gathered the most damage before "
                "surge checks stood (inside the new zone, at its edge), how tall they had built, which approach they used and how "
                "they fared, against everyone and against top-10 finishers.",
            "At risk when the zone appeared": "Approaches and base heights compared within the same zone of the same match, among "
                                              "players who started at or below the cut-off.",
            "p": P_VALUE,
        },
        charts={
            "Safe from surge and still alive, by approach and surge-base height": "For each approach, one bar per base height: the "
                "share of players who weren't surged and were still alive when the next zone appeared. The tallest bar is what worked.",
            "Where surge damage is captured: position in the new zone": "Damage gathered per minute before the check, by where players "
                "stood relative to the new zone, on the ground against built up.",
            "What players do before a surge check: top-10 finishers vs the rest": "How top-10 finishers split between the four "
                                                                                  "approaches, against everyone else.",
            "Everyone at the surge check": "Every player at the moment of one zone's first surge check: red were surged, grey stayed "
                                           "safe, teal stayed safe with the most damage. Choose the game and zone above.",
            "Damage needed to stay safe from surge, by zone": "The median cut-off in each zone, on the measured rule.",
        },
        conclude=[
            "If rotating early and building a tall base beats sitting and hunting on 'safe and alive', the default surge plan is to "
            "get into the new zone early and build up; if not, sitting in zone hunting is the right call.",
            "The top-10% profile shows where surge damage is actually captured: copy the position and height, not just the approach.",
        ],
        limits=[
            "Players who hunt or build tall may be stronger players; the within-zone comparisons and the top-10 finisher column help, "
            "but read differences of a few points with caution.",
            "Base height uses the natural ground under the player; standing on a natural ledge or roof can count as built.",
            "Needs in-match health and damage data, and matches where surge triggers (often later rounds and finals).",
        ],
    ),

    "playbook": Guide(
        question="Did players who happened to play the way the system recommends do better than their lobby?",
        method=[
            "Each rule is judged only on what the system would have advised at that moment, never on what happened next: "
            "positioned for the next zone (zones 2–4) uses the zone forecast from a model trained without that match.",
            "The rules: an uncontested drop; in zones 2–4, standing where the forecast gave at least an 80% chance of already "
            "being inside the next zone, and rotating ahead of players starting a similar distance out; in zones 5+, holding "
            "the inner half of the zone and rotating ahead (to zone 8); in zones 6+, holding mid or high ground.",
            "Each player is compared with rivals alive at the same moment in the same match: the share of them they finished "
            "ahead of. Surviving longer is never credited to a rule.",
            "'Same player' compares each player with themselves, across matches where they followed a rule and matches where "
            "they didn't. That removes skill, so it's the strongest evidence.",
            "Two playbook scores combine the rules: the early game (judged among players alive at zone 5) and the late game "
            "(among players alive at zone 9).",
        ],
        terms={
            "Early playbook": "Players alive at zone 5 who followed most early rules, against those who followed few: the share "
                              "of rivals alive then that each group finished ahead of.",
            "Late playbook": "The same for the late-game rules, among players alive at zone 9.",
            "Rule checks": "Moments where a rule could be followed, across all players and matches.",
            "Each rule's value": "For each rule: how often it was followed, how players finished against rivals alive at the same "
                                 "moment when they followed it and when they didn't, the same-player difference (in points of "
                                 "finishing rank), and whether the difference is real.",
            "Players who followed the system most closely": "The player-matches with the highest share of rules followed (at "
                                                            "least 8 checks), with placement and Power Rankings rank.",
            "Each rule": "Each rule compared within the same match and moment, one summary per match.",
            "Same player": "The same players compared with themselves: matches where they followed a rule against matches where "
                           "they didn't.",
            "Playbook score": "Whether following more rules goes with finishing better.",
            "p": P_VALUE,
        },
        charts={
            "Following the playbook vs finishing": "Players grouped by how much of the playbook they followed: how many rivals "
                "alive then they finished ahead of. A rising line means following more of the system goes with finishing better.",
            "What each rule is worth": "For each rule, players who followed it against players who didn't. The gap between the "
                                       "bars is the rule's value. Further right is better; 50% is average.",
            "A follower and a non-follower in the same match": "The paths of the player who followed the most rules and the one "
                "who followed the fewest, over that match's zones. Choose another match above the takeaway.",
        },
        conclude=[
            "Rules with strong evidence in both 'Each rule' and 'Same player' are the ones to teach: they help the same player, "
            "not just players who happen to be better.",
            "The examples table and map are the cases to show a team: real players doing what the system recommends.",
        ],
        limits=[
            "Associations, not proof: players who follow a rule may differ in ways not measured. The same-player and skill-band "
            "checks narrow that a lot.",
            "The zone-positioning rule needs at least 10 matches from the season for its forecast.",
            "The first calculation on a large selection can take a couple of minutes; it's cached afterwards.",
        ],
    ),

    "decides": Guide(
        question="In tier-1 lobbies, with so many players alive late and loot refreshing, which parts of the game actually decide "
                 "the finish?",
        method=[
            "Matches are split into the strongest third by lobby strength and the rest, so tier-1 lobbies can be compared with the others.",
            "How crowded: players still alive when each zone appears.",
            "Loot convergence: within each match, the players who looted best and worst in their first 3 minutes, compared on the best "
            "weapon rarity they hold in the 90 seconds before each later zone appears.",
            "What decides: among players alive when zone 6 appears, how much of their finish (against the others alive then) each group "
            "of factors explains: skill (Power Rankings), the early game (contested drop, chests, early weapon rarity), the mid game "
            "(falling behind on rotations, storm damage) and endgame position when zone 6 appears (distance outside it, distance "
            "from the centre, height rank, health and shield). Measured on matches the model never saw.",
        ],
        terms={
            "Alive at zone 6": "Median players still alive when zone 6 appears, for each group of lobbies.",
            "Explained by endgame position": "Share of the finish, among players alive at zone 6, explained by position when zone 6 "
                                             "appears, on matches the model never saw.",
            "Explained by the early game": "The same for the drop and early loot.",
            "Explained by skill": "The same for Power Rankings.",
            "Loot gap closes by": "The zone by which players who looted worst early hold about the same weapon quality as those "
                                  "who looted best.",
            "Each factor among players alive at zone 6": "Each factor's effect on the finish: points of finishing rank for one "
                                                         "standard step more of it, with the other factors held equal.",
            "Skill": "Power Rankings.",
            "Early game": "Drop and early loot.",
            "Mid game": "Rotations and storm damage in zones 2–5.",
            "Endgame position": "Where a player is, and in what state, when zone 6 appears.",
            "p": P_VALUE,
        },
        charts={
            "What decides the finish, among players alive at zone 6": "How much of the finish each part of the game explains, for the "
                "strongest lobbies and the rest. Longer bars matter more. Placement is noisy in any battle royale, so compare the "
                "bars with each other rather than with 100%.",
            "Players still alive when each zone appears": "How crowded each zone is: how many players are still in the game.",
            "Does early loot even out?": "Players who looted best and worst early, by the weapon quality they hold at each zone. "
                                         "Where the lines meet, early loot no longer makes a difference.",
        },
        conclude=[
            "If endgame position explains far more than the early game, plan and practise zones 4–6 above early looting.",
            "If the loot lines meet by zone 3 or 4, a fast, safe drop with average loot is enough; spend the time on rotation.",
            "If skill explains much more than any decision, the edge from the system is smaller in that lobby group, but every "
            "point it adds still counts over a tournament.",
        ],
        limits=[
            "Materials aren't in replays, so they can't be included in the endgame state.",
            "Associations, not proof: strong players may both choose good positions and win the endgame.",
            "Needs at least 10 matches with players alive at zone 6; loot convergence needs in-match data.",
        ],
    ),

    "zone_forecast": Guide(
        question="Where will the next zone go, where will the game end, and how far can those forecasts be trusted?",
        method=[
            "Playable ground is mapped from where players stood before the storm first moves (drop and loot), in 25 m "
            "squares, so it isn't shaped by where zones went.",
            "For every zone change, the game's allowed alternatives are generated: a shrinking zone anywhere it fits inside the "
            "current one; 50/50, shifted and moving zones at their fixed distance in any direction.",
            "Models of increasing richness are compared: one zone back (where the current zone sits, the previous pull, land "
            "and height), the whole zone history (the pull two zones back, the drift since zone 1, returning toward zone 1), "
            "and map memory (where this zone number has landed in other matches this season).",
            "Every model is scored on matches it never saw. A richer model is kept only if it does better there. A shuffle "
            "control and a newest-matches test guard against patterns that wouldn't hold up.",
            "The endgame forecast predicts the final zone directly from each earlier zone, compared with what the game's fixed "
            "move distances alone imply.",
        ],
        terms={
            "Next zone forecast": "How often the real next zone landed in the forecast's most likely quarter of the possible "
                                  "area, in matches it never saw. 25% is the game's rules alone.",
            "On the newest matches": "The chosen model trained on older matches only and tested on the newest 20%. The most "
                                     "honest estimate for matches that haven't happened yet.",
            "Endgame forecast": "How often the real final zone landed in the forecast's most likely quarter, forecast from earlier zones.",
            "Endgame from the rules alone": "The same using only what the fixed move distances imply. The forecast's real value "
                                            "is the gap between these two.",
            "Best-guess distance to the final zone": "Median distance from the forecast's single best spot to the real final zone.",
            "Repeated zone positions": "Zone positions that appear identically in more than one match: reused zone sets.",
            "Forecast models compared": "Each model's score on matches it never saw. 'Gain over the rules' is how much better "
                                        "than the game's rules alone it pinpoints the next zone (0 = no better; each +1 "
                                        "halves the uncertainty). The shuffle control should fall back toward 'One zone back'.",
            "Rules: real zones vs random placement": "For each zone type and measure: real zones against the game-allowed "
                                                     "alternatives, and whether the difference is real.",
            "The chosen forecast beats the game's rules": "Whether the chosen model pinpoints next zones better than the rules alone.",
            "Zone history adds to one zone back": "Whether earlier zones carry information beyond the previous zone.",
            "Zone history and the map improve the endgame forecast": "Whether the endgame forecast beats the fixed move distances alone.",
            "Forecast": "The next-zone forecast, on matches it never saw.",
            "Endgame": "The final-zone forecast, on matches it never saw.",
            "Shrinking": "Rules for zones fully inside the current one.",
            "50/50": "Rules for zones partly overlapping the current one.",
            "Shifted": "Rules for zones that move fully outside after a wait.",
            "Moving": "Rules for zones that keep moving with no wait.",
            "p": P_VALUE,
        },
        charts={
            "Next zone forecast": "Standing in the current zone (light circle) of a match the model never saw: possible centres "
                "for the next zone, dark teal for the most likely quarter, light teal the next quarter, grey the rest. The amber "
                "dot is where it really went. Choose the match and zone above the takeaway.",
            "Endgame forecast": "From the same moment: where the final zone is most likely to be. The amber dot is where the "
                                "game really ended.",
            "Endgame forecast accuracy, by the zone you're in": "How well the final zone is forecast from each zone, against the "
                "fixed move distances alone. The zone where the two bars separate is when the endgame becomes readable.",
            "Next zone forecast accuracy by zone": "For each zone, how often the forecast's most likely quarter held the real next zone.",
            "Where zones land on the playable map": "Grey is playable ground; coloured dots are real zone centres by zone type.",
        },
        conclude=[
            "Trust 'On the newest matches' most: it's how the forecast performs on matches it couldn't have learned from.",
            "The zone at which the endgame forecast clearly beats the rules alone is the earliest point to commit to an endgame side.",
            "If 'Whole zone history' is chosen and the shuffle control falls back, earlier zones genuinely predict later ones.",
        ],
        limits=[
            "One season at a time, and at least 10 matches; patterns across whole zone sequences need many matches.",
            "Playable ground is mapped from player positions, so rarely visited land can be missed and swimmable water counts.",
            "The first calculation on a large selection takes about a minute; after that, switching matches and zones is instant.",
        ],
    ),

    "zone_check": Guide(
        question="Are the zones in the data right?",
        method=[
            "Continuity: each zone's next circle must be exactly the following zone's circle, and shrinks must run in order.",
            "Storm damage: the storm is rebuilt second by second (the current zone while waiting, then moving and shrinking "
            "to the next one). Health lost with no player hitting that player should happen only outside it, and players "
            "clearly outside it should take damage. This is an independent test: if a centre, a size or a timing were "
            "wrong, the two would disagree.",
            "Endgame: when the final zone finishes closing, the winner should be at it. Matches often continue in the storm "
            "afterwards, so the winner's very last position isn't used.",
        ],
        terms={
            "Matches checked": "Matches with zones in the selection.",
            "Zone-to-zone continuity": "Zone transitions where the next circle matches the following zone's circle within 1 m.",
            "Storm damage where our storm says outside": "Of all health lost with no player hit (zone 2 on), the share taken "
                                                         "outside the rebuilt storm. Close to 100% means the zones are right.",
            "Clearly outside and damaged": "Moments when a player was more than 5 m outside the rebuilt storm and then took "
                                           "storm damage within 2.5 s. Close to 100% is expected.",
            "Clearly inside but damaged": "Moments when a player was more than 5 m inside the rebuilt storm but still lost "
                                          "health with no player hit. Should be near zero: fall damage, and surge.",
            "Winner at the final zone": "Matches where the winner was within 25 m of the final zone's edge when it closed.",
            "Zone timeline": "For the match on the map: each zone's centre, radius, wait before it, when its shrink starts "
                             "and how long the shrink takes.",
            "Every match checked": "Each match's checks.",
            "Matches that need a look": "Matches where a check failed. Compare them with Fortnite's own replay viewer.",
        },
        charts={
            "Zone replay map": "Every zone of one match drawn to scale (light teal = early zones, navy = late), with named "
                               "places and the spots where players took storm damage. With correct zones, the damage spots sit "
                               "just outside the circles. Pick another match above the takeaway.",
            "Endgame zones close-up": "Zones 5 and later, zoomed in so the small endgame circles and their labels are readable. "
                                      "Storm-damage spots (amber) should sit outside the circles.",
            "Storm damage taken outside our storm, by zone": "For each zone, the share of storm damage taken outside the "
                                                             "rebuilt storm. Bars near 100% mean that zone's data is right.",
            "Where storm damage happened, relative to our storm edge": "Distance from the rebuilt storm edge for every storm "
                "damage tick. Almost everything should sit to the right of the dashed line (outside).",
        },
        conclude=[
            "If the agreement stays near 100% across your matches, the zone positions, sizes and timings can be trusted.",
            "For any flagged match, open it in Fortnite's replay viewer and compare a couple of zones by eye.",
        ],
        limits=[
            "Zone 1's starting circle isn't recorded, so storm checks start at zone 2.",
            "The storm check needs in-match health data (matches processed since the in-match update).",
        ],
    ),

    "height": Guide(
        question="Does being higher than your opponents win fights and games, and from which storm phase does it start to matter?",
        method=[
            "Height is always relative. Raw elevation mostly reflects terrain, so it's compared with nearby opponents.",
            "Fights: for each knock or elimination, the winner's height at that moment is compared with the victim's. "
            "Fights where the height gap is under 3 m are left out; for the rest, the question is how "
            "often the higher player won. 50% means height made no difference.",
            "Standing: when each storm starts closing, the teams in that match are ranked by average height into "
            "thirds (low, mid and high ground) and their final placements compared.",
        ],
        terms={
            "Higher player wins": "Share of fights with a clear height gap won by the higher player, tested per match "
                                  "against 50% (t-test of match shares).",
            "Fights, per match": "One win share per match: the main claim about fights.",
            "Fights by zone": "Binomial test against 50% for fights within each phase. Shows when height starts to "
                               "decide fights.",
            "Higher teams finish better": "Mean Spearman rho between a team's height rank and its placement. "
                                          "Positive means higher teams finish better.",
            "Standing, per match": "One correlation per match: the main claim about standing.",
            "Standing by zone": "The same correlation within each phase. Shows when holding height starts to matter.",
            "Fights matched": "Knocks and eliminations where the winner's position at that moment is known.",
            "Clear height gap": "Fights where the height difference is more than 3 m.",
            "Median height gap": "Typical height difference between winner and victim, over all matched fights.",
            "Team snapshots": "One per team per phase, counting teams with a living player.",
            "p": P_VALUE,
        },
        charts={
            "Fights won by the higher player, by zone": "Bars above the dashed 50% line mean the higher player won "
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
