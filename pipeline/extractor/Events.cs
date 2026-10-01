// Events.cs - capture the in-match events the parser decodes but doesn't keep:
// health and shields, damage, chest opens, item spawns and pickups, weapons held,
// and player-built pieces. Each event is linked to a player ID (the same IDs as the
// players and positions tables) and stamped with the game's world clock.
//
// Linking: a player's character ("pawn") names its player-state actor; player states
// carry the player ID. Damage targets and pickup takers are named by actor IDs,
// which map to channels and from there to pawns.
//
// Health and shield fields are plain floats in the parser, so a field that wasn't in an
// update reads as 0. Non-zero values are trusted; health 0 only happens at elimination
// (known separately); shields are set to 0 when a damage event reports the shield broke.
using FortniteReplayReader.Models.NetFieldExports;
using FortniteReplayReader.Models.NetFieldExports.RPC;
using FortniteReplayReader.Models.NetFieldExports.Weapons;
using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

// ---- Base definitions; one subclass per season-specific path lives in Generated.cs.
public abstract class BuildExport : INetFieldExportGroup
{
    [NetFieldExport("TeamIndex", RepLayoutCmdType.Enum)] public int? TeamIndex { get; set; }
    [NetFieldExport("bPlayerPlaced", RepLayoutCmdType.PropertyBool)] public bool? bPlayerPlaced { get; set; }
    [NetFieldExport("bDestroyed", RepLayoutCmdType.PropertyBool)] public bool? bDestroyed { get; set; }
    [NetFieldExport("Health", RepLayoutCmdType.PropertyInt16)] public short? Health { get; set; }
    [NetFieldExport("MaxHealth", RepLayoutCmdType.PropertyInt16)] public short? MaxHealth { get; set; }
}

public abstract class WeaponExport : INetFieldExportGroup
{
    [NetFieldExport("WeaponData", RepLayoutCmdType.Property)] public ItemDefinition? WeaponData { get; set; }
}

public abstract class ContainerExport : INetFieldExportGroup
{
    [NetFieldExport("bAlreadySearched", RepLayoutCmdType.PropertyBool)] public bool? bAlreadySearched { get; set; }
    [NetFieldExport("SearchingPawn", RepLayoutCmdType.PropertyObject)] public uint? SearchingPawn { get; set; }
    [NetFieldExport("ReplicatedLootTier", RepLayoutCmdType.PropertyInt)] public int? ReplicatedLootTier { get; set; }
}

// Raw health record: every slot, so the table builder can find which slot is health and which is the
// shield each season (Epic adds attributes, shifting slots). Registered before the parser's own
// HealthSet definition, which then gets ignored; if load order ever differs, the parser's version is
// used instead and only health is available.
[NetFieldExportGroup("/Script/FortniteGame.FortRegenHealthSet", ParseMode.Minimal)]
public sealed class RegenHealthRaw : INetFieldExportGroup
{
    [NetFieldExportHandle(0, RepLayoutCmdType.PropertyFloat)] public float? H0 { get; set; }
    [NetFieldExportHandle(1, RepLayoutCmdType.PropertyFloat)] public float? H1 { get; set; }
    [NetFieldExportHandle(2, RepLayoutCmdType.PropertyFloat)] public float? H2 { get; set; }
    [NetFieldExportHandle(3, RepLayoutCmdType.PropertyFloat)] public float? H3 { get; set; }
    [NetFieldExportHandle(4, RepLayoutCmdType.PropertyFloat)] public float? H4 { get; set; }
    [NetFieldExportHandle(5, RepLayoutCmdType.PropertyFloat)] public float? H5 { get; set; }
    [NetFieldExportHandle(6, RepLayoutCmdType.PropertyFloat)] public float? H6 { get; set; }
    [NetFieldExportHandle(7, RepLayoutCmdType.PropertyFloat)] public float? H7 { get; set; }
    [NetFieldExportHandle(8, RepLayoutCmdType.PropertyFloat)] public float? H8 { get; set; }
    [NetFieldExportHandle(9, RepLayoutCmdType.PropertyFloat)] public float? H9 { get; set; }
    [NetFieldExportHandle(10, RepLayoutCmdType.PropertyFloat)] public float? H10 { get; set; }
    [NetFieldExportHandle(11, RepLayoutCmdType.PropertyFloat)] public float? H11 { get; set; }
    [NetFieldExportHandle(12, RepLayoutCmdType.PropertyFloat)] public float? H12 { get; set; }
    [NetFieldExportHandle(13, RepLayoutCmdType.PropertyFloat)] public float? H13 { get; set; }
    [NetFieldExportHandle(14, RepLayoutCmdType.PropertyFloat)] public float? H14 { get; set; }
    [NetFieldExportHandle(15, RepLayoutCmdType.PropertyFloat)] public float? H15 { get; set; }
    [NetFieldExportHandle(16, RepLayoutCmdType.PropertyFloat)] public float? H16 { get; set; }
    [NetFieldExportHandle(17, RepLayoutCmdType.PropertyFloat)] public float? H17 { get; set; }
    [NetFieldExportHandle(18, RepLayoutCmdType.PropertyFloat)] public float? H18 { get; set; }
    [NetFieldExportHandle(19, RepLayoutCmdType.PropertyFloat)] public float? H19 { get; set; }
    [NetFieldExportHandle(20, RepLayoutCmdType.PropertyFloat)] public float? H20 { get; set; }
    [NetFieldExportHandle(21, RepLayoutCmdType.PropertyFloat)] public float? H21 { get; set; }
    [NetFieldExportHandle(22, RepLayoutCmdType.PropertyFloat)] public float? H22 { get; set; }
    [NetFieldExportHandle(23, RepLayoutCmdType.PropertyFloat)] public float? H23 { get; set; }
    [NetFieldExportHandle(24, RepLayoutCmdType.PropertyFloat)] public float? H24 { get; set; }
    [NetFieldExportHandle(25, RepLayoutCmdType.PropertyFloat)] public float? H25 { get; set; }
    [NetFieldExportHandle(26, RepLayoutCmdType.PropertyFloat)] public float? H26 { get; set; }
    [NetFieldExportHandle(27, RepLayoutCmdType.PropertyFloat)] public float? H27 { get; set; }
    [NetFieldExportHandle(28, RepLayoutCmdType.PropertyFloat)] public float? H28 { get; set; }
    [NetFieldExportHandle(29, RepLayoutCmdType.PropertyFloat)] public float? H29 { get; set; }
    [NetFieldExportHandle(30, RepLayoutCmdType.PropertyFloat)] public float? H30 { get; set; }
    [NetFieldExportHandle(31, RepLayoutCmdType.PropertyFloat)] public float? H31 { get; set; }
    [NetFieldExportHandle(32, RepLayoutCmdType.PropertyFloat)] public float? H32 { get; set; }
    [NetFieldExportHandle(33, RepLayoutCmdType.PropertyFloat)] public float? H33 { get; set; }
    [NetFieldExportHandle(34, RepLayoutCmdType.PropertyFloat)] public float? H34 { get; set; }
    [NetFieldExportHandle(35, RepLayoutCmdType.PropertyFloat)] public float? H35 { get; set; }
    [NetFieldExportHandle(36, RepLayoutCmdType.PropertyFloat)] public float? H36 { get; set; }
    [NetFieldExportHandle(37, RepLayoutCmdType.PropertyFloat)] public float? H37 { get; set; }
    [NetFieldExportHandle(38, RepLayoutCmdType.PropertyFloat)] public float? H38 { get; set; }
    [NetFieldExportHandle(39, RepLayoutCmdType.PropertyFloat)] public float? H39 { get; set; }
    [NetFieldExportHandle(40, RepLayoutCmdType.PropertyFloat)] public float? H40 { get; set; }
    [NetFieldExportHandle(41, RepLayoutCmdType.PropertyFloat)] public float? H41 { get; set; }
    [NetFieldExportHandle(42, RepLayoutCmdType.PropertyFloat)] public float? H42 { get; set; }
    [NetFieldExportHandle(43, RepLayoutCmdType.PropertyFloat)] public float? H43 { get; set; }
    [NetFieldExportHandle(44, RepLayoutCmdType.PropertyFloat)] public float? H44 { get; set; }
    [NetFieldExportHandle(45, RepLayoutCmdType.PropertyFloat)] public float? H45 { get; set; }
    [NetFieldExportHandle(46, RepLayoutCmdType.PropertyFloat)] public float? H46 { get; set; }
    [NetFieldExportHandle(47, RepLayoutCmdType.PropertyFloat)] public float? H47 { get; set; }
    [NetFieldExportHandle(48, RepLayoutCmdType.PropertyFloat)] public float? H48 { get; set; }
    [NetFieldExportHandle(49, RepLayoutCmdType.PropertyFloat)] public float? H49 { get; set; }
    [NetFieldExportHandle(50, RepLayoutCmdType.PropertyFloat)] public float? H50 { get; set; }
    [NetFieldExportHandle(51, RepLayoutCmdType.PropertyFloat)] public float? H51 { get; set; }
    [NetFieldExportHandle(52, RepLayoutCmdType.PropertyFloat)] public float? H52 { get; set; }
    [NetFieldExportHandle(53, RepLayoutCmdType.PropertyFloat)] public float? H53 { get; set; }
    [NetFieldExportHandle(54, RepLayoutCmdType.PropertyFloat)] public float? H54 { get; set; }
    [NetFieldExportHandle(55, RepLayoutCmdType.PropertyFloat)] public float? H55 { get; set; }
    [NetFieldExportHandle(56, RepLayoutCmdType.PropertyFloat)] public float? H56 { get; set; }
    [NetFieldExportHandle(57, RepLayoutCmdType.PropertyFloat)] public float? H57 { get; set; }
    [NetFieldExportHandle(58, RepLayoutCmdType.PropertyFloat)] public float? H58 { get; set; }
    [NetFieldExportHandle(59, RepLayoutCmdType.PropertyFloat)] public float? H59 { get; set; }
    [NetFieldExportHandle(60, RepLayoutCmdType.PropertyFloat)] public float? H60 { get; set; }
    [NetFieldExportHandle(61, RepLayoutCmdType.PropertyFloat)] public float? H61 { get; set; }
    [NetFieldExportHandle(62, RepLayoutCmdType.PropertyFloat)] public float? H62 { get; set; }
    [NetFieldExportHandle(63, RepLayoutCmdType.PropertyFloat)] public float? H63 { get; set; }
    [NetFieldExportHandle(64, RepLayoutCmdType.PropertyFloat)] public float? H64 { get; set; }
    [NetFieldExportHandle(65, RepLayoutCmdType.PropertyFloat)] public float? H65 { get; set; }
    [NetFieldExportHandle(66, RepLayoutCmdType.PropertyFloat)] public float? H66 { get; set; }
    [NetFieldExportHandle(67, RepLayoutCmdType.PropertyFloat)] public float? H67 { get; set; }
    [NetFieldExportHandle(68, RepLayoutCmdType.PropertyFloat)] public float? H68 { get; set; }
    [NetFieldExportHandle(69, RepLayoutCmdType.PropertyFloat)] public float? H69 { get; set; }
    [NetFieldExportHandle(70, RepLayoutCmdType.PropertyFloat)] public float? H70 { get; set; }
    [NetFieldExportHandle(71, RepLayoutCmdType.PropertyFloat)] public float? H71 { get; set; }
    public static readonly (int Handle, System.Reflection.PropertyInfo Prop)[] Slots =
        typeof(RegenHealthRaw).GetProperties().Where(p => p.Name.StartsWith("H"))
            .Select(p => (int.Parse(p.Name[1..]), p)).ToArray();
}

public class EventTable
{
    public string[] Columns { get; set; } = Array.Empty<string>();
    public List<object?[]> Rows { get; set; } = new();
}

public class MatchEvents
{
    public EventTable Health { get; set; } = new();
    public EventTable Damage { get; set; } = new();
    public EventTable Chests { get; set; } = new();
    public EventTable Pickups { get; set; } = new();
    public EventTable WeaponsHeld { get; set; } = new();
    public EventTable Builds { get; set; } = new();
    public EventTable Attributes { get; set; } = new();
}

public class EventCapture
{
    sealed class Pickup { public double T; public string? Item; public int? Count; public double[]? At; public bool Tossed; public double? PickedT; public int? PickedBy; }
    double? _closing;  // set while a channel closes, so Finish can stamp when the item disappeared
    sealed class Build { public double T; public string Kind = ""; public int? Team; public bool? Placed; public short? MaxHealth; public double[]? At; public double? DestroyedT; }
    sealed class Chest { public string Kind = ""; public int? Tier; public int? SearchedBy; public bool Logged; }

    double _now;
    readonly Dictionary<uint, uint> _actorToChannel = new();
    readonly Dictionary<uint, uint> _channelToActor = new();
    readonly Dictionary<uint, int> _stateChannelToId = new();
    readonly Dictionary<uint, uint> _pawnToState = new();
    readonly Dictionary<uint, (double H, double S)> _hp = new();
    readonly Dictionary<uint, uint> _held = new();
    readonly Dictionary<uint, string> _weaponName = new();
    // Keyed by actor ID, not channel: objects go dormant (channel closes) and wake up again on a new channel.
    readonly Dictionary<uint, Pickup> _pickups = new();
    readonly Dictionary<uint, Build> _builds = new();
    readonly Dictionary<uint, Chest> _chests = new();
    uint Key(uint ch) => _channelToActor.TryGetValue(ch, out var g) ? g : 0x8000_0000u | ch;
    readonly List<object?[]> _attributes = new();
    readonly List<object?[]> _health = new(), _damage = new(), _chestRows = new(), _pickupRows = new(), _heldRows = new(), _buildRows = new();
    static readonly Dictionary<Type, string> _pathOf = new();

    static string ShortName(Type t)
    {
        if (!_pathOf.TryGetValue(t, out var p))
        {
            p = t.GetCustomAttributes(typeof(NetFieldExportGroupAttribute), false).OfType<NetFieldExportGroupAttribute>()
                 .FirstOrDefault()?.Path ?? t.Name;
            p = p.Split('/').Last().Split('.').First();
            _pathOf[t] = p;
        }
        return p;
    }

    static double R(double v) => Math.Round(v, 2);
    static double[]? Xyz(FVector? v) => v is null ? null : new[] { R(v.X), R(v.Y), R(v.Z) };
    // A channel can be a player's character (pawn) or their player state (which holds health in modern Fortnite).
    int? PlayerOfPawnChannel(uint ch) => _stateChannelToId.TryGetValue(ch, out var direct) ? direct
        : _pawnToState.TryGetValue(ch, out var s) && _stateChannelToId.TryGetValue(s, out var id) ? id : null;
    int? PlayerOfActor(uint? guid) => guid is uint g && _actorToChannel.TryGetValue(g, out var ch) ? PlayerOfPawnChannel(ch) : null;

    public void ChannelOpened(uint ch, NetworkGUID? actor)
    {
        _pawnToState.Remove(ch); _hp.Remove(ch); _held.Remove(ch);
        if (actor is null) return;
        _actorToChannel[actor.Value] = ch;
        _channelToActor[ch] = actor.Value;
    }

    // Destroyed: the object is gone (an item taken, a build broken). Dormant: it stopped changing and will
    // come back on a new channel if it changes again, so its record stays open.
    public void ChannelClosed(uint ch, bool destroyed)
    {
        if (!destroyed) return;
        _closing = _now;
        Finish(Key(ch));
        _closing = null;
    }

    void Finish(uint key)
    {
        if (_pickups.Remove(key, out var p))
            _pickupRows.Add(new object?[] { R(p.T), p.Item, p.Count, p.At?[0], p.At?[1], p.At?[2], p.Tossed, p.PickedT is double pt ? R(pt) : null, p.PickedBy,
                                            _closing is double ct ? R(ct) : null });
        if (_builds.Remove(key, out var b))
        {
            b.DestroyedT ??= _closing;  // a destroyed channel means the build broke (the game rarely sends bDestroyed)
            _buildRows.Add(new object?[] { R(b.T), b.Kind, b.Team, b.Placed, b.MaxHealth, b.At?[0], b.At?[1], b.At?[2], b.DestroyedT is double dt ? R(dt) : null });
        }
        _chests.Remove(key);
    }

    public void Read(uint ch, INetFieldExportGroup group, Func<uint, FVector?> spawnLocation)
    {
        switch (group)
        {
            case GameState gs:
                _now = gs.ReplicatedWorldTimeSecondsDouble ?? gs.ReplicatedWorldTimeSeconds ?? _now;
                break;
            case FortPlayerState ps:
                var id = ps.PlayerId is null ? ps.PlayerID : (int?)ps.PlayerId;
                if (id is int i) _stateChannelToId[ch] = i;
                break;
            case PlayerPawn pawn:
                if (pawn.PlayerState is uint psGuid && _actorToChannel.TryGetValue(psGuid, out var sc)) _pawnToState[ch] = sc;
                if (pawn.CurrentWeapon is uint w && (!_held.TryGetValue(ch, out var prev) || prev != w))
                {
                    _held[ch] = w;
                    _heldRows.Add(new object?[] { R(_now), PlayerOfPawnChannel(ch), w });
                }
                break;
            case BatchedDamageCues dc:
                Damage(ch, dc);
                break;
            case RegenHealthRaw raw:
                {
                    var pid = PlayerOfPawnChannel(ch);
                    foreach (var (handle, prop) in RegenHealthRaw.Slots)
                        if (prop.GetValue(raw) is float v && float.IsFinite(v))
                            _attributes.Add(new object?[] { R(_now), pid, handle, Math.Round(v, 3) });
                    break;
                }
            case HealthSet hs:
                {
                    var (h, s) = _hp.TryGetValue(ch, out var v) ? v : (-1.0, 0.0);
                    double nh = hs.HealthCurrentValue > 0 ? hs.HealthCurrentValue : h;
                    double ns = hs.ShieldCurrentValue > 0 ? hs.ShieldCurrentValue : s;
                    if (nh != h || ns != s)
                    {
                        _hp[ch] = (nh, ns);
                        _health.Add(new object?[] { R(_now), PlayerOfPawnChannel(ch), nh < 0 ? null : R(nh), R(ns) });
                    }
                    break;
                }
            case FortPickup pk:
                {
                    if (!_pickups.TryGetValue(Key(ch), out var p))
                        _pickups[Key(ch)] = p = new Pickup { T = _now, At = Xyz(spawnLocation(ch)) };
                    p.Item ??= pk.ItemDefinition?.Name;
                    p.Count = pk.Count ?? p.Count;
                    if (pk.LootInitialPosition is not null) p.Tossed = true;
                    p.At ??= Xyz(pk.LootFinalPosition) ?? Xyz(pk.ReplicatedMovement?.Location);
                    if (pk.PickupTarget is uint tg && p.PickedT is null)
                    {
                        p.PickedT = _now;
                        p.PickedBy = PlayerOfActor(tg);
                    }
                    break;
                }
            case ContainerExport c:
                {
                    if (!_chests.TryGetValue(Key(ch), out var st)) _chests[Key(ch)] = st = new Chest { Kind = ShortName(group.GetType()) };
                    st.Tier = c.ReplicatedLootTier ?? st.Tier;
                    if (c.SearchingPawn is uint sp) st.SearchedBy ??= PlayerOfActor(sp);
                    if (c.bAlreadySearched == true && !st.Logged)
                    {
                        st.Logged = true;
                        var at = Xyz(spawnLocation(ch));
                        _chestRows.Add(new object?[] { R(_now), ch, st.Kind, st.Tier, st.SearchedBy, at?[0], at?[1], at?[2] });
                    }
                    break;
                }
            case WeaponExport we:
                if (we.WeaponData?.Name is string wn && _channelToActor.TryGetValue(ch, out var wg)) _weaponName[wg] = wn;
                break;
            case BaseWeapon bw:
                if (bw.WeaponData?.Name is string bn && _channelToActor.TryGetValue(ch, out var bg)) _weaponName[bg] = bn;
                break;
            case BuildExport b:
                {
                    if (!_builds.TryGetValue(Key(ch), out var st))
                        _builds[Key(ch)] = st = new Build { T = _now, Kind = ShortName(group.GetType()), At = Xyz(spawnLocation(ch)) };
                    st.Team = b.TeamIndex ?? st.Team;
                    st.Placed = b.bPlayerPlaced ?? st.Placed;
                    st.MaxHealth = b.MaxHealth ?? st.MaxHealth;
                    if (b.bDestroyed == true) st.DestroyedT ??= _now;
                    break;
                }
        }
    }

    void Damage(uint attackerPawnChannel, BatchedDamageCues dc)
    {
        var attacker = PlayerOfPawnChannel(attackerPawnChannel);
        if (dc.HitActor is uint hit && dc.Magnitude is float m)
        {
            var target = PlayerOfActor(hit);
            var kind = target is not null ? "player" : _builds.ContainsKey(hit) ? "build" : "object";
            var at = Xyz(dc.Location);
            _damage.Add(new object?[] { R(_now), attacker, target, kind, R(m), dc.bIsShield, dc.bIsShieldDestroyed,
                                        dc.bIsFatal, dc.bIsCritical, at?[0], at?[1], at?[2] });
            // Shield broke: health lives on the player state, so find that channel.
            if (dc.bIsShieldDestroyed == true && target is int tid)
            {
                var sch = _stateChannelToId.FirstOrDefault(kv => kv.Value == tid).Key;
                if (_hp.TryGetValue(sch, out var hp) && hp.S > 0)
                {
                    _hp[sch] = (hp.H, 0);
                    _health.Add(new object?[] { R(_now), target, hp.H < 0 ? null : R(hp.H), 0.0 });
                }
            }
        }
        if (dc.NonPlayerHitActor is uint np && dc.NonPlayerMagnitude is float nm)
        {
            var kind = _builds.ContainsKey(np) ? "build" : "object";
            var at = Xyz(dc.NonPlayerLocation);
            _damage.Add(new object?[] { R(_now), attacker, null, kind, R(nm), null, null, dc.NonPlayerbIsFatal, dc.NonPlayerbIsCritical,
                                        at?[0], at?[1], at?[2] });
        }
    }

    public MatchEvents Tables()
    {
        foreach (var k in _pickups.Keys.Concat(_builds.Keys).ToList()) Finish(k);
        var held = _heldRows.Select(r => new object?[] { r[0], r[1], r[2] is uint g && _weaponName.TryGetValue(g, out var n) ? n : null }).ToList();
        return new MatchEvents
        {
            Health = new EventTable { Columns = new[] { "t", "id", "health", "shield" }, Rows = _health },
            Damage = new EventTable { Columns = new[] { "t", "attacker_id", "target_id", "target_kind", "amount", "shield_hit",
                                                        "shield_destroyed", "fatal", "critical", "x", "y", "z" }, Rows = _damage },
            Chests = new EventTable { Columns = new[] { "t", "container", "kind", "tier", "searched_by", "x", "y", "z" }, Rows = _chestRows },
            Pickups = new EventTable { Columns = new[] { "spawn_t", "item", "count", "x", "y", "z", "tossed", "picked_t", "picked_by", "gone_t" }, Rows = _pickupRows },
            WeaponsHeld = new EventTable { Columns = new[] { "t", "id", "weapon" }, Rows = held },
            Builds = new EventTable { Columns = new[] { "t", "kind", "team_index", "player_placed", "max_health", "x", "y", "z", "destroyed_t" }, Rows = _buildRows },
            Attributes = new EventTable { Columns = new[] { "t", "id", "handle", "value" }, Rows = _attributes },
        };
    }
}
