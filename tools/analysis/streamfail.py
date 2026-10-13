"""Why does a run break? Three candidate mechanisms, measured against each other.

A run that fails late is usually described as the player "degrading" deep into it, which is
what `patternpredict.py --gradient` measures: misses concentrate at high run position and the
gradient survives removing map level, rhythm, run length and miss clumping. That result says
*where* a run breaks and nothing about *why*, and at least three mechanisms predict the same
table while implying completely different advice.

**Rate.** The tapping hand runs at a slightly wrong tempo. Phase error then accumulates
linearly and crosses the hit window at a position inversely proportional to the error, so a
1% error on 1/4 at 180bpm walks ~0.8 ms per object and reaches a 40 ms edge around position
50; a 4% error gets there by position 12. This mechanism is a property of the *first* notes
of the run, not the last: the slope is already there before anything is missed. It also
survives every control in `--gradient`, because it happens on every map, in every rhythm,
inside long runs, and resyncs in a few taps rather than cascading.

**Along-track cursor error.** The aiming hand travels the stream path at the wrong speed --
ahead of the notes or behind them -- so the cursor is past or short of each circle along the
direction of travel. Distinct from rate: the tap can be perfectly timed while the cursor is
in the wrong place, and vice versa.

**Cross-track cursor error.** The aiming hand leaves the path sideways. This is what the
existing stream work calls a "stray" -- an in-rhythm tap with nothing under the cursor -- and
it is the aim hand rather than the tapping hand.

The first is read off hit error, the other two off the cursor, and they are separable because
aim error is a *vector* once the path direction is known. For object n in a run, the local
direction is from the previous target to this one; the cursor's offset from the target
projects onto that direction as along-track (positive is ahead of the note, the hand running
early in space) and perpendicular to it as cross-track. Radii come from the row itself:
`aimErrorRadii` is the offset's length over the radius, so the radius is recoverable without
knowing CS.

**The design that makes this a test rather than a description.** Take runs long enough to
break late, require them to be *clean through position 9*, and split on whether they break at
position 10 or later. Both groups then had an identical opportunity and differ only in
outcome, and every quantity is measured over positions 0-9 -- before any miss. A mechanism
that is already visible there is a cause; one that only appears after is a consequence.

Hit error is taken only from presses plausibly belonging to their own object (|hit error|
within 0.4 of the gap to the previous object). A shifted stream reports a real press from a
neighbour, displaced by about a whole tap interval, which would swamp a sub-millisecond slope.

**Measured 2026-10-12** on the collected corpus, 5,436 qualifying runs. Magnitudes, ratio of
breaking to holding, and Welch's t:

    snap 0.25: 1,083 runs hold, 604 break at position 10+
      metric              holds   breaks   ratio      t
      rate ms/object      2.074    2.620    1.26    5.3
      rate %              2.278    2.890    1.27    5.5
      hit error ms        9.498   10.262    1.08    1.9
      along radii         0.173    0.187    1.08    2.0
      along slope         0.061    0.054    0.89   -2.8
      cross radii         0.104    0.114    1.10    2.4
      cross slope         0.044    0.036    0.82   -4.7

    snap 0.5: 2,988 runs hold, 761 break at position 10+
      metric              holds   breaks   ratio      t
      rate ms/object      2.751    2.799    1.02    0.4
      rate %              1.277    1.470    1.15    3.7
      hit error ms       11.133   11.513    1.03    0.9
      along radii         0.117    0.130    1.11    3.3
      along slope         0.049    0.050    1.02    0.5
      cross radii         0.086    0.092    1.07    1.9
      cross slope         0.040    0.038    0.94   -1.5

Three readings.

* **A stream breaks on tempo; a chain of jumps breaks on where the cursor is.** At 1/4 the
  tempo error is the one term that stands out -- 27% higher in runs that go on to break,
  t=5.5, against t=2.0 and t=2.4 for the two cursor terms. At 1/2 it reverses: absolute
  tempo error does not separate the groups at all (ratio 1.02, t=0.4) and along-track cursor
  error is the strongest term (t=3.3). So the same gradient in the miss table has different
  causes in different rhythms, and a finding that says "you fail late in runs" is pointing at
  two different problems depending on the snap.

* **All three mechanisms are present in both, and the differences are of degree.** Every
  ratio is between 1.02 and 1.27. Nothing here says a run breaks for one reason; it says
  which reason moves first. That matches the obvious intuition and is worth stating plainly,
  because a ratio of 1.27 with t=5.5 is a reliable difference and a small one.

* **The 1/2 tempo result depends on how tempo is scaled, which is a caution not a finding.**
  Per object the error does not separate (t=0.4); as a percentage of the interval it does
  (t=3.7). The two disagree only if the effect sits in the faster jump runs, since dividing
  by a shorter interval is what distinguishes the measures. Worth splitting by BPM before
  any weight is put on it.

Caveats that bound all of the above. The groups are runs pooled over 4,978 players, so this
is a population statement and not one about any player -- the same split within one player's
runs is a different and better-controlled question. The comparison is across runs rather than
within a player, so a player who taps off-tempo and also fails more would produce this table
without tempo causing any individual failure. And only circles are visible, so a miss on a
slider head inside positions 0-9 does not disqualify a run from the "clean" group.

    PYTHONPATH=tools python3 tools/analysis/streamfail.py --cache build/shapes-collected.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.patternpredict import (CORPUS, EXCLUDE, GEOMETRY, MISSED, _num,  # noqa: E402
                                     read_rhythm, read_shapes)

BEFORE = 10        # positions 0..9: measured, and required to be clean in both groups
MIN_SIZE = 14      # a run has to be long enough for "breaks at 10 or later" to be possible
MIN_POINTS = 6     # own-tap presses needed inside positions 0..9 to fit a slope
MIN_EARLY = 6      # measurable circles needed inside positions 0..9
OWN_TAP = 0.4      # |hit error| within this fraction of the gap is the object's own press


def slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of y on x. Units: ms per object when y is hit error."""
    n = len(points)
    if n < 2:
        return None
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    var = sum((x - mx) ** 2 for x, _ in points)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / var


def compare(a: list[float], b: list[float]) -> tuple:
    """Magnitudes of two groups, their ratio, and Welch's t on the difference.

    Magnitudes, because the question is how far off the quantity is, not which way: a run
    tapped progressively early and one tapped progressively late are both off tempo. Welch
    rather than Student because the groups differ in size and in spread.
    """
    a, b = [abs(v) for v in a], [abs(v) for v in b]
    if len(a) < 2 or len(b) < 2:
        return 0.0, 0.0, 0.0, 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / (len(a) - 1)
    vb = sum((v - mb) ** 2 for v in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return ma, mb, (mb / ma if ma else 0.0), ((mb - ma) / se if se else 0.0)


def runs_of(rows: list[dict]) -> list[list[dict]]:
    """Split one replay's objects into runs, on the run's own id.

    Not on consecutive run indices. These rows drop slider heads and unjudged objects, so a
    run containing either has gaps in its index and would come apart into fragments -- which
    it did, silently, discarding 95% of the long runs before this was caught.
    """
    out: list[list[dict]] = []
    current: list[dict] = []
    previous = None
    for row in rows:
        if previous is None or row["runid"] != previous:
            if current:
                out.append(current)
            current = []
        current.append(row)
        previous = row["runid"]
    if current:
        out.append(current)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", help="the map shapes patternpredict.py writes")
    parser.add_argument("--snaps", nargs="*", default=["0.25", "0.5"])
    args = parser.parse_args()

    if args.cache and os.path.exists(args.cache):
        shapes = json.load(open(args.cache))
    else:
        rhythm, by_replay = read_rhythm()
        shapes = read_shapes(rhythm, by_replay)
        if args.cache:
            json.dump(shapes, open(args.cache, "w"))
    index = {md5: {row["t"]: row for row in rows} for md5, rows in shapes.items()}

    corpus = {os.path.basename(r["Path"]): r["BeatmapMd5"]
              for r in json.load(open(CORPUS)) if not (EXCLUDE & set(r.get("Mods") or ()))}

    # snap -> outcome -> metric -> list of per-run values
    acc: dict = collections.defaultdict(lambda: collections.defaultdict(
        lambda: collections.defaultdict(list)))
    counted = 0

    def flush(rows: list[dict]):
        nonlocal counted
        rows.sort(key=lambda r: r["t"])
        for run in runs_of(rows):
            # The map's run length, not the number of rows surviving. A run of twenty with
            # slider heads in it yields fewer circles than that, and testing the row count
            # threw away most of the long runs.
            if (run[0]["size"] or 0) < MIN_SIZE:
                continue
            snap = run[0]["snap"]
            if snap not in args.snaps or any(r["snap"] != snap for r in run):
                continue
            early = [r for r in run if r["run"] < BEFORE]
            late = [r for r in run if r["run"] >= BEFORE]
            # Both groups must have survived the measured stretch, so the only difference
            # between them is what happened after it. Only circles are visible here, so a
            # miss on a slider head inside positions 0-9 is not seen.
            if len(early) < MIN_EARLY or any(r["miss"] for r in early) or not late:
                continue
            outcome = "breaks at 10+" if any(r["miss"] for r in late) else "holds"

            taps = [(float(r["run"]), r["error"]) for r in early if r["own"]]
            if len(taps) < MIN_POINTS:
                continue
            rate = slope(taps)
            if rate is None:
                continue
            counted += 1
            here = acc[snap][outcome]
            here["rate ms/object"].append(rate)
            here["hit error ms"].append(sum(y for _, y in taps) / len(taps))
            gap = [r["gap"] for r in early if r["gap"]]
            if gap:
                # The same slope as a tempo error: ms of phase per object over the interval.
                here["rate %"].append(100.0 * rate / (sum(gap) / len(gap)))
            for name, key in (("along radii", "along"), ("cross radii", "cross")):
                vals = [r[key] for r in early if r[key] is not None]
                if vals:
                    here[name].append(sum(vals) / len(vals))
                    here[name.replace("radii", "slope")].append(
                        slope([(float(r["run"]), r[key]) for r in early
                               if r[key] is not None]) or 0.0)

    held: list[dict] = []
    current_replay = None
    with open(GEOMETRY) as f:
        for r in csv.DictReader(f):
            if r["replay"] != current_replay:
                if held:
                    flush(held)
                held, current_replay = [], r["replay"]
            if r["kind"] != "circle" or r["exitSlackRadii"] or r["result"] == "Unjudged":
                continue
            md5 = corpus.get(r["replay"])
            if md5 is None:
                continue
            shape = index.get(md5, {}).get(round(float(r["startTime"]), 3))
            if shape is None or shape["run"] is None or not shape["snap"]:
                continue
            if (shape["size"] or 0) < MIN_SIZE:
                continue

            error, gap = _num(r["hitError"]), _num(r["deltaTime"])
            aim = _num(r["aimErrorRadii"])
            own = (error is not None and gap not in (None, 0)
                   and abs(error) <= OWN_TAP * gap)

            # The cursor's offset from the target, split along and across the path it is
            # travelling. The radius comes from the row: aimErrorRadii is the offset's
            # length over the radius, so the two together give the scale.
            along = cross = None
            cx, cy = _num(r["cursorX"]), _num(r["cursorY"])
            tx, ty = _num(r["targetX"]), _num(r["targetY"])
            if None not in (cx, cy, tx, ty) and aim and aim > 0:
                offset = (cx - tx, cy - ty)
                length = math.hypot(*offset)
                radius = length / aim if length else None
                previous = held[-1] if held and held[-1]["run"] == shape["run"] - 1 else None
                if radius and previous is not None:
                    dx, dy = tx - previous["tx"], ty - previous["ty"]
                    travel = math.hypot(dx, dy)
                    if travel > 0:
                        ux, uy = dx / travel, dy / travel
                        along = (offset[0] * ux + offset[1] * uy) / radius
                        cross = (offset[0] * -uy + offset[1] * ux) / radius

            held.append({"t": shape["t"], "run": shape["run"], "runid": shape["runid"],
                         "snap": shape["snap"],
                         "size": shape["size"], "error": error, "gap": gap, "own": own,
                         "miss": r["result"] in MISSED, "along": along, "cross": cross,
                         "tx": tx, "ty": ty})
    if held:
        flush(held)

    print(f"{counted:,} runs of {MIN_SIZE}+ objects, clean through position {BEFORE - 1}, "
          f"every quantity measured\nover positions 0-{BEFORE - 1} only -- before any miss, "
          f"so what shows up here is a cause and not\na consequence. Magnitudes, since a run "
          f"tapped progressively early and one tapped\nprogressively late are equally off "
          f"tempo. t is Welch's on the difference of the two.\n")

    metrics = [("rate ms/object", "tapping tempo error, ms of phase per object"),
               ("rate %", "the same as a percentage of the interval"),
               ("hit error ms", "mean hit error over the stretch, for reference"),
               ("along radii", "cursor ahead of or behind the note along its path"),
               ("along slope", "and whether that is growing, radii per object"),
               ("cross radii", "cursor off the path sideways"),
               ("cross slope", "and whether that is growing, radii per object")]
    for snap in args.snaps:
        if snap not in acc:
            continue
        holds = len(acc[snap]["holds"].get("rate ms/object", []))
        breaks = len(acc[snap]["breaks at 10+"].get("rate ms/object", []))
        print(f"snap {snap}: {holds:,} runs hold, {breaks:,} break at position "
              f"{BEFORE}+\n")
        print(f"  {'metric':<16}{'holds':>9}{'breaks':>9}{'ratio':>8}{'t':>7}   what it is")
        for metric, description in metrics:
            a = acc[snap]["holds"].get(metric, [])
            b = acc[snap]["breaks at 10+"].get(metric, [])
            ma, mb, ratio, t = compare(a, b)
            print(f"  {metric:<16}{ma:>9.3f}{mb:>9.3f}{ratio:>8.2f}{t:>7.1f}   {description}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
