// fn-extract: turns Fortnite .replay files into compact "research JSON" files.
// Usage: dotnet run -c Release -- <file-or-folder> <output-folder> [--sample-sec 1.0] [--mode normal|full] [--overwrite]
//        ... --survey   list every data type and field the replay contains (writes <id>.survey.json)
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;
using FortniteReplayReader;
using FortniteReplayReader.Models;
using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

var argv = args.ToList();
if (argv.Count < 2 || argv.Contains("-h") || argv.Contains("--help"))
{
    Console.WriteLine("usage: fn-extract <file-or-folder> <output-folder> [--sample-sec 1.0] [--mode normal|full] [--overwrite]");
    return 1;
}

string input = argv[0], outDir = argv[1];
double sampleSec = 1.0;
var mode = ParseMode.Normal;
bool overwrite = argv.Contains("--overwrite");
bool survey = argv.Contains("--survey");
int si = argv.IndexOf("--sample-sec");
if (si >= 0 && si + 1 < argv.Count) sampleSec = double.Parse(argv[si + 1], System.Globalization.CultureInfo.InvariantCulture);
int mi = argv.IndexOf("--mode");
if (mi >= 0 && mi + 1 < argv.Count && argv[mi + 1].Equals("full", StringComparison.OrdinalIgnoreCase)) mode = ParseMode.Full;

Directory.CreateDirectory(outDir);
var files = Directory.Exists(input)
    ? Directory.EnumerateFiles(input, "*.replay").OrderBy(f => f).ToList()
    : new List<string> { input };

var jsonOpts = new JsonSerializerOptions
{
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
};

int ok = 0, failed = 0;
foreach (var file in files)
{
    var matchId = Path.GetFileNameWithoutExtension(file);
    var outPath = Path.Combine(outDir, matchId + (survey ? ".survey.json" : ".json"));
    if (File.Exists(outPath) && !overwrite) { Console.WriteLine($"skip   {matchId} (exists)"); continue; }

    var sw = Stopwatch.StartNew();
    try
    {
        if (survey)
        {
            var sr = new ZoneLabReader(ParseMode.Full) { SurveyEnabled = true };
            var rp = sr.ReadReplay(file);
            var report = Survey.Report(matchId, rp, sr);
            File.WriteAllText(outPath, JsonSerializer.Serialize(report, jsonOpts));
            Survey.Print(report);
            ok++;
            continue;
        }
        var reader = new ZoneLabReader(mode);
        var replay = reader.ReadReplay(file);
        var doc = Extract.Build(matchId, file, replay, sampleSec, reader.Buses);
        File.WriteAllText(outPath, JsonSerializer.Serialize(doc, jsonOpts));
        Console.WriteLine($"ok     {matchId}  {sw.Elapsed.TotalSeconds:F1}s  players={doc.Players.Count} zones={doc.Zones.Count} positions={doc.Positions.Rows.Count} bus={doc.Bus.Count}");
        ok++;
    }
    catch (Exception ex)
    {
        Console.WriteLine($"FAIL   {matchId}: {ex.GetType().Name}: {ex.Message}");
        File.AppendAllText(Path.Combine(outDir, "failed.txt"), $"{matchId}\t{ex.GetType().Name}: {ex.Message}\n");
        failed++;
    }
}
Console.WriteLine($"done: {ok} ok, {failed} failed");
return failed > 0 && ok == 0 ? 2 : 0;

// ---------------------------------------------------------------------------
static class Extract
{
    static double[]? V(FVector? v) => v is null ? null : new[] { v.X, v.Y, v.Z };

    public static ResearchDoc Build(string matchId, string file, FortniteReplay r, double sampleSec, IReadOnlyDictionary<uint, BusExport> buses)
    {
        var doc = new ResearchDoc { MatchId = matchId, SourceFile = Path.GetFileName(file) };

        doc.Replay = new ReplayMeta
        {
            LengthMs = r.Info?.LengthInMs,
            NetworkVersion = r.Info?.NetworkVersion,
            Changelist = r.Info?.Changelist,
            Timestamp = r.Info?.Timestamp,
            IsEncrypted = r.Info?.IsEncrypted,
            Branch = r.Header?.Branch,
            HeaderChangelist = r.Header?.Changelist,
            EngineNetworkVersion = r.Header is null ? null : (int)r.Header.EngineNetworkVersion,
            Platform = r.Header?.Platform,
        };

        var g = r.GameData;
        doc.Game = new GameMeta
        {
            SessionId = g?.GameSessionId,
            Playlist = g?.CurrentPlaylist,
            MapInfo = g?.MapInfo,
            TournamentRound = g?.TournamentRound,
            TeamSize = g?.TeamSize,
            TotalTeams = g?.TotalTeams,
            MaxPlayers = g?.MaxPlayers,
            TotalBots = g?.TotalBots,
            AircraftStartT = g?.AircraftStartTime,
            SafeZonesStartT = g?.SafeZonesStartTime,
            MatchEndT = g?.MatchEndTime,
            UtcStarted = g?.UtcTimeStartedMatch,
            WinningTeam = g?.WinningTeam,
            WinningPlayerIds = g?.WinningPlayerIds?.ToList(),
        };

        foreach (var b in r.MapData?.BattleBusFlightPaths ?? Enumerable.Empty<BattleBus>())
        {
            doc.Bus.Add(new BusRow
            {
                Index = b.AircraftIndex,
                Start = V(b.FlightStartLocation),
                Yaw = b.FlightStartRotation?.Yaw,
                Speed = b.FlightSpeed,
                TimeTillFlightEnd = b.TimeTillFlightEnd,
                TimeTillDropStart = b.TimeTillDropStart,
                TimeTillDropEnd = b.TimeTillDropEnd,
                FlightTimestamp = b.ReplicatedFlightTimestamp,
            });
        }

        // The bus actor itself (captured by ZoneLabReader). Recent replays don't fill the
        // parser's own flight-path list, so this is the reliable source.
        if (doc.Bus.Count == 0)
        {
            foreach (var (channel, b) in buses.OrderBy(kv => kv.Value.FlightStartTime ?? float.MaxValue))
            {
                if (b.FlightStartLocation is null) continue;
                doc.Bus.Add(new BusRow
                {
                    Index = channel,
                    Start = V(b.FlightStartLocation),
                    Yaw = b.FlightStartRotation?.Yaw,
                    Speed = b.FlightSpeed ?? 0,
                    TimeTillFlightEnd = b.TimeTillFlightEnd ?? 0,
                    TimeTillDropStart = b.TimeTillDropStart ?? 0,
                    TimeTillDropEnd = b.TimeTillDropEnd ?? 0,
                    FlightTimestamp = b.ReplicatedFlightTimestamp ?? 0,
                    FlightStartTime = b.FlightStartTime, FlightEndTime = b.FlightEndTime,
                    DropStartTime = b.DropStartTime, DropEndTime = b.DropEndTime,
                    Source = "aircraft_actor",
                });
            }
        }

        int zi = 0;
        foreach (var z in r.MapData?.SafeZones ?? new List<SafeZone>())
        {
            doc.Zones.Add(new ZoneRow
            {
                Seq = zi++,
                Radius = z.Radius,
                StartShrinkT = z.StartShrinkTime,
                FinishShrinkT = z.FinishShrinkTime,
                LastCenter = V(z.LastCenter), LastRadius = z.LastRadius,
                NextCenter = V(z.NextCenter), NextRadius = z.NextRadius,
                NextNextCenter = V(z.NextNextCenter), NextNextRadius = z.NextNextRadius,
            });
        }

        var players = (r.PlayerData ?? Enumerable.Empty<PlayerData>()).ToList();
        foreach (var p in players)
        {
            doc.Players.Add(new PlayerRow
            {
                Id = p.Id,
                PlayerId = p.PlayerId,
                Name = p.PlayerName,
                IsBot = p.IsBot,
                TeamIndex = p.TeamIndex,
                Placement = p.Placement,
                Kills = p.Kills,
                TeamKills = p.TeamKills,
                DeathT = p.DeathTimeDouble ?? p.DeathTime,
                DeathCause = p.DeathCause,
                DeathLocation = V(p.DeathLocation),
                Disconnected = p.Disconnected,
                Platform = p.Platform,
                LocationSamplesRaw = p.Locations?.Count ?? 0,
                LocationSamplesUntimed = p.Locations?.Count(m => m.ReplicatedMovement?.Location is not null
                    && (m.ReplicatedWorldTimeSecondsDouble ?? m.ReplicatedWorldTimeSeconds ?? m.LastUpdateTime) is null) ?? 0,
            });

            // Downsample movement to at most one sample per player per `sampleSec`.
            double last = double.NegativeInfinity;
            foreach (var m in p.Locations ?? new List<PlayerMovement>())
            {
                var loc = m.ReplicatedMovement?.Location;
                if (loc is null) continue;
                double? t = m.ReplicatedWorldTimeSecondsDouble ?? m.ReplicatedWorldTimeSeconds ?? m.LastUpdateTime;
                if (t is null || double.IsNaN(t.Value)) continue;
                if (t.Value - last < sampleSec) continue;
                last = t.Value;
                var vel = m.ReplicatedMovement?.LinearVelocity;
                doc.Positions.Rows.Add(new object?[]
                {
                    p.Id, Math.Round(t.Value, 2),
                    Math.Round(loc.X, 1), Math.Round(loc.Y, 1), Math.Round(loc.Z, 1),
                    vel is null ? null : Math.Round(vel.X, 1), vel is null ? null : Math.Round(vel.Y, 1), vel is null ? null : Math.Round(vel.Z, 1),
                    m.bIsInAnyStorm, m.bIsDBNO, m.bIsSkydiving,
                });
            }
        }

        foreach (var t in r.TeamData ?? Enumerable.Empty<TeamData>())
        {
            doc.Teams.Add(new TeamRow { TeamIndex = t.TeamIndex, Placement = t.Placement, TeamKills = t.TeamKills, PlayerIds = t.PlayerIds?.ToList() });
        }

        foreach (var k in r.KillFeed ?? new List<KillFeedEntry>())
        {
            doc.KillFeed.Add(new KillRow
            {
                T = k.ReplicatedWorldTimeSecondsDouble ?? k.ReplicatedWorldTimeSeconds,
                VictimId = k.PlayerId, FinisherId = k.FinisherOrDowner,
                Downed = k.IsDowned, Revived = k.IsRevived,
                Distance = k.Distance, DeathCause = k.DeathCause, Location = V(k.DeathLocation),
            });
        }

        foreach (var e in r.Eliminations ?? new List<FortniteReplayReader.Models.Events.PlayerElimination>())
        {
            doc.Eliminations.Add(new ElimRow
            {
                Time = e.Time, Eliminated = e.Eliminated, Eliminator = e.Eliminator,
                Knocked = e.Knocked, GunType = e.GunType,
                EliminatedLocation = V(e.EliminatedInfo?.Location), EliminatorLocation = V(e.EliminatorInfo?.Location),
            });
        }
        return doc;
    }
}

// ---- output schema --------------------------------------------------------
class ResearchDoc
{
    public string Schema { get; set; } = "fn-research/1";
    public string MatchId { get; set; } = "";
    public string SourceFile { get; set; } = "";
    public ReplayMeta Replay { get; set; } = new();
    public GameMeta Game { get; set; } = new();
    public List<BusRow> Bus { get; set; } = new();
    public List<ZoneRow> Zones { get; set; } = new();
    public List<PlayerRow> Players { get; set; } = new();
    public List<TeamRow> Teams { get; set; } = new();
    public PositionTable Positions { get; set; } = new();
    public List<KillRow> KillFeed { get; set; } = new();
    public List<ElimRow> Eliminations { get; set; } = new();
}
class ReplayMeta { public uint? LengthMs { get; set; } public uint? NetworkVersion { get; set; } public uint? Changelist { get; set; } public DateTime? Timestamp { get; set; } public bool? IsEncrypted { get; set; } public string? Branch { get; set; } public uint? HeaderChangelist { get; set; } public int? EngineNetworkVersion { get; set; } public string? Platform { get; set; } }
class GameMeta { public string? SessionId { get; set; } public string? Playlist { get; set; } public string? MapInfo { get; set; } public int? TournamentRound { get; set; } public int? TeamSize { get; set; } public int? TotalTeams { get; set; } public int? MaxPlayers { get; set; } public int? TotalBots { get; set; } public float? AircraftStartT { get; set; } public float? SafeZonesStartT { get; set; } public float? MatchEndT { get; set; } public DateTime? UtcStarted { get; set; } public uint? WinningTeam { get; set; } public List<int>? WinningPlayerIds { get; set; } }
class BusRow { public uint Index { get; set; } public double[]? Start { get; set; } public float? Yaw { get; set; } public float Speed { get; set; } public float TimeTillFlightEnd { get; set; } public float TimeTillDropStart { get; set; } public float TimeTillDropEnd { get; set; } public float FlightTimestamp { get; set; }
    public float? FlightStartTime { get; set; } public float? FlightEndTime { get; set; } public float? DropStartTime { get; set; } public float? DropEndTime { get; set; } public string Source { get; set; } = "game_state"; }

// ---- Battle Bus capture ----------------------------------------------------
// The parser library defines the bus actor but only parses it in its "Ignore" mode.
// This copy is enabled at the normal level. The parser only scans libraries whose name
// contains "ReplayReader", which is why this project's assembly is ZoneLab.ReplayReader.
[NetFieldExportGroup("/Game/Athena/Aircraft/AthenaAircraft.AthenaAircraft_C", ParseMode.Minimal)]
public class BusExport : INetFieldExportGroup
{
    [NetFieldExport("FlightStartLocation", RepLayoutCmdType.PropertyVector100)] public FVector? FlightStartLocation { get; set; }
    [NetFieldExport("FlightStartRotation", RepLayoutCmdType.PropertyRotator)] public FRotator? FlightStartRotation { get; set; }
    [NetFieldExport("FlightSpeed", RepLayoutCmdType.PropertyFloat)] public float? FlightSpeed { get; set; }
    [NetFieldExport("TimeTillFlightEnd", RepLayoutCmdType.PropertyFloat)] public float? TimeTillFlightEnd { get; set; }
    [NetFieldExport("TimeTillDropStart", RepLayoutCmdType.PropertyFloat)] public float? TimeTillDropStart { get; set; }
    [NetFieldExport("TimeTillDropEnd", RepLayoutCmdType.PropertyFloat)] public float? TimeTillDropEnd { get; set; }
    [NetFieldExport("FlightStartTime", RepLayoutCmdType.PropertyFloat)] public float? FlightStartTime { get; set; }
    [NetFieldExport("FlightEndTime", RepLayoutCmdType.PropertyFloat)] public float? FlightEndTime { get; set; }
    [NetFieldExport("DropStartTime", RepLayoutCmdType.PropertyFloat)] public float? DropStartTime { get; set; }
    [NetFieldExport("DropEndTime", RepLayoutCmdType.PropertyFloat)] public float? DropEndTime { get; set; }
    [NetFieldExport("ReplicatedFlightTimestamp", RepLayoutCmdType.PropertyFloat)] public float? ReplicatedFlightTimestamp { get; set; }
}

class ZoneLabReader : ReplayReader
{
    // Updates arrive as partial deltas; merge them per bus (one channel per bus).
    public Dictionary<uint, BusExport> Buses { get; } = new();

    public ZoneLabReader(ParseMode mode) : base(null, mode) { }

    // ---- survey: every data type (export group) the replay registers, with its field names
    public bool SurveyEnabled { get; set; }
    public Dictionary<string, List<string>> SurveyGroups { get; } = new();
    public Dictionary<string, int> SurveyReads { get; } = new();
    int _surveySeen = -1;

    void CaptureSchema()
    {
        if (!SurveyEnabled) return;
        var map = _netGuidCache.NetFieldExportGroupMap;
        if (map.Count == _surveySeen) return;
        _surveySeen = map.Count;
        foreach (var (path, group) in map)
        {
            var names = group.NetFieldExports?.Where(f => f is not null).Select(f => f!.Name).Where(n => !string.IsNullOrEmpty(n)).ToList() ?? new();
            if (!SurveyGroups.TryGetValue(path, out var have) || have.Count < names.Count) SurveyGroups[path] = names;
        }
    }

    public override void ReadNetFieldExports(Unreal.Core.FArchive archive) { base.ReadNetFieldExports(archive); CaptureSchema(); }
    public override void ReceiveNetFieldExportsCompat(Unreal.Core.FBitArchive bitArchive) { base.ReceiveNetFieldExportsCompat(bitArchive); CaptureSchema(); }

    protected override void OnExportRead(uint channelIndex, INetFieldExportGroup? exportGroup)
    {
        if (SurveyEnabled && exportGroup is not null)
        {
            var t = exportGroup.GetType();
            var path = t.GetCustomAttributes(typeof(NetFieldExportGroupAttribute), false).OfType<NetFieldExportGroupAttribute>().FirstOrDefault()?.Path ?? t.Name;
            SurveyReads[path] = SurveyReads.GetValueOrDefault(path) + 1;
        }
        if (exportGroup is BusExport b)
        {
            if (!Buses.TryGetValue(channelIndex, out var m)) Buses[channelIndex] = m = new BusExport();
            foreach (var prop in typeof(BusExport).GetProperties())
            {
                var v = prop.GetValue(b);
                if (v is not null) prop.SetValue(m, v);
            }
            return;
        }
        base.OnExportRead(channelIndex, exportGroup);
    }
}
static class Survey
{
    // Topics worth knowing about, and the words that point to them in field or type names.
    static readonly (string Topic, string[] Words)[] Topics =
    {
        ("Health and shields", new[] { "health", "shield" }),
        ("Damage", new[] { "damage" }),
        ("Storm surge", new[] { "surge" }),
        ("Storm", new[] { "storm", "safezone" }),
        ("Weapons", new[] { "weapon", "ammo" }),
        ("Inventory and items", new[] { "inventory", "itementry", "pickup", "itemdefinition" }),
        ("Materials", new[] { "wood", "stone", "metal", "material", "resource" }),
        ("Chests and containers", new[] { "container", "chest", "searched", "tiered", "ammobox" }),
        ("Healing", new[] { "heal", "consum", "medkit", "bandage" }),
        ("Revives and reboots", new[] { "revive", "reboot", "respawn", "dbno" }),
        ("Building", new[] { "build", "structure", "edit" }),
        ("Vehicles", new[] { "vehicle" }),
        ("Player stats", new[] { "stat", "score", "kills", "assist" }),
    };

    public static object Report(string matchId, FortniteReplay r, ZoneLabReader reader)
    {
        var groups = reader.SurveyGroups.OrderBy(g => g.Key).Select(g => new
        {
            Path = g.Key,
            Fields = g.Value,
            Reads = reader.SurveyReads.GetValueOrDefault(g.Key),
        }).ToList();
        var topics = Topics.Select(t => new
        {
            t.Topic,
            Matches = groups.SelectMany(g =>
            {
                bool inPath = t.Words.Any(w => g.Path.Contains(w, StringComparison.OrdinalIgnoreCase));
                var fields = g.Fields.Where(f => t.Words.Any(w => f.Contains(w, StringComparison.OrdinalIgnoreCase))).ToList();
                return inPath || fields.Count > 0
                    ? new[] { new { g.Path, Fields = inPath ? g.Fields.Take(40).ToList() : fields, g.Reads } }
                    : Array.Empty<object>().Select(_ => new { Path = "", Fields = new List<string>(), Reads = 0 });
            }).ToList(),
        }).ToList();
        return new
        {
            MatchId = matchId,
            Branch = r.Header?.Branch,
            DataTypes = groups.Count,
            DecodedTypes = reader.SurveyReads.Count,
            Topics = topics,
            Groups = groups,
        };
    }

    public static void Print(object report)
    {
        var json = JsonSerializer.SerializeToElement(report);
        Console.WriteLine($"survey {json.GetProperty("MatchId").GetString()}  {json.GetProperty("Branch").GetString()}: " +
                          $"{json.GetProperty("DataTypes").GetInt32()} data types, {json.GetProperty("DecodedTypes").GetInt32()} decoded");
        foreach (var t in json.GetProperty("Topics").EnumerateArray())
        {
            var m = t.GetProperty("Matches");
            Console.WriteLine($"  {t.GetProperty("Topic").GetString(),-24} {m.GetArrayLength(),3} data types");
        }
    }
}

class ZoneRow { public int Seq { get; set; } public float Radius { get; set; } public float StartShrinkT { get; set; } public float FinishShrinkT { get; set; } public double[]? LastCenter { get; set; } public float LastRadius { get; set; } public double[]? NextCenter { get; set; } public float NextRadius { get; set; } public double[]? NextNextCenter { get; set; } public float NextNextRadius { get; set; } }
class PlayerRow { public int? Id { get; set; } public string? PlayerId { get; set; } public string? Name { get; set; } public bool IsBot { get; set; } public int? TeamIndex { get; set; } public int? Placement { get; set; } public uint? Kills { get; set; } public uint? TeamKills { get; set; } public double? DeathT { get; set; } public int? DeathCause { get; set; } public double[]? DeathLocation { get; set; } public bool? Disconnected { get; set; } public string? Platform { get; set; } public int LocationSamplesRaw { get; set; } public int LocationSamplesUntimed { get; set; } }
class TeamRow { public int? TeamIndex { get; set; } public int? Placement { get; set; } public uint? TeamKills { get; set; } public List<int?>? PlayerIds { get; set; } }
class PositionTable
{
    public string[] Columns { get; set; } = { "id", "t", "x", "y", "z", "vx", "vy", "vz", "in_storm", "dbno", "skydiving" };
    public List<object?[]> Rows { get; set; } = new();
}
class KillRow { public double? T { get; set; } public int? VictimId { get; set; } public int? FinisherId { get; set; } public bool Downed { get; set; } public bool Revived { get; set; } public float? Distance { get; set; } public int? DeathCause { get; set; } public double[]? Location { get; set; } }
class ElimRow { public string? Time { get; set; } public string? Eliminated { get; set; } public string? Eliminator { get; set; } public bool Knocked { get; set; } public byte GunType { get; set; } public double[]? EliminatedLocation { get; set; } public double[]? EliminatorLocation { get; set; } }
