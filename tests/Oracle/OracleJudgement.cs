using System.Text.Json;
using System.Text.Json.Serialization;

namespace Oracle;

/// <summary>
/// One judgement as the real game produced it, reduced to what a diff needs: which object,
/// and what it was awarded.
/// </summary>
public sealed record OracleJudgement(string ObjectType, double StartTime, string Result)
{
    /// <summary>
    /// When the game actually awarded this, which is not the object's start time and is the
    /// only field that says whether the replay still had frames at the moment. Defaults to
    /// zero on recordings made before it was captured.
    /// </summary>
    public double TimeAbsolute { get; init; }
}

public sealed record OracleRun(string ReplayPath, string BeatmapPath, IReadOnlyList<OracleJudgement> Judgements)
{
    /// <summary>
    /// Where the live game stopped judging, when it stopped before the beatmap ended.
    ///
    /// A replay carries no frames past the play that wrote it, and lazer binds
    /// <c>WaitingOnFrames</c> to <c>GameplayClockContainer.Stop()</c>, so playback freezes at
    /// the last frame instead of passing, failing or quitting. Nothing after this time was
    /// offered to the game at all, so a simulation judging past it is not diverging from the
    /// reference — it is going somewhere the reference could not follow.
    ///
    /// Null when the run played to a genuine end.
    /// </summary>
    public double? StalledAt { get; init; }

    /// <summary>
    /// Health at the moment the play was failed, captured in <c>PerformFail</c> rather than
    /// read back afterwards, since judgements keep arriving during the fail sweep and can
    /// pull it back up. Null when playback ended some other way.
    ///
    /// Not the cause of the failure, and not always zero. <c>HealthProcessor</c> fails on
    /// health reaching zero <em>or</em> on any mod fail condition, so a Sudden Death play
    /// fails on its first miss with health wherever it happened to be.
    /// </summary>
    public double? HealthAtFailure { get; init; }

    /// <summary>Whether the health processor failed the replay during playback.</summary>
    public bool Failed { get; init; }
}

public static class OracleStore
{
    public static string DirectoryPath => RepoPaths.Build("oracle");

    private static readonly JsonSerializerOptions options = new()
    {
        WriteIndented = false,
        Converters = { new JsonStringEnumConverter() }
    };

    /// <summary>A stable filename per replay, since replay paths contain characters a path cannot.</summary>
    public static string FileFor(string replayPath) =>
        Path.Combine(DirectoryPath, $"{Convert.ToHexStringLower(System.Security.Cryptography.MD5.HashData(File.ReadAllBytes(replayPath)))}.json");

    public static void Write(OracleRun run)
    {
        Directory.CreateDirectory(DirectoryPath);
        File.WriteAllText(FileFor(run.ReplayPath), JsonSerializer.Serialize(run, options));
    }

    public static OracleRun? Read(string replayPath)
    {
        string path = FileFor(replayPath);

        return File.Exists(path) ? JsonSerializer.Deserialize<OracleRun>(File.ReadAllText(path), options) : null;
    }
}
