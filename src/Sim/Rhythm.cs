using System.Globalization;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Scoring;

namespace Sim;

/// <summary>
/// Rhythmic structure, with no geometry in it at all.
///
/// Two nested groupings, because they answer different questions and one contains the other.
/// A <see cref="Group"/> is a gesture: events close enough together in time to be played
/// without resetting, snap allowed to change inside it. A <see cref="Run"/> is a maximal
/// stretch of one snap. Every run sits inside exactly one group, and a group holding more
/// than one run is a rhythm switch — the thing worth looking at hardest.
///
/// Names stay out of it. A run at a quarter of a beat is what players call a burst and a run
/// at a whole beat is not, but which word goes on which cell is presentation. Putting it in
/// the extractor turns a groupby into a vocabulary argument.
///
/// Two indices per event, and the distinction matters. A slider end occupies a tick and is
/// part of the rhythm, but it is tracked rather than tapped and produces no hit error. Its
/// place in the rhythm and its place among the taps are different numbers, and folding them
/// together would smear the position-in-run curve that is the whole measurement.
/// </summary>
public static class Rhythm
{
    /// <summary>One thing to do at one moment. Clicked events carry an error; the rest keep the rhythm honest.</summary>
    public sealed record Event(double Time, bool Clicked, double? Error, string Result, string Kind, string Action);

    /// <summary>Events and the stretches of time nothing may be grouped across.</summary>
    public sealed record Timeline(IReadOnlyList<Event> Events, IReadOnlyList<(double From, double To)> Barriers);

    /// <summary>A half-open span of the event list, [Start, End).</summary>
    public sealed record Span(int Start, int End, double Snap)
    {
        public int Length => End - Start;
    }

    /// <summary>Two gaps are the same snap when they differ by less than this many beats.</summary>
    public const double DefaultTolerance = 0.02;

    /// <summary>Gaps at or under this many beats keep a group together.</summary>
    public const double DefaultGroupGap = 1.0;

    /// <summary>Snaps worth naming. A gap matching none of them is reported raw.</summary>
    private static readonly double[] lattice = [0.125, 1.0 / 6, 0.25, 1.0 / 3, 0.5, 2.0 / 3, 0.75, 1.0, 1.5, 2.0];

    public static Timeline Build(IBeatmap playable, Score score, Simulator? simulator = null)
    {
        var simulation = (simulator ?? new Simulator()).Run(playable, score);
        var byObject = simulation.Objects.ToDictionary(o => (HitObject)o.HitObject, o => o);

        var events = new List<Event>();
        var barriers = new List<(double, double)>();

        foreach (var hitObject in playable.HitObjects.OfType<OsuHitObject>().OrderBy(h => h.StartTime))
        {
            switch (hitObject)
            {
                case Spinner spinner:
                    // A spinner is not a tap, and nothing either side of it is one gesture
                    // with it, so it breaks any grouping that would span it.
                    barriers.Add((spinner.StartTime, spinner.EndTime));
                    continue;

                case Slider slider:
                    // The head, not the slider. A Slider carries its own judgement, awarded
                    // at the end and worth IgnoreHit, so looking it up here would file the
                    // slider's end time as the player's tap error.
                    var head = slider.NestedHitObjects.OfType<SliderHeadCircle>().FirstOrDefault();
                    events.Add(clicked(head ?? (OsuHitObject)slider, byObject, "sliderHead"));

                    foreach (var repeat in slider.NestedHitObjects.OfType<SliderRepeat>().OrderBy(r => r.StartTime))
                        events.Add(new Event(repeat.StartTime, false, null, "-", "sliderRepeat", "-"));

                    if (slider.EndTime > slider.StartTime)
                        events.Add(new Event(slider.EndTime, false, null, "-", "sliderEnd", "-"));

                    continue;

                case HitCircle circle:
                    events.Add(clicked(circle, byObject, "circle"));
                    continue;
            }
        }

        events.Sort((a, b) => a.Time.CompareTo(b.Time));

        return new Timeline(events, barriers);
    }

    /// <summary>
    /// Whether two adjacent events can belong to the same anything: no timing point change
    /// between them, no spinner across them, and a gap that is positive and not absurd.
    /// </summary>
    private static bool joinable(IBeatmap playable, Timeline timeline, int i, out double gapBeats)
    {
        var previous = timeline.Events[i - 1];
        var timingPoint = playable.ControlPointInfo.TimingPointAt(previous.Time);

        gapBeats = (timeline.Events[i].Time - previous.Time) / timingPoint.BeatLength;

        // A timing point change splits everything. Two beat lengths inside one grouping makes
        // the snap of at least one gap meaningless, and a mapper who changes BPM mid-phrase
        // has written two phrases.
        return gapBeats > 0
               && ReferenceEquals(timingPoint, playable.ControlPointInfo.TimingPointAt(timeline.Events[i].Time))
               && !timeline.Barriers.Any(b => b.To > previous.Time && b.From < timeline.Events[i].Time);
    }

    /// <summary>Gestures: maximal stretches whose every gap is at or under <paramref name="gapBeats"/>.</summary>
    public static IReadOnlyList<Span> Groups(IBeatmap playable, Timeline timeline, double gapBeats)
    {
        var spans = new List<Span>();
        int start = 0;

        for (int i = 1; i <= timeline.Events.Count; i++)
        {
            bool breaks = i == timeline.Events.Count
                          || !joinable(playable, timeline, i, out double gap)
                          || gap > gapBeats + 1e-9;

            if (!breaks)
                continue;

            if (i - start >= 3)
                spans.Add(new Span(start, i, double.NaN));

            start = i;
        }

        return spans;
    }

    /// <summary>Maximal stretches sharing one snap.</summary>
    public static IReadOnlyList<Span> Runs(IBeatmap playable, Timeline timeline, double tolerance)
    {
        var spans = new List<Span>();
        int start = -1;
        double snap = double.NaN;

        for (int i = 1; i < timeline.Events.Count; i++)
        {
            bool ok = joinable(playable, timeline, i, out double gap) && gap <= lattice[^1] + tolerance;

            if (ok && start >= 0 && Math.Abs(gap - snap) < tolerance)
                continue;

            if (start >= 0 && i - start >= 3)
                spans.Add(new Span(start, i, Quantise(snap, tolerance)));

            if (ok)
            {
                start = i - 1;
                snap = gap;
            }
            else
            {
                start = -1;
            }
        }

        if (start >= 0 && timeline.Events.Count - start >= 3)
            spans.Add(new Span(start, timeline.Events.Count, Quantise(snap, tolerance)));

        return spans;
    }

    /// <summary>The lattice entry this snap sits on, or the raw value when it sits on none.</summary>
    public static double Quantise(double snap, double tolerance) =>
        lattice.FirstOrDefault(c => Math.Abs(snap - c) < tolerance, snap);

    /// <summary>
    /// Whether everything between a span's ends is a circle. A burst may be entered off a
    /// slider end and left into a slider head — that is fingering continuity, and both are
    /// real rhythmic members — but a slider in the middle makes it a different shape rather
    /// than a longer one.
    /// </summary>
    public static bool InteriorAllCircles(Timeline timeline, Span span)
    {
        for (int i = span.Start + 1; i < span.End - 1; i++)
        {
            if (timeline.Events[i].Kind != "circle")
                return false;
        }

        return true;
    }

    public const string CsvHeader =
        "replay,groupGap,tolerance,groupId,groupSize,groupIndex,runId,runSize,runIndex,snap,"
        + "interiorCircles,runFirstKind,runLastKind,clickIndex,time,beatLength,kind,result,error,action";

    public static void WriteCsv(
        string replay, IBeatmap playable, Timeline timeline,
        double groupGap, double tolerance, TextWriter output)
    {
        var groups = Groups(playable, timeline, groupGap);
        var runs = Runs(playable, timeline, tolerance);

        var runOf = new int[timeline.Events.Count];
        Array.Fill(runOf, -1);

        for (int r = 0; r < runs.Count; r++)
        {
            for (int i = runs[r].Start; i < runs[r].End; i++)
                runOf[i] = r;
        }

        for (int g = 0; g < groups.Count; g++)
        {
            var group = groups[g];
            int clickIndex = 0;

            for (int i = group.Start; i < group.End; i++)
            {
                var e = timeline.Events[i];
                int r = runOf[i];
                var run = r >= 0 ? runs[r] : null;

                output.WriteLine(string.Create(CultureInfo.InvariantCulture,
                    $"{quote(replay)},{groupGap:0.####},{tolerance:0.####},"
                    + $"{g},{group.Length},{i - group.Start},"
                    + $"{r},{run?.Length ?? 0},{(run != null ? i - run.Start : -1)},"
                    + $"{(run != null ? run.Snap.ToString("0.####", CultureInfo.InvariantCulture) : string.Empty)},"
                    + $"{(run != null && InteriorAllCircles(timeline, run) ? 1 : 0)},"
                    + $"{(run != null ? timeline.Events[run.Start].Kind : string.Empty)},"
                    + $"{(run != null ? timeline.Events[run.End - 1].Kind : string.Empty)},"
                    + $"{(e.Clicked ? clickIndex : -1)},{e.Time:0.###},"
                    + $"{playable.ControlPointInfo.TimingPointAt(e.Time).BeatLength:0.###},"
                    + $"{e.Kind},{e.Result},"
                    + $"{(e.Error is { } err ? err.ToString("0.###", CultureInfo.InvariantCulture) : string.Empty)},{e.Action}"));

                if (e.Clicked)
                    clickIndex++;
            }
        }
    }

    private static Event clicked(OsuHitObject hitObject, Dictionary<HitObject, ObjectState> byObject, string kind)
    {
        var state = byObject.GetValueOrDefault(hitObject);

        return new Event(
            hitObject.StartTime,
            true,
            state?.TimeOffset,
            state is { Judged: true } ? state.Result.ToString() : "Unjudged",
            kind,
            state?.HitAction?.ToString() ?? "-");
    }

    private static string quote(string value) => '"' + value.Replace("\"", "\"\"") + '"';
}
