using System.Text.Json;
using System.Text.Json.Serialization;

namespace Oracle;

/// <summary>
/// One judgement as the real game produced it, reduced to what a diff needs: which object,
/// and what it was awarded.
/// </summary>
public sealed record OracleJudgement(string ObjectType, double StartTime, string Result);

public sealed record OracleRun(string ReplayPath, string BeatmapPath, IReadOnlyList<OracleJudgement> Judgements);

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
