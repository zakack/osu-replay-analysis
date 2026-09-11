using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;

namespace Extract;

public static class Smoke
{
    /// <summary>
    /// Proves a beatmap can be taken all the way to playable form — conversion,
    /// ApplyDefaults and stacking — with no GameHost in existence.
    /// </summary>
    public static (int objects, float firstStackedX) Load(string osuPath)
    {
        var working = new FlatWorkingBeatmap(osuPath);
        var playable = working.GetPlayableBeatmap(new OsuRuleset().RulesetInfo);
        var first = (OsuHitObject)playable.HitObjects[0];
        return (playable.HitObjects.Count, first.StackedPosition.X);
    }
}
