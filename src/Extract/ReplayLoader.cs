using osu.Game.Beatmaps;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Osu;
using osu.Game.Scoring;
using osu.Game.Scoring.Legacy;

namespace Extract;

/// <summary>
/// Thrown when a replay names a beatmap that is not in the index.
/// </summary>
public sealed class BeatmapNotIndexedException(string md5) : Exception($"No indexed beatmap for MD5 {md5}")
{
    public string Md5 { get; } = md5;
}

/// <summary>
/// osu.Game's score decoder leaves exactly two hooks open: which ruleset, and which
/// beatmap. Filling those in is the whole of what is needed to read a <c>.osr</c> through
/// lazer's real code path, with no host and no database.
/// </summary>
public sealed class IndexedScoreDecoder(IReadOnlyDictionary<string, IndexEntry> index) : LegacyScoreDecoder
{
    // Only osu!standard is in scope, and only osu.Game.Rulesets.Osu is referenced, so the
    // other three rulesets are a decode-time rejection rather than a missing feature.
    protected override Ruleset GetRuleset(int rulesetId) => rulesetId == 0
        ? new OsuRuleset()
        : throw new NotSupportedException($"Ruleset {rulesetId} is out of scope; this analyses osu!standard only.");

    protected override WorkingBeatmap GetBeatmap(string md5Hash)
    {
        if (!index.TryGetValue(md5Hash, out var entry))
            throw new BeatmapNotIndexedException(md5Hash);

        return new FlatWorkingBeatmap(entry.Path);
    }
}

/// <summary>
/// The parts of a <c>.osr</c> header that can be read without a beatmap, used to triage a
/// corpus before committing to a full decode.
/// </summary>
public sealed record ReplayHeader(int RulesetId, int Version, string BeatmapMd5)
{
    public bool IsLazerFormat => Version >= 30000000;
}

public static class ReplayLoader
{
    /// <summary>
    /// Read just the leading fields of a <c>.osr</c>: ruleset, version and beatmap hash.
    /// Cheap enough to run over the whole corpus, and it needs no beatmap, which the full
    /// decode does.
    /// </summary>
    public static ReplayHeader ReadHeader(string path)
    {
        using var stream = File.OpenRead(path);
        using var reader = new BinaryReader(stream);

        int rulesetId = reader.ReadByte();
        int version = reader.ReadInt32();
        string md5 = readLegacyString(reader);

        return new ReplayHeader(rulesetId, version, md5);
    }

    public static Score Decode(string path, IReadOnlyDictionary<string, IndexEntry> index)
    {
        using var stream = File.OpenRead(path);
        return new IndexedScoreDecoder(index).Parse(stream);
    }

    /// <summary>
    /// .NET BinaryWriter string framing, as used by osu.Game's SerializationWriter:
    /// a null marker, or 0x0b followed by a ULEB128 length and UTF-8 bytes.
    /// </summary>
    private static string readLegacyString(BinaryReader reader)
    {
        byte marker = reader.ReadByte();

        if (marker != 0x0b)
            return string.Empty;

        return reader.ReadString();
    }
}
