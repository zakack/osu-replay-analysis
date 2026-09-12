using System.Globalization;
using System.Text;
using System.Text.Json;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;

namespace Sim;

/// <summary>
/// The two-click flam: one click where the object was, one where it was actually hit.
///
/// This is a debugging instrument before it is a feature. A constant offset in reconstructed
/// hit times is inaudible as a column of numbers and unmistakable as a slap-back echo, and
/// every bin in the project is built on those times. Ears resolve temporal order roughly an
/// order of magnitude better than eyes, which is the whole reason to listen rather than plot.
///
/// Nothing here carries the beatmap or its audio. The output is object times, hit times and
/// signed errors — numbers derived from two files the user already has — and the page
/// synthesises every sound it makes.
/// </summary>
public static class Flam
{
    public sealed record Event(double T, double? Hit, double? Err, string Result, string Kind);

    public sealed record Track(
        string Replay,
        double Rate,
        Dictionary<string, double> HitWindows,
        IReadOnlyList<Event> Objects);

    public static Track Build(string replayPath, IBeatmap playable, Score score)
    {
        // A spinner carries empty hit windows, so take them from a circle.
        var windows = playable.HitObjects.OfType<HitCircle>().First().HitWindows;

        double rate = score.ScoreInfo.Mods.OfType<IApplicableToRate>()
                           .Aggregate(1.0, (current, mod) => mod.ApplyToRate(0, current));

        var events = new List<Event>();

        foreach (var state in new Simulator().Run(playable, score).Objects)
        {
            // Slider heads count: they are clicked and they carry a hit error. Tails and ticks
            // do not, because tracking is not a tap and putting it in the same click track
            // would make the flam say something it cannot know.
            if (state.HitObject is not HitCircle || state.HitObject is SliderEndCircle)
                continue;

            double start = state.HitObject.StartTime;
            double? offset = state.TimeOffset;

            events.Add(new Event(
                Math.Round(start, 2),
                offset is { } o ? Math.Round(start + o, 2) : null,
                offset is { } e ? Math.Round(e, 2) : null,
                state.Judged ? state.Result.ToString() : "Unjudged",
                state.HitObject is SliderHeadCircle ? "sliderHead" : "circle"));
        }

        var edges = new Dictionary<string, double>
        {
            ["great"] = windows.WindowFor(HitResult.Great),
            ["ok"] = windows.WindowFor(HitResult.Ok),
            ["meh"] = windows.WindowFor(HitResult.Meh)
        };

        return new Track(Path.GetFileNameWithoutExtension(replayPath), rate, edges, events.OrderBy(e => e.T).ToArray());
    }

    /// <summary>
    /// Inline the track into the page template, so the result is one file that opens from
    /// disk. A page that fetched its data beside itself would be blocked reading file://,
    /// and asking for a web server to hear a metronome is a poor trade.
    /// </summary>
    public static void Write(Track track, string templatePath, string destination)
    {
        string template = File.ReadAllText(templatePath);
        // Camel case, because the page reads these names directly and a mismatch here fails
        // silently as an empty track rather than as an error.
        string json = JsonSerializer.Serialize(track, new JsonSerializerOptions
        {
            WriteIndented = false,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase
        });

        const string placeholder = "\"__FLAM_DATA__\"";

        if (!template.Contains(placeholder, StringComparison.Ordinal))
            throw new InvalidDataException($"{templatePath} has no {placeholder} placeholder");

        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);
        File.WriteAllText(destination, template.Replace(placeholder, json), new UTF8Encoding(false));
    }

    public static string Summarise(Track track)
    {
        var errors = track.Objects.Where(o => o.Err != null).Select(o => o.Err!.Value).ToArray();

        if (errors.Length == 0)
            return "no judged clicks";

        double mean = errors.Average();
        double deviation = Math.Sqrt(errors.Sum(e => (e - mean) * (e - mean)) / errors.Length);

        return string.Create(CultureInfo.InvariantCulture,
            $"{track.Objects.Count} clicks, mean error {mean:+0.0;-0.0;0.0}ms, unstable rate {deviation * 10:0.0}");
    }
}
