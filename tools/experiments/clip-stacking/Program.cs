using System.Globalization;
using System.Text.Json;
using osu.Game.Beatmaps;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Objects;

// Clip spike: does cutting a map's [HitObjects] to a time window change lazer's stacked
// positions for the objects that remain? Text-level filter, decoded by lazer itself.
var index = JsonDocument.Parse(File.ReadAllText(args[0])).RootElement;
int maps = int.Parse(args[1]);
var tmp = args[2];
var rng = new Random(1);
var paths = index.EnumerateObject().Select(p => p.Value.GetProperty("Path").GetString()!).OrderBy(_ => rng.Next()).ToList()
    .Where(File.Exists).Where(p => File.ReadLines(p).Take(40).Any(l => l.Replace(" ", "") == "Mode:0")).Take(maps).ToList();
var ruleset = new OsuRuleset().RulesetInfo;

List<OsuHitObject> decode(string path) => new FlatWorkingBeatmap(path).GetPlayableBeatmap(ruleset, Array.Empty<Mod>())
    .HitObjects.Cast<OsuHitObject>().ToList();

int clips = 0, clipsWithShift = 0, objs = 0, shifted = 0, failed = 0, tailClips = 0;
var byEdgeMs = new List<(double fromStart, double fromEnd, double px)>();
foreach (var path in paths)
{
    try
    {
        var lines = File.ReadAllLines(path);
        int h = Array.FindIndex(lines, l => l.Trim() == "[HitObjects]");
        var objLines = lines.Skip(h + 1).Where(l => l.Contains(',')).ToList();
        var full = decode(path);
        if (full.Count < 150) continue;
        var key = full.ToDictionary(o => (o.StartTime, o.Position.X, o.Position.Y), o => o);
        for (int k = 0; k < 4; k++)
        {
            int s = rng.Next(1, full.Count - 100), e = s + rng.Next(30, 100);
            double t0 = full[s].StartTime, t1 = full[e].StartTime;
            var kept = objLines.Where(l => { var t = double.Parse(l.Split(',')[2], CultureInfo.InvariantCulture); return t >= t0 && t <= t1; });
            var outPath = Path.Combine(tmp, "clip.osu");
            File.WriteAllLines(outPath, lines.Take(h + 1).Concat(kept));
            var clip = decode(outPath);
            clips++;
            bool any = false;
            foreach (var o in clip)
            {
                objs++;
                if (!key.TryGetValue((o.StartTime, o.Position.X, o.Position.Y), out var src)) { failed++; continue; }
                double d = (o.StackedPosition - src.StackedPosition).Length;
                if (d > 50) Console.Error.WriteLine($"{d:0} px h{src.StackHeight}->{o.StackHeight} t={o.StartTime} clip {t0}-{t1} {path}");
                if (d > 1e-3) { shifted++; any = true; byEdgeMs.Add((o.StartTime - t0, t1 - o.StartTime, d)); }
            }
            if (any) clipsWithShift++;
        }
    }
    catch (Exception ex) { Console.Error.WriteLine($"{Path.GetFileName(path)}: {ex.Message}"); }
}
Console.WriteLine($"maps {paths.Count}  clips {clips}  clips-with-shift {clipsWithShift}  objects {objs}  shifted {shifted}  unmatched {failed}");
Console.WriteLine($"shift px: median {(byEdgeMs.Count > 0 ? byEdgeMs.Select(x => x.px).Order().ElementAt(byEdgeMs.Count / 2) : 0):0.##} max {(byEdgeMs.Count > 0 ? byEdgeMs.Max(x => x.px) : 0):0.##}");
foreach (var (lo, hi) in new[] { (0, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 1e9) })
    Console.WriteLine($"nearest edge {lo}-{hi}ms: {byEdgeMs.Count(x => Math.Min(x.fromStart, x.fromEnd) >= lo && Math.Min(x.fromStart, x.fromEnd) < hi)}  (lead {byEdgeMs.Count(x => x.fromStart < x.fromEnd && x.fromStart >= lo && x.fromStart < hi)})");
