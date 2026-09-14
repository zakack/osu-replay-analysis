using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Objects.Types;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using osu.Game.Utils;
using osuTK;

namespace Sim;

/// <summary>
/// Everything a replay viewer needs to draw one play, computed here so that the viewer
/// never has to.
///
/// The point of this command is the division in CLAUDE.md's non-goals: the viewer draws the
/// shim's precomputed polyline and never recomputes curves. Slider paths are four curve
/// types with a fallback rule, a truncation rule and an approximation tolerance, and every
/// one of those failure modes shifts positions a few units without ever throwing — so a
/// bezier reimplemented in JS would disagree with the game slightly, everywhere, silently.
/// Lazer has already approximated each path to the piecewise-linear polyline that *is* the
/// path for every gameplay purpose. Emit that polyline and the question is closed.
///
/// The same reasoning does not extend to interpolation. The viewer still has to turn a time
/// into a position along the polyline, because doing it here would mean sampling the path at
/// some fixed rate and shipping the result, which is both larger and worse than the
/// arithmetic it replaces. That arithmetic is a cross-check away from being proven, so
/// <see cref="CrossCheck"/> runs it against lazer's own nested object positions and the
/// command refuses to be trusted without it.
/// </summary>
public static class Scene
{
    /// <summary>
    /// Bumped when the shape of the emitted document changes. The viewer reads this and
    /// refuses a document it does not understand, rather than drawing a plausible-looking
    /// wrong play from fields that moved.
    /// </summary>
    public const int SchemaVersion = 1;

    /// <summary>Lazer's playfield, in osu!pixels. Every position below is in this space.</summary>
    private const float playfield_width = 512;
    private const float playfield_height = 384;

    /// <summary>Bitmask for <see cref="Frames"/>. Smoke is not a gameplay action and is dropped.</summary>
    private const int left_button = 1;
    private const int right_button = 2;

    public sealed record Document(
        int SchemaVersion,
        string Replay,
        string BeatmapMd5,
        string ClientVersion,
        IReadOnlyList<string> Mods,
        double Rate,
        BeatmapInfo Beatmap,
        Playfield Playfield,
        Dictionary<string, double> HitWindows,
        IReadOnlyList<SceneObject> Objects,
        int FrameCount,
        IReadOnlyList<double> Frames);

    public sealed record BeatmapInfo(
        string Artist,
        string Title,
        string Difficulty,
        double CircleSize,
        double ApproachRate,
        double OverallDifficulty,
        double Radius,
        double TimePreempt,
        double TimeFadeIn);

    public sealed record Playfield(float Width, float Height);

    /// <summary>
    /// One top-level object. Positions are stacked play coordinates: the stack offset is
    /// already applied, because the <c>.osu</c> stores pre-stack positions and a viewer that
    /// drew those would put every stacked pattern in the wrong place.
    /// </summary>
    public sealed record SceneObject(
        int I,
        string Type,
        double T,
        float X,
        float Y,
        int ComboIndex,
        int IndexInCombo,
        bool NewCombo,
        string Result,
        double? Error,
        double? HitTime,
        float? Hx,
        float? Hy,
        double? EndTime,
        int? Spans,
        double? SpanDuration,
        double? Distance,
        double? Velocity,
        int? SpinsRequired,
        IReadOnlyList<float>? Path,
        IReadOnlyList<SceneNested>? Nested);

    /// <summary>
    /// A slider's nested objects. <paramref name="Progress"/> is the fraction of one span
    /// they sit at, which is null for head and tail because those are the span endpoints and
    /// lazer does not store one for them.
    /// </summary>
    public sealed record SceneNested(
        string Type,
        double T,
        float X,
        float Y,
        double? Progress,
        string Result,
        double? Error);

    public static Document Build(string replayPath, IBeatmap playable, Score score, string beatmapMd5)
    {
        var simulation = new Simulator().Run(playable, score);
        var byObject = simulation.Objects.ToDictionary(o => (HitObject)o.HitObject, o => o);

        // A spinner carries empty hit windows and zero preempt, so the timing constants come
        // from a circle. Same reason Flam takes them there — but nested objects are searched
        // too, because a map can open with a slider and its head circle carries the same
        // radius, preempt and windows. Slider ends are excluded: they derive from HitCircle
        // in the model but carry empty hit windows, and reading those would report every
        // window as zero.
        var circle = playable.HitObjects
                             .SelectMany(o => o.NestedHitObjects.Prepend(o))
                             .OfType<HitCircle>()
                             .FirstOrDefault(c => c is not SliderEndCircle)
                     ?? throw new InvalidDataException(
                         "beatmap has no hit circle or slider head to read timing constants from");

        var objects = new List<SceneObject>(playable.HitObjects.Count);
        int index = 0;

        foreach (var hitObject in playable.HitObjects.Cast<OsuHitObject>().OrderBy(o => o.StartTime))
        {
            var combo = hitObject as IHasComboInformation;
            var clicked = clickState(hitObject, byObject);

            objects.Add(new SceneObject(
                index++,
                hitObject switch { Slider => "slider", Spinner => "spinner", _ => "circle" },
                round(hitObject.StartTime),
                round(hitObject.StackedPosition.X),
                round(hitObject.StackedPosition.Y),
                combo?.ComboIndex ?? 0,
                combo?.IndexInCurrentCombo ?? 0,
                combo?.NewCombo ?? false,
                result(clicked),
                clicked?.TimeOffset is { } e ? round(e) : null,
                clicked?.JudgementTime is { } j ? round(j) : null,
                clicked?.CursorAtHit is { } c ? round(c.X) : null,
                clicked?.CursorAtHit is { } c2 ? round(c2.Y) : null,
                hitObject switch
                {
                    Slider slider => round(slider.EndTime),
                    Spinner spinner => round(spinner.EndTime),
                    _ => null
                },
                (hitObject as Slider)?.SpanCount(),
                hitObject is Slider s1 ? round(s1.SpanDuration) : null,
                hitObject is Slider s2 ? round(s2.Path.Distance) : null,
                hitObject is Slider s3 ? progress(s3.Velocity) : null,
                (hitObject as Spinner)?.SpinsRequired,
                hitObject is Slider s4 ? polyline(s4) : null,
                hitObject is Slider s5 ? nested(s5, byObject) : null));
        }

        return new Document(
            SchemaVersion,
            Path.GetFileNameWithoutExtension(replayPath),
            beatmapMd5,
            score.ScoreInfo.ClientVersion,
            score.ScoreInfo.Mods.Select(m => m.Acronym).ToArray(),
            ModUtils.CalculateRateWithMods(score.ScoreInfo.Mods),
            new BeatmapInfo(
                playable.Metadata.Artist,
                playable.Metadata.Title,
                playable.BeatmapInfo.DifficultyName,
                playable.Difficulty.CircleSize,
                playable.Difficulty.ApproachRate,
                playable.Difficulty.OverallDifficulty,
                round(circle.Radius),
                round(circle.TimePreempt),
                round(circle.TimeFadeIn)),
            new Playfield(playfield_width, playfield_height),
            new Dictionary<string, double>
            {
                ["great"] = circle.HitWindows.WindowFor(HitResult.Great),
                ["ok"] = circle.HitWindows.WindowFor(HitResult.Ok),
                ["meh"] = circle.HitWindows.WindowFor(HitResult.Meh)
            },
            objects,
            score.Replay.Frames.Count,
            frames(score));
    }

    /// <summary>
    /// The path lazer actually uses, in stacked play coordinates, flattened to
    /// <c>[x0, y0, x1, y1, ...]</c>.
    ///
    /// <see cref="SliderPath.CalculatedPath"/> is the piecewise-linear approximation after
    /// <c>calculateLength</c> has trimmed or extended its last vertex to match the
    /// <c>pixelLength</c> from the <c>.osu</c>, so it needs neither. Vertices are relative to
    /// the slider's position; adding the stacked position puts them in the same space as the
    /// cursor frames.
    /// </summary>
    private static float[] polyline(Slider slider)
    {
        var vertices = slider.Path.CalculatedPath;
        var origin = slider.StackedPosition;
        var flat = new float[vertices.Count * 2];

        for (int i = 0; i < vertices.Count; i++)
        {
            flat[i * 2] = round(origin.X + vertices[i].X);
            flat[i * 2 + 1] = round(origin.Y + vertices[i].Y);
        }

        return flat;
    }

    private static SceneNested[] nested(Slider slider, Dictionary<HitObject, ObjectState> byObject)
    {
        var rows = new List<SceneNested>(slider.NestedHitObjects.Count);

        foreach (OsuHitObject part in slider.NestedHitObjects.Cast<OsuHitObject>())
        {
            var state = byObject.GetValueOrDefault(part);

            rows.Add(new SceneNested(
                part switch
                {
                    SliderHeadCircle => "head",
                    SliderRepeat => "repeat",
                    SliderTailCircle => "tail",
                    SliderTick => "tick",
                    _ => part.GetType().Name
                },
                round(part.StartTime),
                round(part.StackedPosition.X),
                round(part.StackedPosition.Y),
                part switch
                {
                    SliderTick tick => progress(tick.PathProgress),
                    SliderRepeat repeat => progress(repeat.PathProgress),
                    _ => null
                },
                result(state),
                state?.TimeOffset is { } e ? round(e) : null));
        }

        return rows.ToArray();
    }

    /// <summary>
    /// Every recorded frame, flattened to <c>[t, x, y, buttons, ...]</c>.
    ///
    /// These are the frames as written, not the simulator's resampling of them. The recorder
    /// stores position at 60Hz and takes an extra frame whenever a button changes state, so
    /// what is here is a real sample at a real instant; the cadence the simulator evaluates
    /// at is its model of some host's frame rate, and shipping that instead would hand the
    /// viewer an invented signal at a higher rate than the file contains. The viewer should
    /// interpolate linearly between these, which is what playback does.
    ///
    /// <see cref="ReplaySampler.FrameShift"/> is deliberately not applied. It is a diagnostic
    /// for sweeping the residual against the header, and it has no business moving the
    /// cursor in something a player looks at. The consequence is worth knowing: with
    /// <c>ORA_FRAME_SHIFT_MS</c> set, the judgements in this document were computed against
    /// shifted frames while the frames themselves are unshifted, so the two halves disagree.
    /// The same goes for <c>ORA_HOST_STEP_MS</c> and <c>ORA_TAIL_TRIM_MS</c>, which steer the
    /// simulation and not the recording. They are sweep knobs; do not leave them set.
    /// </summary>
    private static double[] frames(Score score)
    {
        var recorded = score.Replay.Frames.Cast<OsuReplayFrame>().ToArray();
        var flat = new double[recorded.Length * 4];

        for (int i = 0; i < recorded.Length; i++)
        {
            var frame = recorded[i];
            int buttons = 0;

            if (frame.Actions.Contains(OsuAction.LeftButton)) buttons |= left_button;
            if (frame.Actions.Contains(OsuAction.RightButton)) buttons |= right_button;

            flat[i * 4] = round(frame.Time);
            flat[i * 4 + 1] = round(frame.Position.X);
            flat[i * 4 + 2] = round(frame.Position.Y);
            flat[i * 4 + 3] = buttons;
        }

        return flat;
    }

    /// <summary>
    /// A slider's own judgement is awarded at its end; the tap is on its head. Looking up the
    /// slider would file the end time as the hit error, which is the bug Geometry documents.
    /// </summary>
    private static ObjectState? clickState(OsuHitObject hitObject, Dictionary<HitObject, ObjectState> byObject)
    {
        if (hitObject is Slider slider)
        {
            var head = slider.NestedHitObjects.OfType<SliderHeadCircle>().FirstOrDefault();
            return head == null ? null : byObject.GetValueOrDefault(head);
        }

        return byObject.GetValueOrDefault(hitObject);
    }

    private static string result(ObjectState? state) =>
        state is { Judged: true } ? state.Result.ToString() : "Unjudged";

    /// <summary>
    /// Two decimal places is finer than a 60Hz recorder resolves and roughly halves the
    /// document. Positions are floats to keep them out of scientific notation in JSON.
    /// </summary>
    private static float round(float value) => MathF.Round(value, 2);

    private static double round(double value) => Math.Round(value, 2);

    /// <summary>
    /// Six decimals, for the two quantities that are ratios rather than pixels.
    ///
    /// Path progress is a fraction of a span, so two decimals is not the sub-pixel precision
    /// it is everywhere else — it is a third of a percent of the slider's whole length, which
    /// on a 441px path is two thirds of a pixel. The cross-check found this: a tick at 2/3
    /// rounded to 0.67 and the walk landed half a pixel past it. Velocity is px/ms and runs
    /// around 0.5, where two decimals is a one percent error that a viewer integrating it
    /// over a slider's duration spends as a couple of pixels at the tail.
    /// </summary>
    private static double progress(double value) => Math.Round(value, 6);

    public static void Write(Document document, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);

        // Camel case and no nulls: the viewer reads these names directly, and dropping the
        // fields that do not apply to a circle is most of the file.
        var options = new JsonSerializerOptions
        {
            WriteIndented = false,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
        };

        using var stream = File.Create(destination);
        JsonSerializer.Serialize(stream, document, options);
    }

    public static string Summarise(Document document, long bytes)
    {
        int sliders = document.Objects.Count(o => o.Type == "slider");
        int vertices = document.Objects.Sum(o => (o.Path?.Count ?? 0) / 2);

        return string.Create(CultureInfo.InvariantCulture,
            $"{document.Objects.Count} objects ({sliders} sliders, {vertices} path vertices), "
            + $"{document.FrameCount} frames, {bytes / 1024.0:0.#} KiB");
    }

    /// <summary>
    /// What <see cref="CrossCheck"/> found. The two halves are counted apart because only
    /// the first one can contain a defect.
    /// </summary>
    public sealed record CrossCheckResult(
        int Checked,
        int Diverged,
        double Worst,
        int OptimisedSliders,
        int OptimisedChecked,
        double OptimisedWorst);

    /// <summary>
    /// The differential harness for the one thing the viewer still has to compute.
    ///
    /// Walking a polyline by arc length is arithmetic rather than geometry, but CLAUDE.md's
    /// rule is that anything reimplemented gets an oracle before it is trusted, and lazer
    /// supplies one for free: every slider tick and repeat carries both a
    /// <c>PathProgress</c> and the position lazer placed it at. Walking to
    /// <c>Distance * progress</c> in the emitted vertices must land on that position. If it
    /// does not, the viewer's contract is already wrong and no JS has been written yet.
    ///
    /// Two cases are known to leave <c>Distance</c> longer than the polyline, and both are
    /// counted separately rather than called failures:
    ///
    /// - <b>Catmull optimisation.</b> <see cref="Slider"/> sets <c>OptimiseCatmull</c>, so
    ///   lazer removes the bulb vertices around a repeated knot and adds their length to the
    ///   distance anyway. The removed length sits at the knots rather than spread along the
    ///   path, so neither scaling by <c>Distance</c> nor by the polyline's own length
    ///   recovers it — measured at 0.29px and 0.24px respectively on the one affected slider
    ///   in 8,466. That is a twentieth of a percent of its length and a hundredth of a follow
    ///   circle, on a legacy curve type, in a layer whose job is to draw. Correcting it would
    ///   mean reproducing <c>SliderPath</c>'s private cumulative-length table, which is the
    ///   edge-case ownership CLAUDE.md exists to refuse.
    /// - <b>Floating-point accumulation.</b> The walk sums segment lengths as it goes, so at
    ///   progress 1 its running total can land a hair under <c>Distance</c> and fall out of
    ///   the loop. Clamping to the last vertex is what <c>SliderPath.interpolateVertices</c>
    ///   does at that boundary, so it is what this — and the viewer — must do too.
    ///
    /// Catmull is the only mechanism that leaves the two genuinely different. A path whose
    /// last two control points coincide looks like a second one and is not: lazer declines to
    /// extend it and then stores the vertex sum as the distance, rather than the longer
    /// <c>pixelLength</c> from the <c>.osu</c>, so the two still agree.
    /// </summary>
    public static CrossCheckResult CrossCheck(Document document)
    {
        int compared = 0, diverged = 0;
        int optimisedSliders = 0, optimisedCompared = 0;
        double worst = 0, optimisedWorst = 0;

        foreach (var scene in document.Objects)
        {
            if (scene.Path is not { Count: >= 4 } path || scene.Distance is not { } distance || scene.Nested == null)
                continue;

            // Length the polyline actually carries. Short of the distance means lazer dropped
            // vertices it still counts, which is a known cause rather than a divergence.
            double carried = 0;

            for (int i = 1; i < path.Count / 2; i++)
                carried += Vector2.Distance(
                    new Vector2(path[(i - 1) * 2], path[(i - 1) * 2 + 1]),
                    new Vector2(path[i * 2], path[i * 2 + 1]));

            bool optimised = distance - carried > 0.01;

            if (optimised)
                optimisedSliders++;

            foreach (var part in scene.Nested)
            {
                if (part.Progress is not { } p)
                    continue;

                var walked = PositionAlong(path, distance * p);
                double delta = Vector2.Distance(walked, new Vector2(part.X, part.Y));

                if (optimised)
                {
                    optimisedCompared++;
                    optimisedWorst = Math.Max(optimisedWorst, delta);
                    continue;
                }

                compared++;

                // Both sides are rounded to two decimals on the way out, so the floor on
                // agreement is the rounding, not the arithmetic.
                if (delta > 0.02)
                {
                    diverged++;
                    worst = Math.Max(worst, delta);
                }
            }
        }

        return new CrossCheckResult(compared, diverged, worst, optimisedSliders, optimisedCompared, optimisedWorst);
    }

    /// <summary>
    /// The reference implementation of the walk, in the form the viewer should copy: linear
    /// interpolation along the emitted vertices by arc length, clamped at both ends.
    /// </summary>
    public static Vector2 PositionAlong(IReadOnlyList<float> path, double distance)
    {
        int count = path.Count / 2;

        if (count == 0)
            return Vector2.Zero;

        var first = new Vector2(path[0], path[1]);

        if (count == 1 || distance <= 0)
            return first;

        double travelled = 0;

        for (int i = 1; i < count; i++)
        {
            var from = new Vector2(path[(i - 1) * 2], path[(i - 1) * 2 + 1]);
            var to = new Vector2(path[i * 2], path[i * 2 + 1]);
            double segment = Vector2.Distance(from, to);

            if (segment <= 0)
                continue;

            if (travelled + segment >= distance)
                return from + (to - from) * (float)((distance - travelled) / segment);

            travelled += segment;
        }

        return new Vector2(path[(count - 1) * 2], path[(count - 1) * 2 + 1]);
    }
}
