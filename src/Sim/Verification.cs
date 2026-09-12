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
    OutOfScopeSpinners,
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
    int ActualCombo);

public static class Verification
{
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
        HitResult.IgnoreHit, HitResult.IgnoreMiss
    ];

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
            if (mods.Any(m => m is OsuModClassic))
                return scoped(record, Outcome.OutOfScopeClassic);

            var working = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path);
            var playable = working.GetPlayableBeatmap(new OsuRuleset().RulesetInfo, mods);

            if (playable.HitObjects.Any(o => o is Spinner))
                return scoped(record, Outcome.OutOfScopeSpinners);

            // Separate a generation problem from a judgement one. If our object counts do
            // not match the score's maximum statistics, the beatmap was converted
            // differently and no amount of judgement work will reconcile the totals.
            var maximums = score.ScoreInfo.MaximumStatistics;
            var expected = groundTruth(score);

            if (expected == null)
                return scoped(record, Outcome.NoGroundTruth);

            var simulation = new Simulator().Run(playable, score);

            if (maximums.Count > 0)
            {
                var generated = simulation.Objects.GroupBy(o => o.HitObject.CreateJudgement().MaxResult)
                                          .ToDictionary(g => g.Key, g => g.Count());

                var wrong = compared.Where(r => maximums.ContainsKey(r) && maximums[r] != generated.GetValueOrDefault(r)).ToArray();

                if (wrong.Length > 0)
                {
                    string counts = string.Join(" ", wrong.Select(r => $"{r}:{maximums[r]}->{generated.GetValueOrDefault(r)}"));
                    return new VerificationResult(record.Path, Outcome.ObjectCountMismatch, counts, null, null, 0, 0);
                }
            }
            var actual = compared.ToDictionary(r => r, r => simulation.Statistics.GetValueOrDefault(r));

            bool statisticsMatch = compared.All(r => expected.GetValueOrDefault(r) == actual[r]);
            bool comboMatches = score.ScoreInfo.MaxCombo == simulation.MaxCombo;

            if (statisticsMatch && comboMatches)
                return new VerificationResult(record.Path, Outcome.Match, null, expected, actual, score.ScoreInfo.MaxCombo, simulation.MaxCombo);

            // An object left unjudged is a different failure from one judged wrongly: it
            // means the loop never reached a state where the object could resolve, which
            // points at ordering or termination rather than at the ruleset.
            int unjudged = simulation.Objects.Count(o => !o.Judged);
            string unjudgedNote = unjudged > 0 ? $" [unjudged:{unjudged}]" : string.Empty;

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

            return new VerificationResult(record.Path, Outcome.Mismatch, detail, expected, actual, score.ScoreInfo.MaxCombo, simulation.MaxCombo);
        }
        catch (Exception e)
        {
            return new VerificationResult(record.Path, Outcome.Error, $"{e.GetType().Name}: {e.Message}", null, null, 0, 0);
        }
    }

    private static VerificationResult scoped(ReplayRecord record, Outcome outcome) =>
        new(record.Path, outcome, null, null, null, 0, 0);

    private static Dictionary<HitResult, int>? groundTruth(Score score)
    {
        var statistics = score.ScoreInfo.Statistics;

        if (statistics.Count == 0)
            return null;

        return compared.ToDictionary(r => r, r => statistics.GetValueOrDefault(r));
    }
}
