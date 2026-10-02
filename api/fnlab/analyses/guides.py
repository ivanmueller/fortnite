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
        question="When does competitive storm surge trigger, who does it hit, and how much damage keeps a player safe?",
        method=[
            "Replays don't record surge directly. It appears as 3 or more players inside the safe zone losing health in "
            "the same second with no player hitting them; ticks within 10 s form one episode.",
            "For each episode: players alive, players hit, damage per tick, and the damage each alive player had dealt to "
            "other players before it started.",
            "Surge targets the lowest damage-dealers, so comparing surged and safe players' damage estimates the "
            "threshold to stay safe.",
        ],
        terms={
            "Surge hits players who dealt less damage": "Per episode, a rank test of damage dealt by surged vs safe "
                                                        "players, combined across episodes.",
            "Surged players finish worse": "Per match, surged players' average placement minus safe players'.",
            "Matches with in-match data": "Matches that include health and damage.",
            "Surge episodes": "Surge episodes detected.",
            "Matches with surge": "Matches with at least one episode.",
            "Players alive at surge": "Median players alive when an episode starts.",
            "Players hit per episode": "Median players surged per episode.",
            "Damage per tick": "Median health or shield lost per surge tick.",
            "Surge by zone": "Per phase: episodes, players alive and hit, damage per tick, and the damage dealt by the "
                              "highest-damage surged player and the lowest-damage safe player.",
            "Per episode": "One test per surge episode, combined.",
            "Per match": "One summary per match.",
            "p": P_VALUE,
        },
        charts={
            "Surge episodes by zone": "Which phases trigger surge.",
            "Chance of being surged by damage dealt beforehand": "How the risk falls as a player deals more damage.",
        },
        conclude=[
            "The 'Most damage dealt by a surged player' column is a practical target: above it, nobody was surged in "
            "that phase.",
            "Surge is round-specific: early qualifier rounds may never trigger it. Collect later rounds with option T.",
        ],
        limits=[
            "Detection is inferred from damage patterns; very small surges (fewer than 3 players) aren't caught.",
            "Damage dealt counts damage to players only.",
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
