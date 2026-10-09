using System.Globalization;
using System.Text;
using Extract;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;

namespace Sim;

/// <summary>
/// Every miss on a circle or slider head, and every judgement that broke combo, with whether
/// lazer would have played the combo-break sound for it.
///
/// Combo is walked over the simulator's ordered judgements, nested slider objects included,
/// exactly as <see cref="Simulator"/>'s summary counts it. The sound follows
/// <c>ComboEffects.onComboChange</c> (ppy/osu osu.Game/Screens/Play/ComboEffects.cs:59): it
/// fires when combo becomes 0 from more than 20, or on the first break of the play when
/// AlwaysPlayFirstComboBreak is set (it is, in the player's game.ini), and any sound played
/// counts as that first break. A bindable only fires on change, so a miss at combo 0 never
/// sounds.
///
/// Columns, map time: <c>judgementTime</c>, <c>objectStart</c> (the hit object's StartTime),
/// <c>kind</c> (the nested type: HitCircle, SliderHeadCircle, SliderTick, SliderTailCircle...),
/// <c>result</c>, <c>comboBefore</c>, <c>sound</c> (1/0).
/// </summary>
public static class ComboBreaks
{
    public const string CsvHeader = "replay,judgementTime,objectStart,kind,result,comboBefore,sound";

    public static string ExtractCsv(ReplayRecord record, IReadOnlyDictionary<string, IndexEntry> index)
    {
        var score = ReplayLoader.Decode(record.Path, index);
        var playable = new FlatWorkingBeatmap(index[record.BeatmapMd5].Path)
            .GetPlayableBeatmap(new OsuRuleset().RulesetInfo, score.ScoreInfo.Mods);

        var simulation = new Simulator().Run(playable, score);

        string quoted = "\"" + Path.GetFileName(record.Path).Replace("\"", "\"\"") + "\"";
        string f(double v) => v.ToString("0.###", CultureInfo.InvariantCulture);
        var sb = new StringBuilder();

        int combo = 0;
        bool anySound = false;

        foreach (var state in simulation.InJudgementOrder)
        {
            if (!state.Result.AffectsCombo())
                continue;

            int before = combo;
            combo = state.Result.IsHit() ? combo + 1 : 0;

            bool broke = before > 0 && combo == 0;
            bool sound = broke && (before > 20 || !anySound);
            if (sound)
                anySound = true;

            bool tapMiss = state.Result == HitResult.Miss && state.HitObject is HitCircle;
            if (!broke && !tapMiss)
                continue;

            sb.Append(quoted).Append(',')
              .Append(f(state.JudgementTime ?? state.HitObject.StartTime)).Append(',')
              .Append(f(state.HitObject.StartTime)).Append(',')
              .Append(state.HitObject.GetType().Name).Append(',')
              .Append(state.Result).Append(',')
              .Append(before).Append(',')
              .Append(sound ? '1' : '0').Append('\n');
        }

        return sb.ToString();
    }
}
