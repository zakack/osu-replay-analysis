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
    /// <summary>
    /// The measurement that matters: how the clock steps during a real replay, where
    /// SetFrameFromTime clamps to each frame's span and frame stability can cap an advance.
    /// The idle-clock measurement below is not representative of that.
    /// </summary>
    [Test]
    public void MeasureGameplayClockStepDuringRealReplay()
    {
        var deltas = new List<double>();
        ScoreAccessibleOracleReplayPlayer player = null!;
        Score score = null!;
        IBeatmap beatmap = null!;

        AddStep("load a real replay", () =>
        {
            var targets = System.Text.Json.JsonSerializer.Deserialize<List<Target>>(
                File.ReadAllText(RepoPaths.Build("oracle-targets.json")))!;

            var target = targets[0];
            var index = Extract.BeatmapIndex.Read(RepoPaths.Build("beatmap-index.json"));

            score = Extract.ReplayLoader.Decode(target.ReplayPath, index);
            beatmap = new FlatWorkingBeatmap(target.BeatmapPath).Beatmap;
            beatmap.BeatmapInfo.Ruleset = new OsuRuleset().RulesetInfo;
        });

        AddStep("set beatmap", () =>
        {
            Beatmap.Value = CreateWorkingBeatmap(beatmap);
            Ruleset.Value = new OsuRuleset().RulesetInfo;
            SelectedMods.Value = score.ScoreInfo.Mods;
        });

        AddStep("push player", () => LoadScreen(player = new ScoreAccessibleOracleReplayPlayer(score)));

        AddUntilStep("wait for running", () => player.IsCurrentScreen()
                                               && player.ChildrenOfType<GameplayClockContainer>().SingleOrDefault()?.IsRunning == true);

        AddUntilStep("sample deltas mid-play", () =>
        {
            double elapsed = player.ChildrenOfType<GameplayClockContainer>().Single().ElapsedFrameTime;

            if (elapsed > 0)
                deltas.Add(elapsed);

            return deltas.Count >= 300;
        });

        AddStep("report", () =>
        {
            deltas.Sort();

            int atSixtyFps = deltas.Count(d => Math.Abs(d - 1000.0 / 60) < 0.5);
            int subMillisecond = deltas.Count(d => d < 1);

            TestContext.Out.WriteLine($"REAL REPLAY clock step over {deltas.Count} frames:");
            TestContext.Out.WriteLine($"  median {deltas[deltas.Count / 2]:F3} ms");
            TestContext.Out.WriteLine($"  p10    {deltas[deltas.Count / 10]:F3} ms");
            TestContext.Out.WriteLine($"  p90    {deltas[deltas.Count * 9 / 10]:F3} ms");
            TestContext.Out.WriteLine($"  mean   {deltas.Average():F3} ms");
            TestContext.Out.WriteLine($"  within 0.5ms of a 60fps step: {atSixtyFps} ({100.0 * atSixtyFps / deltas.Count:F1}%)");
            TestContext.Out.WriteLine($"  sub-millisecond:              {subMillisecond} ({100.0 * subMillisecond / deltas.Count:F1}%)");
        });
    }

    private sealed record Target(string ReplayPath, string BeatmapPath);

    private partial class ScoreAccessibleOracleReplayPlayer(Score score) : ReplayPlayer(score, new PlayerConfiguration
    {
        AllowPause = false,
        ShowResults = false
    })
    {
        protected override bool PauseOnFocusLost => false;
    }

    [Test]
    public void MeasureIdleGameplayClockStep()
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
