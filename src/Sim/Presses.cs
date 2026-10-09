using System.Globalization;
using System.Text;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;

namespace Sim;

/// <summary>
/// Every button-down in a replay and what lazer made of it, one row per press.
///
/// clicks.csv is per object, so a press that judged nothing appears there at most once, as the
/// first stray attributed to some object. Aligning presses to objects in a stream needs all of
/// them: the extra tap, the notelocked one, the one that landed on the next circle. This is
/// <see cref="Simulator.PressLog"/> written out, nothing derived.
///
/// Columns, times in <b>map time</b>: <c>time</c>; <c>action</c> (LeftButton/RightButton);
/// <c>outcome</c> — <c>hit</c>, <c>notelock</c> (hovered a circle inside its window while an
/// earlier one was still open), <c>window</c> (hovered a circle outside its hit window) or
/// <c>nothing</c>; <c>target</c>, the start time of the circle the press landed on (blank for
/// <c>nothing</c>); <c>result</c>, the judgement a hit produced; <c>x</c>/<c>y</c>, the cursor.
/// </summary>
public static class Presses
{
    public const string CsvHeader = "replay,time,action,outcome,target,result,x,y";

    public static string ExtractCsv(ReplayRecord record, IReadOnlyDictionary<string, IndexEntry> index)
    {
        var score = ReplayLoader.Decode(record.Path, index);
        var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        var simulator = new Simulator { PressLog = [] };
        var simulation = simulator.Run(playable, score);

        // Circles and slider heads by start time, for the result a hit produced. Two objects
        // can share a start time only in a broken map; the first one wins, as it would in play.
        var results = new Dictionary<double, string>();
        foreach (var o in simulation.Objects)
        {
            if (o.HitObject is HitCircle && o.Judged)
                results.TryAdd(o.HitObject.StartTime, o.Result.ToString());
        }

        string name = Path.GetFileName(record.Path);
        string quoted = "\"" + name.Replace("\"", "\"\"") + "\"";
        string f(double v) => v.ToString("0.###", CultureInfo.InvariantCulture);

        var sb = new StringBuilder();
        foreach (var (time, cursor, outcome, target, action) in simulator.PressLog!)
        {
            string result = outcome == "hit" && target is { } t && results.TryGetValue(t, out var r) ? r : string.Empty;
            sb.Append(quoted).Append(',')
              .Append(f(time)).Append(',')
              .Append(action).Append(',')
              .Append(outcome).Append(',')
              .Append(target is { } tt ? f(tt) : string.Empty).Append(',')
              .Append(result).Append(',')
              .Append(f(cursor.X)).Append(',')
              .Append(f(cursor.Y)).Append('\n');
        }

        return sb.ToString();
    }
}
