using NUnit.Framework;
using osu.Framework.Screens;
using osu.Framework.Testing;
using osu.Game.Beatmaps;
using osu.Game.Replays;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Beatmaps;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.Replays;
using osu.Game.Rulesets.Osu.UI;
using osu.Game.Scoring;
using osu.Game.Screens.Play;
using osu.Game.Tests.Visual;
using osuTK;

namespace Oracle;

/// <summary>
/// Measures how fast the gameplay clock actually advances under the headless test host.
///
/// This matters because the oracle is only a fair reference for slider tracking when the
/// simulation samples at the same rate it does. The simulation's step was fitted against the
/// .osr header, which came from an unknown machine; diffing against the oracle at that fitted
/// rate would attribute pure sampling-phase differences to the port.
/// </summary>
[HeadlessTest]
[TestFixture]
public partial class HostFrameRateTests : RateAdjustedBeatmapTestScene
{
    [Test]
    public void MeasureGameplayClockStep()
    {
        var deltas = new List<double>();
        ReplayPlayer player = null!;

        // A long empty-ish beatmap so the clock runs for a while with nothing to judge.
        var beatmap = new OsuBeatmap
        {
            HitObjects = { new HitCircle { StartTime = 30000, Position = OsuPlayfield.BASE_SIZE / 2 } },
            Difficulty = new BeatmapDifficulty(),
            BeatmapInfo = { Ruleset = new OsuRuleset().RulesetInfo }
        };

        var score = new Score
        {
            ScoreInfo = new ScoreInfo { Ruleset = new OsuRuleset().RulesetInfo },
            Replay = new Replay
            {
                Frames =
                {
                    new OsuReplayFrame(0, OsuPlayfield.BASE_SIZE / 2),
                    // One frame far in the future, so the clock has a long uninterrupted run
                    // and its step is the host's, not the replay's frame spacing.
                    new OsuReplayFrame(30000, OsuPlayfield.BASE_SIZE / 2)
                }
            }
        };

        AddStep("set beatmap", () =>
        {
            Beatmap.Value = CreateWorkingBeatmap(beatmap);
            Ruleset.Value = new OsuRuleset().RulesetInfo;
        });

        AddStep("push player", () => LoadScreen(player = new ReplayPlayer(score, new PlayerConfiguration
        {
            AllowPause = false,
            ShowResults = false
        })));

        AddUntilStep("wait for player", () => player.IsCurrentScreen()
                                            && player.ChildrenOfType<GameplayClockContainer>().SingleOrDefault()?.IsRunning == true);

        AddUntilStep("sample clock deltas", () =>
        {
            double elapsed = player.ChildrenOfType<GameplayClockContainer>().Single().ElapsedFrameTime;

            if (elapsed > 0)
                deltas.Add(elapsed);

            return deltas.Count >= 200;
        });

        AddStep("report", () =>
        {
            deltas.Sort();

            TestContext.Out.WriteLine($"gameplay clock step under the test host, over {deltas.Count} frames:");
            TestContext.Out.WriteLine($"  median {deltas[deltas.Count / 2]:F3} ms");
            TestContext.Out.WriteLine($"  p10    {deltas[deltas.Count / 10]:F3} ms");
            TestContext.Out.WriteLine($"  p90    {deltas[deltas.Count * 9 / 10]:F3} ms");
            TestContext.Out.WriteLine($"  mean   {deltas.Average():F3} ms");
        });
    }
}
