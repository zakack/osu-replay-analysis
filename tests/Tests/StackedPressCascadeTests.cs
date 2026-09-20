using NUnit.Framework;
using osu.Game.Beatmaps;
using osu.Game.Replays;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Beatmaps;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using osuTK;
using Sim;

namespace Tests;

/// <summary>
/// A press that lands a fraction of a unit outside one circle's hit area is not discarded —
/// it is taken by the next circle under the cursor, and notelock then force-misses the one
/// that was skipped. Every later tap in the pattern is off by one object.
///
/// Transcribed from <c>build/best/best-6473309535.osr</c> at 69450ms: a three-stack at
/// (204, 14) on CS4, tapped 1-2-1 with offsets of 0ms, -5ms and -15ms — a clean triple. The
/// cursor sat 36.703 units from the first circle's stacked centre against a radius of
/// 36.495, so the first press missed its hit area by 0.21 units, 0.57% of the radius, and
/// the read-out became Miss / Ok(-79) / Meh(-83) instead of three Greats.
///
/// The differential oracle reproduces this object for object, so it is lazer's behaviour and
/// not a porting defect. The value of pinning it is that the margin is well under a pixel:
/// any future drift in stack offsets, radius or the hover test flips it silently, and the
/// damage shows up three objects downstream as a hit-error reading rather than at the cause.
/// </summary>
[TestFixture]
public class StackedPressCascadeTests
{
    /// <summary>The stacked centres of a three-object stack at (204, 14) on CS4, as lazer
    /// computes them: each earlier object shifted by <c>Scale * -6.4</c>.</summary>
    private static readonly Vector2[] stack =
    [
        new Vector2(196.70f, 6.70f),
        new Vector2(200.35f, 10.35f),
        new Vector2(204.00f, 14.00f)
    ];

    private const double first = 1000;
    private const double second = 1079;
    private const double third = 1157;

    /// <summary>Where the cursor was at each of the three presses, in stacked play
    /// coordinates. 36.703 units from <c>stack[0]</c>, which is outside it.</summary>
    private static readonly Vector2[] cursor =
    [
        new Vector2(204.00f, 42.67f),
        new Vector2(202.33f, 42.67f),
        new Vector2(207.67f, 40.00f)
    ];

    [Test]
    public void PressOutsideTheFirstCircleCascadesOntoTheStack()
    {
        var result = run();

        Assert.Multiple(() =>
        {
            // The press at the first object's own start time went to the second object.
            Assert.That(result.Objects[0].Result, Is.EqualTo(HitResult.Miss), "first circle, notelocked");
            Assert.That(result.Objects[1].Result, Is.EqualTo(HitResult.Ok), "second circle, from the first press");
            Assert.That(result.Objects[2].Result, Is.EqualTo(HitResult.Meh), "third circle, from the second press");

            // The replay's own numbers: the first press is 79ms early onto the second
            // circle, the second press 83ms early onto the third. Both taps were within 5ms
            // and 15ms of the object they were aimed at.
            Assert.That(result.Objects[1].TimeOffset, Is.EqualTo(-79).Within(0.001));
            Assert.That(result.Objects[2].TimeOffset, Is.EqualTo(-83).Within(0.001));

            // Notelock resolves the skipped circle at the instant the later one is struck,
            // not when its own window expires.
            Assert.That(result.Objects[0].JudgementTime, Is.EqualTo(first).Within(0.001));
        });
    }

    [Test]
    public void TheSamePressesInsideTheFirstCircleAreThreeGreats()
    {
        // Nudge the cursor one unit towards the stack. Nothing else changes, and the whole
        // read-out does: this is the size of the margin the case above turns on.
        var result = run(new Vector2(0, -1));

        Assert.Multiple(() =>
        {
            Assert.That(result.Objects[0].Result, Is.EqualTo(HitResult.Great));
            Assert.That(result.Objects[1].Result, Is.EqualTo(HitResult.Great));
            Assert.That(result.Objects[2].Result, Is.EqualTo(HitResult.Great));
        });
    }

    private static SimulationResult run(Vector2 nudge = default)
    {
        var beatmap = new OsuBeatmap
        {
            HitObjects =
            {
                new HitCircle { StartTime = first, Position = stack[0] },
                new HitCircle { StartTime = second, Position = stack[1] },
                new HitCircle { StartTime = third, Position = stack[2] }
            },
            Difficulty = new BeatmapDifficulty
            {
                CircleSize = 4,
                OverallDifficulty = 7.5f,
                ApproachRate = 9
            },
            BeatmapInfo = { Ruleset = new OsuRuleset().RulesetInfo }
        };

        var score = new Score
        {
            ScoreInfo = new ScoreInfo(),
            Replay = new Replay
            {
                Frames =
                {
                    new OsuReplayFrame(0, cursor[0] + nudge),
                    new OsuReplayFrame(first, cursor[0] + nudge, OsuAction.LeftButton),
                    new OsuReplayFrame(first + 60, cursor[1] + nudge),
                    new OsuReplayFrame(second - 5, cursor[1] + nudge, OsuAction.RightButton),
                    new OsuReplayFrame(second + 50, cursor[2] + nudge),
                    new OsuReplayFrame(third - 15, cursor[2] + nudge, OsuAction.LeftButton),
                    new OsuReplayFrame(third + 400, cursor[2] + nudge)
                }
            }
        };

        var playable = new FlatWorkingBeatmap(beatmap).GetPlayableBeatmap(new OsuRuleset().RulesetInfo);

        return new Simulator().Run(playable, score);
    }
}
