using NUnit.Framework;
using Sim;

namespace Tests;

/// <summary>
/// Transcribed from ppy's own <c>SpinnerSpinHistoryTest</c>, minus its rewind cases, which
/// only arise when a player scrubs a replay backwards and cannot occur in forward-only
/// resimulation.
///
/// The behaviour worth pinning is that reversing direction does not subtract from the score.
/// A spin keeps the furthest point it reached, so shaking the cursor back and forth earns
/// nothing — which is not obvious from the accumulator and is easy to get subtly wrong.
/// </summary>
[TestFixture]
public class SpinHistoryTests
{
    private SpinHistory history = null!;

    [SetUp]
    public void SetUp() => history = new SpinHistory();

    [TestCase(0, 0)]
    [TestCase(10, 10)]
    [TestCase(180, 180)]
    [TestCase(350, 350)]
    [TestCase(360, 360)]
    [TestCase(370, 370)]
    [TestCase(540, 540)]
    [TestCase(720, 720)]
    [TestCase(-10, 10)]
    [TestCase(-180, 180)]
    [TestCase(-350, 350)]
    [TestCase(-360, 360)]
    [TestCase(-370, 370)]
    [TestCase(-540, 540)]
    [TestCase(-720, 720)]
    public void SpinsInOneDirection(float spin, float expected)
    {
        history.ReportDelta(spin);

        Assert.That(history.TotalRotation, Is.EqualTo(expected));
    }

    [TestCase(0, 0, 0, 0)]
    [TestCase(10, -10, 0, 10)]
    [TestCase(-10, 10, 0, 10)]
    [TestCase(10, -20, 0, 10)]
    [TestCase(-10, 20, 0, 10)]
    [TestCase(20, -10, 0, 20)]
    [TestCase(-20, 10, 0, 20)]
    [TestCase(10, -360, 0, 350)]
    [TestCase(-10, 360, 0, 350)]
    [TestCase(360, -10, 0, 370)]
    [TestCase(360, 10, 0, 370)]
    [TestCase(-360, 10, 0, 370)]
    [TestCase(-360, -10, 0, 370)]
    [TestCase(10, 10, 10, 30)]
    [TestCase(10, 10, -10, 20)]
    [TestCase(10, -10, 10, 10)]
    [TestCase(-10, -10, -10, 30)]
    [TestCase(-10, -10, 10, 20)]
    [TestCase(-10, 10, 10, 10)]
    [TestCase(10, -20, -350, 360)]
    [TestCase(10, -20, 350, 340)]
    [TestCase(-10, 20, 350, 360)]
    [TestCase(-10, 20, -350, 340)]
    public void SpinsInMultipleDirections(float first, float second, float third, float expected)
    {
        history.ReportDelta(first);
        history.ReportDelta(second);
        history.ReportDelta(third);

        Assert.That(history.TotalRotation, Is.EqualTo(expected));
    }
}
