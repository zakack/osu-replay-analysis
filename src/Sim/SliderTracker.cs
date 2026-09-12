using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Scoring;
using osu.Game.Rulesets.Objects.Types;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osuTK;

namespace Sim;

/// <summary>
/// Slider tracking, ported from <c>SliderInputManager</c>.
///
/// This is the part of the ruleset with no shortcut. Whether a slider's ticks, repeats and
/// tail are awarded comes down to whether the cursor was inside a follow circle that moves
/// along the path and changes size depending on whether tracking is already active, while
/// a key that is allowed to track is held. None of it is recoverable from the hit objects
/// alone, and all of it is sampled per update — which is why the sampling cadence has to
/// match the client's.
/// </summary>
public sealed class SliderTracker(ObjectState sliderState)
{
    /// <summary>The follow circle's expanded radius multiplier, from DrawableSliderBall.</summary>
    private const float follow_area = 2.4f;

    private readonly Slider slider = (Slider)sliderState.HitObject;

    /// <summary>
    /// After the head is hit with one key, only that key tracks until every other key has
    /// been seen released. Without this, holding one key from before the slider and tapping
    /// the second would track the whole slider with nothing actually holding it.
    /// </summary>
    private double? timeToAcceptAnyKeyAfter;

    private IReadOnlyList<OsuAction> lastPressedActions = [];

    public bool Tracking { get; private set; }

    /// <summary>Why tracking was false at the last update, for diagnosing dropped tails.</summary>
    public bool LastPositionValid { get; private set; }

    public bool LastActionValid { get; private set; }

    /// <summary>
    /// Whether the cursor was ever inside the expanded follow area during the tail's
    /// leniency window. Separates "the cursor left the slider" from "no key was holding it".
    /// </summary>
    public bool CursorReachedTail { get; set; }

    public ObjectState State => sliderState;

    public ObjectState Head { get; init; } = null!;
    public IReadOnlyList<ObjectState> TicksAndRepeats { get; init; } = [];
    public ObjectState? Tail { get; init; }

    public bool IsAlive(double time) => time >= slider.StartTime - slider.TimePreempt;

    /// <summary>
    /// Whether the cursor is inside the follow circle at this instant.
    /// </summary>
    /// <param name="expanded">Test against the enlarged follow area rather than the ball.</param>
    public bool IsCursorInFollowArea(double time, Vector2 cursor, bool expanded)
    {
        float radius = followRadius(expanded);
        double progress = Math.Clamp((time - slider.StartTime) / slider.Duration, 0, 1);

        return Vector2.DistanceSquared(cursor, slider.StackedPosition + slider.CurvePositionAt(progress)) <= radius * radius;
    }

    private float followRadius(bool expanded) => (float)slider.Radius * (expanded ? follow_area : 1);

    public void Update(double time, Vector2 cursor, IReadOnlyList<OsuAction> pressed)
    {
        // The follow area is tested expanded only while already tracking, which is what
        // makes tracking sticky once established and strict to re-acquire once lost.
        UpdateTracking(time, pressed, IsCursorInFollowArea(time, cursor, Tracking));
    }

    public void UpdateTracking(double time, IReadOnlyList<OsuAction> pressed, bool validPosition)
    {
        var headAction = Head.HitAction;

        if (headAction == null)
            timeToAcceptAnyKeyAfter = null;

        if (headAction != null && timeToAcceptAnyKeyAfter == null)
        {
            var otherKey = headAction == OsuAction.RightButton ? OsuAction.LeftButton : OsuAction.RightButton;

            if (!lastPressedActions.Contains(otherKey))
                timeToAcceptAnyKeyAfter = time;
        }

        bool validAction = pressed.Any(a => isValidTrackingAction(a, time));
        lastPressedActions = pressed;

        LastPositionValid = validPosition;
        LastActionValid = validAction;

        Tracking =
            // Even past the slider's end time judging may be unfinished, and tracking must
            // not flip to false underneath it.
            (!State.AllJudged || time <= slider.GetEndTime())
            && validPosition
            && validAction;
    }

    private bool isValidTrackingAction(OsuAction action, double time)
    {
        var headAction = Head.HitAction;

        if (headAction.HasValue && (!timeToAcceptAnyKeyAfter.HasValue || time <= timeToAcceptAnyKeyAfter.Value))
            return action == headAction;

        return action is OsuAction.LeftButton or OsuAction.RightButton;
    }

    /// <summary>
    /// Resolve nested objects already passed when the head is hit late.
    ///
    /// If the cursor stayed within the expanded follow area across every passed object, they
    /// are all awarded. If it left at any point, they are all missed — which covers a slider
    /// that overlaps itself, where tracking a tick on an outer edge is the thing being tested.
    /// </summary>
    public void PostProcessHeadJudgement(double time, Vector2 cursor, IReadOnlyList<OsuAction> pressed, ref int sequence)
    {
        if (!Head.Judged || !Head.Result.IsHit())
            return;

        if (!IsCursorInFollowArea(time, cursor, true))
            return;

        var cursorInSlider = cursor - slider.StackedPosition;
        float radius = followRadius(true);
        bool allTicksInRange = true;

        foreach (var nested in State.Nested)
        {
            if (nested.Judged)
                continue;

            if (nested.HitObject.StartTime > time)
                break;

            double progress = Math.Clamp((nested.HitObject.StartTime - slider.StartTime) / slider.Duration, 0, 1);

            if (Vector2.DistanceSquared(slider.CurvePositionAt(progress), cursorInSlider) > radius * radius)
            {
                allTicksInRange = false;
                break;
            }
        }

        foreach (var nested in State.Nested)
        {
            if (nested.Judged)
                continue;

            if (nested.HitObject.StartTime > time)
                break;

            if (allTicksInRange)
                nested.HitForcefully(time, ref sequence);
            else
                nested.MissForcefully(time, ref sequence);
        }

        // If everything so far was tracked, tracking continues at full extent. If anything
        // was missed, assume tracking broke, and only re-acquire inside the ball itself.
        UpdateTracking(time, pressed, allTicksInRange || IsCursorInFollowArea(time, cursor, false));
    }

    /// <summary>
    /// Judge a tick, repeat or tail if it is due. Ported from <c>TryJudgeNestedObject</c>.
    /// </summary>
    public void TryJudgeNested(ObjectState nested, double time, ref int sequence)
    {
        double timeOffset = time - nested.HitObject.StartTime;

        switch (nested.HitObject)
        {
            case SliderRepeat:
            case SliderTick:
                if (timeOffset < 0)
                    return;

                break;

            case SliderTailCircle:
                if (timeOffset < SliderEventGenerator.TAIL_LENIENCY)
                    return;

                // The leniency can let the tail resolve before the final tick, which would
                // award score and combo out of order. Hold it back until they are done.
                var lastTick = TicksAndRepeats.LastOrDefault();

                if (lastTick is { Judged: false })
                    return;

                break;

            default:
                return;
        }

        if (!Head.Judged)
            return;

        if (Tracking)
            nested.HitForcefully(time, ref sequence);
        else if (timeOffset >= 0)
        {
            if (nested.HitObject is SliderTailCircle)
                nested.DropReason = LastPositionValid ? "action" : LastActionValid ? "position" : "both";

            nested.MissForcefully(time, ref sequence);
        }
    }
}
