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
/// Reproduces the cadence at which lazer actually samples a replay.
///
/// Two mechanisms combine to produce it, and getting either wrong changes results.
/// <see cref="osu.Game.Rulesets.Replays.FramedReplayInputHandler.SetFrameFromTime"/> snaps
/// the clock to precisely a replay frame's time whenever one is crossed, so key state
/// changes are always evaluated exactly on a frame boundary where interpolation is exact.
/// <c>FrameStabilityContainer</c> then caps any single advance at one 60fps step, so long
/// gaps between frames are still sampled at a bounded rate — which is what makes slider
/// tracking and spinner rotation come out close to the client rather than wildly off.
/// </summary>
public sealed class ReplaySampler(Replay replay)
{
    private const double sixty_frame_time = 1000.0 / 60;

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
            double proposed = Math.Min(nextFrameTime, time + sixty_frame_time);

            if (double.IsPositiveInfinity(proposed))
                proposed = Math.Min(until, time + sixty_frame_time);

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
