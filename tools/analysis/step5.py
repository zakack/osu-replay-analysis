"""Step 5: where error concentrates.

Joins build/geometry.csv to build/rhythm.csv and asks three questions:

  1. Through a run, does timing spread grow because the hand is losing the target,
     or because the clock is drifting? Aim error in radii answers it directly.
  2. Does turn angle cost timing consistency once rhythm density is held constant?
  3. Is there a handedness asymmetry, reported as paired deltas at matched geometry?

Every cell below MIN_N is dropped rather than footnoted. A spread estimate on ninety
samples is not a number.

The corpus is one player. Nothing here generalises past them until step 6.
"""
import csv, collections, statistics, math, sys

MIN_N = 200
DEG = 180 / math.pi
PLAYER = "zaksynack playing"


def positions():
    """(replay, time) -> (index, length) for clicks inside all-circle sixteenth runs."""
    out = {}
    with open("build/rhythm.csv") as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["snap"] != "0.25" or r["interiorCircles"] != "1":
                continue
            if r["clickIndex"] == "-1":
                continue
            out[(r["replay"], round(float(r["time"]), 3))] = (int(r["runIndex"]), int(r["runSize"]))
    return out


def geometry():
    with open("build/geometry.csv") as f:
        for r in csv.DictReader(f):
            if r["replay"].startswith(PLAYER):
                yield r


def band(size):
    return "5-8" if 5 <= size <= 8 else "9-12" if 9 <= size <= 12 else "13+" if size >= 13 else None


def run_position(pos):
    """Question 1. Length is banded, because position 16 only exists inside long
    streams and those sit on harder maps; without banding the trend is confounded."""
    cells = collections.defaultdict(lambda: ([], []))

    for r in geometry():
        key = (r["replay"], round(float(r["startTime"]), 3))
        if key not in pos or not r["hitError"] or not r["aimErrorRadii"]:
            continue
        index, size = pos[key]
        if (b := band(size)) is not None:
            cells[(b, index)][0].append(float(r["hitError"]))
            cells[(b, index)][1].append(float(r["aimErrorRadii"]))

    for b, limit in (("5-8", 8), ("9-12", 12), ("13+", 17)):
        print(f"\n  sixteenth runs of {b} notes")
        print(f"  {'position':<9}{'n':>8}{'hit sd (ms)':>13}{'aim mean (r)':>14}")
        for i in range(limit):
            hit, aim = cells[(b, i)]
            if len(hit) < MIN_N:
                continue
            print(f"  {i:<9}{len(hit):>8}{statistics.pstdev(hit):>13.2f}{statistics.mean(aim):>14.3f}")


def angle_by_density():
    """Question 2. Spacing and rhythm density are entangled — wide jumps are slow and
    streams are tight — so angle is only interpretable inside a fixed gap band."""
    gaps = [(80, "<80ms"), (120, "80-120"), (200, "120-200"), (float("inf"), "200+")]
    angles = [(60, "0-60"), (120, "60-120"), (181, "120-180")]
    cells = collections.defaultdict(list)

    for r in geometry():
        if r["kind"] != "circle" or not r["signedAngle"] or not r["hitError"]:
            continue
        gap = next(hi for hi, _ in gaps if float(r["deltaTime"]) < hi)
        turn = next(hi for hi, _ in angles if abs(float(r["signedAngle"])) * DEG < hi)
        cells[(gap, turn)].append(float(r["hitError"]))

    print("\n  timing spread in ms, by turn angle within a fixed gap band")
    print("  " + "gap".ljust(10) + "".join(f"{label:>12}" for _, label in angles))
    for hi, label in gaps:
        row = "".join(
            f"{statistics.pstdev(cells[(hi, a)]):>12.2f}" if len(cells[(hi, a)]) >= MIN_N else f"{'--':>12}"
            for a, _ in angles)
        print(f"  {label:<10}{row}")


def chirality():
    """Question 3. Reported as a paired delta at matched angle and spacing, because a
    real asymmetry shows as a consistent sign across pairs and two separate tables
    would let noise look like a pattern."""
    cells = collections.defaultdict(lambda: ([], []))

    for r in geometry():
        if r["kind"] != "circle" or not r["signedAngle"] or not r["hitError"] or not r["aimErrorRadii"]:
            continue
        a = float(r["signedAngle"])
        turn = next(hi for hi in (30, 60, 90, 120, 150, 181) if abs(a) * DEG < hi)
        space = next(hi for hi in (1, 2, 3, 4, 99) if float(r["spacingRadii"]) < hi)
        cell = cells[(turn, space, a >= 0)]
        cell[0].append(float(r["hitError"]))
        cell[1].append(float(r["aimErrorRadii"]))

    print("\n  counter-clockwise minus clockwise, matched angle and spacing")
    print(f"  {'angle':<10}{'spacing':<9}{'d aim (r)':>11}{'d spread (ms)':>15}")
    deltas = []
    for turn in (30, 60, 90, 120, 150, 181):
        for space in (1, 2, 3, 4, 99):
            ccw, cw = cells[(turn, space, True)], cells[(turn, space, False)]
            if len(ccw[0]) < MIN_N or len(cw[0]) < MIN_N:
                continue
            da = statistics.mean(ccw[1]) - statistics.mean(cw[1])
            ds = statistics.pstdev(ccw[0]) - statistics.pstdev(cw[0])
            deltas.append((da, ds))
            print(f"  {turn:<10}{space:<9}{da:>+11.3f}{ds:>+15.2f}")

    if deltas:
        print(f"\n  {len(deltas)} pairs, mean aim delta {statistics.mean(d for d, _ in deltas):+.4f} r, "
              f"mean spread delta {statistics.mean(s for _, s in deltas):+.2f} ms")


if __name__ == "__main__":
    pos = positions()
    print(f"rhythm keys {len(pos)}")
    run_position(pos)
    angle_by_density()
    chirality()
