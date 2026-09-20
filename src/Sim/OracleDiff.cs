using System.Text.Json;
using System.Text.Json.Serialization;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;

namespace Sim;

public sealed record OracleTarget(string ReplayPath, string BeatmapPath);

public sealed record OracleJudgement(string ObjectType, double StartTime, string Result)
{
    /// <summary>When the game awarded this, not when the object starts. Zero on recordings
    /// made before the field existed.</summary>
    public double TimeAbsolute { get; init; }
}

public sealed record OracleRun(string ReplayPath, string BeatmapPath, IReadOnlyList<OracleJudgement> Judgements)
{
    /// <summary>
    /// Where the live game stopped judging, when it stopped before the beatmap ended. Null
    /// when the run played to a genuine end.
    ///
    /// A replay of a failed play ends twice over. It carries no frames past the play that
    /// wrote it, and lazer's own playback does not end the screen there — it either stops
    /// the gameplay clock for want of frames, or fails the play and lets
    /// <c>ReplayFailIndicator</c> sweep the track frequency to zero. Either way time
    /// freezes, and nothing after this point was ever offered to the reference.
    /// </summary>
    public double? StalledAt { get; init; }

    /// <summary>
    /// Health at the instant the play was failed, which is not the reason it failed: a mod
    /// fail condition such as Sudden Death ends the play at whatever health it was at.
    /// </summary>
    public double? HealthAtFailure { get; init; }

    /// <summary>Whether the health processor failed the replay during playback.</summary>
    public bool Failed { get; init; }
}

/// <summary>
/// Diffs the simulation against a recording of what the real game judged.
///
/// The replay header only ever carried totals, which is enough to know a run is wrong and
/// useless for knowing where. This pairs judgements object by object and reports the first
/// place the two disagree, which is the object actually worth looking at.
/// </summary>
public static class OracleDiff
{
    /// <summary>
    /// Rough ceiling on replay size. The file is mostly compressed frames, so size tracks
    /// length closely enough to exclude marathons without decoding every candidate first.
    /// </summary>
    private const long max_replay_bytes = 48 * 1024;

    private static readonly JsonSerializerOptions options = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() }
    };

    /// <summary>
    /// Choose replays worth the cost of hosting the game.
    ///
    /// Candidates are ordered by how far off they are, then sampled evenly across that
    /// range rather than taken from the front. Taking the smallest mismatches only would
    /// answer whether single tracking breaks are our fault while saying nothing about the
    /// loud cases, and the point of the oracle is to split the whole residual into what the
    /// simulation got wrong and what the replay could never have reproduced.
    /// </summary>
    public static IReadOnlyList<OracleTarget> ChooseTargets(
        IReadOnlyList<VerificationResult> results,
        IReadOnlyList<ReplayRecord> corpus,
        int limit,
        Cause? only = null,
        long maxBytes = max_replay_bytes)
    {
        var beatmapByReplay = corpus.ToDictionary(r => r.Path, r => r.BeatmapPath);

        // Skip replays the oracle cannot record in bounded time. Playback speed under the
        // headless host is many times realtime on ordinary maps but drops towards realtime
        // on dense marathon ones, and a single stuck target can outlast the whole run.
        //
        // The cap is a time budget, not a neutral filter: it excludes long dense maps, which
        // is where an attribution cascade is most likely. Raise it when the target list is
        // small enough to afford, and say which cap a run was made under.
        var matching = results
                       .Where(r => r.Outcome == Outcome.Mismatch && r.Expected != null && r.Actual != null)
                       .Where(r => only == null || Taxonomy.Classify(r) == only)
                       .Where(r => beatmapByReplay.GetValueOrDefault(r.ReplayPath) != null)
                       .Where(r => new FileInfo(r.ReplayPath).Length <= maxBytes)
                       .ToArray();

        // Asking for one named cause means taking all of it, cheapest first so a run that is
        // cut short still leaves usable recordings. Sampling is for the unfiltered case,
        // where the population is thousands and mostly one cause.
        var candidates = only != null
            ? matching.OrderBy(r => new FileInfo(r.ReplayPath).Length).ToArray()
            : matching
              .OrderBy(r => r.Expected!.Sum(kv => Math.Abs(kv.Value - r.Actual!.GetValueOrDefault(kv.Key))))
              .ThenBy(r => r.ReplayPath, StringComparer.Ordinal)
              .ToArray();

        if (candidates.Length <= limit)
            return candidates.Select(r => new OracleTarget(r.ReplayPath, beatmapByReplay[r.ReplayPath]!)).ToArray();

        double stride = (double)candidates.Length / limit;

        return Enumerable.Range(0, limit)
                         .Select(i => candidates[(int)(i * stride)])
                         .Select(r => new OracleTarget(r.ReplayPath, beatmapByReplay[r.ReplayPath]!))
                         .ToArray();
    }

    public static void WriteTargets(IReadOnlyList<OracleTarget> targets, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);
        File.WriteAllText(destination, JsonSerializer.Serialize(targets, options));
    }

    public sealed record Divergence(string ObjectType, double StartTime, string Reference, string Simulated);

    /// <summary>
    /// Whether this object's judgement is settled by a single press rather than by tracking.
    /// Only these can convict the port: everything else shares lazer's own framerate
    /// dependence (ppy/osu#34016) and is unreproducible in principle.
    /// </summary>
    private static bool clickJudged(string objectType) =>
        objectType is "HitCircle" or "SliderHeadCircle";

    /// <summary>
    /// The outcome of one diff. Judgements the simulation made past the point the reference
    /// froze are counted apart from divergences: the reference did not judge them
    /// differently, it never saw them, so folding them in would report a truncated replay as
    /// dozens of defects.
    /// </summary>
    public sealed record Comparison(IReadOnlyList<Divergence> Divergences, int BeyondReference, int BeyondFrames);

    /// <summary>
    /// Pair the two judgement streams by object identity rather than by position, so one
    /// extra or missing judgement does not cascade into every later object looking wrong.
    /// </summary>
    public static Comparison Compare(OracleRun run, SimulationResult simulation, double lastFrameTime)
    {
        // A queue per key rather than one entry: 2B maps can carry two objects of the same
        // type at the same start time, and a dictionary would silently drop one of them.
        var simulated = new Dictionary<(string, double), Queue<string>>();

        foreach (var state in simulation.InJudgementOrder)
        {
            var key = (state.HitObject.GetType().Name, state.HitObject.StartTime);

            if (!simulated.TryGetValue(key, out var queue))
                simulated[key] = queue = new Queue<string>();

            queue.Enqueue(state.Result.ToString());
        }

        var divergences = new List<Divergence>();

        // Past the last replay frame the reference is not a reference. Lazer keeps judging
        // there — a failed replay hands the track to ReplayFailIndicator, which takes a full
        // second to sweep the frequency to zero, and every object resolving in that second is
        // judged against no input at all and misses. That is the host's fail UI, not the
        // ruleset, and counting it as disagreement would say the port is wrong for declining
        // to invent misses the play never had.
        //
        // The boundary is when the game awarded the judgement, not when the object started.
        // An object can begin before the last frame and still only reach its miss window
        // after it, and that one is just as far out of reach as the objects that start later.
        int beyondFrames = 0;

        foreach (var judgement in run.Judgements)
        {
            var key = (judgement.ObjectType, judgement.StartTime);

            string simulatedResult = simulated.TryGetValue(key, out var queue) && queue.Count > 0
                ? queue.Dequeue()
                : "<not judged>";

            if (simulatedResult == judgement.Result)
                continue;

            if (judgement.TimeAbsolute > lastFrameTime)
                beyondFrames++;
            else
                divergences.Add(new Divergence(judgement.ObjectType, judgement.StartTime, judgement.Result, simulatedResult));
        }

        // Anything left was judged by the simulation and not by the game at all. Past the
        // stall that is expected rather than wrong, so it is counted, not listed.
        int beyondReference = 0;

        foreach (var ((type, startTime), queue) in simulated)
        {
            while (queue.Count > 0)
            {
                string result = queue.Dequeue();

                if (startTime > run.StalledAt)
                    beyondReference++;
                else
                    divergences.Add(new Divergence(type, startTime, "<not judged>", result));
            }
        }

        return new Comparison(divergences.OrderBy(d => d.StartTime).ToArray(), beyondReference, beyondFrames);
    }

    public static IReadOnlyList<VerificationResult> ReadVerification(string source) =>
        JsonSerializer.Deserialize<List<VerificationResult>>(File.ReadAllText(source), options)
        ?? throw new InvalidDataException($"Could not read verification results from {source}");

    public static IReadOnlyList<OracleTarget> ReadTargets(string source) =>
        JsonSerializer.Deserialize<List<OracleTarget>>(File.ReadAllText(source), options)
        ?? throw new InvalidDataException($"Could not read oracle targets from {source}");

    /// <summary>
    /// Every replay the oracle has a recording for, by the path the recording names. Cheaper
    /// and more direct than re-hashing the corpus to find them.
    /// </summary>
    public static IEnumerable<string> RecordedReplayPaths(string directory) =>
        Directory.Exists(directory)
            ? Directory.EnumerateFiles(directory, "*.json")
                       .Select(f => JsonSerializer.Deserialize<OracleRun>(File.ReadAllText(f), options)?.ReplayPath)
                       .OfType<string>()
            : [];

    public static OracleRun? ReadRun(string replayPath)
    {
        string path = Path.Combine("build", "oracle",
            $"{Convert.ToHexStringLower(System.Security.Cryptography.MD5.HashData(File.ReadAllBytes(replayPath)))}.json");

        return File.Exists(path) ? JsonSerializer.Deserialize<OracleRun>(File.ReadAllText(path), options) : null;
    }

    public static void Run(IReadOnlyList<OracleTarget> targets, IReadOnlyDictionary<string, IndexEntry> index, TextWriter output)
    {
        int compared = 0;
        int agreed = 0;
        int truncated = 0;
        int totalBeyondFrames = 0;
        int totalDivergences = 0;
        int totalJudgements = 0;

        // Which kind of object disagreed is the whole question. A slider tail or tick is
        // decided by continuous cursor state, so the oracle is one draw from the same
        // distribution the original play drew from and a disagreement is not a defect. A
        // circle or a slider head is decided at a single instant, and there the oracle is
        // authoritative.
        var byObjectType = new Dictionary<string, int>(StringComparer.Ordinal);

        foreach (var target in targets)
        {
            var run = ReadRun(target.ReplayPath);

            if (run == null)
            {
                output.WriteLine($"no oracle recording for {Path.GetFileName(target.ReplayPath)}");
                continue;
            }

            var score = ReplayLoader.Decode(target.ReplayPath, index);
            var playable = new FlatWorkingBeatmap(target.BeatmapPath)
                .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

            // Match the oracle's cadence, not the production default. Otherwise every diff
            // mixes rule defects with the fact that two machines sampled at different rates.
            var simulation = new Simulator { StepOverride = ReplaySampler.OracleMatchStep }.Run(playable, score);
            double lastFrameTime = score.Replay.Frames.Count > 0 ? score.Replay.Frames[^1].Time : double.PositiveInfinity;
            var comparison = Compare(run, simulation, lastFrameTime);
            var divergences = comparison.Divergences;
            compared++;
            totalDivergences += divergences.Count;
            totalJudgements += run.Judgements.Count;

            if (divergences.Count == 0)
                agreed++;

            if (run.StalledAt != null)
                truncated++;

            totalBeyondFrames += comparison.BeyondFrames;

            output.WriteLine();
            output.WriteLine(Path.GetFileName(target.ReplayPath));
            output.WriteLine($"  reference judgements {run.Judgements.Count}, divergences {divergences.Count}");

            if (run.StalledAt != null)
            {
                output.WriteLine($"  replay ends {lastFrameTime:F0}ms, reference stopped {run.StalledAt:F0}ms "
                                 + $"({(run.Failed ? $"failed at health {run.HealthAtFailure:F3}" : "out of frames")})");
            }

            // Report this wherever it happened, not only on a run flagged as truncated. The
            // exclusion is driven by judgement times against the last frame, so it can bite
            // on a run that reached the end of the beatmap anyway.
            if (comparison.BeyondFrames > 0 || comparison.BeyondReference > 0)
            {
                output.WriteLine($"  past the last frame: reference judged {comparison.BeyondFrames} objects the simulation did not, "
                                 + $"simulation judged {comparison.BeyondReference} the reference did not");
            }

            // Three-way, because the two comparisons answer different questions. Against the
            // live game: is the reimplemented loop faithful? Against the header: does replaying
            // the replay reproduce the play it came from? Those can disagree, and when they do
            // the header is not evidence of a bug here.
            var headerTotals = score.ScoreInfo.Statistics;
            var oracleTotals = run.Judgements.GroupBy(j => j.Result).ToDictionary(g => g.Key, g => g.Count());
            var simTotals = simulation.Statistics.ToDictionary(kv => kv.Key.ToString(), kv => kv.Value);

            var keys = headerTotals.Select(kv => kv.Key.ToString())
                                   .Concat(oracleTotals.Keys)
                                   .Concat(simTotals.Keys)
                                   .Distinct()
                                   .Order(StringComparer.Ordinal);

            foreach (string key in keys)
            {
                int header = headerTotals.FirstOrDefault(kv => kv.Key.ToString() == key).Value;
                int oracle = oracleTotals.GetValueOrDefault(key);
                int sim = simTotals.GetValueOrDefault(key);

                if (header == oracle && oracle == sim)
                    continue;

                output.WriteLine($"    {key,-16} header={header,-6} game={oracle,-6} sim={sim}");
            }

            foreach (var divergence in divergences)
                byObjectType[divergence.ObjectType] = byObjectType.GetValueOrDefault(divergence.ObjectType) + 1;

            foreach (var divergence in divergences.Take(4))
                output.WriteLine($"  {divergence.StartTime,10:F0}ms  {divergence.ObjectType,-18}  game={divergence.Reference,-14} sim={divergence.Simulated}");

            if (divergences.Count > 4)
                output.WriteLine($"  ... and {divergences.Count - 4} more");
        }

        output.WriteLine();
        output.WriteLine($"compared {compared} of {targets.Count} targets at the oracle-matched step of {ReplaySampler.OracleMatchStep:F2}ms");
        output.WriteLine($"  simulation agrees with the live game  {agreed}");
        output.WriteLine($"  simulation diverges from it           {compared - agreed}");
        output.WriteLine($"  diverging judgements                  {totalDivergences} of {totalJudgements}" +
                         (totalJudgements > 0 ? $"  ({100.0 * totalDivergences / totalJudgements:F3}%)" : string.Empty));

        if (byObjectType.Count > 0)
        {
            output.WriteLine();
            output.WriteLine("  divergences by object type:");

            foreach (var (type, count) in byObjectType.OrderByDescending(kv => kv.Value))
                output.WriteLine($"    {type,-20} {count,5}{(clickJudged(type) ? "   <- decided at an instant, the oracle is authoritative here" : string.Empty)}");

            int clicks = byObjectType.Where(kv => clickJudged(kv.Key)).Sum(kv => kv.Value);
            output.WriteLine($"    click-judged total   {clicks,5} of {totalDivergences}");
        }

        if (truncated > 0)
        {
            output.WriteLine($"  reference ran out of replay input           {truncated}");
            output.WriteLine($"  judgements past the last replay frame       {totalBeyondFrames} (excluded above)");
        }

        output.WriteLine();
        output.WriteLine("Every target here is a mismatch against the .osr header. The ones where the");
        output.WriteLine("simulation agrees with the live game are not defects here: replaying a replay");
        output.WriteLine("does not reproduce the play that wrote the header.");
    }
}
