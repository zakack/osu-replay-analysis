using osu.Game.Beatmaps;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Objects.Types;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Scoring;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using osu.Game.Utils;
using osuTK;

namespace Sim;

public sealed record SimulationResult(
    IReadOnlyList<ObjectState> Objects,
    Dictionary<HitResult, int> Statistics,
    int MaxCombo)
{
    /// <summary>Judgements in the order they were applied, which is the order scoring needs.</summary>
    public IEnumerable<ObjectState> InJudgementOrder => Objects.Where(o => o.Judged).OrderBy(o => o.Order);
}

/// <summary>
/// Turns replay input into an ordered list of judgements.
///
/// This is the only part of lazer's behaviour reimplemented rather than called, because
/// lazer produces judgements in the drawable layer and hosting that layer would make
/// extraction framerate-coupled. Everything it consumes — stacked positions, hit windows,
/// slider paths, mod-adjusted difficulty — comes from lazer itself.
/// </summary>
public sealed class Simulator(IHitPolicy policy)
{
    public Simulator() : this(new StartTimeOrderedPolicy()) { }

    private int sequence;

    /// <summary>Trace tracking state for the slider containing this time, for diagnosis.</summary>
    public (double Time, Action<string> Write)? TraceAround { get; set; }

    public SimulationResult Run(IBeatmap playable, Score score)
    {
        sequence = 0;

        var layout = build(playable);

        if (TraceAround is { } trace)
        {
            var target = layout.Trackers
                               .Where(kv => trace.Time >= kv.Key.HitObject.StartTime - 500 && trace.Time <= ((Slider)kv.Key.HitObject).EndTime + 500)
                               .OrderBy(kv => Math.Abs(((Slider)kv.Key.HitObject).EndTime - trace.Time))
                               .Select(kv => kv.Value)
                               .FirstOrDefault();

            if (target != null)
            {
                var s = (Slider)target.State.HitObject;
                trace.Write($"# slider start={s.StartTime:F1} end={s.EndTime:F1} duration={s.Duration:F1} " +
                            $"spans={s.SpanCount()} spanDuration={s.SpanDuration:F1} radius={s.Radius:F2} " +
                            $"pathDistance={s.Path.Distance:F1} stacked={s.StackedPosition}");

                foreach (var nested in target.State.Nested)
                    trace.Write($"#   {nested.HitObject.GetType().Name,-18} start={nested.HitObject.StartTime:F1}");

                target.Trace = trace.Write;
            }
        }
        var flat = layout.Flat;
        var trackers = layout.Trackers;
        // Rate mods stretch the client's wall-clock frame time across more beatmap time,
        // so the sampling step has to be scaled the same way the recorder scales its own.
        var sampler = new ReplaySampler(score.Replay, ModUtils.CalculateRateWithMods(score.ScoreInfo.Mods));

        // Stop exactly when the replay stops. Gameplay ends with the frames: a completed
        // play keeps recording past the final object anyway, while a failed or abandoned one
        // simply stops, and lazer judges nothing after that. Running even a miss window
        // longer invents judgements around the point of failure.
        double until = sampler.LastFrameTime;

        IReadOnlyList<OsuAction> held = [];

        foreach (var frame in sampler.Sample(until))
        {
            // Input is delivered before drawables update, so a press lands before the
            // automatic sweep of the same instant.
            foreach (var action in frame.Actions)
            {
                if (!held.Contains(action))
                    press(layout, frame.Time, frame.Cursor, action, frame.Actions);
            }

            held = frame.Actions;

            // SliderInputManager is a child component, so its Update runs before the nested
            // objects' UpdateAfterChildren. Tracking for this instant is therefore settled
            // before anything reads it.
            foreach (var tracker in trackers.Values)
            {
                if (tracker.IsAlive(frame.Time) && !tracker.State.AllJudged)
                    tracker.Update(frame.Time, frame.Cursor, frame.Actions);
            }

            sweep(flat, trackers, frame.Time, frame.Cursor, frame.Actions);
        }

        return summarise(flat);
    }

    /// <summary>
    /// Flatten to the order lazer enumerates alive objects in: each top-level object
    /// followed by its nested objects. Notelock walks this list, so the order is load-bearing.
    /// </summary>
    private static Layout build(IBeatmap playable)
    {
        var flat = new List<ObjectState>();
        var topLevel = new List<ObjectState>();
        var trackers = new Dictionary<ObjectState, SliderTracker>();

        foreach (var hitObject in playable.HitObjects.Cast<OsuHitObject>().OrderBy(o => o.StartTime))
        {
            var state = new ObjectState(hitObject, null, flat.Count);
            flat.Add(state);
            topLevel.Add(state);

            foreach (var nested in hitObject.NestedHitObjects.Cast<OsuHitObject>())
            {
                var nestedState = new ObjectState(nested, state, flat.Count);
                state.Nested.Add(nestedState);
                flat.Add(nestedState);
            }

            if (hitObject is Slider)
            {
                trackers[state] = new SliderTracker(state)
                {
                    Head = state.Nested.First(n => n.HitObject is SliderHeadCircle),
                    TicksAndRepeats = state.Nested.Where(n => n.HitObject is SliderTick or SliderRepeat).ToArray(),
                    Tail = state.Nested.FirstOrDefault(n => n.HitObject is SliderTailCircle)
                };
            }
        }

        // Only drawable hit circles receive presses: plain circles and slider heads. Slider
        // repeats and tails derive from HitCircle in the model but not in the drawable tree,
        // and they carry empty hit windows — letting them take a press would swallow input
        // meant for a real circle.
        var pressable = flat.Where(StartTimeOrderedPolicy.IsBlocker)
                            .OrderBy(s => s.HitObject.StartTime)
                            .ToList();

        return new Layout(flat, topLevel, pressable, trackers);
    }

    /// <summary>
    /// The three orderings the loop needs: every object for the sweep, top-level objects for
    /// notelock's nested enumeration, and the press targets sorted by start time.
    /// </summary>
    private sealed record Layout(
        List<ObjectState> Flat,
        List<ObjectState> TopLevel,
        List<ObjectState> Pressable,
        Dictionary<ObjectState, SliderTracker> Trackers);

    private void press(Layout layout, double time, Vector2 cursor, OsuAction action, IReadOnlyList<OsuAction> pressed)
    {
        // Input propagates front to back and the hit object container orders by descending
        // start time, so the earliest unjudged circle under the cursor receives the press.
        // Once one does the press is consumed, even if notelock then refuses the hit.
        foreach (var state in layout.Pressable)
        {
            if (state.Judged)
                continue;

            var hitCircle = (HitCircle)state.HitObject;

            if (time < hitCircle.StartTime - hitCircle.TimePreempt)
                break;

            if (Vector2.Distance(cursor, hitCircle.StackedPosition) > hitCircle.Radius)
                continue;

            // Lazer records the pressing key on any hovered press, hit or not, and slider
            // tracking reads it back off the head.
            state.HitAction ??= action;

            var result = hitCircle.HitWindows.ResultFor(time - hitCircle.StartTime);
            var clickAction = policy.CheckHittable(state, time, result, layout.TopLevel);

            if (clickAction == ClickAction.Hit && result != HitResult.None)
            {
                state.Apply(result, time, ref sequence);
                policy.HandleHit(state, layout.TopLevel, time, ref sequence);

                if (state.Parent != null && layout.Trackers.TryGetValue(state.Parent, out var tracker))
                    tracker.PostProcessHeadJudgement(time, cursor, pressed, ref sequence);
            }

            return;
        }
    }

    private void sweep(List<ObjectState> flat, Dictionary<ObjectState, SliderTracker> trackers, double time, Vector2 cursor, IReadOnlyList<OsuAction> pressed)
    {
        foreach (var state in flat)
        {
            if (state.Judged)
                continue;

            switch (state.HitObject)
            {
                case Slider slider:
                    // The slider resolves once its tail has, and only past its end time. Its
                    // own judgement is ignored for scoring, but it still carries combo state.
                    if (trackers[state].Tail is { Judged: true } && time >= slider.EndTime)
                    {
                        if (state.Nested.Any(n => n.Result.IsHit()))
                            state.HitForcefully(time, ref sequence);
                        else
                            state.MissForcefully(time, ref sequence);
                    }

                    break;

                case SliderTick or SliderRepeat or SliderTailCircle:
                    trackers[state.Parent!].TryJudgeNested(state, time, ref sequence);
                    break;

                case HitCircle circle:
                {
                    double offset = time - circle.StartTime;

                    if (offset < 0)
                        continue;

                    if (!circle.HitWindows.CanBeHit(offset))
                    {
                        state.MissForcefully(time, ref sequence);

                        if (state.Parent != null && trackers.TryGetValue(state.Parent, out var tracker))
                            tracker.PostProcessHeadJudgement(time, cursor, pressed, ref sequence);
                    }

                    break;
                }
            }
        }
    }

    private static SimulationResult summarise(IReadOnlyList<ObjectState> objects)
    {
        var statistics = new Dictionary<HitResult, int>();
        int combo = 0;
        int maxCombo = 0;

        foreach (var state in objects.Where(o => o.Judged).OrderBy(o => o.Order))
        {
            statistics[state.Result] = statistics.GetValueOrDefault(state.Result) + 1;

            if (state.Result.AffectsCombo())
            {
                combo = state.Result.IsHit() ? combo + 1 : 0;
                maxCombo = Math.Max(maxCombo, combo);
            }
        }

        return new SimulationResult(objects, statistics, maxCombo);
    }
}
