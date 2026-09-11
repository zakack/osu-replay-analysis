using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Rulesets.Scoring;

namespace Sim;

/// <summary>
/// The per-object state the simulation tracks. Lazer keeps this on the drawable; we keep it
/// beside the model object, which is the whole reason this loop is reimplemented rather
/// than hosted.
/// </summary>
public sealed class ObjectState(OsuHitObject hitObject, int index)
{
    public OsuHitObject HitObject { get; } = hitObject;

    /// <summary>Position in the beatmap's ordering, used to resolve ties on start time.</summary>
    public int Index { get; } = index;

    public bool Judged { get; private set; }
    public HitResult Result { get; private set; } = HitResult.None;
    public double? JudgementTime { get; private set; }

    /// <summary>Signed hit error in milliseconds. Negative is early. Null for anything not struck.</summary>
    public double? TimeOffset { get; private set; }

    public void Apply(HitResult result, double time)
    {
        if (Judged)
            throw new InvalidOperationException($"{HitObject.GetType().Name} at {HitObject.StartTime} judged twice");

        Judged = true;
        Result = result;
        JudgementTime = time;
        TimeOffset = result.IsHit() ? time - HitObject.StartTime : null;
    }
}

/// <summary>
/// Notelock: object n+1 cannot be judged before n resolves.
///
/// This mirrors <c>StartTimeOrderedHitPolicy</c> and <c>LegacyHitPolicy</c>. Only hit
/// circles block, and only circles get force-missed when a later object is struck first —
/// slider ticks and spinners neither block nor break.
/// </summary>
public interface IHitPolicy
{
    ClickAction CheckHittable(ObjectState candidate, double time, HitResult result, IReadOnlyList<ObjectState> all);
    void HandleHit(ObjectState hit, IReadOnlyList<ObjectState> all, double time);
}

public sealed class StartTimeOrderedPolicy : IHitPolicy
{
    public ClickAction CheckHittable(ObjectState candidate, double time, HitResult result, IReadOnlyList<ObjectState> all)
    {
        ObjectState? blocking = null;

        foreach (var other in before(candidate, all))
        {
            if (blocks(other))
                blocking = other;
        }

        // Hits at exactly the blocking object's start time are allowed, for maps with
        // genuinely simultaneous objects.
        if (blocking is { Judged: false } && time < blocking.HitObject.StartTime)
            return ClickAction.Shake;

        return result == HitResult.None ? ClickAction.Shake : ClickAction.Hit;
    }

    public void HandleHit(ObjectState hit, IReadOnlyList<ObjectState> all, double time)
    {
        if (!blocks(hit))
            return;

        foreach (var other in before(hit, all))
        {
            if (!other.Judged && blocks(other))
                other.Apply(HitResult.Miss, time);
        }
    }

    private static bool blocks(ObjectState state) => state.HitObject is HitCircle and not SliderHeadCircle;

    private static IEnumerable<ObjectState> before(ObjectState target, IReadOnlyList<ObjectState> all)
    {
        foreach (var other in all)
        {
            if (other.HitObject.StartTime >= target.HitObject.StartTime)
                yield break;

            yield return other;
        }
    }
}
