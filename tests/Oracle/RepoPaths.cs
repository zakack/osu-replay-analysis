namespace Oracle;

/// <summary>
/// The test host runs from its own output directory, so anything under build/ has to be
/// found by walking up to the repository root rather than by a relative path.
/// </summary>
public static class RepoPaths
{
    public static string Root { get; } = findRoot();

    public static string Build(params string[] parts) => Path.Combine([Root, "build", .. parts]);

    private static string findRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);

        while (directory != null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "osu-replay-analysis.slnx")))
                return directory.FullName;

            directory = directory.Parent;
        }

        throw new DirectoryNotFoundException("Could not locate the repository root from " + AppContext.BaseDirectory);
    }
}
