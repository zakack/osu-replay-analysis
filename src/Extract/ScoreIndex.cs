// Copyright (c) ppy Pty Ltd <contact@ppy.sh>. Licensed under the MIT Licence.
// Portions derived from ppy/osu naming conventions; see LICENCE.

using System.Text.Json;
using osu.Framework.Platform;
using osu.Game.Beatmaps;
using osu.Game.Database;
using osu.Game.Extensions;
using osu.Game.Scoring;

namespace Extract;

/// <summary>
/// Lazer keeps a replay for every score it has ever recorded, not only the ones exported by
/// hand. They live in the same content-addressed file store as the beatmaps, and only realm
/// holds the mapping from a score to its file — so reaching them is the same index build as
/// <see cref="BeatmapIndex"/>, pointed at a different table.
///
/// Exporting is a byte copy. <c>LegacyScoreExporter.ExportToStream</c> opens the file store
/// entry and copies it to the destination without re-encoding, so a file written here is
/// identical to one written by the game's own export button. That is what makes bulk export
/// safe to treat as equivalent to the existing corpus rather than as a second format.
/// </summary>
public sealed record ScoreEntry(
    string Id,
    string Hash,
    long OnlineId,
    string Ruleset,
    string Mods,
    string User,
    string Rank,
    bool Passed,
    double Accuracy,
    int MaxCombo,
    long TotalScore,
    double? Pp,
    string ClientVersion,
    string Date,
    string BeatmapHash,
    string BeatmapMd5,
    string Beatmap,
    string Filename,
    string? Path);

public static class ScoreIndex
{
    /// <summary>
    /// Read every score out of a <em>copy</em> of the live realm. The copy is load-bearing for
    /// the same reason it is in <see cref="BeatmapIndex.Build"/>: opening a realm can migrate
    /// its schema and prune pending deletions, both of which write.
    /// </summary>
    public static IReadOnlyList<ScoreEntry> Build(string lazerRoot, string scratchDirectory)
    {
        string source = System.IO.Path.Combine(lazerRoot, "client.realm");

        if (!File.Exists(source))
            throw new FileNotFoundException($"No client.realm under {lazerRoot}", source);

        Directory.CreateDirectory(scratchDirectory);
        string working = System.IO.Path.Combine(scratchDirectory, "client.realm");
        File.Copy(source, working, overwrite: true);

        string filesRoot = System.IO.Path.Combine(lazerRoot, "files");
        var entries = new List<ScoreEntry>();

        using var realm = new RealmAccess(new NativeStorage(scratchDirectory), "client");

        realm.Run(context =>
        {
            foreach (var score in context.All<ScoreInfo>())
            {
                // Soft-deleted scores are still in the table and still have files. The user
                // deleted them; exporting them back out would undo that silently.
                if (score.DeletePending)
                    continue;

                var file = score.Files.FirstOrDefault();

                entries.Add(new ScoreEntry(
                    score.ID.ToString(),
                    score.Hash,
                    score.OnlineID,
                    score.Ruleset.ShortName,
                    acronyms(score.ModsJson),
                    score.User.Username,
                    score.Rank.ToString(),
                    score.Passed,
                    score.Accuracy,
                    score.MaxCombo,
                    score.TotalScore,
                    score.PP,
                    score.ClientVersion,
                    score.Date.UtcDateTime.ToString("o"),
                    score.BeatmapHash,
                    score.BeatmapInfo?.MD5Hash ?? string.Empty,
                    score.BeatmapInfo?.GetDisplayTitle() ?? "unknown",
                    // Reproduces LegacyScoreExporter.GetFilename, so a file written here
                    // collides with one the game wrote for the same score instead of
                    // duplicating it under a different name.
                    $"{score.GetDisplayString()} ({score.Date.LocalDateTime:yyyy-MM-dd_HH-mm})".GetValidFilename(),
                    file == null ? null : System.IO.Path.Combine(filesRoot, file.File.GetStoragePath())));
            }
        });

        return entries;
    }

    /// <summary>
    /// The mod list, as acronyms, without instantiating a ruleset. <c>ScoreInfo.APIMods</c>
    /// would deserialize properly but calls <c>Ruleset.CreateInstance()</c> to do it, which
    /// is more of lazer than this needs to be loading.
    /// </summary>
    private static string acronyms(string modsJson)
    {
        if (string.IsNullOrEmpty(modsJson))
            return string.Empty;

        try
        {
            using var document = JsonDocument.Parse(modsJson);

            return string.Join(string.Empty, document.RootElement
                .EnumerateArray()
                .Select(m => m.TryGetProperty("acronym", out var a) ? a.GetString() : null)
                .Where(a => !string.IsNullOrEmpty(a)));
        }
        catch (JsonException)
        {
            return string.Empty;
        }
    }

    public static void Write(IReadOnlyList<ScoreEntry> entries, string destination)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(destination))!);

        using var stream = File.Create(destination);
        JsonSerializer.Serialize(stream, entries, new JsonSerializerOptions { WriteIndented = true });
    }

    public sealed record ExportResult(int Written, int Skipped, int Missing, long Bytes);

    /// <summary>
    /// Copy each score's replay out of the file store. Never touches the store itself beyond
    /// reading the paths realm named, and never writes to lazer's own exports directory by
    /// default — the corpus seam at 2026-09-11 is a real signal and bulk export would erase it.
    /// </summary>
    public static ExportResult Export(IEnumerable<ScoreEntry> entries, string destination)
    {
        Directory.CreateDirectory(destination);

        int written = 0, skipped = 0, missing = 0;
        long bytes = 0;

        // Two scores can produce the same display string and minute. Lazer's own exporter
        // would overwrite; number the collisions instead, so a bulk run is lossless.
        var used = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var entry in entries)
        {
            if (entry.Path == null || !File.Exists(entry.Path))
            {
                missing++;
                continue;
            }

            string name = entry.Filename;
            string candidate = System.IO.Path.Combine(destination, $"{name}.osr");

            for (int n = 2; !used.Add(candidate); n++)
                candidate = System.IO.Path.Combine(destination, $"{name} ({n}).osr");

            if (File.Exists(candidate))
            {
                skipped++;
                continue;
            }

            File.Copy(entry.Path, candidate);
            written++;
            bytes += new FileInfo(candidate).Length;
        }

        return new ExportResult(written, skipped, missing, bytes);
    }
}
