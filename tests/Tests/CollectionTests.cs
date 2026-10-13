using System.Reflection;
using Extract;
using NUnit.Framework;
using osu.Framework.Platform;
using osu.Game.Collections;
using osu.Game.Database;

namespace Tests;

/// <summary>
/// The written file is read back through lazer's own <c>LegacyCollectionImporter</c>, not a
/// reader of ours, so a format mistake fails here rather than as an import that silently
/// finds nothing. The importer never touches realm on the read path.
/// </summary>
[TestFixture]
public class CollectionTests
{
    private string root = null!;

    [SetUp]
    public void SetUp() => root = Path.Combine(Path.GetTempPath(), "ora-collection-" + Guid.NewGuid().ToString("N"));

    [TearDown]
    public void TearDown()
    {
        if (Directory.Exists(root))
            Directory.Delete(root, true);
    }

    [Test]
    public void LazerReadsWhatIsWritten()
    {
        string[] md5s = ["27d9765612170a9517f0a5e8b4613f06", "0123456789abcdef0123456789abcdef"];
        Collection.Write(root, "spaced circles ✓", md5s);

        var importer = new LegacyCollectionImporter(null!);
        var storage = new NativeStorage(root);
        Assert.That(importer.GetAvailableCount(storage).Result, Is.EqualTo(1));

        var read = typeof(LegacyCollectionImporter).GetMethod("readCollections", BindingFlags.Instance | BindingFlags.NonPublic)
                   ?? throw new MissingMethodException("LegacyCollectionImporter.readCollections moved; find lazer's reader again");

        using var stream = storage.GetStream("collection.db");
        var collections = (List<BeatmapCollection>)read.Invoke(importer, [stream, null])!;

        Assert.That(collections, Has.Count.EqualTo(1));
        Assert.That(collections[0].Name, Is.EqualTo("spaced circles ✓"));
        Assert.That(collections[0].BeatmapMD5Hashes, Is.EqualTo(md5s));
    }

    [Test]
    public void FolderQualifiesAsAStableInstall()
    {
        // LegacyImportManager.IsUsableForStableImport accepts a Songs or Skins folder.
        Collection.Write(root, "x", ["27d9765612170a9517f0a5e8b4613f06"]);
        Assert.That(Directory.Exists(Path.Combine(root, "Songs")));
    }

    [Test]
    public void Md5sAreTakenFromAnyText()
    {
        const string text = "27D9765612170A9517F0A5E8B4613F06  b/123  Artist - Title [Hard]\n"
                            + "sha a1556d0801b3a6b175dda32ef546f0ec812b400499f575c44fccbe9c67f9b1e5\n"
                            + "again: 27d9765612170a9517f0a5e8b4613f06,0123456789abcdef0123456789abcdef";

        Assert.That(Collection.Md5sIn(text), Is.EqualTo(new[] { "27d9765612170a9517f0a5e8b4613f06", "0123456789abcdef0123456789abcdef" }));
    }
}
