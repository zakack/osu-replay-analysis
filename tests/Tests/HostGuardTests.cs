using Extract;
using NUnit.Framework;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Beatmaps;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Osu.UI;
using osuTK;

namespace Tests;

/// <summary>
/// Extraction must not stand up a game host. The project is non-commercial, so this is not
/// a licensing constraint — it is how we notice that a code path whose job is to read two
/// files has acquired a game loop, which would make it framerate-coupled and slow.
///
/// The beatmap is built in code rather than loaded from a fixture, because the one
/// licensing rule that does still bind is that this repository never carries beatmap files.
/// </summary>
[TestFixture]
public class HostGuardTests
{
    [Test]
    public void TakingABeatmapToPlayableFormMapsNoHostLibraries()
    {
        if (!HostGuard.IsSupported)
            Assert.Ignore("No /proc/self/maps on this platform, so the guard cannot observe anything.");

        var beatmap = new OsuBeatmap
        {
            HitObjects =
            {
                new HitCircle { StartTime = 1000, Position = new Vector2(100, 100) },
                // Stacked on the previous object, so conversion has stacking work to do.
                new HitCircle { StartTime = 1100, Position = new Vector2(100, 100) },
                new Slider
                {
                    StartTime = 1400,
                    Position = new Vector2(200, 150),
                    Path = new osu.Game.Rulesets.Objects.SliderPath(
                        osu.Game.Rulesets.Objects.Types.PathType.LINEAR,
                        [Vector2.Zero, new Vector2(120, 0)])
                },
                new Spinner { StartTime = 2000, Duration = 1000, Position = OsuPlayfield.BASE_SIZE / 2 }
            },
            Difficulty = new BeatmapDifficulty { CircleSize = 4, OverallDifficulty = 8 },
            BeatmapInfo = { Ruleset = new OsuRuleset().RulesetInfo }
        };

        var playable = new FlatWorkingBeatmap(beatmap).GetPlayableBeatmap(new OsuRuleset().RulesetInfo);

        Assert.Multiple(() =>
        {
            Assert.That(playable.HitObjects, Is.Not.Empty, "conversion produced no objects");
            Assert.That(((OsuHitObject)playable.HitObjects[0]).StackedPosition, Is.Not.EqualTo(((OsuHitObject)playable.HitObjects[1]).StackedPosition),
                "stacking was not applied, so this did not exercise the full playable path");
            Assert.That(HostGuard.LoadedHostLibraries(), Is.Empty, "a game host was constructed somewhere in the extraction path");
        });
    }
}
