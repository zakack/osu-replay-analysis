using osu.Game.Replays;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Replays;
using osuTK;

namespace Sim;

/// <summary>
/// One evaluated instant of gameplay: the time, where the cursor was, and what was held.
/// </summary>
public readonly record struct GameplayFrame(double Time, Vector2 Cursor, IReadOnlyList<OsuAction> Actions);

/// <summary>
/// Reproduces the cadence at which lazer actually samples a replay, which decides every
/// judgement that depends on continuous cursor state rather than a single instant.
///
/// <c>FramedReplayInputHandler</c> has an "important section" rule that refuses mid-frame
/// times while a button is held, which would mean sliders are only ever evaluated on replay
/// frame boundaries. It never applies: the rule is gated on
/// <c>FramedReplayInputHandler.FrameAccuratePlayback</c>, a public field assigned in exactly
/// one place across the whole ppy/osu tree — one of osu.Game's own unit tests — and never in
/// game code, so it is always false during playback. Implementing the rule anyway was
/// measurably wrong: it suppressed fine sampling exactly where tracking is decided.
///
/// What actually happens: the clock advances at the host's frame rate, clamped to the
/// current replay frame's span and snapped to a frame's time as it is crossed.
/// <c>FrameStabilityContainer</c> only caps a single advance at one 60fps step, and only
/// when the jump already exceeds 1.2 of those, so it is a floor on resolution during lag
/// and a seek guard — not the sampling rate.
/// </summary>
public sealed class ReplaySampler(Replay replay, double clockRate = 1, double? stepOverride = null)
{
    /// <summary>
    /// Stand-in for the host's frame time, and a genuinely free parameter.
    ///
    /// Slider tracking is sampled per update, so the rate decides tails outright: a short
    /// slider's ball covers roughly half a unit per millisecond, and the follow circle is
    /// only a few units wider than the cursor's drift at the point players leave. The
    /// machine that set the score sampled at its own frame rate, which the replay does not
    /// record, so no value here is "correct" — it is fitted to the corpus. Override with
    /// ORA_HOST_STEP_MS to re-run the sweep.
    /// </summary>
    /// <summary>
    /// The step in beatmap time. The client's frame time is a wall-clock quantity, so on a
    /// rate-adjusted replay it covers more beatmap time per frame — the recorder itself
    /// scales its own threshold by the clock rate for exactly this reason.
    /// </summary>
    private double step => (stepOverride ?? HostFrameTime) * clockRate;

    public static readonly double HostFrameTime =
        double.TryParse(Environment.GetEnvironmentVariable("ORA_HOST_STEP_MS"), out double configured) && configured > 0
            ? configured
            : default_host_frame_time;

    /// <summary>
    /// Two references disagree about this value, and they are not measuring the same thing:
    ///
    ///   step      agrees with the header    agrees with the oracle
    ///   16.7ms    150 of 513                33 of 47
    ///   2ms       177 of 513                13 of 47
    ///   0.5ms     —                         13 of 47
    ///   0.25ms    —                         13 of 47
    ///
    /// The header came from a real client at an unknown, probably high, frame rate, and is
    /// the only evidence there is about real clients. The oracle runs the real ruleset but
    /// under a headless test host whose update cadence is its own, not a player's. So the
    /// header sets this default, and <see cref="OracleMatchStep"/> is used when diffing
    /// against the oracle, where the point is to isolate rule bugs rather than to model a
    /// machine.
    ///
    /// Why the oracle behaves like a 16.7ms sampler is not established. Sampling its
    /// gameplay clock from the test scene gives a 0.38ms median with a long tail and no
    /// clustering at 60fps, but that cannot see inside <c>FrameStabilityContainer</c>'s
    /// catch-up loop, which runs many clock advances inside a single host frame. Treat the
    /// number as measured and the mechanism as open.
    /// </summary>
    private const double default_host_frame_time = 2.0;

    /// <summary>
    /// The step at which this simulation agrees with the differential oracle best. Used only
    /// for oracle diffs, so that a divergence there means a rule was ported wrong rather
    /// than that two machines sampled at different rates.
    /// </summary>
    public const double OracleMatchStep = 1000.0 / 60;

    private readonly List<OsuReplayFrame> frames = replay.Frames.Cast<OsuReplayFrame>().ToList();

    public IReadOnlyList<OsuReplayFrame> Frames => frames;

    public double FirstFrameTime => frames.Count > 0 ? frames[0].Time : 0;
    public double LastFrameTime => frames.Count > 0 ? frames[^1].Time : 0;

    /// <summary>
    /// Walk the replay from its first frame until <paramref name="until"/>, yielding every
    /// instant the client would have evaluated.
    /// </summary>
    public IEnumerable<GameplayFrame> Sample(double until)
    {
        if (frames.Count == 0)
            yield break;

        int index = 0;
        double time = frames[0].Time;

        yield return new GameplayFrame(time, frames[0].Position, frames[0].Actions);

        while (time < until)
        {
            double nextFrameTime = index + 1 < frames.Count ? frames[index + 1].Time : double.PositiveInfinity;

            double proposed = Math.Min(nextFrameTime, time + step);

            if (double.IsPositiveInfinity(proposed))
                proposed = Math.Min(until, time + step);

            time = proposed;

            if (index + 1 < frames.Count && time >= nextFrameTime)
                index++;

            yield return new GameplayFrame(time, positionAt(index, time), frames[index].Actions);
        }
    }

    private Vector2 positionAt(int index, double time)
    {
        var start = frames[index];

        if (index + 1 >= frames.Count)
            return start.Position;

        var end = frames[index + 1];
        double span = end.Time - start.Time;

        if (span <= 0)
            return start.Position;

        float t = (float)Math.Clamp((time - start.Time) / span, 0, 1);
        return start.Position + (end.Position - start.Position) * t;
    }
}
