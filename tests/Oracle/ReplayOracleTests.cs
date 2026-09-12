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

        // HasCompleted covers passing, failing and quitting, so a replay that ends early
        // still terminates the run rather than hanging until the NUnit timeout.
        AddUntilStep("wait for completion", () => currentPlayer.GameplayState.HasCompleted);

        AddStep("record", () =>
        {
            var judgements = results.Select(r => new OracleJudgement(
                r.HitObject.GetType().Name,
                r.HitObject.StartTime,
                r.Type.ToString())).ToArray();

            OracleStore.Write(new OracleRun(replayPath, beatmapPath, judgements));
            TestContext.Out.WriteLine($"recorded {judgements.Length} judgements for {Path.GetFileName(replayPath)}");
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

        protected override bool PauseOnFocusLost => false;
    }
}
