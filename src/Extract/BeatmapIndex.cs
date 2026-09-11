using System.Text.Json;
using osu.Framework.Platform;
using osu.Game.Beatmaps;
using osu.Game.Database;
using osu.Game.Extensions;

namespace Extract;

/// <summary>
/// A replay's <c>.osr</c> header identifies its beatmap by MD5, but lazer's file store is
/// addressed by SHA-256. Only lazer's realm database holds the mapping between them, so
/// pairing a replay with its beatmap is an index build, not a search.
/// </summary>
public sealed record IndexEntry(string Md5, string Sha256, string Path, string Artist, string Title, string Difficulty);

public static class BeatmapIndex
{
    /// <summary>
    /// Build the MD5-to-path index by reading a <em>copy</em> of the live realm database.
    ///
    /// The copy is not a convenience. Opening a realm can migrate its schema and osu.Game's
    /// <see cref="RealmAccess"/> also prunes pending deletions on construction, both of
    /// which write. Neither may ever touch the database the user actually plays on.
    /// </summary>
    /// <param name="lazerRoot">The lazer install root, the directory holding client.realm and files/.</param>
    /// <param name="scratchDirectory">Where to place the working copy.</param>
    public static IReadOnlyList<IndexEntry> Build(string lazerRoot, string scratchDirectory)
    {
        string source = Path.Combine(lazerRoot, "client.realm");

        if (!File.Exists(source))
            throw new FileNotFoundException($"No client.realm under {lazerRoot}", source);

        Directory.CreateDirectory(scratchDirectory);
        string working = Path.Combine(scratchDirectory, "client.realm");
        File.Copy(source, working, overwrite: true);

        string filesRoot = Path.Combine(lazerRoot, "files");
        var entries = new List<IndexEntry>();

        // NativeStorage is an osu!framework type but not a host — constructing it starts
        // no threads and loads no native libraries. The HostGuard test asserts this.
        using var realm = new RealmAccess(new NativeStorage(scratchDirectory), "client");

        realm.Run(context =>
        {
            foreach (var beatmap in context.All<BeatmapInfo>())
            {
                if (string.IsNullOrEmpty(beatmap.MD5Hash) || string.IsNullOrEmpty(beatmap.Hash))
                    continue;

                var file = beatmap.File;

                if (file == null)
                    continue;

                entries.Add(new IndexEntry(
                    beatmap.MD5Hash,
                    beatmap.Hash,
                    Path.Combine(filesRoot, file.File.GetStoragePath()),
                    beatmap.Metadata.Artist,
                    beatmap.Metadata.Title,
                    beatmap.DifficultyName));
            }
        });

        return entries;
    }

    public static void Write(IReadOnlyList<IndexEntry> entries, string destination)
    {
        // Last writer wins on duplicate MD5s; identical content means identical beatmap.
        var byMd5 = new Dictionary<string, IndexEntry>(StringComparer.OrdinalIgnoreCase);

        foreach (var entry in entries)
            byMd5[entry.Md5] = entry;

        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);

        using var stream = File.Create(destination);
        JsonSerializer.Serialize(stream, byMd5, new JsonSerializerOptions { WriteIndented = true });
    }

    public static Dictionary<string, IndexEntry> Read(string source)
    {
        using var stream = File.OpenRead(source);

        return JsonSerializer.Deserialize<Dictionary<string, IndexEntry>>(stream)
               ?? throw new InvalidDataException($"Could not read a beatmap index from {source}");
    }
}
