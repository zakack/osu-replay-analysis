using Extract;
using osu.Game.Rulesets.Scoring;

namespace Sim;

/// <summary>
/// Asks one question: is the residual against the <c>.osr</c> header a constant timing
/// shift, or is it scatter?
///
/// The legacy encoder rounds every replay frame time to a whole millisecond, so a replay is
/// quantised relative to the play that wrote it. If the corpus-wide drift in click
/// judgements collapses at some non-zero offset, the header is still reachable and there is
/// a half-millisecond to go and find in the code. If the drift is smallest at zero and grows
/// either way, the timing is already as close as it can be and the residual is information
/// the file no longer carries.
/// </summary>
public static class ShiftSweep
{
    private static readonly HitResult[] clicks = [HitResult.Great, HitResult.Ok, HitResult.Meh, HitResult.Miss];

    public static void Run(IReadOnlyList<ReplayRecord> records, IReadOnlyDictionary<string, IndexEntry> index,
                           IReadOnlyList<double> shifts, TextWriter output)
    {
        double original = ReplaySampler.FrameShift;

        output.WriteLine($"{records.Count} replays, click judgements only (simulation minus header)");
        output.WriteLine();
        output.WriteLine("  shift     Great      Ok     Meh    Miss   |  gross   exact");

        try
        {
            foreach (double shift in shifts)
            {
                ReplaySampler.FrameShift = shift;

                var drift = clicks.ToDictionary(r => r, _ => 0);
                int gross = 0;
                int exact = 0;
                int counted = 0;

                foreach (var record in records)
                {
                    var result = Verification.Verify(record, index);

                    if (result.Expected == null || result.Actual == null)
                        continue;

                    counted++;
                    int replayGross = 0;

                    foreach (var click in clicks)
                    {
                        int delta = result.Actual.GetValueOrDefault(click) - result.Expected.GetValueOrDefault(click);
                        drift[click] += delta;
                        replayGross += Math.Abs(delta);
                    }

                    gross += replayGross;

                    if (replayGross == 0)
                        exact++;
                }

                string columns = string.Join(" ", clicks.Select(c => $"{drift[c],7}"));
                output.WriteLine($"  {shift,5:+0.00;-0.00; 0.00} {columns}   | {gross,6} {exact,4}/{counted}");
            }
        }
        finally
        {
            ReplaySampler.FrameShift = original;
        }
    }
}
