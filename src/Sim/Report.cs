using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sim;

public static class Report
{
    public static void Print(IReadOnlyList<VerificationResult> results, TextWriter output)
    {
        var byOutcome = results.GroupBy(r => r.Outcome).ToDictionary(g => g.Key, g => g.ToArray());

        int inScope = byOutcome.GetValueOrDefault(Outcome.Match)?.Length ?? 0;
        int mismatched = byOutcome.GetValueOrDefault(Outcome.Mismatch)?.Length ?? 0;
        int attempted = inScope + mismatched;

        output.WriteLine($"eligible replays  {results.Count}");
        output.WriteLine($"in scope          {attempted}");
        output.WriteLine(attempted > 0
            ? $"exact match       {inScope} of {attempted}  ({100.0 * inScope / attempted:F1}%)"
            : "exact match       n/a, nothing in scope");
        output.WriteLine();

        output.WriteLine("outcomes:");

        foreach (var group in byOutcome.OrderByDescending(g => g.Value.Length))
            output.WriteLine($"  {group.Value.Length,5}  {group.Key}");

        // The taxonomy, which is the part worth reading. Three of these classes are not
        // defects, and the last one is the only place a disagreement means a bug.
        var byCause = results.GroupBy(Taxonomy.Classify).ToDictionary(g => g.Key, g => g.ToArray());

        output.WriteLine();
        output.WriteLine("by cause:");

        foreach (var cause in Enum.GetValues<Cause>())
        {
            var group = byCause.GetValueOrDefault(cause) ?? [];

            if (group.Length == 0)
                continue;

            output.WriteLine($"  {group.Length,5}  {cause}");

            // A cause that is not the whole story. Classification reports one, and ordering
            // decides which, so say when a second one also applies rather than hiding it.
            int alsoLegacy = cause == Cause.LegacyHitWindows ? 0 : group.Count(Taxonomy.OnLegacyHitWindows);

            if (alsoLegacy > 0)
                output.WriteLine($"  {alsoLegacy,5}    of which also on a legacy hit-window client");
        }

        // The question the taxonomy exists to answer: on a modern client, for a play that
        // ran to the end, does this reproduce what lazer judged?
        int comparable = (byCause.GetValueOrDefault(Cause.Exact)?.Length ?? 0)
                         + (byCause.GetValueOrDefault(Cause.TrackingOnly)?.Length ?? 0)
                         + (byCause.GetValueOrDefault(Cause.ClickMismatch)?.Length ?? 0);

        if (comparable > 0)
        {
            int clicksExact = comparable - (byCause.GetValueOrDefault(Cause.ClickMismatch)?.Length ?? 0);
            int allExact = byCause.GetValueOrDefault(Cause.Exact)?.Length ?? 0;

            output.WriteLine();
            output.WriteLine($"of {comparable} replays on a modern client that played to the end:");
            output.WriteLine($"  {clicksExact,5}  reproduce every click judgement  ({100.0 * clicksExact / comparable:F1}%)");
            output.WriteLine($"  {allExact,5}  reproduce every statistic        ({100.0 * allExact / comparable:F1}%)");
        }

        var mismatches = byOutcome.GetValueOrDefault(Outcome.Mismatch) ?? [];

        if (mismatches.Length > 0)
        {
            output.WriteLine();
            output.WriteLine("mismatches by signature:");

            foreach (var group in mismatches.GroupBy(r => r.Detail ?? "?").OrderByDescending(g => g.Count()).Take(20))
                output.WriteLine($"  {group.Count(),5}  {group.Key}");
        }

        var errors = byOutcome.GetValueOrDefault(Outcome.Error) ?? [];

        if (errors.Length > 0)
        {
            output.WriteLine();
            output.WriteLine("errors by cause:");

            foreach (var group in errors.GroupBy(r => r.Detail ?? "?").OrderByDescending(g => g.Count()).Take(20))
                output.WriteLine($"  {group.Count(),5}  {group.Key}");
        }
    }

    public static void Write(IReadOnlyList<VerificationResult> results, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(destination))!);

        var options = new JsonSerializerOptions
        {
            WriteIndented = true,
            Converters = { new JsonStringEnumConverter() }
        };

        using var stream = File.Create(destination);
        JsonSerializer.Serialize(stream, results, options);
    }
}
