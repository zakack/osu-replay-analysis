using System.Globalization;
using System.Text;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Scoring;
using osuTK;

namespace Sim;

/// <summary>
/// Where the hand was against when the finger fired, for every circle and slider head.
///
/// Aim error and hit error each describe the press alone. The failure this exists for is
/// neither: the cursor reaches the circle on time, and the press comes as it is already
/// leaving for the next one. On a hit that reads as an outbound press; past the circle's edge
/// it is a miss with the hand having been there. Telling those apart needs the cursor path
/// around the object, which geometry.csv does not carry.
///
/// Per object, all in <b>map time</b>, searched over the object's meh window either side of
/// its start (the span in which a press can judge it):
///
/// <list type="bullet">
/// <item><c>closestTime</c>/<c>closestDist</c>: the cursor's closest approach to the centre,
/// in radii. The recorder samples at 60Hz, so the nearest frame is refined with a parabola
/// through it and its neighbours, as tools/analysis/clicksync.py does.</item>
/// <item><c>enterTime</c>/<c>exitTime</c>: the first time the cursor is inside the circle
/// and the last, interpolated between frames. Blank if it never was.</item>
/// <item><c>pressTime</c>/<c>pressInside</c>: the press the simulation judged the object with.
/// Lazer only registers a press on a hovered circle, so a judged press is always inside; the
/// column is there so the reader need not know that.</item>
/// <item><c>firstPressAfter</c>: the first button-down after <c>enterTime</c> (after the
/// window's start if the cursor never entered), judged or not. On a miss this is the press
/// that came too late, if any came at all.</item>
/// <item><c>strayPressTime</c>/<c>strayOutcome</c>: a press that judged nothing, attributed to
/// this object. The simulator logs every press's fate (<see cref="Simulator.PressLog"/>);
/// one that did not hit — it hovered nothing, was refused by notelock, or hovered a circle
/// outside its window — goes to the most recent object the cursor had been inside, provided
/// it is no later than that object's start + meh + 100 ms and earlier than the next
/// object's start + its meh. This is the press a late-click miss was meant to be.</item>
/// </list>
///
/// Hit windows come from lazer (<c>HitWindows.WindowFor</c>), never derived here, so a
/// what-if can re-judge a moved press without porting the window arithmetic.
/// </summary>
public static class Clicks
{
    public const string CsvHeader =
        "replay,startTime,kind,radius,great,ok,meh,result,pressTime,pressInside,closestTime,closestDist,enterTime,exitTime,firstPressAfter,strayPressTime,strayOutcome";

    public static string ExtractCsv(ReplayRecord record, IReadOnlyDictionary<string, IndexEntry> index)
    {
        var score = ReplayLoader.Decode(record.Path, index);
        var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        var simulator = new Simulator { PressLog = [] };
        var simulation = simulator.Run(playable, score);
        var byObject = simulation.Objects.ToDictionary(o => o.HitObject, o => o);

        var frames = score.Replay.Frames.OfType<OsuReplayFrame>().ToArray();
        var path = frames.Select(f => (f.Time, f.Position)).ToArray();

        // Button-downs, either action. A press is an action present in a frame and absent in
        // the one before; a button change always forces a frame, so these times are exact.
        var presses = new List<double>();
        for (int i = 0; i < frames.Length; i++)
        {
            foreach (var action in frames[i].Actions)
            {
                if (i == 0 || !frames[i - 1].Actions.Contains(action))
                {
                    presses.Add(frames[i].Time);
                    break;
                }
            }
        }

        var rows = new List<Row>();

        foreach (var hitObject in playable.HitObjects.OfType<OsuHitObject>())
        {
            OsuHitObject? target = hitObject switch
            {
                HitCircle c => c,
                Slider s => s.NestedHitObjects.OfType<SliderHeadCircle>().FirstOrDefault(),
                _ => null,
            };
            if (target == null)
                continue;

            var state = byObject.GetValueOrDefault(target);
            var windows = target.HitWindows;
            double meh = windows.WindowFor(HitResult.Meh);
            double start = target.StartTime;
            double from = start - meh, to = start + meh;
            Vector2 centre = target.StackedPosition;
            float radius = (float)target.Radius;

            var (closestTime, closestDist) = closest(path, centre, from, to);
            var (enter, exit) = inside(path, centre, radius, from, to);

            double after = enter ?? from;
            int k = presses.BinarySearch(after);
            if (k < 0) k = ~k;

            rows.Add(new Row(
                start, hitObject is Slider ? "slider" : "circle", radius,
                windows.WindowFor(HitResult.Great), windows.WindowFor(HitResult.Ok), meh,
                state is { Judged: true } ? state.Result.ToString() : "Unjudged",
                state?.TimeOffset is { } offset ? start + offset : null,
                state?.CursorAtHit is { } at ? Vector2.Distance(at, centre) <= radius : null,
                closestTime, closestDist is { } d ? d / radius : null, enter, exit,
                k < presses.Count && presses[k] <= to + meh ? presses[k] : null));
        }

        // Presses that judged nothing, each to the most recent object the cursor had been
        // inside, within that object's late bound and before the next object's window closes.
        rows.Sort((x, y) => x.Start.CompareTo(y.Start));
        foreach (var (time, _, outcome) in simulator.PressLog!)
        {
            if (outcome == "hit")
                continue;

            for (int i = rows.Count - 1; i >= 0; i--)
            {
                var row = rows[i];
                if (row.Start > time + row.Meh)
                    continue;
                if (row.Enter is not { } enter || enter > time)
                    continue;
                if (time > row.Start + row.Meh + 100)
                    break;
                if (i + 1 < rows.Count && time >= rows[i + 1].Start + rows[i + 1].Meh)
                    break;

                if (row.StrayTime == null)
                {
                    row.StrayTime = time;
                    row.StrayOutcome = outcome;
                }
                break;
            }
        }

        var output = new StringBuilder();
        string replay = quote(Path.GetFileName(record.Path));

        foreach (var r in rows)
        {
            output.Append(replay).Append(',')
                  .Append(f(r.Start)).Append(',')
                  .Append(r.Kind).Append(',')
                  .Append(r.Radius.ToString("0.###", CultureInfo.InvariantCulture)).Append(',')
                  .Append(f(r.Great)).Append(',').Append(f(r.Ok)).Append(',').Append(f(r.Meh)).Append(',')
                  .Append(r.Result).Append(',')
                  .Append(f(r.Press)).Append(',')
                  .Append(r.PressInside is { } p ? (p ? "1" : "0") : string.Empty).Append(',')
                  .Append(f(r.ClosestTime)).Append(',')
                  .Append(r.ClosestDist is { } d ? d.ToString("0.###", CultureInfo.InvariantCulture) : string.Empty).Append(',')
                  .Append(f(r.Enter)).Append(',')
                  .Append(f(r.Exit)).Append(',')
                  .Append(f(r.FirstPressAfter)).Append(',')
                  .Append(f(r.StrayTime)).Append(',')
                  .Append(r.StrayOutcome ?? string.Empty).Append('\n');
        }

        return output.ToString();
    }

    private sealed record Row(
        double Start, string Kind, float Radius, double Great, double Ok, double Meh, string Result,
        double? Press, bool? PressInside, double? ClosestTime, double? ClosestDist,
        double? Enter, double? Exit, double? FirstPressAfter)
    {
        public double? StrayTime { get; set; }
        public string? StrayOutcome { get; set; }
    }

    /// <summary>
    /// Closest approach in [from, to]. The best frame is refined by a parabola through it and
    /// its neighbours (distance is locally quadratic near a minimum); at the window's edge, or
    /// where the fit is not a minimum, the frame itself stands.
    /// </summary>
    private static (double? Time, double? Dist) closest((double Time, Vector2 Position)[] path, Vector2 centre, double from, double to)
    {
        int lo = lowerBound(path, from);
        int best = -1;
        float bestD = float.MaxValue;
        for (int i = lo; i < path.Length && path[i].Time <= to; i++)
        {
            float d = Vector2.Distance(path[i].Position, centre);
            if (d < bestD) { bestD = d; best = i; }
        }
        if (best < 0)
            return (null, null);

        double t = path[best].Time, dist = bestD;
        if (best > 0 && best < path.Length - 1)
        {
            double t0 = path[best - 1].Time, t1 = t, t2 = path[best + 1].Time;
            double d0 = Vector2.Distance(path[best - 1].Position, centre), d1 = bestD, d2 = Vector2.Distance(path[best + 1].Position, centre);
            double denom = (t0 - t1) * (t0 - t2) * (t1 - t2);
            if (Math.Abs(denom) > 1e-9)
            {
                double a = (t2 * (d1 - d0) + t1 * (d0 - d2) + t0 * (d2 - d1)) / denom;
                double b = (t2 * t2 * (d0 - d1) + t1 * t1 * (d2 - d0) + t0 * t0 * (d1 - d2)) / denom;
                double c = (t1 * t2 * (t1 - t2) * d0 + t2 * t0 * (t2 - t0) * d1 + t0 * t1 * (t0 - t1) * d2) / denom;
                if (a > 0)
                {
                    double tv = -b / (2 * a);
                    if (tv >= t0 && tv <= t2)
                    {
                        t = tv;
                        dist = Math.Max(0, a * tv * tv + b * tv + c);
                    }
                }
            }
        }
        return (t, dist);
    }

    /// <summary>
    /// First and last time the cursor is within the radius in [from, to]. The path between
    /// frames is linear (lazer's replay handler interpolates it so), so each segment is
    /// intersected with the circle exactly: a fast jump can pass clean through a circle
    /// between two 17ms frames, and testing the frames alone would miss it.
    /// </summary>
    private static (double? Enter, double? Exit) inside((double Time, Vector2 Position)[] path, Vector2 centre, float radius, double from, double to)
    {
        double? enter = null, exit = null;
        int lo = Math.Max(1, lowerBound(path, from));
        double r2 = (double)radius * radius;

        for (int i = lo; i < path.Length && path[i - 1].Time <= to; i++)
        {
            var (ta, pa) = path[i - 1];
            var (tb, pb) = path[i];
            if (tb <= ta) continue;

            // |pa + s (pb - pa) - centre|^2 = r^2, for s in [0, 1].
            double dx = pb.X - pa.X, dy = pb.Y - pa.Y;
            double fx = pa.X - centre.X, fy = pa.Y - centre.Y;
            double qa = dx * dx + dy * dy, qb = 2 * (fx * dx + fy * dy), qc = fx * fx + fy * fy - r2;

            double s0, s1;
            if (qa < 1e-9)
            {
                if (qc > 0) continue;
                s0 = 0; s1 = 1;
            }
            else
            {
                double disc = qb * qb - 4 * qa * qc;
                if (disc < 0) continue;
                double root = Math.Sqrt(disc);
                s0 = Math.Max(0, (-qb - root) / (2 * qa));
                s1 = Math.Min(1, (-qb + root) / (2 * qa));
                if (s0 > s1) continue;
            }

            double t0 = Math.Max(ta + (tb - ta) * s0, from), t1 = Math.Min(ta + (tb - ta) * s1, to);
            if (t1 < t0) continue;
            enter ??= t0;
            exit = t1;
        }
        return (enter, exit);
    }

    private static int lowerBound((double Time, Vector2 Position)[] path, double time)
    {
        int lo = 0, hi = path.Length;
        while (lo < hi)
        {
            int mid = (lo + hi) / 2;
            if (path[mid].Time < time) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

    private static string f(double? value) =>
        value is { } v ? v.ToString("0.#", CultureInfo.InvariantCulture) : string.Empty;

    private static string quote(string value) => '"' + value.Replace("\"", "\"\"") + '"';
}
