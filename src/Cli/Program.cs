using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
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
