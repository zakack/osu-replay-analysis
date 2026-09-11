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
