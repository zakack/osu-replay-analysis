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
///
/// <see cref="Offset"/> is the player's per-beatmap offset as it stands now. It shifts gameplay
/// time and so every recorded hit error on that map, and no score records the value it was
/// played with; realm keeps only the current one. Snapshots of it over time are the only
/// history there is.
///
/// <see cref="StarRating"/> is lazer's own no-mod star rating, as stored in realm by the
/// client's background processor for the difficulty algorithm the client last ran. It is
/// recomputed after every algorithm change, so a value is only comparable with values read at
/// the same time; mod-adjusted ratings are never stored. -1 means not computed yet.
/// </summary>
public sealed record IndexEntry(string Md5, string Sha256, string Path, string Artist, string Title, string Difficulty,
                                double Offset = 0, double StarRating = -1);

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
                    beatmap.DifficultyName,
                    beatmap.UserSettings?.Offset ?? 0,
                    beatmap.StarRating));
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

        var index = JsonSerializer.Deserialize<Dictionary<string, IndexEntry>>(stream)
                    ?? throw new InvalidDataException($"Could not read a beatmap index from {source}");

        Augment(index, MirrorDirectory);
        return index;
    }

    /// <summary>Where beatmaps the library does not hold are kept, named by their own MD5.</summary>
    public const string MirrorDirectory = "build/mirror-beatmaps";

    /// <summary>
    /// Add beatmaps fetched from outside the lazer library, each named <c>&lt;md5&gt;.osu</c>.
    ///
    /// The collected archives reach far past what any one player has imported, and a replay
    /// without its beatmap cannot be extracted at all. Fetched files are folded in here rather
    /// than written into the index file, so that rebuilding the index from realm cannot
    /// silently drop them again.
    ///
    /// Every file is hashed and checked against its own name before it is trusted. A mirror
    /// can serve a later revision of a map under the same id, and a beatmap whose contents
    /// hash to something else is not the beatmap the replay was set on -- a mismatch that
    /// throws nowhere and merely compares a play against the wrong geometry. The library
    /// always wins: an entry already present is never replaced.
    /// </summary>
    public static int Augment(Dictionary<string, IndexEntry> index, string directory)
    {
        if (!Directory.Exists(directory))
            return 0;

        int added = 0;

        foreach (string path in Directory.EnumerateFiles(directory, "*.osu"))
        {
            string md5 = Path.GetFileNameWithoutExtension(path);

            if (index.ContainsKey(md5))
                continue;

            using (var file = File.OpenRead(path))
            {
                if (!Convert.ToHexString(System.Security.Cryptography.MD5.HashData(file))
                            .Equals(md5, StringComparison.OrdinalIgnoreCase))
                    continue;
            }

            index[md5] = new IndexEntry(md5, string.Empty, path, string.Empty, string.Empty, string.Empty);
            added++;
        }

        return added;
    }
}
