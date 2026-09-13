using System.Globalization;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Difficulty.Preprocessing;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu.Difficulty.Preprocessing;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Scoring;
using osu.Game.Utils;
using osuTK;

namespace Sim;

/// <summary>
/// Per-object geometry, taken from lazer wherever lazer already computes it.
///
/// Almost nothing here is reimplemented, and that is deliberate. Stacked positions, slider
/// path approximation, the lazy end position a player is assumed to leave a slider from, and
/// radius-normalised jump distances all come out of <see cref="OsuDifficultyHitObject"/>.
/// Porting any of that would mean owning edge-case fidelity to someone else's implementation
/// forever, and its failures do not crash — they shift positions a few units and corrupt
/// bins silently.
///
/// The one thing computed here is the signed angle, because lazer does not have it and
/// cannot: its <c>Angle</c> is an unsigned difficulty heuristic, minimum-ed against a slider
/// angle, and its <c>NormalisedVectorAngle</c> folds every direction into one quadrant. Both
/// deliberately destroy handedness. Handedness is a finding — the same player often shows
/// different error left-to-right than right-to-left at the same magnitude — so it is kept,
/// and lazer's value is emitted beside it as a cross-check.
/// </summary>
public static class Geometry
{
    /// <summary>
    /// Lazer normalises jump distances to a 50-unit radius, so dividing by it gives spacing
    /// in circle radii and circle size drops out.
    /// </summary>
    private const double normalised_radius = OsuDifficultyHitObject.NORMALISED_RADIUS;

    public sealed record Row(
        double StartTime,
        string Kind,
        double? SignedAngle,
        double? LazerAngle,
        double? ObservedSignedAngle,
        double SpacingRadii,
        double MinimumJumpRadii,
        double DeltaTime,
        double? AimErrorRadii,
        double? HitError,
        string Result,
        double TravelRadii,
        double TravelTime,
        double? ExitSlackRadii)
    {
        /// <summary>
        /// Whether <see cref="LazerAngle"/> is a fair comparison for <see cref="SignedAngle"/>.
        ///
        /// Lazer's value is <c>Math.Min(angle, sliderAngle)</c>, so wherever a slider is one of
        /// the three objects forming the turn it may report the slider's angle instead and
        /// legitimately differ. Those cases are not evidence either way, and counting them as
        /// failures buries the check that matters in a 9% noise floor.
        /// </summary>
        public bool AngleComparable { get; init; }
    }

    public static IReadOnlyList<Row> Extract(IBeatmap playable, Score score, Simulator? simulator = null)
    {
        var simulation = (simulator ?? new Simulator()).Run(playable, score);
        var byObject = simulation.Objects.ToDictionary(o => (HitObject)o.HitObject, o => o);

        double clockRate = ModUtils.CalculateRateWithMods(score.ScoreInfo.Mods);
        var difficulty = new List<DifficultyHitObject>(playable.HitObjects.Count);

        for (int i = 1; i < playable.HitObjects.Count; i++)
            difficulty.Add(new OsuDifficultyHitObject(playable.HitObjects[i], playable.HitObjects[i - 1], clockRate, difficulty, difficulty.Count));

        var rows = new List<Row>();

        foreach (OsuDifficultyHitObject current in difficulty)
        {
            if (current.BaseObject is Spinner)
                continue;

            var previous = current.Previous(0) as OsuDifficultyHitObject;
            var beforeThat = current.Previous(1) as OsuDifficultyHitObject;

            // Lazer measures the angle at the *previous* object, between where the player came
            // from and where they are going. Use its own positions so the two agree by
            // construction and a divergence means the arithmetic is wrong, not the inputs.
            Vector2? apex = previous == null ? null : mapCursorLeaving(previous, current);
            Vector2? from = beforeThat == null ? null : endPosition(beforeThat);

            double? signed = apex == null || from == null
                ? null
                : signedAngle(from.Value, apex.Value, Base(current).StackedPosition);

            // The same angle as the player actually traced it. Where the previous object is a
            // slider this can differ a lot from the map's version, because the map assumes a
            // lazy exit and the player may have left early or followed through.
            Vector2? observedApex = previous == null ? null : observedLeaving(previous, byObject) ?? apex;
            Vector2? observedFrom = beforeThat == null ? null : observedLeaving(beforeThat, byObject) ?? from;

            double? observed = observedApex == null || observedFrom == null
                ? null
                : signedAngle(observedFrom.Value, observedApex.Value, Base(current).StackedPosition);

            var clicked = clickState(Base(current), byObject);

            double? aimError = clicked?.CursorAtHit is { } cursor
                ? Vector2.Distance(cursor, ((OsuHitObject)clicked.HitObject).StackedPosition) / ((OsuHitObject)clicked.HitObject).Radius
                : null;

            // How far the cursor was from where the map assumed it would leave the previous
            // slider. Zero for a circle, and the size of the correction a slider exit cost.
            double? exitSlack = previous?.BaseObject is Slider && observedApex != null && apex != null
                ? Vector2.Distance(observedApex.Value, apex.Value) / Base(current).Radius
                : null;

            bool comparable = previous?.BaseObject is not Slider
                              && beforeThat?.BaseObject is not Slider
                              && Base(current) is not Slider;

            rows.Add(new Row(
                current.BaseObject.StartTime,
                Base(current) is Slider ? "slider" : "circle",
                signed,
                current.Angle,
                observed,
                current.LazyJumpDistance / normalised_radius,
                current.MinimumJumpDistance / normalised_radius,
                current.AdjustedDeltaTime,
                aimError,
                clicked?.TimeOffset,
                clicked is { Judged: true } ? clicked.Result.ToString() : "Unjudged",
                current.TravelDistance / normalised_radius,
                current.TravelTime,
                exitSlack) { AngleComparable = comparable });
        }

        return rows;
    }

    /// <summary>
    /// The turn at <paramref name="apex"/>, measured the way lazer measures it so the
    /// magnitudes can be compared: pi is straight through, zero is a full reversal. The sign
    /// is ours, and it is positive for a counter-clockwise turn.
    /// </summary>
    private static double signedAngle(Vector2 from, Vector2 apex, Vector2 to)
    {
        var v1 = from - apex;
        var v2 = to - apex;

        return Math.Atan2(v1.X * v2.Y - v1.Y * v2.X, Vector2.Dot(v1, v2));
    }

    /// <summary>Where lazer assumes the player leaves an object from.</summary>
    private static Vector2 endPosition(OsuDifficultyHitObject o) =>
        o.LazyEndPosition ?? Base(o).StackedPosition;

    /// <summary>The difficulty object's hit object, typed. DifficultyHitObject erases it.</summary>
    private static OsuHitObject Base(OsuDifficultyHitObject o) => (OsuHitObject)o.BaseObject;

    /// <summary>
    /// Lazer's own quirk, reproduced rather than corrected: when the previous object is a
    /// slider that was travelled, the angle is measured from its <em>head</em>, not its lazy
    /// end. Departing from that here would make the cross-check meaningless.
    /// </summary>
    private static Vector2 mapCursorLeaving(OsuDifficultyHitObject previous, OsuDifficultyHitObject current) =>
        previous.BaseObject is Slider slider && previous.TravelDistance > 0
            ? slider.HeadCircle.StackedPosition
            : endPosition(previous);

    /// <summary>Where the cursor actually was leaving an object, when the replay knows.</summary>
    private static Vector2? observedLeaving(OsuDifficultyHitObject o, Dictionary<HitObject, ObjectState> byObject)
    {
        var state = byObject.GetValueOrDefault(o.BaseObject);

        if (o.BaseObject is Slider)
            return state?.CursorAtEnd;

        return state?.CursorAtHit;
    }

    private static ObjectState? clickState(OsuHitObject hitObject, Dictionary<HitObject, ObjectState> byObject)
    {
        // A slider's own judgement is awarded at its end and worth IgnoreHit. The tap is on
        // its head, and looking up the slider instead files the end time as the hit error.
        if (hitObject is Slider slider)
        {
            var head = slider.NestedHitObjects.OfType<SliderHeadCircle>().FirstOrDefault();
            return head == null ? null : byObject.GetValueOrDefault(head);
        }

        return byObject.GetValueOrDefault(hitObject);
    }

    public const string CsvHeader =
        "replay,startTime,kind,signedAngle,lazerAngle,observedAngle,spacingRadii,minJumpRadii,"
        + "deltaTime,requiredVelocity,aimErrorRadii,hitError,result,travelRadii,travelTime,exitSlackRadii";

    public static void WriteCsv(string replay, IReadOnlyList<Row> rows, TextWriter output)
    {
        foreach (var r in rows)
        {
            output.WriteLine(string.Create(CultureInfo.InvariantCulture,
                $"{quote(replay)},{r.StartTime:0.###},{r.Kind},{num(r.SignedAngle)},{num(r.LazerAngle)},{num(r.ObservedSignedAngle)},"
                + $"{r.SpacingRadii:0.####},{r.MinimumJumpRadii:0.####},{r.DeltaTime:0.###},"
                + $"{(r.DeltaTime > 0 ? (r.SpacingRadii / r.DeltaTime).ToString("0.######", CultureInfo.InvariantCulture) : string.Empty)},"
                + $"{num(r.AimErrorRadii)},{num(r.HitError)},{r.Result},{r.TravelRadii:0.####},{r.TravelTime:0.###},{num(r.ExitSlackRadii)}"));
        }
    }

    private static string num(double? value) =>
        value is { } v ? v.ToString("0.#####", CultureInfo.InvariantCulture) : string.Empty;

    private static string quote(string value) => '"' + value.Replace("\"", "\"\"") + '"';
}
