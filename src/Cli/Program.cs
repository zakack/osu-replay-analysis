using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;
using Sim;

if (args.Length == 0)
{
    Console.Error.WriteLine("usage: ora <command> [args]");
    Console.Error.WriteLine("  smoke <beatmap.osu>   load a beatmap to playable form and report the host-guard result");
    Console.Error.WriteLine("  index [lazer-root]    build the beatmap MD5 index from a copy of lazer's realm");
    Console.Error.WriteLine("  survey <dir>...       triage a replay corpus: format, ruleset, pairing, decodability");
    Console.Error.WriteLine("  verify                simulate the corpus and report the match rate and mismatch taxonomy");
    Console.Error.WriteLine("  oracle-list [n]       pick mismatching replays for the differential oracle to record");
    Console.Error.WriteLine("  oracle-diff           diff the simulation against the oracle's recordings, object by object");
    Console.Error.WriteLine("  trace <replay> <ms>   dump per-sample slider tracking state around a time");
    Console.Error.WriteLine("  offsets <replay>      per click-judged object: result, hit offset, distance to the great edge");
    Console.Error.WriteLine("  sweep [ms]...         shift every replay frame time and report the drift in click judgements");
    return 2;
}

switch (args[0])
{
    case "smoke":
        if (args.Length < 2)
        {
            Console.Error.WriteLine("smoke: expected a path to a .osu file");
            return 2;
        }

        var (objects, firstStackedX) = Smoke.Load(args[1]);
        Console.WriteLine($"hit objects: {objects}");
        Console.WriteLine($"first stacked x: {firstStackedX}");

        if (!HostGuard.IsSupported)
        {
            Console.WriteLine("host guard: not checked (no /proc/self/maps)");
            return 0;
        }

        var loaded = HostGuard.LoadedHostLibraries();
        Console.WriteLine(loaded.Count == 0
            ? "host guard: clean, no host libraries mapped"
            : $"host guard: FAILED, mapped {string.Join(", ", loaded)}");
        return loaded.Count == 0 ? 0 : 1;

    case "index":
    {
        string lazerRoot = args.Length > 1
            ? args[1]
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "osu-lazer");

        string scratch = Path.Combine("build", "realm-copy");

        var entries = BeatmapIndex.Build(lazerRoot, scratch);
        BeatmapIndex.Write(entries, "build/beatmap-index.json");

        int present = entries.Count(e => File.Exists(e.Path));
        Console.WriteLine($"indexed beatmaps: {entries.Count}");
        Console.WriteLine($"files present on disk: {present}");
        Console.WriteLine("written: build/beatmap-index.json");

        if (HostGuard.IsSupported)
        {
            var mapped = HostGuard.LoadedHostLibraries();
            Console.WriteLine(mapped.Count == 0
                ? "host guard: clean, no host libraries mapped"
                : $"host guard: FAILED, mapped {string.Join(", ", mapped)}");
        }

        return 0;
    }

    case "survey":
    {
        var directories = args.Skip(1).ToArray();

        if (directories.Length == 0)
        {
            string lazer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "osu-lazer");
            directories = [Path.Combine(lazer, "exports"), Path.Combine(lazer, "exports_backup")];
        }

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var report = CorpusSurvey.Run(directories, index);
        CorpusSurvey.Print(report, Console.Out);
        CorpusSurvey.Write(report, "build/corpus.json");
        Console.WriteLine("written: build/corpus.json");
        return 0;
    }

    case "verify":
    {
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords("build/corpus.json");

        var eligible = corpus.Where(r => r is { RulesetId: 0, Paired: true, DecodeError: null }).ToArray();
        var results = new List<VerificationResult>(eligible.Length);

        foreach (var record in eligible)
            results.Add(Verification.Verify(record, index));

        Report.Print(results, Console.Out);
        Report.Write(results, "build/verification.json");
        Console.WriteLine("written: build/verification.json");
        return 0;
    }

    case "offsets":
    {
        // Per click-judged object: what it was, when it was due, what it got, and by how
        // much it was early or late. The point is to see where the judgements that disagree
        // with the header sit relative to a hit window edge.
        if (args.Length < 2)
        {
            Console.Error.WriteLine("offsets: expected a replay path");
            return 2;
        }

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var score = ReplayLoader.Decode(args[1], index);
        var header = ReplayLoader.ReadHeader(args[1]);
        var playable = new FlatWorkingBeatmap(index[header.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        // A spinner carries empty hit windows, so take them from a circle.
        var windows = playable.HitObjects.OfType<HitCircle>().First().HitWindows;

        Console.WriteLine($"# great <= {windows.WindowFor(HitResult.Great):0.###}  "
                          + $"ok <= {windows.WindowFor(HitResult.Ok):0.###}  "
                          + $"meh <= {windows.WindowFor(HitResult.Meh):0.###}");
        Console.WriteLine("type,startTime,result,offset,distanceToGreatEdge");

        double great = windows.WindowFor(HitResult.Great);

        foreach (var state in new Simulator().Run(playable, score).Objects)
        {
            if (state.HitObject is not HitCircle || state.HitObject is SliderEndCircle || state.TimeOffset is not { } offset)
                continue;

            Console.WriteLine($"{state.HitObject.GetType().Name},{state.HitObject.StartTime:0.###},"
                              + $"{state.Result},{offset:0.####},{Math.Abs(offset) - great:0.####}");
        }

        return 0;
    }

    case "sweep":
    {
        // Only the replays the oracle recorded: they were sampled across the mismatch
        // distribution already, and they are the set where the live game's own answer is
        // also on disk, so a shift that helps here can be checked against lazer rather than
        // only against the header.
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords("build/corpus.json");
        var recorded = OracleDiff.RecordedReplayPaths("build/oracle").ToHashSet();

        var subset = corpus.Where(r => r is { RulesetId: 0, Paired: true, DecodeError: null })
                           .Where(r => recorded.Contains(r.Path))
                           .ToArray();

        double[] shifts = args.Length > 1
            ? args.Skip(1).Select(double.Parse).ToArray()
            : [-1, -0.5, -0.25, 0, 0.25, 0.5, 1];

        ShiftSweep.Run(subset, index, shifts, Console.Out);
        return 0;
    }

    case "oracle-list":
    {
        int limit = args.Length > 1 ? int.Parse(args[1]) : 8;

        var results = OracleDiff.ReadVerification("build/verification.json");
        var corpus = CorpusSurvey.ReadRecords("build/corpus.json");
        var targets = OracleDiff.ChooseTargets(results, corpus, limit);

        OracleDiff.WriteTargets(targets, "build/oracle-targets.json");

        foreach (var target in targets)
            Console.WriteLine($"  {Path.GetFileName(target.ReplayPath)}");

        Console.WriteLine($"written: build/oracle-targets.json ({targets.Count} targets)");
        return 0;
    }

    case "oracle-diff":
    {
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var targets = OracleDiff.ReadTargets("build/oracle-targets.json");

        OracleDiff.Run(targets, index, Console.Out);
        return 0;
    }

    case "trace":
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("trace: expected a replay path and a time in milliseconds");
            return 2;
        }

        double around = double.Parse(args[2]);
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var score = ReplayLoader.Decode(args[1], index);
        var header = ReplayLoader.ReadHeader(args[1]);
        var playable = new FlatWorkingBeatmap(index[header.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        var lines = new List<string>();
        var simulator = new Simulator { TraceAround = (around, lines.Add) };
        simulator.Run(playable, score);

        foreach (string line in lines.Where(l => l.StartsWith('#')
                                                 || Math.Abs(double.Parse(l.Split('\u0020', StringSplitOptions.RemoveEmptyEntries)[0]) - around) <= 400))
            Console.WriteLine(line);

        return 0;
    }

    default:
        Console.Error.WriteLine($"unknown command: {args[0]}");
        return 2;
}
