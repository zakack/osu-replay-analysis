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
    OutOfScopeSliders,
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
    /// <summary>Judgement types a circles-only stage is expected to account for.</summary>
    private static readonly HitResult[] compared = [HitResult.Great, HitResult.Ok, HitResult.Meh, HitResult.Miss];

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

            if (playable.HitObjects.Any(o => o is Slider))
                return scoped(record, Outcome.OutOfScopeSliders);

            if (playable.HitObjects.Any(o => o is Spinner))
                return scoped(record, Outcome.OutOfScopeSpinners);

            var expected = groundTruth(score);

            if (expected == null)
                return scoped(record, Outcome.NoGroundTruth);

            var simulation = new Simulator().Run(playable, score);
            var actual = compared.ToDictionary(r => r, r => simulation.Statistics.GetValueOrDefault(r));

            bool statisticsMatch = compared.All(r => expected.GetValueOrDefault(r) == actual[r]);
            bool comboMatches = score.ScoreInfo.MaxCombo == simulation.MaxCombo;

            if (statisticsMatch && comboMatches)
                return new VerificationResult(record.Path, Outcome.Match, null, expected, actual, score.ScoreInfo.MaxCombo, simulation.MaxCombo);

            string detail = !statisticsMatch
                ? string.Join(" ", compared.Where(r => expected.GetValueOrDefault(r) != actual[r])
                                           .Select(r => $"{r}:{expected.GetValueOrDefault(r)}->{actual[r]}"))
                : $"combo:{score.ScoreInfo.MaxCombo}->{simulation.MaxCombo}";

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
