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
/// <c>FramedReplayInputHandler.FrameAccuratePlayback</c>, a public field that nothing in
/// osu.Game ever assigns, so it is always false. Implementing the rule anyway was measurably
/// wrong — it suppressed fine sampling exactly where tracking is decided.
///
/// What actually happens: the clock advances at the host's frame rate, clamped to the
/// current replay frame's span and snapped to a frame's time as it is crossed.
/// <c>FrameStabilityContainer</c> only caps a single advance at one 60fps step, and only
/// when the jump already exceeds 1.2 of those, so it is a floor on resolution during lag
/// and a seek guard — not the sampling rate.
/// </summary>
public sealed class ReplaySampler(Replay replay, double clockRate = 1)
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
    private double step => HostFrameTime * clockRate;

    public static readonly double HostFrameTime =
        double.TryParse(Environment.GetEnvironmentVariable("ORA_HOST_STEP_MS"), out double configured) && configured > 0
            ? configured
            : default_host_frame_time;

    /// <summary>
    /// Fitted against the corpus, not derived. Measured exact-match counts out of 513
    /// in-scope replays: 16.7ms gave 150, 8ms gave 167, 4ms gave 173, 2ms gave 177. The
    /// curve flattens while the cost doubles each halving, so 2ms is where it stops paying.
    /// </summary>
    private const double default_host_frame_time = 2.0;

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
