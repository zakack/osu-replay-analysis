"""Does the pattern card draw the turn its header names?

The viewer is the one place in the project that does geometry outside the shim: it takes a
row's cell key, finds the objects it belongs to, and rotates them into the pattern's own
frame. CLAUDE.md's rule for reimplemented geometry is that it needs an oracle, and here the
oracle is the shim itself — `build/geometry.csv`, joined to a cached `build/scene/*.json` on
object start time. Both sides describe the same three objects, so the frame the viewer builds
must reproduce the row the analysis binned.

The check that matters is which object is the apex. `Geometry.cs` keys a row at object n and
apexes its angle at n-1, from (n-2, n-1, n); the viewer drew (n-1, n, n+1) for a while, which
is the *next* turn. Nothing crashed and nothing looked wrong — the leg it drew as the approach
was a real spacing leg, just the wrong one. So this test is written as a discriminator rather
than as an assertion: it demands that the right assignment agrees with the shim and that the
wrong one does not. A test that only accepted the right answer would have passed throughout
the bug's life.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from viewer import pattern  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCENES = os.path.join(ROOT, "build", "scene")
GEOMETRY = os.path.join(ROOT, "build", "geometry.csv")

# Enough documents to cover several beatmaps and both eras of client, few enough that the
# whole run stays under a second. Twelve gave ~4800 eligible chains.
DOCUMENTS = 12

ANGLE_TOL = 1e-4     # radians
SPACING_TOL = 1e-3   # circle radii, before the scale term below

# A scene document rounds `beatmap.radius` to two decimals, so a length expressed in radii
# carries a relative error as well as an absolute one, and on a ten-radii jump the relative
# term is the larger of the two. Budget for both rather than loosening the flat tolerance
# until the long jumps fit: measured against the corpus the worst chain uses 61% of this.
RADIUS_ROUNDING = 0.005

# Scene positions are rounded, so a leg of nearly zero length has an angle that is noise.
# Two objects at the same position do occur — a repeated placement outside stack leniency —
# and there the turn is undefined rather than wrong.
MIN_LEG = 0.25       # circle radii

# Below a 30-unit radius lazer multiplies its normalised distance by a small-circle bonus,
# and `spacingRadii` stops being distance over radius. No document in the local corpus trips
# this; the guard is here so a future one is excluded loudly instead of failing oddly.
MIN_RADIUS = 30.0


class Skipped(Exception):
    """No fixture. Loud, because a vacuous pass here is worse than no test."""


def _shim_chain(index: int) -> tuple[int, int, int]:
    """The three objects the shim's row at `index` is built from.

    Read straight off `src/Sim/Geometry.cs:83-94`, which takes its apex from `Previous(0)`
    and its incoming leg from `Previous(1)`. Written out here rather than imported from
    `pattern` so the fixture is not selected by the code the fixture is judging.
    """
    return index - 2, index - 1, index


def _rows(replays: set[str]) -> dict[str, dict[float, dict]]:
    """The shim's rows for these replays, keyed by replay and start time.

    Streamed and prefiltered on the leading quoted replay name: the file is 576k rows and
    over 100MB, and parsing all of it to keep 0.5% of it costs more than the rest of the
    test put together.
    """
    wanted = {r + ".osr" for r in replays}
    kept: list[str] = []

    with open(GEOMETRY) as handle:
        header = next(handle)
        for line in handle:
            end = line.find('"', 1)
            if line[1:end] in wanted:
                kept.append(line)

    out: dict[str, dict[float, dict]] = {}
    for row in csv.DictReader([header] + kept):
        replay = row["replay"][:-len(".osr")]
        out.setdefault(replay, {})[round(float(row["startTime"]), 3)] = row
    return out


def _canonical(objects: list[dict], indices: tuple[int, int, int],
               radius: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The turn in the pattern's own frame: apex at the origin, incoming leg on +x.

    Written out rather than imported so that the thing under test and the thing testing it
    are not the same six lines.
    """
    before, apex, after = (objects[i] for i in indices)
    bearing = math.atan2(before["y"] - apex["y"], before["x"] - apex["x"])
    cos, sin = math.cos(-bearing), math.sin(-bearing)

    def canon(o):
        dx, dy = (o["x"] - apex["x"]) / radius, (o["y"] - apex["y"]) / radius
        return dx * cos - dy * sin, dx * sin + dy * cos

    return canon(before), canon(after)


def _spacing_tol(expected: float, radius: float) -> float:
    return SPACING_TOL + expected * (RADIUS_ROUNDING / radius)


def _wrap(a: float, b: float) -> float:
    """Angular distance. The corpus contains exact reversals, where +pi and -pi are one turn."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def _agrees(frame, row, previous, radius) -> bool:
    """Does this frame reproduce the shim's row?

    Three claims, not one: the angle, the leg the row's own spacing measures, and the leg the
    previous row measures. An apex one object out moves all three, but the angle is the one
    that moves unmistakably.
    """
    incoming, outgoing = frame

    if _wrap(math.atan2(outgoing[1], outgoing[0]), float(row["signedAngle"])) > ANGLE_TOL:
        return False

    for leg, expected in ((outgoing, float(row["spacingRadii"])),
                          (incoming, float(previous["spacingRadii"]))):
        if abs(math.hypot(*leg) - expected) > _spacing_tol(expected, radius):
            return False
    return True


_cache: list | None = None


def _chains() -> list[dict]:
    """Every chain in the fixture that the shim and the scene can both describe."""
    global _cache
    if _cache is not None:
        return _cache

    if not os.path.isdir(SCENES) or not glob.glob(os.path.join(SCENES, "*.json")):
        raise Skipped(f"no scene documents in {SCENES}")
    if not os.path.exists(GEOMETRY):
        raise Skipped(f"no {GEOMETRY}")

    documents = {}
    for path in sorted(glob.glob(os.path.join(SCENES, "*.json")))[:DOCUMENTS]:
        with open(path) as handle:
            document = json.load(handle)
        documents[document["replay"]] = document

    rows = _rows(set(documents))
    chains: list[dict] = []
    dropped = {"small circles": 0, "not all circles": 0, "degenerate leg": 0, "no row": 0}

    for replay, document in documents.items():
        objects = document["objects"]
        radius = document["beatmap"]["radius"]
        table = rows.get(replay, {})

        if radius < MIN_RADIUS:
            dropped["small circles"] += max(0, len(objects) - 2)
            continue

        for k in range(2, len(objects)):
            indices = _shim_chain(k)

            if any(objects[i]["type"] != "circle" for i in indices):
                dropped["not all circles"] += 1
                continue

            row = table.get(round(objects[k]["t"], 3))
            previous = table.get(round(objects[k - 1]["t"], 3))
            if row is None or previous is None or not row["signedAngle"]:
                dropped["no row"] += 1
                continue

            legs = [math.dist((objects[a]["x"], objects[a]["y"]),
                              (objects[b]["x"], objects[b]["y"])) / radius
                    for a, b in ((indices[0], indices[1]), (indices[1], indices[2]))]
            if min(legs) < MIN_LEG:
                dropped["degenerate leg"] += 1
                continue

            chains.append({"document": document, "k": k, "radius": radius,
                           "row": row, "previous": previous})

    print(f"      fixture: {len(chains)} chains over {len(documents)} scenes "
          f"({', '.join(f'{v} {k}' for k, v in dropped.items() if v)} excluded)")

    if not chains:
        raise AssertionError("the fixture loaded but produced no eligible chains")

    _cache = chains
    return _cache


def test_the_shim_frame_reproduces_the_row():
    """The turn apexed at n-1 is the turn the row at n describes."""
    chains = _chains()
    bad = []

    for c in chains:
        frame = _canonical(c["document"]["objects"], _shim_chain(c["k"]), c["radius"])
        if not _agrees(frame, c["row"], c["previous"], c["radius"]):
            bad.append((c["document"]["replay"], c["k"],
                        math.atan2(frame[1][1], frame[1][0]), float(c["row"]["signedAngle"])))

    assert not bad, (f"{len(bad)} of {len(chains)} chains disagree with the shim, "
                     f"first: {bad[0]}")


def test_the_next_turn_is_rejected():
    """The frame the viewer used to draw must fail the same check.

    This is the half of the test that has teeth. The old assignment produced a picture that
    looked like a pattern card and was a different pattern; only a test that refuses it
    distinguishes the two.
    """
    chains = _chains()
    agreeing = 0

    for c in chains:
        objects = c["document"]["objects"]
        k = c["k"]
        if k + 1 >= len(objects):
            continue
        frame = _canonical(objects, (k - 1, k, k + 1), c["radius"])
        if _agrees(frame, c["row"], c["previous"], c["radius"]):
            agreeing += 1

    share = agreeing / len(chains)
    assert share < 0.05, (f"the off-by-one frame agrees with the shim on {share:.1%} of "
                          f"chains; this check cannot tell the two apart")


def test_traversal_builds_the_shim_frame():
    """`pattern.traversal` — the shipping code — returns that frame and not another."""
    chains = _chains()
    checked, bad = 0, []

    for c in chains:
        document = c["document"]
        row = pattern.traversal(document, c["k"], document["frames"])
        if row is None:
            continue

        incoming, outgoing = _canonical(document["objects"], _shim_chain(c["k"]), c["radius"])
        checked += 1

        # The frame's own definition: the approach lies on +x, so every instance is
        # superimposed on the same bearing.
        if abs(row["in"][1]) > SPACING_TOL or row["in"][0] <= 0:
            bad.append((document["replay"], c["k"], "approach is off the +x axis", row["in"]))
            continue

        if (abs(row["in"][0] - incoming[0]) > SPACING_TOL
                or abs(row["in"][1] - incoming[1]) > SPACING_TOL
                or abs(row["out"][0] - outgoing[0]) > SPACING_TOL
                or abs(row["out"][1] - outgoing[1]) > SPACING_TOL):
            bad.append((document["replay"], c["k"], "frame differs", row["in"], row["out"],
                        incoming, outgoing))

    assert checked > 100, f"only {checked} traversals had frames to draw"
    assert not bad, f"{len(bad)} of {checked} traversals are in the wrong frame, first: {bad[0]}"


def test_traversal_measures_the_row_object():
    """Error and result stay on the object the row is keyed at, not on the apex.

    The apex moved when the frame was fixed; the click did not. Every number the card prints
    is that click's, and rehoming it onto the apex would make the card disagree with
    findings.csv while still looking right.
    """
    chains = _chains()
    checked = 0

    for c in chains:
        document = c["document"]
        row = pattern.traversal(document, c["k"], document["frames"])
        if row is None:
            continue

        target = document["objects"][c["k"]]
        assert row["result"] == target["result"], (
            f"{document['replay']} object {c['k']}: result taken from the wrong object")
        assert row["err"] == target.get("error"), (
            f"{document['replay']} object {c['k']}: error taken from the wrong object")
        checked += 1

    assert checked > 100, f"only {checked} traversals to check"


def test_chain_is_the_turn_the_row_describes():
    """Both the overlay and the contact sheet read this, so it is pinned here."""
    assert pattern.chain(7) == (5, 6, 7), (
        f"the row at object 7 is the turn (5, 6, 7), not {pattern.chain(7)}")
    assert pattern.traversal({"objects": [], "beatmap": {"radius": 40}}, 1, []) is None, (
        "a chain running off the start of the map should be dropped, not drawn")


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0

    for test in tests:
        try:
            test()
        except Skipped as e:
            print(f"\nSKIP  no fixture: {e}")
            print("      this test is differential against the shim and has nothing to")
            print("      compare against. Fill build/scene with, for each replay:")
            print("      dotnet src/Cli/bin/Release/net10.0/ora.dll scene <replay.osr> "
                  "build/scene/<name>.json")
            return 2
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
        else:
            print(f"ok    {test.__name__}")

    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
