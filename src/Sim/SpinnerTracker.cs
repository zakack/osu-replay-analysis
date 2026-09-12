using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Scoring;
using osu.Game.Rulesets.Osu.Objects;
using osuTK;

namespace Sim;

/// <summary>
/// Accumulated spinner rotation, ported from <c>SpinnerSpinHistory</c>.
///
/// A full spin is 360 degrees in one direction from zero. Reversing does not subtract from
/// the score: the running total keeps the furthest point reached in the current spin, which
/// is what stops a player from farming rotation by shaking the cursor back and forth.
/// </summary>
public sealed class SpinHistory
{
    private int completedSpins;
    private float totalAccumulatedRotation;
    private float totalAccumulatedRotationAtLastCompletion;

    /// <summary>Furthest absolute rotation reached within the current, incomplete spin.</summary>
    private float currentSpinMaxRotation;

    /// <summary>The sum of completed spins and the current partial one, in degrees.</summary>
    public float TotalRotation => 360 * completedSpins + currentSpinMaxRotation;

    private float currentSpinRotation => totalAccumulatedRotation - totalAccumulatedRotationAtLastCompletion;

    public void ReportDelta(float delta)
    {
        if (delta == 0)
            return;

        totalAccumulatedRotation += delta;
        currentSpinMaxRotation = Math.Max(currentSpinMaxRotation, Math.Abs(currentSpinRotation));

        // A loop rather than a branch only because a single frame's delta can exceed 360
        // once the rate multiplier has been applied.
        while (currentSpinMaxRotation >= 360)
        {
            completedSpins++;
            totalAccumulatedRotationAtLastCompletion += Math.Sign(currentSpinRotation) * 360;

            // Carry the remainder into the new spin rather than discarding it.
            currentSpinMaxRotation = Math.Abs(currentSpinRotation);
        }
    }
}

/// <summary>
/// Spinner judgement, ported from <c>DrawableSpinner</c> and <c>SpinnerRotationTracker</c>.
///
/// Rotation is measured as the change in the cursor's angle about the spinner's centre
/// between consecutive samples, accumulated only while a button is held and the clock is
/// inside the spinner. Like slider tracking it is a per-update quantity, so it depends on
/// sampling cadence — but far less sharply, since it integrates over the whole spinner
/// rather than turning on a single instant at a boundary.
/// </summary>
public sealed class SpinnerTracker(ObjectState spinnerState, double clockRate)
{
    private readonly Spinner spinner = (Spinner)spinnerState.HitObject;
    private readonly SpinHistory history = new();

    private float? lastAngle;
    private int awardedSpins;

    public ObjectState State => spinnerState;

    /// <summary>Ticks in order. The first are ordinary, the remainder award bonus.</summary>
    public IReadOnlyList<ObjectState> Ticks { get; init; } = [];

    public bool Tracking { get; private set; }

    public bool IsAlive(double time) => time >= spinner.StartTime - spinner.TimePreempt;

    /// <summary>Rotation only accumulates strictly inside the spinner's span.</summary>
    private bool isSpinnableTime(double time) => spinner.StartTime <= time && spinner.EndTime > time;

    public double Progress => history.TotalRotation / 360.0 / spinner.SpinsRequired;

    public void Update(double time, Vector2 cursor, IReadOnlyList<OsuAction> pressed)
    {
        Tracking = isSpinnableTime(time)
                   && !State.Judged
                   && pressed.Any(a => a is OsuAction.LeftButton or OsuAction.RightButton);

        // Note the argument order: lazer measures the angle as atan2(x, y), negated. Only
        // the deltas matter, but reproducing the convention keeps sign handling identical.
        float thisAngle = -float.RadiansToDegrees(MathF.Atan2(cursor.X - spinner.StackedPosition.X, cursor.Y - spinner.StackedPosition.Y));
        float delta = lastAngle == null ? 0 : thisAngle - lastAngle.Value;

        if (delta > 180) delta -= 360;
        if (delta < -180) delta += 360;

        if (Tracking && isSpinnableTime(time))
        {
            // Scaled by the clock rate, exactly as AddRotation does. A rate mod therefore
            // makes a given cursor movement worth proportionally more rotation.
            history.ReportDelta((float)(delta * Math.Abs(clockRate)));
        }

        // The angle is tracked whether or not the player is holding, so that starting to
        // hold mid-spinner does not register the gap as one enormous delta.
        lastAngle = thisAngle;
    }

    /// <summary>
    /// Award ticks for whole spins completed, then resolve the spinner once its time is up.
    /// Mirrors <c>UpdateAfterChildren</c>, where the result check precedes the tick award.
    /// </summary>
    public void Resolve(double time, ref int sequence)
    {
        if (time < spinner.StartTime)
            return;

        if (!State.Judged && time >= spinner.EndTime)
        {
            // Remaining ticks are missed before the spinner itself resolves, so that a spin
            // completing on this very frame cannot award one.
            foreach (var tick in Ticks.Where(t => !t.Judged))
                tick.MissForcefully(time, ref sequence);

            double progress = Progress;

            var result = progress >= 1 ? HitResult.Great
                : progress > 0.9 ? HitResult.Ok
                : progress > 0.75 ? HitResult.Meh
                : HitResult.Miss;

            if (result == HitResult.Miss)
                State.MissForcefully(time, ref sequence);
            else
                State.Apply(result, time, ref sequence);

            return;
        }

        awardTicks(time, ref sequence);
    }

    private void awardTicks(double time, ref int sequence)
    {
        if (Ticks.Count == 0)
            return;

        int spins = (int)(history.TotalRotation / 360);

        while (awardedSpins < spins)
        {
            var tick = Ticks.FirstOrDefault(t => !t.Judged);

            // Null once the spin limit is reached; lazer just plays a sound and moves on.
            tick?.HitForcefully(time, ref sequence);
            awardedSpins++;
        }
    }
}
