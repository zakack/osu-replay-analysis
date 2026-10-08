using System.Globalization;
using System.Text;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Difficulty.Preprocessing;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Difficulty.Preprocessing;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Utils;
using osuTK;

namespace Sim;

/// <summary>
/// When the cursor sets off toward each object, against when that object appeared.
///
/// Hit error and aim error both describe the moment of the press. Neither says when the hand
/// committed to the jump, and that is the quantity a change in how early a circle can be seen
/// (approach rate, a brighter display) would move first. So for every directed jump this
/// reports three onset times, defined differently on purpose, and leaves the choice to the
/// analysis:
///
/// <list type="bullet">
/// <item><c>onsetVel</c>: the first time the cursor's speed toward the target reaches a
/// quarter of the jump's average required speed. Speed is a central difference over
/// ±<see cref="half_window"/> ms of interpolated path, about two recorder frames each side,
/// never adjacent frames: at 60Hz an adjacent-frame difference is mostly sampling noise.</item>
/// <item><c>half</c>: the first time the remaining distance falls to half the starting one.
/// A midpoint, not an onset, but the least sensitive to how the hand idles before leaving.</item>
/// <item><c>leave</c>: the first time the cursor is half a radius from where it was when the
/// previous object ended, whichever way it went.</item>
/// </list>
///
/// Every time is in <b>map time</b>, the timebase replay frames and object times share, so it
/// is directly comparable with <c>startTime</c>, <c>appearTime</c> and <c>prevTime</c>. Under
/// a rate mod the player experiences map time divided by <c>rate</c>, which is emitted so the
/// analysis can convert; derived quantities such as onset minus appearance are left to it.
///
/// The search runs from the end of the previous object to <see cref="window_after"/> ms past
/// this one. A hand that left before the previous object ended reports the end itself, which
/// the analysis can recognise as onset equal to <c>prevTime</c>; one that never qualifies in
/// the window leaves the column blank. Jumps shorter than a radius, measured from where the
/// cursor actually was, are skipped: there is no direction to set off in.
/// </summary>
public static class Onset
{
    private const double normalised_radius = OsuDifficultyHitObject.NORMALISED_RADIUS;
    private const double half_window = 17;
    private const double window_after = 50;
    private const double velocity_fraction = 0.25;

    public const string CsvHeader =
        "replay,startTime,appearTime,prevTime,preemptMs,rate,spacingRadii,startDistRadii,onsetVel,half,leave";

    public static string ExtractCsv(ReplayRecord record, IReadOnlyDictionary<string, IndexEntry> index)
    {
        var score = ReplayLoader.Decode(record.Path, index);
        var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        double rate = ModUtils.CalculateRateWithMods(score.ScoreInfo.Mods);
        var path = score.Replay.Frames.OfType<OsuReplayFrame>().Select(f => (f.Time, f.Position)).ToArray();

        var difficulty = new List<DifficultyHitObject>(playable.HitObjects.Count);
        for (int i = 1; i < playable.HitObjects.Count; i++)
            difficulty.Add(new OsuDifficultyHitObject(playable.HitObjects[i], playable.HitObjects[i - 1], rate, difficulty, difficulty.Count));

        var output = new StringBuilder();
        string replay = quote(Path.GetFileName(record.Path));

        foreach (OsuDifficultyHitObject current in difficulty)
        {
            var target = (OsuHitObject)current.BaseObject;
            if (target is Spinner || current.Previous(0) is not OsuDifficultyHitObject previous || previous.BaseObject is Spinner)
                continue;

            double spacing = current.LazyJumpDistance / normalised_radius;
            if (spacing < 1)
                continue;

            double prevTime = previous.BaseObject.GetEndTime();
            double startTime = target.StartTime;
            if (startTime <= prevTime || at(path, prevTime) is not { } origin)
                continue;

            float radius = (float)target.Radius;
            Vector2 aim = target.StackedPosition;
            float startDist = Vector2.Distance(origin, aim);
            if (startDist < radius)
                continue;

            double end = startTime + window_after;
            var direction = (aim - origin) / startDist;
            double required = startDist / (startTime - prevTime);

            double? onsetVel = null, half = null, leave = null;

            // The frame times inside the window, plus its start, are where the path changes
            // slope; between them it is linear, so threshold crossings are interpolated there.
            int first = lowerBound(path, prevTime);
            double lastT = prevTime;
            float lastRemaining = startDist, lastMoved = 0;

            for (int i = first; i < path.Length && path[i].Time <= end && (onsetVel == null || half == null || leave == null); i++)
            {
                double t = path[i].Time;
                if (t <= prevTime)
                    continue;

                var p = path[i].Position;
                float remaining = Vector2.Distance(p, aim);
                float moved = Vector2.Distance(p, origin);

                if (half == null && remaining <= startDist / 2)
                    half = cross(lastT, lastRemaining, t, remaining, startDist / 2);

                if (leave == null && moved >= radius / 2)
                    leave = cross(lastT, lastMoved, t, moved, radius / 2);

                if (onsetVel == null && at(path, t - half_window) is { } a && at(path, t + half_window) is { } b
                    && Vector2.Dot(b - a, direction) / (2 * half_window) >= velocity_fraction * required)
                    onsetVel = t;

                lastT = t;
                lastRemaining = remaining;
                lastMoved = moved;
            }

            output.Append(replay).Append(',')
                  .Append(f(startTime)).Append(',')
                  .Append(f(startTime - target.TimePreempt)).Append(',')
                  .Append(f(prevTime)).Append(',')
                  .Append(f(target.TimePreempt)).Append(',')
                  .Append(rate.ToString("0.####", CultureInfo.InvariantCulture)).Append(',')
                  .Append(spacing.ToString("0.####", CultureInfo.InvariantCulture)).Append(',')
                  .Append((startDist / radius).ToString("0.####", CultureInfo.InvariantCulture)).Append(',')
                  .Append(f(onsetVel)).Append(',')
                  .Append(f(half)).Append(',')
                  .Append(f(leave)).Append('\n');
        }

        return output.ToString();
    }

    /// <summary>
    /// Every record's rows, in record order. Work is done in parallel blocks and each block
    /// written before the next starts, so the file is byte-identical at any thread count and
    /// the whole corpus is never held in memory at once.
    /// </summary>
    public static (int Done, int Failed) WriteAll(IReadOnlyList<ReplayRecord> records,
                                                  IReadOnlyDictionary<string, IndexEntry> index,
                                                  TextWriter output, TextWriter? progress = null)
    {
        output.Write(CsvHeader + "\n");
        if (records.Count == 0)
            return (0, 0);

        int done = 0, failed = 0;
        var clock = System.Diagnostics.Stopwatch.StartNew();

        // One replay first, alone: osu.Game's beatmap decoders register into a lazily built
        // static dictionary, and racing the pool into it is how a long run dies (see Batch).
        string? one(int i)
        {
            try { return ExtractCsv(records[i], index); }
            catch (Exception e)
            {
                if (Interlocked.Increment(ref failed) <= 5)
                    Console.Error.WriteLine($"  {Path.GetFileName(records[i].Path)}: {e.GetType().Name}: {e.Message}");
                return null;
            }
        }

        output.Write(one(0));
        done = 1;

        const int block = 512;
        for (int start = 1; start < records.Count; start += block)
        {
            int count = Math.Min(block, records.Count - start);
            var chunk = new string?[count];
            Parallel.For(0, count, new ParallelOptions { MaxDegreeOfParallelism = Batch.Threads }, j => chunk[j] = one(start + j));

            foreach (var text in chunk)
                output.Write(text);

            done += count;
            if (progress != null)
                progress.Write($"\r  {done,7:N0} / {records.Count:N0}   {done / clock.Elapsed.TotalSeconds,5:F0}/s");
        }

        progress?.Write("\n");
        return (done - failed, failed);
    }

    private static double cross(double t0, float v0, double t1, float v1, float level) =>
        v1 == v0 ? t1 : t0 + (t1 - t0) * (level - v0) / (v1 - v0);

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

    /// <summary>The cursor at a time, interpolated linearly the way lazer's replay handler does.</summary>
    private static Vector2? at((double Time, Vector2 Position)[] path, double time)
    {
        if (path.Length == 0 || time < path[0].Time || time > path[^1].Time)
            return null;

        int hi = lowerBound(path, time);
        if (hi == 0)
            return path[0].Position;

        var (t0, p0) = path[hi - 1];
        var (t1, p1) = path[hi];
        return t1 > t0 ? Vector2.Lerp(p0, p1, (float)((time - t0) / (t1 - t0))) : p1;
    }

    private static string f(double? value) =>
        value is { } v ? v.ToString("0.#", CultureInfo.InvariantCulture) : string.Empty;

    private static string quote(string value) => '"' + value.Replace("\"", "\"\"") + '"';
}
