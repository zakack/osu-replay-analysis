using NUnit.Framework;
using osuTK;
using Sim;

namespace Tests;

/// <summary>
/// Which way a signed turn goes on screen. The number has been right all along and the word
/// for it has been wrong twice, because reading the formula invites the mistake: the first
/// vector is (from − apex), the incoming leg reversed. Turns are labelled for players and the
/// HR / Mirror question is about exactly this sign, so it is pinned here on turns whose
/// direction anyone can check by drawing them on a screen where y grows downward.
/// </summary>
[TestFixture]
public class GeometryTests
{
    private static double degrees(double radians) => radians * 180 / Math.PI;

    [Test]
    public void RightThenDownIsClockwiseAndNegative()
    {
        // Heading right, then down the screen: a right-hand turn, clockwise to the eye.
        double angle = Geometry.SignedAngle(new Vector2(0, 0), new Vector2(100, 0), new Vector2(100, 100));
        Assert.That(degrees(angle), Is.EqualTo(-90).Within(1e-9));
    }

    [Test]
    public void RightThenUpIsCounterClockwiseAndPositive()
    {
        double angle = Geometry.SignedAngle(new Vector2(0, 0), new Vector2(100, 0), new Vector2(100, -100));
        Assert.That(degrees(angle), Is.EqualTo(90).Within(1e-9));
    }

    [Test]
    public void StraightThroughIsPiAndAReversalIsZero()
    {
        // Lazer's magnitude convention, which the cross-check against its Angle relies on.
        // Straight through has no direction, and the sign it gets is a signed zero's (-pi
        // here), so only its magnitude means anything: analyses must drop near-straight turns
        // before reading the sign, not trust it.
        Assert.Multiple(() =>
        {
            Assert.That(Math.Abs(Geometry.SignedAngle(new Vector2(0, 0), new Vector2(100, 0), new Vector2(200, 0))),
                Is.EqualTo(Math.PI).Within(1e-9));
            Assert.That(Geometry.SignedAngle(new Vector2(0, 0), new Vector2(100, 0), new Vector2(0, 0)),
                Is.EqualTo(0).Within(1e-9));
        });
    }

    [TestCase(512f, 0f, TestName = "MirroredLeftRight")]
    [TestCase(0f, 384f, TestName = "MirroredTopBottom")]
    public void EitherSingleAxisReflectionReversesTheTurn(float width, float height)
    {
        // Hard Rock reflects top to bottom and Mirror defaults to left to right. Either one
        // must swap the sign and keep the magnitude, or a flipped play is read as unflipped.
        Vector2 reflect(Vector2 p) => new Vector2(width > 0 ? width - p.X : p.X, height > 0 ? height - p.Y : p.Y);

        var (from, apex, to) = (new Vector2(100, 100), new Vector2(250, 120), new Vector2(300, 260));
        double original = Geometry.SignedAngle(from, apex, to);
        double reflected = Geometry.SignedAngle(reflect(from), reflect(apex), reflect(to));

        Assert.That(reflected, Is.EqualTo(-original).Within(1e-6));
    }
}
