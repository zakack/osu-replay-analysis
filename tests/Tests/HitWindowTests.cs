using NUnit.Framework;
using osu.Game.Beatmaps;
using osu.Game.Replays;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Beatmaps;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using Sim;

namespace Tests;

/// <summary>
/// The boundary cases are transcribed from ppy's own
/// <c>osu.Game.Rulesets.Osu.Tests/TestSceneReplayStability.cs</c>, which asserts them
/// against real gameplay. They test precisely the behaviour this project reimplements
/// rather than calls, and the sub-millisecond boundaries are where a plausible-looking
/// reimplementation quietly disagrees.
/// </summary>
[TestFixture]
public class HitWindowTests
{
    private const double hit_circle_time = 100;

    private static readonly object[] cases =
    [
        // OD 5: Great [-49.5, 49.5], Ok [-99.5, 99.5], Meh [-149.5, 149.5].
        new object[] { 5f, 49d, HitResult.Great },
        new object[] { 5f, 49.2d, HitResult.Great },
        new object[] { 5f, 49.7d, HitResult.Ok },
        new object[] { 5f, 50d, HitResult.Ok },
        new object[] { 5f, 50.4d, HitResult.Ok },
        new object[] { 5f, 99d, HitResult.Ok },
        new object[] { 5f, 99.2d, HitResult.Ok },
        new object[] { 5f, 99.7d, HitResult.Meh },
        new object[] { 5f, 100d, HitResult.Meh },
        new object[] { 5f, 149d, HitResult.Meh },
        new object[] { 5f, 149.2d, HitResult.Meh },
        new object[] { 5f, 149.7d, HitResult.Miss },
        new object[] { 5f, 150d, HitResult.Miss },
        new object[] { 5f, 151d, HitResult.Miss },

        // OD 5.7: Great [-44.5, 44.5], Ok [-93.5, 93.5], Meh [-142.5, 142.5].
        new object[] { 5.7f, 44d, HitResult.Great },
        new object[] { 5.7f, 44.2d, HitResult.Great },
        new object[] { 5.7f, 44.8d, HitResult.Ok },
        new object[] { 5.7f, 45d, HitResult.Ok },
        new object[] { 5.7f, 93d, HitResult.Ok },
        new object[] { 5.7f, 93.9d, HitResult.Meh },
        new object[] { 5.7f, 94d, HitResult.Meh },
        new object[] { 5.7f, 142d, HitResult.Meh },
        new object[] { 5.7f, 142.7d, HitResult.Miss },
        new object[] { 5.7f, 143d, HitResult.Miss },
    ];

    [TestCaseSource(nameof(cases))]
    public void JudgesAtWindowBoundary(float overallDifficulty, double hitOffset, HitResult expected)
    {
        var beatmap = new OsuBeatmap
        {
            HitObjects = { new HitCircle { StartTime = hit_circle_time, Position = OsuPlayfield.BASE_SIZE / 2 } },
            Difficulty = new BeatmapDifficulty { OverallDifficulty = overallDifficulty },
            BeatmapInfo = { Ruleset = new OsuRuleset().RulesetInfo }
        };

        var score = new Score
        {
            ScoreInfo = new ScoreInfo(),
            Replay = new Replay
            {
                Frames =
                {
                    new OsuReplayFrame(0, OsuPlayfield.BASE_SIZE / 2),
                    new OsuReplayFrame(hit_circle_time + hitOffset, OsuPlayfield.BASE_SIZE / 2, OsuAction.LeftButton),
                    new OsuReplayFrame(hit_circle_time + hitOffset + 20, OsuPlayfield.BASE_SIZE / 2)
                }
            }
        };

        var playable = new FlatWorkingBeatmap(beatmap).GetPlayableBeatmap(new OsuRuleset().RulesetInfo);
        var result = new Simulator().Run(playable, score);

        Assert.That(result.Objects.Single().Result, Is.EqualTo(expected));
    }
}
