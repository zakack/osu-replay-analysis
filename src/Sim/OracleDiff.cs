using System.Text.Json;
using System.Text.Json.Serialization;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;

namespace Sim;

public sealed record OracleTarget(string ReplayPath, string BeatmapPath);

public sealed record OracleJudgement(string ObjectType, double StartTime, string Result);

public sealed record OracleRun(string ReplayPath, string BeatmapPath, IReadOnlyList<OracleJudgement> Judgements);

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
        int limit)
    {
        var beatmapByReplay = corpus.ToDictionary(r => r.Path, r => r.BeatmapPath);

        // Skip replays the oracle cannot record in bounded time. Playback speed under the
        // headless host is many times realtime on ordinary maps but drops towards realtime
        // on dense marathon ones, and a single stuck target can outlast the whole run.
        var candidates = results
                         .Where(r => r.Outcome == Outcome.Mismatch && r.Expected != null && r.Actual != null)
                         .Where(r => beatmapByReplay.GetValueOrDefault(r.ReplayPath) != null)
                         .Where(r => new FileInfo(r.ReplayPath).Length <= max_replay_bytes)
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
    /// Pair the two judgement streams by object identity rather than by position, so one
    /// extra or missing judgement does not cascade into every later object looking wrong.
    /// </summary>
    public static IReadOnlyList<Divergence> Compare(OracleRun run, SimulationResult simulation)
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

        foreach (var judgement in run.Judgements)
        {
            var key = (judgement.ObjectType, judgement.StartTime);

            string simulatedResult = simulated.TryGetValue(key, out var queue) && queue.Count > 0
                ? queue.Dequeue()
                : "<not judged>";

            if (simulatedResult != judgement.Result)
                divergences.Add(new Divergence(judgement.ObjectType, judgement.StartTime, judgement.Result, simulatedResult));
        }

        // Anything left was judged by the simulation and not by the game at all.
        foreach (var ((type, startTime), queue) in simulated)
        {
            while (queue.Count > 0)
                divergences.Add(new Divergence(type, startTime, "<not judged>", queue.Dequeue()));
        }

        return divergences.OrderBy(d => d.StartTime).ToArray();
    }

    public static IReadOnlyList<VerificationResult> ReadVerification(string source) =>
        JsonSerializer.Deserialize<List<VerificationResult>>(File.ReadAllText(source), options)
        ?? throw new InvalidDataException($"Could not read verification results from {source}");

    public static IReadOnlyList<OracleTarget> ReadTargets(string source) =>
        JsonSerializer.Deserialize<List<OracleTarget>>(File.ReadAllText(source), options)
        ?? throw new InvalidDataException($"Could not read oracle targets from {source}");

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
        int totalDivergences = 0;
        int totalJudgements = 0;

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
            var divergences = Compare(run, simulation);
            compared++;
            totalDivergences += divergences.Count;
            totalJudgements += run.Judgements.Count;

            if (divergences.Count == 0)
                agreed++;

            output.WriteLine();
            output.WriteLine(Path.GetFileName(target.ReplayPath));
            output.WriteLine($"  reference judgements {run.Judgements.Count}, divergences {divergences.Count}");

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
        output.WriteLine();
        output.WriteLine("Every target here is a mismatch against the .osr header. The ones where the");
        output.WriteLine("simulation agrees with the live game are not defects here: replaying a replay");
        output.WriteLine("does not reproduce the play that wrote the header.");
    }
}
