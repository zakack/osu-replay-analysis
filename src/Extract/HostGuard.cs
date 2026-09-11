namespace Extract;

/// <summary>
/// Extraction must never stand up an osu!framework <c>GameHost</c> or an <c>OsuGameBase</c>.
/// Not for licensing reasons — the project is non-commercial, so BASS and osu-resources are
/// both fine — but because a hosted game loop makes extraction framerate-coupled and slow,
/// and framerate-coupling is the one property the extraction layer cannot have.
///
/// Loading BASS is the cheapest observable proxy for "a host came up", since the audio
/// subsystem is initialised by the framework's audio thread and by nothing else we call.
/// </summary>
public static class HostGuard
{
    private static readonly string[] watched_libraries = ["libbass", "libSDL", "libveldrid"];

    /// <summary>
    /// Native libraries mapped into this process that indicate a host was constructed.
    /// Empty is the expected result after any amount of extraction work.
    /// </summary>
    public static IReadOnlyList<string> LoadedHostLibraries()
    {
        var found = new SortedSet<string>(StringComparer.Ordinal);

        // Linux-only. Elsewhere the guard degrades to a no-op rather than a false pass,
        // so callers should treat an unsupported platform as "not checked".
        if (!File.Exists("/proc/self/maps"))
            return [];

        foreach (string line in File.ReadLines("/proc/self/maps"))
        {
            int slash = line.LastIndexOf('/');
            if (slash < 0)
                continue;

            string file = line[(slash + 1)..];

            foreach (string watched in watched_libraries)
            {
                if (file.StartsWith(watched, StringComparison.OrdinalIgnoreCase))
                    found.Add(file);
            }
        }

        return found.ToArray();
    }

    public static bool IsSupported => File.Exists("/proc/self/maps");
}
