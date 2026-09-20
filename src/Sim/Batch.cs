using Extract;

namespace Sim;

/// <summary>
/// Runs <see cref="Verification.Verify"/> over a corpus in parallel.
///
/// The work is embarrassingly parallel and worth parallelising: measured over a 200-replay
/// sample, 90% of the time is <see cref="Simulator.Run"/>, 9% is decoding the replay's LZMA
/// frame stream, and 0.7% is loading the beatmap. That last number is the one that matters
/// for what this class does <em>not</em> do — the corpus plays each beatmap 5.3 times on
/// average, but caching playable beatmaps across replays would save half a percent in
/// exchange for a cache key that has to encode every mod affecting conversion. Not worth it.
///
/// <para>
/// The contract this class exists to enforce: <see cref="Verification.IncludeClassic"/>,
/// <see cref="ReplaySampler.FrameShift"/> and <see cref="Simulator.TailTrim"/> are process
/// globals that a sweep rewrites between passes. Parallelism therefore belongs <em>inside</em>
/// a pass and never across them, so a sweep calls this once per parameter value and the
/// mutation stays on the single thread between calls.
/// </para>
/// </summary>
public static class Batch
{
    /// <summary>
    /// How many replays to simulate at once.
    ///
    /// Measured on a 13700KF (8 performance cores with SMT, 8 efficiency cores, 24 logical)
    /// over a 1,000-replay sample whose sequential cost is 440s:
    ///
    /// <code>
    ///  threads   wall    cpu   speedup   cpu cost
    ///        1    440s   440s     1.0x      1.00x
    ///        8   61.8s   457s     7.1x      1.04x
    ///       16   48.4s   600s     9.1x      1.36x
    ///       24   44.3s   781s     9.9x      1.77x
    /// </code>
    ///
    /// Scaling is near-perfectly linear to 8 — one thread per performance core, no SMT
    /// sharing, 4% overhead. Past that it is bought rather than free: the hyperthread
    /// siblings add 28% wall for 31% more cpu, and the efficiency cores add 9% for another
    /// 30%. Nothing there is a defect; a core-second on an SMT sibling or an efficiency core
    /// simply carries less work. Default to every processor because wall time is what a batch
    /// run is spending, but 8 is the setting that leaves the machine usable for almost none
    /// of the wall-clock, and 16 gets 92% of peak.
    /// </summary>
    public static int Threads { get; set; } = Environment.ProcessorCount;

    /// <summary>
    /// Verify every record, returning results in the order given.
    ///
    /// Order is not incidental. The taxonomy report is the deliverable and gets diffed
    /// between runs, so results are written into a pre-sized array by index rather than
    /// appended, and a parallel run produces a file byte-identical to a sequential one.
    /// </summary>
    public static VerificationResult[] VerifyAll(IReadOnlyList<ReplayRecord> records,
                                                 IReadOnlyDictionary<string, IndexEntry> index,
                                                 TextWriter? progress = null)
    {
        var results = new VerificationResult[records.Count];

        if (records.Count == 0)
            return results;

        // Verify one replay before opening the throttle. osu.Game registers its beatmap
        // decoders into a lazily-initialised static dictionary on first use, and racing the
        // whole pool into that initialisation is how a run dies forty minutes in.
        results[0] = Verification.Verify(records[0], index);

        var clock = System.Diagnostics.Stopwatch.StartNew();
        int done = 1;

        Parallel.For(1, records.Count, new ParallelOptions { MaxDegreeOfParallelism = Threads }, i =>
        {
            results[i] = Verification.Verify(records[i], index);

            int finished = Interlocked.Increment(ref done);

            // A quarter-hour of silence is indistinguishable from a hang. Reported on stderr
            // so a redirected stdout still holds only the report.
            if (progress != null && finished % 250 == 0)
            {
                double rate = finished / clock.Elapsed.TotalSeconds;
                var left = TimeSpan.FromSeconds((records.Count - finished) / rate);
                progress.Write($"\r  {finished,7:N0} / {records.Count:N0}   {rate,5:F0}/s   {left:hh\\:mm\\:ss} left");
            }
        });

        if (progress != null)
            progress.Write($"\r  {records.Count,7:N0} / {records.Count:N0}   done in {clock.Elapsed:hh\\:mm\\:ss}          \n");

        return results;
    }
}
