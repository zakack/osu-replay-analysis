using NUnit.Framework;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.ControlPoints;
using osu.Game.Replays;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Objects.Types;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Beatmaps;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Scoring;
using osuTK;
using Sim;

namespace Tests;

/// <summary>
/// The viewer's one piece of arithmetic, pinned before the viewer exists.
///
/// Everything else in a scene document is copied out of lazer, but turning a time into a
/// position along the emitted polyline is code someone will write in JS, and CLAUDE.md's
/// rule is that anything reimplemented gets an oracle first. Lazer is the oracle: it places
/// every slider tick and repeat itself and records the path progress it placed them at, so
/// the walk has a right answer for each one. <see cref="Scene.CrossCheck"/> runs that
/// comparison on real corpus replays on every invocation of the command; these cases pin
/// the shape of the walk on beatmaps built in code, where the curve is known.
/// </summary>
[TestFixture]
public class SceneTests
{
    private static readonly float[] straight_path = [0, 0, 30, 0, 30, 40];

    [Test]
    public void WalksAlongTheEmittedPolyline()
    {
        // 30 across then 40 down: 70 of arc length, and the corner is the interesting part
        // because a viewer that lerped between the endpoints instead would cut it.
        Assert.Multiple(() =>
        {
            Assert.That(Scene.PositionAlong(straight_path, 0), Is.EqualTo(new Vector2(0, 0)));
            Assert.That(Scene.PositionAlong(straight_path, 15), Is.EqualTo(new Vector2(15, 0)));
            Assert.That(Scene.PositionAlong(straight_path, 30), Is.EqualTo(new Vector2(30, 0)));
            Assert.That(Scene.PositionAlong(straight_path, 40), Is.EqualTo(new Vector2(30, 10)));
            Assert.That(Scene.PositionAlong(straight_path, 50), Is.EqualTo(new Vector2(30, 20)));
            Assert.That(Scene.PositionAlong(straight_path, 70), Is.EqualTo(new Vector2(30, 40)));
        });
    }

    [Test]
    public void ClampsRatherThanExtrapolating()
    {
        // The walk is asked for a distance the vertices do not cover whenever lazer counted
        // length it did not keep — an optimised Catmull path — and, harmlessly, whenever
        // floating-point accumulation lands a hair under the distance at progress 1.
        // SliderPath.interpolateVertices returns the last vertex in both cases, and running
        // off the end instead would fling the ball off-screen.
        Assert.Multiple(() =>
        {
            Assert.That(Scene.PositionAlong(straight_path, 500), Is.EqualTo(new Vector2(30, 40)));
            Assert.That(Scene.PositionAlong(straight_path, -10), Is.EqualTo(new Vector2(0, 0)));
            Assert.That(Scene.PositionAlong([7, 9], 100), Is.EqualTo(new Vector2(7, 9)));
            Assert.That(Scene.PositionAlong([], 1), Is.EqualTo(Vector2.Zero));
        });
    }

    [TestCase(1)]
    [TestCase(2)]
    [TestCase(3)]
    public void ReproducesEveryNestedPositionLazerPlaced(int spans)
    {
        var document = build(spans);
        var check = Scene.CrossCheck(document);

        Assert.Multiple(() =>
        {
            Assert.That(check.Checked, Is.GreaterThan(0), "nothing was compared, so nothing was proven");
            Assert.That(check.Diverged, Is.Zero, $"worst divergence {check.Worst} px");
            Assert.That(check.OptimisedSliders, Is.Zero, "a linear path has no length optimised out");
        });
    }

    [Test]
    public void EmitsStackedPositionsInPlayCoordinates()
    {
        var slider = build(1).Objects.Single(o => o.Type == "slider");

        Assert.Multiple(() =>
        {
            // The polyline is absolute, not slider-relative: it must start on the head and
            // end on the tail, in the same space as the cursor frames.
            var head = slider.Nested!.Single(n => n.Type == "head");
            var tail = slider.Nested!.Single(n => n.Type == "tail");

            Assert.That(slider.Path![0], Is.EqualTo(head.X).Within(0.02));
            Assert.That(slider.Path![1], Is.EqualTo(head.Y).Within(0.02));
            Assert.That(slider.Path![^2], Is.EqualTo(tail.X).Within(0.02));
            Assert.That(slider.Path![^1], Is.EqualTo(tail.Y).Within(0.02));
        });
    }

    [Test]
    public void EmitsFramesAsRecordedRatherThanResampled()
    {
        var document = build(1);

        Assert.Multiple(() =>
        {
            // Four numbers per frame, one frame per recorded frame. The simulator evaluates
            // far more instants than this; shipping those would hand the viewer an invented
            // signal at a rate the file does not contain.
            Assert.That(document.FrameCount, Is.EqualTo(3));
            Assert.That(document.Frames, Has.Count.EqualTo(12));
            Assert.That(document.Frames[0], Is.EqualTo(0));
            Assert.That(document.Frames[3], Is.Zero, "no button held on the first frame");
            Assert.That(document.Frames[7], Is.EqualTo(1), "left button is bit 0");
        });
    }

    /// <summary>
    /// A three-point linear slider, long enough to carry ticks, built in code. Nothing here
    /// ships a beatmap: CLAUDE.md forbids carrying fixtures, and the curve has to be known
    /// anyway for the assertion to mean anything.
    /// </summary>
    private static Scene.Document build(int spans)
    {
        var path = new SliderPath(PathType.LINEAR, [new Vector2(0, 0), new Vector2(200, 0), new Vector2(200, 120)]);

        var beatmap = new OsuBeatmap
        {
            HitObjects =
            {
                new Slider
                {
                    StartTime = 1000,
                    Position = new Vector2(80, 100),
                    Path = path,
                    RepeatCount = spans - 1
                }
            },
            Difficulty = new BeatmapDifficulty { CircleSize = 4, OverallDifficulty = 8 },
            BeatmapInfo = { Ruleset = new OsuRuleset().RulesetInfo },
            ControlPointInfo = controlPoints()
        };

        var score = new Score
        {
            ScoreInfo = new ScoreInfo(),
            Replay = new Replay
            {
                Frames =
                {
                    new OsuReplayFrame(0, OsuPlayfield.BASE_SIZE / 2),
                    new OsuReplayFrame(1000, new Vector2(80, 100), OsuAction.LeftButton),
                    new OsuReplayFrame(5000, new Vector2(280, 220), OsuAction.LeftButton)
                }
            }
        };

        var playable = new FlatWorkingBeatmap(beatmap).GetPlayableBeatmap(new OsuRuleset().RulesetInfo);

        return Scene.Build("synthetic.osr", playable, score, "0");
    }

    private static ControlPointInfo controlPoints()
    {
        var info = new ControlPointInfo();
        info.Add(0, new TimingControlPoint { BeatLength = 500 });
        return info;
    }
}
