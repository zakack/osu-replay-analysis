using System.Security.Cryptography;
using System.Text.Json;

namespace Extract;

public sealed record ReplayRecord(
    string Path,
    string ContentHash,
    int RulesetId,
    int Version,
    string BeatmapMd5,
    bool Paired,
    string? BeatmapPath,
    string? DecodeError);

public sealed record SurveyReport(IReadOnlyList<ReplayRecord> Replays)
{
    /// <summary>
    /// One entry per distinct replay. The export directories overlap heavily and the same
    /// score is often exported more than once, so counts over files overstate the corpus.
    /// </summary>
    public IReadOnlyList<ReplayRecord> Distinct { get; } =
        Replays.GroupBy(r => r.ContentHash).Select(g => g.First()).ToArray();
}

public static class CorpusSurvey
{
    public static SurveyReport Run(IReadOnlyList<string> directories, IReadOnlyDictionary<string, IndexEntry> index)
    {
        var records = new List<ReplayRecord>();

        foreach (string given in directories)
        {
            if (!Directory.Exists(given))
                continue;

            // Absolute, always. EnumerateFiles hands back paths shaped like the one it was
            // given, so surveying "build/replays" records relative paths -- which resolve
            // against whatever process reads the corpus later. The oracle runs from its own
            // bin directory, so a relative corpus silently produces a file-not-found on every
            // target rather than anything that looks like a path problem.
            string directory = Path.GetFullPath(given);

            foreach (string path in Directory.EnumerateFiles(directory, "*.osr", SearchOption.TopDirectoryOnly).Order(StringComparer.Ordinal))
                records.Add(inspect(path, index));
        }

        return new SurveyReport(records);
    }

    private static ReplayRecord inspect(string path, IReadOnlyDictionary<string, IndexEntry> index)
    {
        string contentHash = Convert.ToHexStringLower(MD5.HashData(File.ReadAllBytes(path)));

        ReplayHeader header;

        try
        {
            header = ReplayLoader.ReadHeader(path);
        }
        catch (Exception e)
        {
            return new ReplayRecord(path, contentHash, -1, -1, string.Empty, false, null, $"header: {e.Message}");
        }

        bool paired = index.TryGetValue(header.BeatmapMd5, out var entry);

        // Only osu!standard is in scope, and a full decode needs the beatmap, so anything
        // unpaired is recorded and skipped rather than attempted.
        if (header.RulesetId != 0 || !paired)
            return new ReplayRecord(path, contentHash, header.RulesetId, header.Version, header.BeatmapMd5, paired, entry?.Path, null);

        string? decodeError = null;

        try
        {
            ReplayLoader.Decode(path, index);
        }
        catch (Exception e)
        {
            decodeError = $"{e.GetType().Name}: {e.Message}";
        }

        return new ReplayRecord(path, contentHash, header.RulesetId, header.Version, header.BeatmapMd5, true, entry!.Path, decodeError);
    }

    public static void Print(SurveyReport report, TextWriter output)
    {
        var distinct = report.Distinct;

        output.WriteLine($"replay files      {report.Replays.Count}");
        output.WriteLine($"distinct replays  {distinct.Count}");
        output.WriteLine($"osu!standard      {distinct.Count(r => r.RulesetId == 0)}");
        output.WriteLine($"lazer format      {distinct.Count(r => r.Version >= 30000000)}");
        output.WriteLine($"paired to beatmap {distinct.Count(r => r.Paired)}");

        var attempted = distinct.Where(r => r is { RulesetId: 0, Paired: true }).ToArray();
        var failed = attempted.Where(r => r.DecodeError != null).ToArray();

        output.WriteLine($"decoded           {attempted.Length - failed.Length} of {attempted.Length}");

        if (failed.Length > 0)
        {
            output.WriteLine();
            output.WriteLine("decode failures by cause:");

            foreach (var group in failed.GroupBy(r => r.DecodeError!).OrderByDescending(g => g.Count()))
                output.WriteLine($"  {group.Count(),5}  {group.Key}");
        }

        var unpaired = distinct.Where(r => !r.Paired).ToArray();

        if (unpaired.Length > 0)
        {
            output.WriteLine();
            output.WriteLine($"unpaired replays: {unpaired.Length}");

            foreach (var record in unpaired.Take(10))
                output.WriteLine($"  {record.BeatmapMd5}  {Path.GetFileName(record.Path)}");
        }
    }

    public static IReadOnlyList<ReplayRecord> ReadRecords(string source)
    {
        using var stream = File.OpenRead(source);

        return JsonSerializer.Deserialize<List<ReplayRecord>>(stream)
               ?? throw new InvalidDataException($"Could not read a corpus survey from {source}");
    }

    public static void Write(SurveyReport report, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);

        using var stream = File.Create(destination);
        JsonSerializer.Serialize(stream, report.Distinct, new JsonSerializerOptions { WriteIndented = true });
    }
}
