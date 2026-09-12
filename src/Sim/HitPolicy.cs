using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Rulesets.Scoring;

namespace Sim;

/// <summary>
/// The per-object state lazer keeps on the drawable. Keeping it beside the model object is
/// the whole reason this loop is reimplemented rather than hosted.
/// </summary>
public sealed class ObjectState(OsuHitObject hitObject, ObjectState? parent, int index)
{
    private readonly HitResult maxResult = hitObject.CreateJudgement().MaxResult;
    private readonly HitResult minResult = hitObject.CreateJudgement().MinResult;

    public OsuHitObject HitObject { get; } = hitObject;

    /// <summary>The slider a nested object belongs to, or null for a top-level object.</summary>
    public ObjectState? Parent { get; } = parent;

    public List<ObjectState> Nested { get; } = [];

    /// <summary>Position in the flattened ordering, which mirrors lazer's alive-object enumeration.</summary>
    public int Index { get; } = index;

    public bool Judged { get; private set; }
    public HitResult Result { get; private set; } = HitResult.None;
    public double? JudgementTime { get; private set; }

    /// <summary>
    /// The order judgements were applied in. ScoreProcessor's combo portion depends on the
    /// combo at each judgement, so the sequence matters as much as the counts.
    /// </summary>
    public int Order { get; private set; } = -1;

    /// <summary>Signed hit error in milliseconds, negative for early. Null for anything not struck.</summary>
    public double? TimeOffset { get; private set; }

    /// <summary>
    /// The key that pressed this object while hovered. Lazer records it even when the hit is
    /// refused, and slider tracking reads it off the head to decide which key may track.
    /// </summary>
    public OsuAction? HitAction { get; set; }

    /// <summary>For a dropped slider tail, which tracking condition failed.</summary>
    public string? DropReason { get; set; }

    public bool AllJudged => Judged && Nested.All(n => n.AllJudged);

    public void Apply(HitResult result, double time, ref int sequence)
    {
        if (Judged)
            throw new InvalidOperationException($"{HitObject.GetType().Name} at {HitObject.StartTime} judged twice");

        Judged = true;
        Result = result;
        JudgementTime = time;
        Order = sequence++;
        TimeOffset = result.IsHit() ? time - HitObject.StartTime : null;
    }

    public void HitForcefully(double time, ref int sequence) => Apply(maxResult, time, ref sequence);

    public void MissForcefully(double time, ref int sequence) => Apply(minResult, time, ref sequence);
}

public interface IHitPolicy
{
    ClickAction CheckHittable(ObjectState candidate, double time, HitResult result, IReadOnlyList<ObjectState> topLevel);
    void HandleHit(ObjectState hit, IReadOnlyList<ObjectState> topLevel, double time, ref int sequence);
}

/// <summary>
/// Notelock: object n+1 cannot be judged before n resolves.
///
/// Mirrors <c>StartTimeOrderedHitPolicy</c>. What blocks is any *drawable* hit circle.
/// <c>DrawableSliderHead</c> is one, so slider heads both block later hits and get
/// force-missed when a later object is struck first. Slider repeats and tails are not —
/// despite their models deriving from <c>HitCircle</c>, their drawables derive from
/// <c>DrawableOsuHitObject</c> — and neither are ticks or spinners.
/// </summary>
public sealed class StartTimeOrderedPolicy : IHitPolicy
{
    public ClickAction CheckHittable(ObjectState candidate, double time, HitResult result, IReadOnlyList<ObjectState> topLevel)
    {
        ObjectState? blocking = null;

        foreach (var other in before(candidate.HitObject.StartTime, topLevel))
        {
            if (blocks(other))
                blocking = other;
        }

        // Hits at exactly the blocking object's start time are allowed, for maps that
        // genuinely contain simultaneous objects.
        if (blocking is { Judged: false } && time < blocking.HitObject.StartTime)
            return ClickAction.Shake;

        return result == HitResult.None ? ClickAction.Shake : ClickAction.Hit;
    }

    public void HandleHit(ObjectState hit, IReadOnlyList<ObjectState> topLevel, double time, ref int sequence)
    {
        if (!blocks(hit))
            return;

        foreach (var other in before(hit.HitObject.StartTime, topLevel))
        {
            if (!other.Judged && blocks(other))
                other.MissForcefully(time, ref sequence);
        }
    }

    public static bool IsBlocker(ObjectState state) => state.HitObject is HitCircle and not SliderEndCircle;

    private static bool blocks(ObjectState state) => IsBlocker(state);

    /// <summary>
    /// Walk objects starting before <paramref name="targetTime"/>, in lazer's enumeration
    /// order: each top-level object then its nested ones. The outer walk stops at the first
    /// top-level object starting at or after the target, while the inner walk only skips the
    /// rest of that object's nested list — so a long slider does not hide the objects after it.
    /// </summary>
    private static IEnumerable<ObjectState> before(double targetTime, IReadOnlyList<ObjectState> topLevel)
    {
        foreach (var parent in topLevel)
        {
            if (parent.HitObject.StartTime >= targetTime)
                yield break;

            yield return parent;

            foreach (var nested in parent.Nested)
            {
                if (nested.HitObject.StartTime >= targetTime)
                    break;

                yield return nested;
            }
        }
    }
}
