using System.Globalization;
using System.IO.Compression;
using System.Text;
using osu.Framework.Platform;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Timing;
using osu.Game.Database;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Osu.Objects;

namespace Extract;

public sealed record ClipSource(string Md5, int OnlineId, string Artist, string Title, string Difficulty, string OsuPath, string AudioName, string AudioPath);

public sealed record ClipResult(string OszPath, string Version, int First, int Last, int WrittenFirst, int WrittenLast, int LeadIn, string LeadInNote, double StartTime, double EndTime);

/// <summary>
/// Cuts a range of a beatmap's objects into its own <c>.osz</c>, for practising in the real
/// game, where mods apply and replays are saved, neither of which the editor's test play allows.
///
/// The cut is a text filter over the source's own <c>[HitObjects]</c> lines, so nothing is
/// re-encoded and lazer still does all the geometry when the clip is imported. Three rules
/// are enforced rather than left to the caller; docs/practice-maps.md says why each exists.
/// </summary>
public static class Clip
{
    /// <summary>
    /// Every beatmap in a <em>copy</em> of realm whose online id is the query, or whose
    /// "artist - title [difficulty]" contains it, ignoring case. The copy is for the reason
    /// <see cref="BeatmapIndex.Build"/> gives: opening a realm can write to it.
    /// </summary>
    public static IReadOnlyList<ClipSource> Find(string lazerRoot, string scratch, string query)
    {
        var found = new List<ClipSource>();
        bool byId = int.TryParse(query, NumberStyles.None, CultureInfo.InvariantCulture, out int id);

        withRealm(lazerRoot, scratch, realm => realm.Run(r =>
        {
            foreach (var b in r.All<BeatmapInfo>())
            {
                if (b.BeatmapSet == null || b.File == null || b.BeatmapSet.DeletePending || b.Ruleset.ShortName != "osu")
                    continue;

                bool match = byId
                    ? b.OnlineID == id
                    : $"{b.Metadata.Artist} - {b.Metadata.Title} [{b.DifficultyName}]".Contains(query, StringComparison.OrdinalIgnoreCase);

                if (match)
                    found.Add(source(lazerRoot, b));
            }
        }));

        return found;
    }

    /// <summary>Look one beatmap up by MD5, with the store paths of its <c>.osu</c> and audio.</summary>
    public static ClipSource Resolve(string lazerRoot, string scratch, string md5)
    {
        ClipSource? found = null;

        withRealm(lazerRoot, scratch, realm => realm.Run(r =>
        {
            var b = r.All<BeatmapInfo>().FirstOrDefault(x => x.MD5Hash == md5)
                    ?? throw new InvalidOperationException($"no beatmap with MD5 {md5} in realm");
            found = source(lazerRoot, b);
        }));

        return found!;
    }

    /// <summary>
    /// The map as runs of constant snap: each maximal stretch of objects whose start-to-start
    /// gaps are the same fraction of a beat. A long stream is one line, which is what makes a
    /// request like "the long stream" answerable without reading every object.
    /// </summary>
    public static void WriteRuns(string osuPath, int minimum, TextWriter output)
    {
        var beatmap = playable(osuPath, []);
        var objects = beatmap.HitObjects.Cast<OsuHitObject>().ToList();

        output.WriteLine($"{objects.Count} objects, {objects[0].StartTime:0}-{objects[^1].GetEndTime():0} ms");

        foreach (var b in beatmap.Breaks)
            output.WriteLine($"break {b.StartTime:0}-{b.EndTime:0} ms");

        output.WriteLine($"runs of {minimum}+ objects at one snap (index range, time, count, snap, bpm, circles/sliders/spinners):");

        // snaps[i] is the gap into object i. Neighbouring runs share their boundary object.
        var snaps = Enumerable.Range(0, objects.Count).Select(i => i == 0 ? string.Empty : snapAt(beatmap, objects, i)).ToArray();
        int start = 0;

        for (int i = 2; i <= objects.Count; i++)
        {
            if (i < objects.Count && snaps[i] == snaps[i - 1])
                continue;

            int end = i - 1;

            if (end - start + 1 >= minimum)
            {
                var run = objects.Skip(start).Take(end - start + 1).ToList();
                double bpm = 60000 / beatmap.ControlPointInfo.TimingPointAt(run[0].StartTime).BeatLength;
                output.WriteLine($"  {start}-{end}  {run[0].StartTime:0}-{run[^1].StartTime:0} ms  {run.Count}  {snaps[start + 1]}  {bpm:0}bpm"
                                 + $"  {run.Count(o => o is HitCircle)}/{run.Count(o => o is Slider)}/{run.Count(o => o is Spinner)}");
            }

            start = end;
        }
    }

    /// <summary>One line per object in a time range, for choosing exact boundaries.</summary>
    public static void WriteObjects(string osuPath, double from, double to, TextWriter output)
    {
        var beatmap = playable(osuPath, []);
        var objects = beatmap.HitObjects.Cast<OsuHitObject>().ToList();

        output.WriteLine("index  time  type  snap-from-previous  x,y  new-combo");

        for (int i = 0; i < objects.Count; i++)
        {
            var o = objects[i];

            if (o.StartTime < from || o.StartTime > to)
                continue;

            string type = o switch { Slider => "slider", Spinner => "spinner", _ => "circle" };
            output.WriteLine($"{i}  {o.StartTime:0}  {type}  {(i > 0 ? snapAt(beatmap, objects, i) : "-")}  {o.Position.X:0},{o.Position.Y:0}  {(o.NewCombo ? "NC" : "")}");
        }
    }

    /// <summary>
    /// Write source objects <paramref name="first"/>..<paramref name="last"/> (indices into the
    /// decoded beatmap) as a new beatmapset, preceded by a lead-in of objects
    /// <paramref name="leadInFrom"/>..first-1 (pass <paramref name="first"/> for none). The
    /// lead-in is played and stacking-checked like the clip, but recorded apart from it.
    /// </summary>
    public static ClipResult Build(ClipSource source, int first, int last, int leadInFrom, string label, string outDir, string scratch)
    {
        var lines = File.ReadAllLines(source.OsuPath);
        int hitObjectsHeader = Array.FindIndex(lines, l => l.Trim() == "[HitObjects]");
        var objectLines = lines.Skip(hitObjectsHeader + 1).Where(l => l.Contains(',')).ToList();

        // The variants under which the clip must place every played object exactly where the
        // source does. Stacking depends on the objects around the cut, and its threshold is
        // preempt x leniency, so the widest and narrowest preempt are checked as well.
        Mod[][] modSets = [[], [new OsuModHardRock()], [new OsuModEasy()]];
        var sources = modSets.Select(m => playable(source.OsuPath, m).HitObjects.Cast<OsuHitObject>().ToList()).ToArray();
        var sourceBeatmap = playable(source.OsuPath, []);
        var objects = sources[0];

        // Indices are into the decoded beatmap and are applied to the file's lines, so the two
        // have to be the same list in the same order.
        if (objects.Count != objectLines.Count)
            throw new InvalidOperationException($"decoded {objects.Count} objects but the file has {objectLines.Count} lines");

        for (int i = 0; i < objects.Count; i++)
        {
            if ((int)objects[i].StartTime != int.Parse(objectLines[i].Split(',')[2], CultureInfo.InvariantCulture))
                throw new InvalidOperationException($"object {i} is out of file order; indices cannot be applied to lines");
        }

        if (first < 0 || last >= objects.Count || first > last)
            throw new ArgumentOutOfRangeException(nameof(first), $"range {first}-{last} is outside 0-{objects.Count - 1}");

        if (leadInFrom < 0 || leadInFrom > first)
            throw new ArgumentOutOfRangeException(nameof(leadInFrom), $"lead-in start {leadInFrom} must be between 0 and {first}");

        int leadInFirst = leadInFrom;
        string leadInNote = describeLeadIn(sourceBeatmap, objects, leadInFirst, first);

        // Every object the player plays must sit where it does in the source. Pad outward with
        // objects beyond the lead-in until it does; the source decode is the oracle.
        int writtenFirst = leadInFirst, writtenLast = last;
        // Names the whole written range, so clips of the same objects with different lead-ins
        // stay distinguishable in song select.
        string version = $"{source.Difficulty} - clip {first}-{last}" + (leadInFirst < first ? $" from {leadInFirst}" : "")
                         + (string.IsNullOrWhiteSpace(label) ? "" : $" {label.Trim()}");
        string fileName = sanitise($"{source.Artist} - {source.Title} ({creator(lines)}) [{version}].osu");
        string clipPath = Path.Combine(scratch, fileName);

        while (true)
        {
            File.WriteAllText(clipPath, compose(lines, hitObjectsHeader, objectLines, objects, sourceBeatmap.Breaks, source.Md5, version,
                first, last, writtenFirst, writtenLast));

            if (mismatches(clipPath, modSets, sources, leadInFirst, last, writtenFirst) == 0)
                break;

            if (writtenFirst == 0 && writtenLast == objects.Count - 1)
                throw new InvalidOperationException("no padding reproduces the source's stacking");

            writtenFirst = Math.Max(0, writtenFirst - 1);
            writtenLast = Math.Min(objects.Count - 1, writtenLast + 1);
        }

        // Rule 1, checked on what lazer actually decodes: a set id shared with the source makes
        // lazer delete the source from the player's library on import.
        var check = new FlatWorkingBeatmap(clipPath).BeatmapInfo;
        if (check.OnlineID > 0 || (check.BeatmapSet?.OnlineID ?? -1) > 0)
            throw new InvalidOperationException("the clip still carries an online id");

        Directory.CreateDirectory(outDir);
        string osz = Path.GetFullPath(Path.Combine(outDir, Path.ChangeExtension(fileName, ".osz")));
        File.Delete(osz);

        using (var zip = ZipFile.Open(osz, ZipArchiveMode.Create))
        {
            zip.CreateEntryFromFile(clipPath, fileName);
            zip.CreateEntryFromFile(source.AudioPath, source.AudioName);
        }

        return new ClipResult(osz, version, first, last, writtenFirst, writtenLast, first - leadInFirst, leadInNote,
            objects[writtenFirst].StartTime, objects[writtenLast].GetEndTime());
    }

    /// <summary>
    /// Describe a lead-in starting at object <paramref name="from"/>, and flag a break inside
    /// it, since nothing before a break leads into what follows. Choosing the lead-in is left
    /// to the caller.
    /// </summary>
    private static string describeLeadIn(IBeatmap beatmap, List<OsuHitObject> objects, int from, int first)
    {
        if (from == first)
            return "none";

        var timing = beatmap.ControlPointInfo.TimingPointAt(objects[first].StartTime);
        double bars = (objects[first].StartTime - objects[from].StartTime) / (timing.TimeSignature.Numerator * timing.BeatLength);
        string note = $"{first - from} objects from {objects[from].StartTime:0} ms ({bars:0.#} bars)";

        if (beatmap.Breaks.Any(b => b.StartTime >= objects[from].StartTime && b.EndTime <= objects[first].StartTime))
            note += "; WARNING: a break lies inside the lead-in";

        return note;
    }

    private static string compose(string[] lines, int hitObjectsHeader, List<string> objectLines, List<OsuHitObject> objects,
                                  IEnumerable<BreakPeriod> breaks, string md5, string version, int first, int last, int writtenFirst, int writtenLast)
    {
        // Targets and the written range are both recorded, so the analysis can tell the clip's
        // objects from its lead-in and padding when it pools plays with the source's.
        string tag = $"clip:{md5}:{first}-{last}:{writtenFirst}-{writtenLast}:0";
        double from = objects[writtenFirst].StartTime, to = objects[writtenLast].GetEndTime();
        var output = new StringBuilder();
        string section = string.Empty;

        foreach (string line in lines.Take(hitObjectsHeader))
        {
            string trimmed = line.Trim();

            if (trimmed.StartsWith('[') && trimmed.EndsWith(']'))
            {
                section = trimmed;
                output.Append(line).Append("\r\n");

                // [Events] is rebuilt from scratch: the source's background, video and
                // storyboard reference files the clip does not carry, and a storyboard event in
                // negative time would move where playback starts.
                if (section == "[Events]")
                {
                    foreach (var b in breaks.Where(b => b.StartTime >= from && b.EndTime <= to))
                        output.Append(FormattableString.Invariant($"2,{b.StartTime:0},{b.EndTime:0}")).Append("\r\n");
                }

                continue;
            }

            if (section == "[Events]" && trimmed.Length > 0)
                continue;

            // Rule 1: without an online id, the import cannot be taken for the source's set.
            if (trimmed.StartsWith("BeatmapID:") || trimmed.StartsWith("BeatmapSetID:"))
                continue;

            // Rule 2: a distinct difficulty name, which also gives the file a distinct name, so
            // the metadata lookup cannot match the clip to the ranked map by filename.
            if (trimmed.StartsWith("Version:"))
            {
                output.Append("Version:").Append(version).Append("\r\n");
                continue;
            }

            // Rule 3: provenance travels inside the file, because every index here keys on MD5.
            if (trimmed.StartsWith("Tags:"))
            {
                output.Append(line.TrimEnd()).Append(" practice ").Append(tag).Append("\r\n");
                continue;
            }

            output.Append(line).Append("\r\n");
        }

        output.Append("[HitObjects]\r\n");

        foreach (string line in objectLines.Skip(writtenFirst).Take(writtenLast - writtenFirst + 1))
            output.Append(line).Append("\r\n");

        return output.ToString();
    }

    private static int mismatches(string clipPath, Mod[][] modSets, List<OsuHitObject>[] sources, int playedFirst, int playedLast, int writtenFirst)
    {
        int bad = 0;

        for (int m = 0; m < modSets.Length; m++)
        {
            var clip = playable(clipPath, modSets[m]).HitObjects.Cast<OsuHitObject>().ToList();

            for (int i = playedFirst; i <= playedLast; i++)
            {
                var c = clip[i - writtenFirst];
                var s = sources[m][i];

                if (c.StartTime != s.StartTime || (c.StackedPosition - s.StackedPosition).Length > 1e-3)
                    bad++;
            }
        }

        return bad;
    }

    private static string snapAt(IBeatmap beatmap, List<OsuHitObject> objects, int i)
    {
        double gap = objects[i].StartTime - objects[i - 1].StartTime;
        double beat = beatmap.ControlPointInfo.TimingPointAt(objects[i].StartTime).BeatLength;

        foreach (int d in new[] { 1, 2, 3, 4, 6, 8, 12, 16 })
        {
            double n = Math.Round(gap / beat * d);

            if (n > 0 && Math.Abs(gap - n * beat / d) < 3)
            {
                int g = gcd((int)n, d);
                return $"{(int)n / g}/{d / g}";
            }
        }

        return $"{gap:0}ms";
    }

    private static int gcd(int a, int b) => b == 0 ? a : gcd(b, a % b);

    private static IBeatmap playable(string path, Mod[] mods) =>
        new FlatWorkingBeatmap(path).GetPlayableBeatmap(new OsuRuleset().RulesetInfo, mods);

    private static string creator(string[] lines) =>
        lines.FirstOrDefault(l => l.StartsWith("Creator:"))?[8..].Trim() ?? "unknown";

    private static string sanitise(string name) => string.Concat(name.Split(Path.GetInvalidFileNameChars()));

    private static ClipSource source(string lazerRoot, BeatmapInfo b)
    {
        string store(string hash) => Path.Combine(lazerRoot, "files", hash[..1], hash[..2], hash);
        var audio = b.BeatmapSet!.Files.FirstOrDefault(f => f.Filename.Equals(b.Metadata.AudioFile, StringComparison.OrdinalIgnoreCase));

        return new ClipSource(b.MD5Hash, b.OnlineID, b.Metadata.Artist, b.Metadata.Title, b.DifficultyName,
            store(b.File!.File.Hash), b.Metadata.AudioFile, audio == null ? string.Empty : store(audio.File.Hash));
    }

    private static void withRealm(string lazerRoot, string scratch, Action<RealmAccess> action)
    {
        Directory.CreateDirectory(scratch);
        File.Copy(Path.Combine(lazerRoot, "client.realm"), Path.Combine(scratch, "client.realm"), overwrite: true);

        // NativeStorage is not a host; see BeatmapIndex.Build.
        using var realm = new RealmAccess(new NativeStorage(scratch), "client");
        action(realm);
    }
}
