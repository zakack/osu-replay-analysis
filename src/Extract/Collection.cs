using System.Text.RegularExpressions;
using osu.Game.IO.Legacy;

namespace Extract;

/// <summary>
/// Writes a named list of beatmap MD5s as a stable-format <c>collection.db</c>, inside a
/// folder lazer accepts as a stable install. Lazer's collections are keyed on beatmap MD5,
/// the same key as every query here, so any set of maps the analysis selects is one import
/// away from being a playlist in the song select.
///
/// There is no file-drop handler for <c>collection.db</c>. The only importer is
/// <c>LegacyCollectionImporter</c>, reached through the stable import, which wants a folder
/// holding <c>osu!.*.cfg</c>, <c>Songs</c> or <c>Skins</c>; an empty <c>Songs</c> is the
/// smallest folder that qualifies. The import merges into a collection of the same name
/// rather than replacing it, so it can add maps and never remove them.
/// </summary>
public static partial class Collection
{
    /// <summary>Stable writes its build date here. Lazer reads it and discards it.</summary>
    private const int version = 20150203;

    [GeneratedRegex("(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")]
    private static partial Regex md5Pattern();

    /// <summary>
    /// Every MD5 in free text, lower-cased, de-duplicated, in order of first appearance, so
    /// the output of any command that prints MD5s can be piped in without reshaping it.
    /// </summary>
    public static IReadOnlyList<string> Md5sIn(string text) =>
        md5Pattern().Matches(text).Select(m => m.Value.ToLowerInvariant()).Distinct().ToList();

    /// <summary>
    /// Replace <paramref name="root"/>'s <c>collection.db</c> with one collection, and make
    /// sure the folder qualifies as a stable install. Returns the path of the database.
    /// </summary>
    public static string Write(string root, string name, IReadOnlyList<string> md5s) =>
        Write(root, [(name, md5s)]);

    /// <summary>
    /// Replace <paramref name="root"/>'s <c>collection.db</c> with several collections at
    /// once. The format carries a count of collections, so a week-by-week split is still one
    /// import rather than one trip through the setup wizard per week.
    /// </summary>
    public static string Write(string root, IReadOnlyList<(string Name, IReadOnlyList<string> Md5s)> collections)
    {
        Directory.CreateDirectory(Path.Combine(root, "Songs"));
        string path = Path.Combine(root, "collection.db");

        using (var stream = File.Create(path))
        using (var writer = new SerializationWriter(stream))
        {
            // The mirror of LegacyCollectionImporter.readCollections.
            writer.Write(version);
            writer.Write(collections.Count);

            foreach (var (name, md5s) in collections)
            {
                writer.Write(name);
                writer.Write(md5s.Count);

                foreach (string md5 in md5s)
                    writer.Write(md5);
            }
        }

        return path;
    }

    /// <summary>
    /// Collections from lines of <c>md5 TAB name</c>, in order of each name's first line and
    /// each MD5's first appearance within it. Lines without a tab or an MD5 are skipped.
    /// </summary>
    public static IReadOnlyList<(string Name, IReadOnlyList<string> Md5s)> GroupsIn(string text)
    {
        var order = new List<string>();
        var groups = new Dictionary<string, List<string>>();

        foreach (string line in text.Split('\n'))
        {
            int tab = line.IndexOf('\t');
            if (tab < 0)
                continue;

            var md5 = Md5sIn(line[..tab]);
            string name = line[(tab + 1)..].Trim();
            if (md5.Count == 0 || name.Length == 0)
                continue;

            if (!groups.TryGetValue(name, out var list))
            {
                groups[name] = list = [];
                order.Add(name);
            }

            if (!list.Contains(md5[0]))
                list.Add(md5[0]);
        }

        return order.Select(n => (n, (IReadOnlyList<string>)groups[n])).ToList();
    }
}
