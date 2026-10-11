using System.Globalization;
using osuTK;

namespace Sim;

/// <summary>
/// The cursor the game was actually given during a play, recorded outside it (OpenTabletDriver's
/// virtual tablet, tools/corpus/cursor-trace) and already put in this replay's map time and
/// playfield coordinates by tools/traces/export_cursor.py.
///
/// A replay keeps the cursor at 60 Hz plus one frame per button change, so anything judged
/// between frames is judged against an interpolation. Supplying this replaces that
/// interpolation with what the game had; the replay's own frames, press frames above all,
/// keep their recorded positions. It is piecewise constant, as it was in the game: a
/// cursor does not move between the input events that move it.
///
/// Opt-in: set ORA_CURSOR_DIR to a directory of <c>&lt;replay file name&gt;.cursor.csv</c>
/// (columns <c>t,x,y</c>). With it unset, or with no file for a replay, nothing changes.
/// </summary>
public sealed class CursorTrace
{
    private readonly double[] times;
    private readonly Vector2[] positions;

    private CursorTrace(double[] times, Vector2[] positions)
    {
        this.times = times;
        this.positions = positions;
    }

    public static readonly string? Directory = Environment.GetEnvironmentVariable("ORA_CURSOR_DIR");

    /// <summary>
    /// A diagnostic, off by default: judge presses against the trace this many milliseconds
    /// before the press frame instead of against the frame's own position. Off is right. On
    /// 2026-10-11's 14 completed plays, the replay's press positions reproduce every click
    /// judgement and the trace at the press time reproduces 7, so the game judged a press at
    /// the cursor of the update that saw it, which is what the press frame records.
    /// </summary>
    public static readonly double PressLead =
        double.TryParse(Environment.GetEnvironmentVariable("ORA_CURSOR_PRESS_LEAD_MS"), NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double lead) ? lead : 0;

    /// <summary>
    /// A diagnostic: read the trace this many milliseconds earlier than asked, for the delay
    /// between a position leaving the tablet driver and the game acting on it.
    /// </summary>
    public static readonly double Shift =
        double.TryParse(Environment.GetEnvironmentVariable("ORA_CURSOR_SHIFT_MS"), NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double shift) ? shift : 0;

    public static CursorTrace? For(string replayPath)
    {
        if (string.IsNullOrEmpty(Directory))
            return null;

        string path = Path.Combine(Directory, Path.GetFileName(replayPath) + ".cursor.csv");

        if (!File.Exists(path))
            return null;

        var t = new List<double>();
        var p = new List<Vector2>();

        foreach (string line in File.ReadLines(path))
        {
            if (line.Length == 0 || !char.IsAsciiDigit(line[^1]) || !(char.IsAsciiDigit(line[0]) || line[0] == '-'))
                continue;

            string[] f = line.Split(',');
            t.Add(double.Parse(f[0], CultureInfo.InvariantCulture));
            p.Add(new Vector2(float.Parse(f[1], CultureInfo.InvariantCulture), float.Parse(f[2], CultureInfo.InvariantCulture)));
        }

        return t.Count == 0 ? null : new CursorTrace(t.ToArray(), p.ToArray());
    }

    /// <summary>The last recorded position at or before <paramref name="time"/>, or null outside the trace.</summary>
    public Vector2? At(double time)
    {
        time -= Shift;
        if (time < times[0] || time > times[^1] + 50)
            return null;

        int i = Array.BinarySearch(times, time);
        if (i < 0)
            i = ~i - 1;

        return positions[i];
    }
}
