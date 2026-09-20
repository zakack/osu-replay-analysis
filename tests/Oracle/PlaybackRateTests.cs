using System.Text.Json;
using Extract;
using NUnit.Framework;
using osu.Framework.Screens;
using osu.Framework.Testing;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Judgements;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Scoring;
using osu.Game.Screens.Play;
using osu.Game.Tests.Visual;

namespace Oracle;

/// <summary>
/// Plays one replay through lazer's own gameplay stack twice, at two playback rates, and
/// compares the click judgements.
///
/// This answers a question the corpus cannot. 85 of the corpus's completed modern-client
/// plays have lazer's playback reaching different click counts than the header the play
/// wrote — but that is play against playback, and the play is gone. Whether lazer is
/// *self*-inconsistent, reaching two different answers from the same file depending only on
/// how finely it samples, has never been shown. ppy/osu#34016 demonstrates exactly that for
/// a slider tail at 0.05x; nobody has shown it for a press.
///
/// If clicks are identical across rates, playback is cadence-stable and the 85 come from the
/// recording side instead — and no replay-against-replay test can reach them.
/// </summary>
[HeadlessTest]
[TestFixture]
public partial class PlaybackRateTests : RateAdjustedBeatmapTestScene
{
    private readonly List<JudgementResult> results = [];
    private RatePlayer currentPlayer = null!;

    private sealed record Target(string ReplayPath, string BeatmapPath);

    /// <summary>Only objects settled by a single press. Tails and ticks are decided by
    /// continuous cursor state and are known to move with sampling rate already.</summary>
    private static bool clickJudged(object hitObject) => hitObject is HitCircle or SliderHeadCircle;

    private static IEnumerable<TestCaseData> targets()
    {
        string list = RepoPaths.Build("oracle-targets.json");

        if (!File.Exists(list))
        {
            yield return new TestCaseData(null, null).Ignore($"No target list at {list}.");

            yield break;
        }

        foreach (var t in JsonSerializer.Deserialize<List<Target>>(File.ReadAllText(list))!)
            yield return new TestCaseData(t.ReplayPath, t.BeatmapPath)
                .SetName($"Rate_{Path.GetFileNameWithoutExtension(t.ReplayPath)![..Math.Min(50, Path.GetFileNameWithoutExtension(t.ReplayPath)!.Length)]}");
    }

    [TestCaseSource(nameof(targets))]
    public void ClickJudgementsAcrossPlaybackRates(string replay, string beatmapPath)
    {
        Score score = null!;
        IBeatmap beatmap = null!;
        string replayPath = replay;

        var captured = new Dictionary<double, Dictionary<double, string>>();
        var cadence = new Dictionary<double, double>();

        AddStep("decode the target", () =>
        {
            var index = BeatmapIndex.Read(RepoPaths.Build("beatmap-index.json"));
            score = ReplayLoader.Decode(replayPath, index);
            beatmap = new FlatWorkingBeatmap(beatmapPath).Beatmap;
            beatmap.BeatmapInfo.Ruleset = new OsuRuleset().RulesetInfo;

            TestContext.Out.WriteLine($"target: {Path.GetFileName(replayPath)}");
        });

        foreach (double rate in new[] { 1.0, 0.25 })
        {
            double captureRate = rate;
            var steps = new List<double>();

            AddStep($"set beatmap for {captureRate}x", () =>
            {
                Beatmap.Value = CreateWorkingBeatmap(beatmap);
                Ruleset.Value = new OsuRuleset().RulesetInfo;
                SelectedMods.Value = score.ScoreInfo.Mods;
            });

            AddStep($"push player at {captureRate}x", () =>
            {
                results.Clear();
                steps.Clear();

                var player = new RatePlayer(score);

                player.OnLoadComplete += _ =>
                {
                    player.ScoreProcessor.NewJudgement += r =>
                    {
                        if (currentPlayer == player)
                            results.Add(r);
                    };
                };

                LoadScreen(currentPlayer = player);
            });

            AddUntilStep("wait for player", () => currentPlayer.IsCurrentScreen());

            // Set on the master clock, which is what the in-game replay speed slider drives.
            // Whether it reaches the gameplay clock in a host with no real audio track is
            // exactly what the measured cadence below is here to show.
            AddStep($"apply {captureRate}x", () =>
            {
                foreach (var master in currentPlayer.ChildrenOfType<MasterGameplayClockContainer>())
                    master.UserPlaybackRate.Value = captureRate;

                if (currentPlayer.ChildrenOfType<GameplayClockContainer>().Single().CurrentTime < 0)
                    currentPlayer.Seek(0);
            });

            for (int chunk = 1; chunk <= 160; chunk++)
            {
                double at = chunk * 5000.0;

                AddUntilStep($"{captureRate}x reach {at / 1000:F0}s", () =>
                {
                    double elapsed = currentPlayer.ChildrenOfType<GameplayClockContainer>().Single().ElapsedFrameTime;

                    if (elapsed > 0 && steps.Count < 4000)
                        steps.Add(elapsed);

                    return currentPlayer.ReachedAnEnd
                           || currentPlayer.ChildrenOfType<GameplayClockContainer>().Single().CurrentTime >= at;
                });
            }

            double previous = double.NaN;
            int still = 0;

            AddUntilStep("gameplay time settles", () =>
            {
                if (currentPlayer.GameplayState.HasCompleted)
                    return true;

                double now = currentPlayer.FrameStableTime;
                still = now == previous ? still + 1 : 0;
                previous = now;

                return still >= 20 && currentPlayer.ReachedAnEnd;
            });

            AddStep($"capture {captureRate}x", () =>
            {
                // Keyed by object start time: the same object across two runs, whatever order
                // the judgements arrived in.
                captured[captureRate] = results
                                        .Where(r => clickJudged(r.HitObject))
                                        .GroupBy(r => r.HitObject.StartTime)
                                        .ToDictionary(g => g.Key, g => g.Last().Type.ToString());

                steps.Sort();
                cadence[captureRate] = steps.Count > 0 ? steps[steps.Count / 2] : double.NaN;

                TestContext.Out.WriteLine($"  {captureRate}x: {captured[captureRate].Count} click judgements, "
                                          + $"median clock step {cadence[captureRate]:F3}ms over {steps.Count} samples");
            });

            AddStep("exit player", () => currentPlayer.Exit());
            AddUntilStep("player exited", () => !currentPlayer.IsCurrentScreen());
        }

        AddStep("compare", () =>
        {
            var fast = captured[1.0];
            var slow = captured[0.25];

            TestContext.Out.WriteLine();
            TestContext.Out.WriteLine($"cadence 1.0x={cadence[1.0]:F3}ms  0.25x={cadence[0.25]:F3}ms  "
                                      + $"(ratio {cadence[1.0] / cadence[0.25]:F1}x)");

            if (Math.Abs(cadence[1.0] - cadence[0.25]) < 0.001)
                TestContext.Out.WriteLine("WARNING: the rate did not change the sampling cadence; this proves nothing.");

            var moved = fast.Keys.Intersect(slow.Keys).Where(t => fast[t] != slow[t]).OrderBy(t => t).ToArray();
            var onlyFast = fast.Keys.Except(slow.Keys).OrderBy(t => t).ToArray();
            var onlySlow = slow.Keys.Except(fast.Keys).OrderBy(t => t).ToArray();

            TestContext.Out.WriteLine($"objects judged at both rates: {fast.Keys.Intersect(slow.Keys).Count()}");
            TestContext.Out.WriteLine($"  judged DIFFERENTLY:         {moved.Length}");
            TestContext.Out.WriteLine($"  judged only at 1.0x:        {onlyFast.Length}");
            TestContext.Out.WriteLine($"  judged only at 0.25x:       {onlySlow.Length}");

            foreach (double t in moved.Take(20))
                TestContext.Out.WriteLine($"    {t,10:F0}ms   1.0x={fast[t],-6} 0.25x={slow[t]}");
        });
    }

    private partial class RatePlayer(Score score) : ReplayPlayer(score, new PlayerConfiguration
    {
        AllowPause = false,
        ShowResults = false
    })
    {
        public new osu.Game.Rulesets.Scoring.ScoreProcessor ScoreProcessor => base.ScoreProcessor;

        public bool WaitingOnFrames => DrawableRuleset?.FrameStableClock.WaitingOnFrames.Value == true;

        public double FrameStableTime => DrawableRuleset?.FrameStableClock.CurrentTime ?? 0;

        public bool ReachedAnEnd => GameplayState.HasCompleted || WaitingOnFrames;

        protected override bool PauseOnFocusLost => false;
    }
}
