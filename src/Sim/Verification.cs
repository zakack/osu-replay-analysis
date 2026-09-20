using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;

namespace Sim;

/// <summary>
/// Why a replay was not simulated, or why its simulation disagreed with the ground truth.
/// The taxonomy is the deliverable: a match rate is one number, but a named cause per
/// mismatch is what tells you which part of the ruleset is still wrong.
/// </summary>
public enum Outcome
{
    Match,
    Mismatch,
    ObjectCountMismatch,
    OutOfScopeClassic,
    NoGroundTruth,
    Error
}

public sealed record VerificationResult(
    string ReplayPath,
    Outcome Outcome,
    string? Detail,
    Dictionary<HitResult, int>? Expected,
    Dictionary<HitResult, int>? Actual,
    int ExpectedCombo,
    int ActualCombo)
{
    /// <summary>
    /// The lazer build that wrote the score. Hit windows changed shape on 2025.710.0, so a
    /// disagreement means different things either side of it.
    /// </summary>
    public string ClientVersion { get; init; } = string.Empty;

    /// <summary>
    /// Objects the simulation never resolved, which means the replay stopped before the
    /// beatmap did.
    /// </summary>
    public int Unjudged { get; init; }

    /// <summary>
    /// Click-judged objects the <em>header</em> never accounts for, which means the play
    /// stopped before the beatmap did.
    ///
    /// This catches what <see cref="Unjudged"/> cannot. A play that fails near the end keeps
    /// recording through the fail animation, and if that tail runs past the last object then
    /// the replay has frames for the whole beatmap and the simulation resolves everything.
    /// Nothing is unjudged, so the only remaining evidence that the play ended early is that
    /// its own counts are short.
    /// </summary>
    public int HeaderShortfall { get; init; }

    /// <summary>
    /// The <c>.osr</c> format version. Carried because it is the only evidence of whether an
    /// absent client version means an old client or a silent one — see
    /// <see cref="Taxonomy.OnLegacyHitWindows"/>. Zero when the field was not recorded.
    /// </summary>
    public int ReplayFormat { get; init; }
}

public static class Verification
{
    /// <summary>The results a click on a hit object or slider head can produce.</summary>
    private static readonly HitResult[] clicks = [HitResult.Great, HitResult.Ok, HitResult.Meh, HitResult.Miss];

    /// <summary>
    /// Every judgement type a circles-and-sliders stage should account for. Slider tails and
    /// large ticks are compared too, because lazer replays carry them in the statistics blob
    /// and they are the sharpest signal on whether tracking is right.
    /// </summary>
    private static readonly HitResult[] compared =
    [
        HitResult.Great, HitResult.Ok, HitResult.Meh, HitResult.Miss,
        HitResult.LargeTickHit, HitResult.LargeTickMiss, HitResult.SliderTailHit,
        // A dropped slider tail becomes IgnoreMiss, not LargeTickMiss, so leaving these out
        // hides exactly the failure sliders are most likely to produce.
        HitResult.IgnoreHit, HitResult.IgnoreMiss,
        // Spinner ticks and bonus spins.
        HitResult.SmallBonus, HitResult.LargeBonus
    ];

    /// <summary>
    /// Simulate the Classic branch anyway rather than skipping it.
    ///
    /// Off by default, because Classic swaps in the legacy hit policy and changes slider
    /// judgement, and none of that is ported. On for measuring what that costs: a reference
    /// corpus pulled from any ranked leaderboard is overwhelmingly Classic, so "we cannot use
    /// those" is an expensive conclusion to reach by assumption. Run it and read the
    /// taxonomy instead.
    /// </summary>
    public static bool IncludeClassic { get; set; }

    public static VerificationResult Verify(ReplayRecord record, IReadOnlyDictionary<string, IndexEntry> index)
    {
        try
        {
            var score = ReplayLoader.Decode(record.Path, index);
            // ScoreInfo.Mods are already real Mod instances with their settings applied,
            // including any freely-set rate multiplier from a lazer score's APIMod blob.
            var mods = score.ScoreInfo.Mods;

            // The Classic mod swaps in LegacyHitPolicy and changes slider head and tail
            // judgement. It is a different ruleset branch, not a variation, so it waits.
            string clientVersion = score.ScoreInfo.ClientVersion;

            if (!IncludeClassic && mods.Any(m => m is OsuModClassic))
                return scoped(record, Outcome.OutOfScopeClassic) with { ClientVersion = clientVersion, ReplayFormat = record.Version };

            var working = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path);
            var playable = working.GetPlayableBeatmap(new OsuRuleset().RulesetInfo, mods);

            // Separate a generation problem from a judgement one. If our object counts do
            // not match the score's maximum statistics, the beatmap was converted
            // differently and no amount of judgement work will reconcile the totals.
            var maximums = score.ScoreInfo.MaximumStatistics;
            var expected = groundTruth(score);

            if (expected == null)
                return scoped(record, Outcome.NoGroundTruth) with { ClientVersion = clientVersion, ReplayFormat = record.Version };

            var simulation = new Simulator().Run(playable, score);

            if (maximums.Count > 0)
            {
                var generated = simulation.Objects.GroupBy(o => o.HitObject.CreateJudgement().MaxResult)
                                          .ToDictionary(g => g.Key, g => g.Count());

                var wrong = compared.Where(r => maximums.ContainsKey(r) && maximums[r] != generated.GetValueOrDefault(r)).ToArray();

                if (wrong.Length > 0)
                {
                    string counts = string.Join(" ", wrong.Select(r => $"{r}:{maximums[r]}->{generated.GetValueOrDefault(r)}"));
                    return new VerificationResult(record.Path, Outcome.ObjectCountMismatch, counts, null, null, 0, 0)
                        { ClientVersion = clientVersion, ReplayFormat = record.Version };
                }
            }
            var actual = compared.ToDictionary(r => r, r => simulation.Statistics.GetValueOrDefault(r));

            // Every click-judged object has Great as its maximum, and nothing else does:
            // spinners max out at bonus results and slider parts at their own. So the header's
            // maximum Great is the count of objects the play should have clicked, and the
            // difference is what it never reached.
            int shortfall = maximums.TryGetValue(HitResult.Great, out int maximumGreat)
                ? maximumGreat - clicks.Sum(r => expected.GetValueOrDefault(r))
                : 0;

            bool statisticsMatch = compared.All(r => expected.GetValueOrDefault(r) == actual[r]);
            bool comboMatches = score.ScoreInfo.MaxCombo == simulation.MaxCombo;

            // An object left unjudged is a different failure from one judged wrongly: it
            // means the loop never reached a state where the object could resolve, which for
            // a replay that stops early is not a failure at all.
            int unjudged = simulation.Objects.Count(o => !o.Judged);

            if (statisticsMatch && comboMatches)
                return new VerificationResult(record.Path, Outcome.Match, null, expected, actual, score.ScoreInfo.MaxCombo, simulation.MaxCombo)
                    { ClientVersion = clientVersion, ReplayFormat = record.Version, Unjudged = unjudged, HeaderShortfall = shortfall };

            string unjudgedNote = unjudged > 0 ? $" [unjudged:{unjudged}]" : string.Empty;

            if (shortfall > 0)
                unjudgedNote += $" [header short:{shortfall}]";

            string detail = !statisticsMatch
                ? string.Join(" ", compared.Where(r => expected.GetValueOrDefault(r) != actual[r])
                                           .Select(r => $"{r}:{expected.GetValueOrDefault(r)}->{actual[r]}"))
                : $"combo:{score.ScoreInfo.MaxCombo}->{simulation.MaxCombo}";

            detail += unjudgedNote;

            var dropReasons = simulation.Objects.Where(o => o.DropReason != null)
                                        .GroupBy(o => o.DropReason!)
                                        .OrderByDescending(g => g.Count())
                                        .Select(g => $"{g.Key}x{g.Count()}")
                                        .ToArray();

            if (dropReasons.Length > 0)
                detail += $" [tails lost: {string.Join(",", dropReasons)}]";

            return new VerificationResult(record.Path, Outcome.Mismatch, detail, expected, actual, score.ScoreInfo.MaxCombo, simulation.MaxCombo)
                { ClientVersion = clientVersion, ReplayFormat = record.Version, Unjudged = unjudged, HeaderShortfall = shortfall };
        }
        catch (Exception e)
        {
            return new VerificationResult(record.Path, Outcome.Error, $"{e.GetType().Name}: {e.Message}", null, null, 0, 0)
                { ReplayFormat = record.Version };
        }
    }

    private static VerificationResult scoped(ReplayRecord record, Outcome outcome) =>
        new(record.Path, outcome, null, null, null, 0, 0) { ReplayFormat = record.Version };

    private static Dictionary<HitResult, int>? groundTruth(Score score)
    {
        var statistics = score.ScoreInfo.Statistics;

        if (statistics.Count == 0)
            return null;

        return compared.ToDictionary(r => r, r => statistics.GetValueOrDefault(r));
    }
}
