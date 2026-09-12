using System.Globalization;
using osu.Game.Rulesets.Scoring;

namespace Sim;

/// <summary>
/// Why a replay's simulation does not equal its <c>.osr</c> header.
///
/// The match rate on its own is close to meaningless, because three of these classes are
/// not defects: they are properties of the file or of which lazer wrote it. Separating them
/// is what turns a residual into knowledge.
/// </summary>
public enum Cause
{
    /// <summary>Every compared statistic and the max combo agree.</summary>
    Exact,

    /// <summary>The Classic mod branch, which has not been ported.</summary>
    ClassicMod,

    /// <summary>
    /// The score was set on a client whose hit windows were the raw difficulty range rather
    /// than floored to a half-integer. Replay frame times are whole milliseconds, so under
    /// the old windows a judgement could sit exactly on an edge and flip on playback — the
    /// bug ppy/osu 0f078ee550 fixed by moving every edge half a millisecond off the grid.
    /// Scores from before that fix shipped were judged under different rules from the ones
    /// this simulation applies, and no amount of correctness here will reproduce them.
    /// </summary>
    LegacyHitWindows,

    /// <summary>
    /// The replay stops before the beatmap does, so objects were left unjudged.
    ///
    /// For a failed play this one is ours. <c>Player.onFail</c> schedules
    /// <c>ConcludeFailedScore</c> for the very next frame, so the header's counts are fixed
    /// at the instant of failure — but <c>FailAnimationContainer</c> then ramps the track
    /// frequency from 1 to 0 across 2500ms of wall clock, and the recorder keeps writing
    /// frames the whole way down. The replay therefore carries roughly 1250ms of beatmap
    /// time the header never saw, and judging it invents about five misses. Trimming that
    /// much off the end takes exact matches in this class from 3 of 111 to 34, where every
    /// other trim value gives 0 to 3 — a sharp optimum exactly where the animation predicts
    /// one. The fix is to locate the failure rather than subtract a constant, by feeding the
    /// judgement stream through lazer's own <c>OsuHealthProcessor</c>, which runs unloaded
    /// with no host exactly as <c>ScoreProcessor</c> does.
    /// </summary>
    EndedEarly,

    /// <summary>
    /// Click judgements agree exactly; slider tails, ticks or spinner bonuses do not. The
    /// recorder stores cursor position at 60Hz, so whether the cursor stayed inside a follow
    /// circle between two samples is information the file does not carry.
    /// </summary>
    TrackingOnly,

    /// <summary>
    /// A click judgement differs on a modern client's completed play. This is the bucket
    /// that should be empty, and the only one where a disagreement is evidence of a bug.
    /// </summary>
    ClickMismatch
}

public static class Taxonomy
{
    private static readonly HitResult[] clicks = [HitResult.Great, HitResult.Ok, HitResult.Meh, HitResult.Miss];

    /// <summary>
    /// The first lazer release carrying ppy/osu 0f078ee550, which floors every hit window and
    /// moves it half a millisecond off the whole-millisecond grid that replay frame times sit
    /// on. The commit landed on master on 2025-04-18 and did not ship until this build, and
    /// the corpus shows the change exactly there: click judgements drift on every earlier
    /// build and on none from this one onwards.
    /// </summary>
    public static readonly (int Year, int Month, int Day) FlooredHitWindowsFrom = (2025, 7, 10);

    /// <summary>
    /// Lazer builds are dated: <c>2025.710.0-lazer</c> is the first release of 10 July 2025.
    /// Returns null when the score carries no client version, which the pre-30000001 replay
    /// format has no room for.
    /// </summary>
    public static (int Year, int Month, int Day)? ParseBuild(string? clientVersion)
    {
        if (string.IsNullOrWhiteSpace(clientVersion))
            return null;

        string[] parts = clientVersion.Split('-')[0].Split('.');

        if (parts.Length < 2
            || !int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out int year)
            || parts[1].Length is < 3 or > 4
            || !int.TryParse(parts[1][..^2], NumberStyles.None, CultureInfo.InvariantCulture, out int month)
            || !int.TryParse(parts[1][^2..], NumberStyles.None, CultureInfo.InvariantCulture, out int day))
            return null;

        return (year, month, day);
    }

    public static Cause Classify(VerificationResult result)
    {
        switch (result.Outcome)
        {
            case Outcome.Match:
                return Cause.Exact;

            case Outcome.OutOfScopeClassic:
                return Cause.ClassicMod;

            case not Outcome.Mismatch:
                return Cause.ClickMismatch;
        }

        // Order matters, and it runs from the least to the most attributable to us. A replay
        // that ended early on an old client is reported as ended early, because that is the
        // confound that has to be removed before the other question can even be asked.
        if (result.Unjudged > 0)
            return Cause.EndedEarly;

        var build = ParseBuild(result.ClientVersion);

        if (build == null || build.Value.CompareTo(FlooredHitWindowsFrom) < 0)
            return Cause.LegacyHitWindows;

        if (result.Expected != null && result.Actual != null
            && clicks.All(c => result.Expected.GetValueOrDefault(c) == result.Actual.GetValueOrDefault(c)))
            return Cause.TrackingOnly;

        return Cause.ClickMismatch;
    }
}
