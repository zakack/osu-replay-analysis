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

    /// <summary>
    /// The same idea applied to the other end: trim the replay's tail and watch the extra
    /// misses on plays that stopped early. See <see cref="Simulator.TailTrim"/> for why
    /// 1250ms is the value to look at.
    /// </summary>
    public static void RunTailTrim(IReadOnlyList<ReplayRecord> records, IReadOnlyDictionary<string, IndexEntry> index,
                                   IReadOnlyList<double> trims, TextWriter output)
    {
        double original = Simulator.TailTrim;

        output.WriteLine($"{records.Count} replays that stopped before the beatmap did");
        output.WriteLine();
        output.WriteLine("   trim    dGreat     dOk    dMeh   dMiss  |   gross   exact");

        try
        {
            foreach (double trim in trims)
            {
                Simulator.TailTrim = trim;

                var drift = clicks.ToDictionary(r => r, _ => 0);
                int gross = 0;
                int exact = 0;

                // Parallel within the pass, never across passes: the loop above rewrites a
                // process global, so the passes have to stay on one thread. See Batch.
                foreach (var result in Batch.VerifyAll(records, index))
                {
                    if (result.Expected == null || result.Actual == null)
                        continue;

                    int replayGross = 0;

                    foreach (var click in clicks)
                    {
                        int delta = result.Actual.GetValueOrDefault(click) - result.Expected.GetValueOrDefault(click);
                        drift[click] += delta;
                        replayGross += Math.Abs(delta);
                    }

                    gross += replayGross;

                    if (result.Outcome == Outcome.Match)
                        exact++;
                }

                string columns = string.Join(" ", clicks.Select(c => $"{drift[c],7}"));
                output.WriteLine($"  {trim,5:0} {columns}   | {gross,7} {exact,4}");
            }
        }
        finally
        {
            Simulator.TailTrim = original;
        }
    }

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

                // Parallel within the pass, never across passes: the loop above rewrites a
                // process global, so the passes have to stay on one thread. See Batch.
                foreach (var result in Batch.VerifyAll(records, index))
                {
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
