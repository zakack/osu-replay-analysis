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
    /// The replay stops before the beatmap does, so objects were left unjudged. On a failed
    /// play this is not a defect, and the differential oracle now says so: across the eleven
    /// replays of this class it has recorded, the simulation reproduces every one of the
    /// 4,088 judgements the live game made while the replay still had frames.
    ///
    /// There are two tails past the play, not one, and they stack.
    ///
    /// The first is in the file. <c>Player.onFail</c> schedules <c>ConcludeFailedScore</c>
    /// for the very next frame, so the header's counts are fixed at the instant of failure —
    /// but <c>FailAnimationContainer</c> then ramps the track frequency from 1 to 0 across
    /// 2500ms of wall clock and the recorder keeps writing frames all the way down, so the
    /// replay carries roughly 1250ms of beatmap time the header never saw. Judging it
    /// invents about five misses. Trimming that much off the end takes exact matches in this
    /// class from 3 of 111 to 34, where every other trim value gives 0 to 3.
    ///
    /// The second belongs to playback, and lazer walks into it too. A replay of a failed play
    /// never ends the screen: <c>ReplayPlayer.PerformFail</c> overrides the base and
    /// deliberately never sets <c>HasFailed</c>, showing a <c>ReplayFailIndicator</c>
    /// instead, which sweeps the track frequency to zero over a second and leaves gameplay
    /// time frozen there. Objects keep resolving throughout that second against a replay
    /// with no frames left, and all of them miss. The ordering is therefore header &lt;
    /// simulation &lt; live game: we overshoot the header by the recorder's tail, and lazer
    /// overshoots us by its own fail sweep.
    /// </summary>
    EndedEarly,

    /// <summary>
    /// Click judgements agree exactly; slider tails, ticks or spinner bonuses do not. The
    /// recorder stores cursor position at 60Hz, so whether the cursor stayed inside a follow
    /// circle between two samples is information the file does not carry.
    /// </summary>
    TrackingOnly,

    /// <summary>
    /// A click judgement differs on a modern client's completed play — because replaying a
    /// replay does not reproduce the clicks of the play that wrote it.
    ///
    /// This used to be the bucket that "should be empty", the one place a disagreement was
    /// held to be evidence of a defect in the port. It is not, and the oracle settled it at
    /// scale. Of the 86 members at 17,236 replays, 85 have the <em>live game</em> disagreeing
    /// with the header its own play wrote, and exactly one has the live game disagreeing with
    /// this simulation. The net drift of lazer's playback against those headers is Great -137,
    /// Ok +11, Meh +157, Miss -31; this simulation's drift against the same headers is Great
    /// -138, Ok +14, Meh +156, Miss -32. Two implementations walking away from the header in
    /// the same direction by the same amount is agreement, not a shared bug.
    ///
    /// So the file's press <em>times</em> survive exactly, as a button change forces a frame,
    /// but which object a press is <em>attributed</em> to does not: a circle only registers a
    /// press while <c>IsHovered</c>, and hover is tested against a cursor interpolated between
    /// 17ms samples. Sub-pixel differences there hand a press to a neighbour, and the intended
    /// object is judged later and worse — which is the Great-to-Meh direction above, skipping
    /// the Ok window entirely in a way no hit-window change produces. Distinct from the
    /// tracking feedback of ppy/osu#34016, and it contradicts the common reading of
    /// ppy/osu#28744 that only tracking is lost.
    ///
    /// Membership is therefore not an accusation. Only <c>oracle-diff</c> can convict, by
    /// asking the live game directly, and across this corpus it convicted once: one HitCircle
    /// at 73620ms of Thriller [Insane].
    /// </summary>
    ClicksUnreproducible,

    /// <summary>
    /// Nothing was compared, so nothing can be concluded: the beatmap converted to a
    /// different object count, the header carried no usable ground truth, or the replay threw.
    ///
    /// These used to fall through into the click bucket, which quietly inflated the one
    /// number that was supposed to mean a defect. A failure to compare is not a disagreement.
    /// </summary>
    NotComparable
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

    /// <summary>
    /// Whether this score was set under the pre-<c>0f078ee550</c> hit windows, regardless of
    /// what else is true about it.
    ///
    /// <see cref="Classify"/> reports one cause, and a replay can have two: a play that ended
    /// early on an old client is filed as <see cref="Cause.EndedEarly"/>, because that is the
    /// confound which has to be removed before the window question can be asked at all. This
    /// keeps the other half visible rather than letting the ordering swallow it.
    /// </summary>
    public static bool OnLegacyHitWindows(VerificationResult result)
    {
        var build = ParseBuild(result.ClientVersion);

        return build == null || build.Value.CompareTo(FlooredHitWindowsFrom) < 0;
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
                return Cause.NotComparable;
        }

        // Order matters, and it runs from the least to the most attributable to us. A replay
        // that ended early on an old client is reported as ended early, because that is the
        // confound that has to be removed before the other question can even be asked.
        // Two independent signs of the same thing. The replay running out of frames leaves
        // objects unresolved; a play that failed close enough to the end that its fail
        // animation recorded past the last object leaves nothing unresolved at all, and shows
        // up only as a header that does not account for every object.
        if (result.Unjudged > 0 || result.HeaderShortfall > 0)
            return Cause.EndedEarly;

        // Tracking is tested before the client version, and the order is load-bearing. If
        // every click agrees then the hit windows demonstrably did not cause this mismatch,
        // whichever client wrote it, and filing it under them is just the older label winning
        // a race. It ran the other way until measured: 614 of the 6,150 replays the legacy
        // class held had clicks agreeing exactly, a tenth of the class and all of it
        // mislabelled.
        if (result.Expected != null && result.Actual != null
            && clicks.All(c => result.Expected.GetValueOrDefault(c) == result.Actual.GetValueOrDefault(c)))
            return Cause.TrackingOnly;

        if (OnLegacyHitWindows(result))
            return Cause.LegacyHitWindows;

        return Cause.ClicksUnreproducible;
    }
}
