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
/// Two mechanisms combine. <c>FramedReplayInputHandler.SetFrameFromTime</c> refuses
/// mid-frame times while the replay is in an "important section" — a button is held and the
/// next frame is close — so during any slider the clock lands only on replay frame times,
/// where interpolation is exact. Outside those sections the clock advances at the host's
/// frame rate, which is far finer than 60fps on real hardware;
/// <c>FrameStabilityContainer</c> only caps a single advance at one 60fps step, so that is
/// a floor on resolution, not the resolution itself.
/// </summary>
public sealed class ReplaySampler(Replay replay)
{
    /// <summary>
    /// Matches <c>FramedReplayInputHandler.AllowedImportantTimeSpan</c>: a held button only
    /// forces frame-exact playback while the next frame is this close.
    /// </summary>
    private const double important_time_span = 1000.0 / 60 * 1.2;

    /// <summary>
    /// Step size outside important sections, matching <c>FrameStabilityContainer</c>'s
    /// 60fps cap. A real client steps finer than this, but outside an important section no
    /// key is held, so nothing that depends on continuous cursor state is being judged —
    /// only miss sweeps, which a 60fps grid resolves identically. Stepping at 1ms here was
    /// measured as 16x the work for no gain in agreement.
    /// </summary>
    private const double host_frame_time = 1000.0 / 60;

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

            // While a button is held and the next frame is near, mid-frame times are
            // rejected outright by the replay handler, so gameplay advances frame to frame.
            bool important = frames[index].Actions.Count > 0 && nextFrameTime - time <= important_time_span;

            double proposed = important
                ? nextFrameTime
                : Math.Min(nextFrameTime, time + host_frame_time);

            if (double.IsPositiveInfinity(proposed))
                proposed = Math.Min(until, time + host_frame_time);

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
