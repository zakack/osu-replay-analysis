using System.Text.Json;
using Extract;
using NUnit.Framework;
using osu.Framework.Screens;
using osu.Framework.Testing;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Judgements;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Scoring;
using osu.Game.Screens.Play;
using osu.Game.Tests.Visual;

namespace Oracle;

/// <summary>
/// Plays real replays through lazer's own gameplay stack and records what it judged, object
/// by object, so the reimplemented loop in Sim can be diffed against the reference.
///
/// The replay header only ever gave totals. A count that is two short says nothing about
/// which slider went wrong or when; this says exactly which one.
///
/// One caveat on how far to trust it. The oracle samples at the headless test host's frame
/// rate, not at the rate of whatever machine set the score. For judgements decided by a
/// single instant — hit windows, notelock, ordering — it is authoritative, and a divergence
/// is a defect in the port. For judgements decided by continuous cursor state, chiefly
/// slider tracking, the oracle is one draw from the same distribution the original play
/// drew from, so agreeing with it means the rules are right, not that the outcome was
/// inevitable.
/// </summary>
[HeadlessTest]
[TestFixture]
public partial class ReplayOracleTests : RateAdjustedBeatmapTestScene
{
    private ScoreAccessibleReplayPlayer currentPlayer = null!;
    private readonly List<JudgementResult> results = [];

    /// <summary>
    /// Replays to run, chosen by the `ora oracle-list` command. Hosting the game is slow, so
    /// this is deliberately a short list of interesting cases rather than the whole corpus.
    /// </summary>
    private static IEnumerable<TestCaseData> cases()
    {
        string listPath = RepoPaths.Build("oracle-targets.json");

        if (!File.Exists(listPath))
        {
            yield return new TestCaseData(null, null).Ignore($"No target list at {listPath}; run `ora oracle-list` first.");

            yield break;
        }

        var targets = JsonSerializer.Deserialize<List<OracleTarget>>(File.ReadAllText(listPath)) ?? [];

        foreach (var target in targets)
            yield return new TestCaseData(target.ReplayPath, target.BeatmapPath).SetName($"Oracle_{Path.GetFileNameWithoutExtension(target.ReplayPath)![..Math.Min(60, Path.GetFileNameWithoutExtension(target.ReplayPath)!.Length)]}");
    }

    private sealed record OracleTarget(string ReplayPath, string BeatmapPath);

    [TestCaseSource(nameof(cases))]
    public void RecordJudgements(string replayPath, string beatmapPath)
    {
        Score score = null!;
        IBeatmap beatmap = null!;

        AddStep("decode beatmap and replay", () =>
        {
            var index = BeatmapIndex.Read(RepoPaths.Build("beatmap-index.json"));
            score = ReplayLoader.Decode(replayPath, index);
            beatmap = new FlatWorkingBeatmap(beatmapPath).Beatmap;
            beatmap.BeatmapInfo.Ruleset = new OsuRuleset().RulesetInfo;
        });

        AddStep("set beatmap and ruleset", () =>
        {
            Beatmap.Value = CreateWorkingBeatmap(beatmap);
            Ruleset.Value = new OsuRuleset().RulesetInfo;
            SelectedMods.Value = score.ScoreInfo.Mods;
        });

        AddStep("push player", () =>
        {
            results.Clear();

            var player = new ScoreAccessibleReplayPlayer(score);

            player.OnLoadComplete += _ =>
            {
                player.ScoreProcessor.NewJudgement += result =>
                {
                    if (currentPlayer == player)
                        results.Add(result);
                };
            };

            LoadScreen(currentPlayer = player);
        });

        AddUntilStep("wait for player", () => currentPlayer.IsCurrentScreen());

        AddStep("skip intro if present", () =>
        {
            if (currentPlayer.ChildrenOfType<GameplayClockContainer>().Single().CurrentTime < 0)
                currentPlayer.Seek(0);
        });

        // The framework's per-step timeout is wall-clock and not settable, and a dense map
        // takes longer than it allows even at the headless host's many-times-realtime pace.
        // Waiting in chunks of beatmap time keeps every individual step well inside it.
        //
        // GameplayState.HasCompleted alone is not enough to stop on. It is HasPassed ||
        // HasFailed || HasQuit, and a replay of a failed play reaches none of the three:
        // ReplayPlayer.PerformFail overrides the base and deliberately never sets HasFailed,
        // because a replay gets an indicator and a button rather than the fail sequence.
        // ReachedAnEnd covers the two replay-only endings as well.
        for (int chunk = 1; chunk <= 160; chunk++)
        {
            double target = chunk * 5000.0;

            AddUntilStep($"reach {target / 1000:F0}s", () =>
                currentPlayer.ReachedAnEnd
                || currentPlayer.ChildrenOfType<GameplayClockContainer>().Single().CurrentTime >= target);
        }

        // Both replay-only endings freeze gameplay time rather than ending the screen, so
        // settle before recording. Running out of frames stops the gameplay clock outright;
        // a failure ramps the track frequency to zero over a second, and objects keep being
        // judged the whole way down. Waiting for time to actually stop moving captures that
        // tail instead of cutting it off mid-sweep.
        double previousTime = double.NaN;
        int samplesWithoutProgress = 0;

        AddUntilStep("gameplay time settles", () =>
        {
            if (currentPlayer.GameplayState.HasCompleted)
                return true;

            double now = currentPlayer.FrameStableTime;

            samplesWithoutProgress = now == previousTime ? samplesWithoutProgress + 1 : 0;
            previousTime = now;

            return samplesWithoutProgress >= 20 && currentPlayer.ReachedAnEnd;
        });

        AddAssert("gameplay reached an end", () =>
        {
            Assert.That(currentPlayer.ReachedAnEnd, Is.True,
                $"neither completed, failed nor ran out of frames; frame-stable clock only reached {currentPlayer.FrameStableTime:F0}ms");

            return true;
        });

        AddStep("record", () =>
        {
            var judgements = results.Select(r => new OracleJudgement(
                r.HitObject.GetType().Name,
                r.HitObject.StartTime,
                r.Type.ToString()) { TimeAbsolute = r.TimeAbsolute }).ToArray();

            // Not simply "the screen did not end". A replay can run out of frames close
            // enough to the last object that the fail sweep carries it over the end of the
            // beatmap, so the play both fails and completes. What matters for a diff is that
            // the reference stopped having input, which is these two and not HasCompleted.
            bool stalled = currentPlayer.Failed || currentPlayer.WaitingOnFrames;

            var run = new OracleRun(replayPath, beatmapPath, judgements)
            {
                StalledAt = stalled ? currentPlayer.FrameStableTime : null,
                HealthAtFailure = currentPlayer.HealthAtFailure,
                Failed = currentPlayer.Failed
            };

            OracleStore.Write(run);

            TestContext.Out.WriteLine($"recorded {judgements.Length} judgements for {Path.GetFileName(replayPath)}"
                                      + (stalled
                                          ? $" ({(currentPlayer.Failed ? "failed" : "out of frames")} at {run.StalledAt:F0}ms,"
                                            + $" health at failure {run.HealthAtFailure:F3})"
                                          : string.Empty));
        });

        AddStep("exit player", () => currentPlayer.Exit());
        AddUntilStep("player exited", () => !currentPlayer.IsCurrentScreen());
    }

    private partial class ScoreAccessibleReplayPlayer(Score score) : ReplayPlayer(score, new PlayerConfiguration
    {
        AllowPause = false,
        ShowResults = false
    })
    {
        public new osu.Game.Rulesets.Scoring.ScoreProcessor ScoreProcessor => base.ScoreProcessor;

        /// <summary>True once the replay has run out of frames. Lazer binds this to
        /// <c>GameplayClockContainer.Stop()</c>, so it is also the moment gameplay time
        /// freezes for good.</summary>
        public bool WaitingOnFrames => DrawableRuleset?.FrameStableClock.WaitingOnFrames.Value == true;

        /// <summary>
        /// True once the health processor has failed this replay.
        ///
        /// <see cref="Player.GameplayState"/> cannot answer this: <see cref="PerformFail"/>
        /// is overridden here to show an indicator instead of running the fail sequence, and
        /// never sets <c>HasFailed</c>. What it does do is call
        /// <c>ScoreProcessor.FailScore</c>, which moves the rank to F, so the rank is the
        /// signal that survives.
        /// </summary>
        public bool Failed => ScoreProcessor.Rank.Value == ScoreRank.F;

        /// <summary>
        /// Every way this screen can stop producing judgements. The last two do not end the
        /// screen at all, they just stop time: out of frames stops the gameplay clock, and a
        /// failure hands the track frequency to <c>ReplayFailIndicator</c>, which sweeps it
        /// to zero over a second and leaves it there pending a click this host will never
        /// make.
        /// </summary>
        public bool ReachedAnEnd => GameplayState.HasCompleted || WaitingOnFrames || Failed;

        /// <summary>The clock judgements are actually made against, which is not the one the
        /// gameplay clock container exposes.</summary>
        public double FrameStableTime => DrawableRuleset?.FrameStableClock.CurrentTime ?? 0;

        /// <summary>Health as it stood when the play was failed. Read afterwards it is
        /// meaningless: the fail sweep keeps judging for a second and hits can lift it back
        /// above zero.</summary>
        public double? HealthAtFailure { get; private set; }

        protected override void PerformFail()
        {
            HealthAtFailure ??= HealthProcessor.Health.Value;
            base.PerformFail();
        }

        public double LastFrameTime => Score.Replay.Frames.LastOrDefault()?.Time ?? double.NaN;

        protected override bool PauseOnFocusLost => false;
    }
}
