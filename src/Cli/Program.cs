using System.Globalization;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;
using Sim;

#if DEBUG
// A solution build ignores Directory.Build.props' Release default, so `dotnet build` at the
// repository root leaves bin/Release untouched and the next run silently uses the previous
// build. Say so, rather than letting it look like an edit that did not take.
Console.Error.WriteLine("warning: this is a Debug build. Batch commands are much slower, and "
                        + "a root `dotnet build` does not update bin/Release. Use -c Release.");
#endif

if (args.Length == 0)
{
    Console.Error.WriteLine("usage: ora <command> [args]");
    Console.Error.WriteLine("  smoke <beatmap.osu>   load a beatmap to playable form and report the host-guard result");
    Console.Error.WriteLine("  index [lazer-root]    build the beatmap MD5 index from a copy of lazer's realm");
    Console.Error.WriteLine("  survey [--out p] <dir>...  triage a replay corpus: format, ruleset, pairing, decodability");
    Console.Error.WriteLine("  verify                simulate the corpus and report the match rate and mismatch taxonomy");
    Console.Error.WriteLine("  verify-classic        the same, but simulate Classic scores too, to measure what not porting it costs");
    Console.Error.WriteLine("  oracle-list [n]       pick mismatching replays for the differential oracle to record");
    Console.Error.WriteLine("  oracle-diff           diff the simulation against the oracle's recordings, object by object");
    Console.Error.WriteLine("  trace <replay> <ms>   dump per-sample slider tracking state around a time");
    Console.Error.WriteLine("  versions              print the lazer build that wrote each score in the corpus");
    Console.Error.WriteLine("  offsets <replay>      per click-judged object: result, hit offset, distance to the great edge");
    Console.Error.WriteLine("  flam <replay> [out]   build a self-contained page that clicks the map and your taps");
    Console.Error.WriteLine("  scene <replay> [out]  emit the viewer's input: slider polylines, cursor frames, judgements");
    Console.Error.WriteLine("  rhythm [gap] [tol]... extract rhythmic groups and constant-snap runs across the corpus");
    Console.Error.WriteLine("  geometry              extract per-object geometry, cross-checking the angle against lazer");
    Console.Error.WriteLine("    these, and verify, take --corpus <path> and --out <path>");
    Console.Error.WriteLine("  tail-sweep [ms]...    trim the end off replays that stopped early and report the drift");
    Console.Error.WriteLine("  sweep [ms]...         shift every replay frame time and report the drift in click judgements");
    return 2;
}

// Shared by the extractors: which corpus to read and where to write. The reference set is a
// second corpus, not an addition to the first, and mixing them in one file would put other
// people's replays into every local result by default.
static (string Corpus, string Out) paths(string[] args, string fallbackOut)
{
    string corpus = "build/corpus.json";
    string destination = fallbackOut;

    for (int i = 1; i < args.Length - 1; i++)
    {
        if (args[i] == "--corpus") corpus = args[i + 1];
        if (args[i] == "--out") destination = args[i + 1];
    }

    return (corpus, destination);
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
        // Surveying a subdirectory used to overwrite the main corpus, which is a quiet way
        // to lose it: the next command reads an empty map list and reports having done
        // nothing rather than failing.
        string destination = "build/corpus.json";
        var rest = args.Skip(1).ToList();
        int flag = rest.IndexOf("--out");

        if (flag >= 0)
        {
            if (flag + 1 >= rest.Count)
            {
                Console.Error.WriteLine("survey: --out expects a path");
                return 2;
            }

            destination = rest[flag + 1];
            rest.RemoveRange(flag, 2);
        }

        var directories = rest.ToArray();

        if (directories.Length == 0)
        {
            string lazer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "osu-lazer");
            directories = [Path.Combine(lazer, "exports"), Path.Combine(lazer, "exports_backup")];
        }

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var report = CorpusSurvey.Run(directories, index);
        CorpusSurvey.Print(report, Console.Out);
        CorpusSurvey.Write(report, destination);
        Console.WriteLine($"written: {destination}");
        return 0;
    }

    case "verify-classic":
    case "verify":
    {
        Verification.IncludeClassic = args[0] == "verify-classic";

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var (corpusPath, verificationOut) = paths(args, "build/verification.json");
        var corpus = CorpusSurvey.ReadRecords(corpusPath);

        var eligible = corpus.Where(r => r is { RulesetId: 0, Paired: true, DecodeError: null }).ToArray();
        var results = new List<VerificationResult>(eligible.Length);

        foreach (var record in eligible)
            results.Add(Verification.Verify(record, index));

        Report.Print(results, Console.Out);
        Report.Write(results, verificationOut);
        Console.WriteLine($"written: {verificationOut}");
        return 0;
    }

    case "versions":
    {
        // Which lazer built each score. Hit windows were floored to half-integers on
        // 2025-04-18 (ppy/osu 0f078ee550), so a score's client build decides which rules
        // its header was written under, and comparing against one reference ruleset makes
        // that a source of disagreement all by itself.
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords("build/corpus.json");

        Console.WriteLine("clientVersion,replayPath");

        foreach (var record in corpus.Where(r => r is { RulesetId: 0, Paired: true, DecodeError: null }))
        {
            string version;

            try
            {
                version = ReplayLoader.Decode(record.Path, index).ScoreInfo.ClientVersion;
            }
            catch (Exception e)
            {
                version = $"error:{e.GetType().Name}";
            }

            Console.WriteLine($"{(string.IsNullOrEmpty(version) ? "unknown" : version)},{record.Path}");
        }

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

    case "flam":
    {
        // Two clicks per object: one where the object was, one where it was hit. A constant
        // offset in reconstructed hit times is inaudible in a table and unmistakable as an
        // echo, and everything downstream is built on those times.
        if (args.Length < 2)
        {
            Console.Error.WriteLine("flam: expected a replay path");
            return 2;
        }

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var score = ReplayLoader.Decode(args[1], index);
        var header = ReplayLoader.ReadHeader(args[1]);
        var playable = new FlatWorkingBeatmap(index[header.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        var track = Flam.Build(args[1], playable, score);
        string destination = args.Length > 2
            ? args[2]
            : Path.Combine("build", "flam", Path.GetFileNameWithoutExtension(args[1]) + ".html");

        Flam.Write(track, Path.Combine("tools", "flam", "template.html"), destination);

        Console.WriteLine(Flam.Summarise(track));
        Console.WriteLine($"written: {destination}");
        return 0;
    }

    case "scene":
    {
        // Everything the viewer needs, precomputed. The slider polylines are the reason this
        // exists: lazer's piecewise-linear approximation *is* the path for every gameplay
        // purpose, and a viewer that fitted its own curves would disagree with the game
        // slightly, everywhere, without ever throwing.
        if (args.Length < 2)
        {
            Console.Error.WriteLine("scene: expected a replay path");
            return 2;
        }

        // How far a Catmull-optimised path is allowed to be out before it stops counting as
        // the known residual. Measured at 0.29px on the one affected slider in 8,466; a pixel
        // leaves room for a longer path to accumulate more of the same without leaving room
        // for a different bug to hide behind the same exemption.
        const double catmull_residual_limit = 1.0;

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var score = ReplayLoader.Decode(args[1], index);
        var header = ReplayLoader.ReadHeader(args[1]);
        var playable = new FlatWorkingBeatmap(index[header.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        // The polylines and frames are unaffected by the mod, but the judgements are not:
        // Classic swaps in the legacy hit policy and changes slider head and tail rules, none
        // of which is ported — which is why `verify` puts Classic scores out of scope rather
        // than counting them. Say so, instead of colouring a trail from results the player
        // never saw.
        if (score.ScoreInfo.Mods.Any(m => m is OsuModClassic))
        {
            Console.Error.WriteLine("warning: this score is Classic. Slider paths and cursor frames are unaffected, "
                                    + "but the per-object judgements come from the lazer-strict rules this project "
                                    + "ports, not the ones it was played under.");
        }

        var document = Scene.Build(args[1], playable, score, header.BeatmapMd5);
        string destination = args.Length > 2
            ? args[2]
            : Path.Combine("build", "scene", Path.GetFileNameWithoutExtension(args[1]) + ".json");

        Scene.Write(document, destination);

        // The one piece of arithmetic left to the viewer, checked against lazer before any
        // of it is written. Ticks and repeats carry both a path progress and the position
        // lazer placed them at, so walking the emitted polyline to that progress has an
        // oracle for free.
        var check = Scene.CrossCheck(document);

        Console.WriteLine(Scene.Summarise(document, new FileInfo(destination).Length));
        Console.WriteLine($"path walk cross-check: {check.Checked - check.Diverged} of {check.Checked} nested positions reproduced"
                          + (check.Diverged > 0 ? $", worst divergence {check.Worst:0.###} px" : string.Empty));

        // A check that compared nothing is not a check that passed. Every slider now carries
        // at least a head and a tail to walk to, so zero comparisons on a document with
        // sliders means the oracle stopped being wired up, not that there was nothing to ask.
        // Both buckets count: on a map whose sliders are all Catmull every comparison lands
        // in the bounded one, and that is a check that ran, not a check that was skipped.
        if (check.Checked + check.OptimisedChecked == 0 && document.Objects.Any(o => o.Path != null))
        {
            Console.Error.WriteLine("scene: the path walk cross-check compared nothing on a document that has sliders. "
                                    + "Refusing to report a pass it did not earn.");
            return 1;
        }

        // Not a failure: lazer drops vertices from an optimised Catmull path and counts their
        // length anyway, so the walk cannot land exactly. Reported so the residual stays
        // visible and bounded instead of being absorbed into a wider tolerance.
        if (check.OptimisedSliders > 0)
        {
            Console.WriteLine($"  plus {check.OptimisedChecked} on {check.OptimisedSliders} Catmull-optimised slider(s), "
                              + $"within {check.OptimisedWorst:0.###} px");
        }

        // Excluded is not unbounded. The known residual is a third of a pixel; a Catmull path
        // drifting by more than a follow circle's hundredth is a different problem wearing
        // the same exemption, and it should stop the run like any other divergence.
        if (check.OptimisedWorst > catmull_residual_limit)
        {
            Console.Error.WriteLine($"scene: a Catmull-optimised slider is out by {check.OptimisedWorst:0.###} px, "
                                    + $"past the {catmull_residual_limit:0.#} px this exemption covers.");
            return 1;
        }

        Console.WriteLine($"written: {destination}");
        return check.Diverged > 0 ? 1 : 0;
    }

    case "rhythm":
    {
        // Rhythmic structure across the corpus, with no geometry in it. Groups are gestures
        // and runs are constant-snap stretches inside them; both thresholds are arguments
        // because they decide how long each gets to be, and every number downstream inherits
        // that, so run more than one value and keep what survives all of them.
        var (corpusPath, rhythmOut) = paths(args, "build/rhythm.csv");
        var numbers = args.Skip(1).Where(a => double.TryParse(a, NumberStyles.Float, CultureInfo.InvariantCulture, out _))
                          .Select(a => double.Parse(a, CultureInfo.InvariantCulture)).ToArray();

        double groupGap = numbers.Length > 0 ? numbers[0] : Rhythm.DefaultGroupGap;
        double[] tolerances = numbers.Length > 1 ? numbers[1..] : [Rhythm.DefaultTolerance];

        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords(corpusPath).Where(r => r.Paired).ToArray();

        Directory.CreateDirectory("build");

        using var writer = new StreamWriter(rhythmOut);
        writer.WriteLine(Rhythm.CsvHeader);

        int done = 0, failed = 0;

        foreach (var record in corpus)
        {
            try
            {
                var score = ReplayLoader.Decode(record.Path, index);
                var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
                    .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

                var timeline = Rhythm.Build(playable, score);

                foreach (double tolerance in tolerances)
                    Rhythm.WriteCsv(Path.GetFileName(record.Path), playable, timeline, groupGap, tolerance, writer);

                done++;
            }
            catch (Exception e)
            {
                failed++;

                if (failed <= 5)
                    Console.Error.WriteLine($"  {Path.GetFileName(record.Path)}: {e.GetType().Name}: {e.Message}");
            }

            if (done % 100 == 0 && done > 0)
                Console.Error.WriteLine($"  {done} of {corpus.Length}");
        }

        Console.WriteLine($"extracted {done} replays, group gap {groupGap:0.###} beats, "
                          + $"tolerance {string.Join(", ", tolerances.Select(t => $"{t:0.####}"))}, {failed} failed");
        Console.WriteLine($"written: {rhythmOut}");
        return 0;
    }

    case "geometry":
    {
        // Per-object geometry across the corpus. Almost everything comes from lazer; the
        // signed angle is ours, and lazer's unsigned one rides along so a divergence in
        // magnitude says the arithmetic is wrong rather than the inputs.
        var (corpusPath, geometryOut) = paths(args, "build/geometry.csv");
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords(corpusPath).Where(r => r.Paired).ToArray();

        Directory.CreateDirectory("build");

        using var writer = new StreamWriter(geometryOut);
        writer.WriteLine(Geometry.CsvHeader);

        int done = 0, failed = 0;
        int checkedAngles = 0, diverged = 0;
        double worst = 0;

        foreach (var record in corpus)
        {
            try
            {
                var score = ReplayLoader.Decode(record.Path, index);
                var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
                    .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

                var rows = Geometry.Extract(playable, score);
                Geometry.WriteCsv(Path.GetFileName(record.Path), rows, writer);

                // The differential check, run on every row rather than sampled. Lazer's Angle
                // is minimum-ed against a slider angle, so only the slider-free turns are a
                // fair comparison; there the two must agree in magnitude.
                foreach (var row in rows)
                {
                    if (row is { AngleComparable: true, SignedAngle: { } mine, LazerAngle: { } theirs })
                    {
                        checkedAngles++;
                        double delta = Math.Abs(Math.Abs(mine) - theirs);

                        if (delta > 1e-4)
                        {
                            diverged++;
                            worst = Math.Max(worst, delta);
                        }
                    }
                }

                done++;
            }
            catch (Exception e)
            {
                failed++;

                if (failed <= 5)
                    Console.Error.WriteLine($"  {Path.GetFileName(record.Path)}: {e.GetType().Name}: {e.Message}");
            }

            if (done % 100 == 0 && done > 0)
                Console.Error.WriteLine($"  {done} of {corpus.Length}");
        }

        Console.WriteLine($"extracted {done} replays, {failed} failed");
        Console.WriteLine($"angle cross-check (slider-free turns): {checkedAngles - diverged} of {checkedAngles} agree with lazer"
                          + (diverged > 0 ? $", worst divergence {worst:0.#####} rad" : string.Empty));
        Console.WriteLine($"written: {geometryOut}");
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

    case "tail-sweep":
    {
        var index = BeatmapIndex.Read("build/beatmap-index.json");
        var corpus = CorpusSurvey.ReadRecords("build/corpus.json");

        // The replays that stopped before the beatmap did, taken from the last verification
        // run rather than re-derived.
        var early = OracleDiff.ReadVerification("build/verification.json")
                              .Where(r => r.Unjudged > 0)
                              .Select(r => r.ReplayPath)
                              .ToHashSet();

        var subset = corpus.Where(r => r is { RulesetId: 0, Paired: true, DecodeError: null })
                           .Where(r => early.Contains(r.Path))
                           .ToArray();

        double[] trims = args.Length > 1
            ? args.Skip(1).Select(double.Parse).ToArray()
            : [0, 500, 1000, 1250, 1500, 2000, 2500];

        ShiftSweep.RunTailTrim(subset, index, trims, Console.Out);
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
