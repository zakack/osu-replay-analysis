using System.Linq;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Scoring;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using osuTK;

namespace Sim;

public sealed record SimulationResult(
    IReadOnlyList<ObjectState> Objects,
    Dictionary<HitResult, int> Statistics,
    int MaxCombo);

/// <summary>
/// Turns replay input into an ordered list of judgements.
///
/// This is the only part of lazer's behaviour reimplemented rather than called, because
/// lazer produces judgements in the drawable layer and hosting that layer would make
/// extraction framerate-coupled. Everything it consumes — stacked positions, hit windows,
/// slider paths, mod-adjusted difficulty — comes from lazer itself, and the judgements it
/// emits go back to lazer's ScoreProcessor for scoring.
/// </summary>
public sealed class Simulator(IHitPolicy policy)
{
    public Simulator() : this(new StartTimeOrderedPolicy()) { }

    public SimulationResult Run(IBeatmap playable, Score score)
    {
        var objects = playable.HitObjects
                              .Cast<OsuHitObject>()
                              .OrderBy(o => o.StartTime)
                              .Select((o, i) => new ObjectState(o, i))
                              .ToArray();

        var sampler = new ReplaySampler(score.Replay);

        // Stop when the replay stops. A failed or abandoned play simply has no frames past
        // the point it ended, and lazer judges nothing after that — so running on to the end
        // of the beatmap would invent a miss for every remaining object. The miss window is
        // added so that objects the player genuinely dropped at the very end still resolve.
        double until = sampler.LastFrameTime + OsuHitWindows.MISS_WINDOW;

        IReadOnlyList<OsuAction> held = [];

        foreach (var frame in sampler.Sample(until))
        {
            // Input is delivered before drawables update, so a press lands before the
            // automatic miss sweep of the same instant. A circle struck on the exact
            // millisecond its window closes is a hit, not a miss.
            foreach (var action in frame.Actions)
            {
                if (!held.Contains(action))
                    press(objects, frame.Time, frame.Cursor);
            }

            held = frame.Actions;

            sweepMisses(objects, frame.Time);
        }

        return summarise(objects);
    }

    private void press(IReadOnlyList<ObjectState> objects, double time, Vector2 cursor)
    {
        // Input propagates front to back, and the hit object container orders by descending
        // start time, so the earliest unjudged object under the cursor receives the press.
        // Once one does, the press is consumed even if notelock refuses the hit.
        foreach (var state in objects)
        {
            if (state.Judged)
                continue;

            var hitObject = state.HitObject;

            if (time < hitObject.StartTime - hitObject.TimePreempt)
                break;

            if (Vector2.Distance(cursor, hitObject.StackedPosition) > hitObject.Radius)
                continue;

            var result = hitObject.HitWindows.ResultFor(time - hitObject.StartTime);
            var action = policy.CheckHittable(state, time, result, objects);

            if (action == ClickAction.Hit && result != HitResult.None)
            {
                state.Apply(result, time);
                policy.HandleHit(state, objects, time);
            }

            return;
        }
    }

    private static void sweepMisses(IReadOnlyList<ObjectState> objects, double time)
    {
        foreach (var state in objects)
        {
            if (state.Judged)
                continue;

            double offset = time - state.HitObject.GetEndTime();

            if (offset < 0)
                break;

            if (!state.HitObject.HitWindows.CanBeHit(offset))
                state.Apply(HitResult.Miss, time);
        }
    }

    private static SimulationResult summarise(IReadOnlyList<ObjectState> objects)
    {
        var statistics = new Dictionary<HitResult, int>();
        int combo = 0;
        int maxCombo = 0;

        foreach (var state in objects.Where(o => o.Judged).OrderBy(o => o.JudgementTime).ThenBy(o => o.Index))
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
