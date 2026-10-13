"""Did the cursor reach the circle, and was it still there when the button went down?

`streamfail.py` measures where the cursor was at the instant of the press, which cannot say
how it got there. Three mechanisms produce the same negative reach and imply different advice,
and the file spent three revisions arguing between two of them from position alone:

1. **The cursor never arrived.** It was still on its way in when the button went down.
2. **The cursor overshot ballistically** -- too fast to turn, past the circle on the outside
   of the bend, and on its way back when the button went down.
3. **The cursor arrived, dwelled, and left.** It reached the centre, sat for a moment, and
   departed for the next object before the button went down. Observed directly by the player
   watching replays: cursor to the centre of each circle, a fraction of a second still, then
   away, with the press marker landing half a radius to a radius outside the target -- a miss
   with no timing judgement at all, because a press with nothing under the cursor is not a
   late hit, it is not a hit.

None of these is distinguishable from the press sample, and all three were being argued about
as though it were. They are trivially distinguishable from `clicks.csv`, which has carried the
answer the whole time: `enterTime` and `exitTime` are when the cursor crossed into and out of
the circle, `closestDist` is how close it ever got in radii, and `pressInside` is whether the
button went down with the cursor on the object. The frame-level work that looked necessary was
already done in `Clicks.cs`.

**Vocabulary.** A timing word is never used without the hand it belongs to. "The cursor
rushed" and "the button was late" are different claims about different hands, and either hand
can do either thing -- so "rushed" and "late" alone are banned here, and so is "early".

**Measured 2026-10-13** on jumps of 3.5+ radii, turn 5:50 degrees -- the acute wide jumps the
player was watching. 770,552 of them in his corpus, 294,717 on the top-50 boards:

    player                                   n  share  miss% closest  when  turn  after   out  visit
    button down on target              704,319 91.4%   0.1%   0.297    -1  0.01     +0  0.45   0.53
    cursor left, then button down       24,925  3.2% 100.0%   0.488   -22  0.02      -  1.70      -
    cursor never reached it             12,841  1.7%  99.7%   1.465    +3  0.05      -  1.74      -
    cursor reached it, no stray press   11,726  1.5% 100.0%   0.613   +30  0.03      -     -      -
    both, and still nothing judged       5,629  0.7% 100.0%   0.402    -4  0.03      -  1.17      -

    top 50
    button down on target              294,242 99.8%   0.0%   0.245    -6  0.01     +4  0.35   0.57

* **The watched mechanism is real, and it is the largest miss class here.** 3.2% of the
  player's acute wide jumps end with the cursor having reached 0.488 radii from the centre,
  been deepest 22 ms *before* the beat, left, and the button going down 1.70 radii out -- 0.7
  radii outside the circle's edge. The player described this from replays as half a radius to
  a radius outside the target, which is what it measures. It is nearly twice as common as the
  cursor simply failing to arrive (1.7%), and it was invisible to every press-sample measure
  in `streamfail.py`, which can only see where the cursor was and not that it had been
  somewhere better a moment earlier.

* **Nothing about it is a timing failure of the button hand.** These objects carry no
  judgement at all. A press with nothing under the cursor is not a hit scored badly, it is
  not a hit, so the hit error distribution never sees them and a player watching sees a miss
  with no early or late marker and reasonably reads it as lateness. It is the cursor that ran
  ahead of the music, not the button that ran behind it.

* **The milder form exists but is not where this player differs.** The same shape should land
  inside the circle and judge perfectly -- cursor arrives, dwells, starts away, button falls
  while it is still on the object but off centre -- and it does: the button falls at 0.53 of
  the cursor's stay rather than at its deepest point. But the top 50 press *later* in the
  visit, 0.57, and 4 ms after their deepest point against the player's 0. So the phasing is
  not the player's problem. Precision is: 0.297 radii deep against 0.245, and 0.45 radii out
  at the press against 0.35.

* **The reference corpus can set a baseline here and cannot rank miss causes.** 99.8% of its
  acute wide jumps are clean, which is what a corpus of people's best plays looks like, so its
  sixteen misses say nothing about which mechanism dominates for good players. What it does
  give is the on-target numbers above, and the fact that "cursor left, then button down"
  accounts for 3 of 294,717 jumps against the player's 24,925 of 770,552.

**Against tempo** (`--by-bpm`, acute jumps of 3.5+ radii, turn under 50 degrees, tempo from
the rhythm pass's `beatLength` rather than inferred from the gap). The claim under test was
that this mechanism is absent below some tempo, remembered as about 160:

    bpm            jumps   cursor left, then button down   share of all misses
    under 140    145,858      3,787    2.6%                        40%
    140-160       94,702      3,280    3.5%                        42%
    160-180      245,607      6,605    2.7%                        39%
    180-200      253,684      7,851    3.1%                        45%
    200-220       75,749      4,280    5.7%                        57%
    220+           6,114        638   10.4%                        58%

* **It is not absent at low tempo and there is no threshold at 160.** The mechanism runs
  2.6-3.5% of acute wide jumps everywhere below 200 BPM -- and the 140-160 band is *higher*
  than the 160-180 band, which is the opposite of a threshold there. So the recollection of
  a tempo below which it vanishes does not survive this measurement, and whatever produced
  that number was either a narrower regime or a smaller sample.

* **It is strongly tempo-dependent in rate, and the rise is above 200 rather than at 160.**
  2.6% under 140 against 10.4% above 220, a fourfold increase concentrated in the top two
  bands. And at every tempo it is the single largest miss mechanism, from 40% of all misses
  on these jumps at the bottom to 58% at the top -- so the shape of the failure does not
  change with tempo, only how often it happens.

* **Tempo here is confounded with two things and the axis may not be tempo.** At a fixed
  spacing a higher tempo is a higher required cursor velocity, which is the physically
  meaningful quantity and the one the charter names; and low-tempo maps carrying 3.5-radii
  acute jumps are a different population of maps from high-tempo ones. Crossing
  `requiredVelocity` in would say which axis it is, and that has not been run.

* **The frame-level work this needed was already done.** `Clicks.cs` has written
  `enterTime`, `exitTime`, `closestTime`, `closestDist`, `turnDist` and the stray press with
  its cursor position since the clicks pass existed. Three revisions of `streamfail.py`
  argued between mechanisms from press samples while the columns that separate them sat in
  `build/clicks.csv`. Check what the corpus already carries before concluding a measurement
  needs new extraction.

    PYTHONPATH=tools python3 tools/analysis/arrival.py --source player
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bins.schema import ANGLE, SPACING  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEG = 180.0 / 3.141592653589793

SOURCES = {
    "player": ("build/geometry.csv", "build/clicks.csv", "build/rhythm.csv",
               "zaksynack playing "),
    "reference": ("build/geometry-reference-nm.csv", "build/clicks-reference-nm.csv",
                  "build/rhythm-reference-nm.csv", ""),
}
# Tempo bands, for the claim that the cursor-leaves-first mechanism is absent below some
# tempo. Cut around 160 because that is where the player remembers it being reported, so the
# bands either side of it are the test rather than the conclusion.
BPM_EDGES = (0.0, 140.0, 160.0, 180.0, 200.0, 220.0, 1e9)
LABELS = ("under 140", "140-160", "160-180", "180-200", "200-220", "220+")
MISSED = ("Miss", "LargeTickMiss")


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def classify(row) -> str:
    """What the cursor did, from the circle's point of view.

    A judged press appears in `pressTime`; a missed object has none, because nothing was
    attributed to it. The press that *did* happen is in `strayPressTime` -- `Clicks.cs`
    describes it as "a press that judged nothing, attributed to this object" -- and that is
    the marker the player watched land outside the circle. Keying the miss classes on
    `pressTime` finds nothing at all, which the first version of this file did.
    """
    closest = _num(row["closestDist"])
    enter, exit_ = _num(row["enterTime"]), _num(row["exitTime"])
    stray = _num(row["strayPressTime"])

    if closest is None:
        return "no cursor data"
    if row["pressInside"] == "1":
        return "button down on target"
    if closest > 1.0:
        return "cursor never reached it"
    # From here the cursor was inside the circle at some point and nothing judged.
    if stray is None:
        return "cursor reached it, no stray press"
    if exit_ is not None and stray > exit_:
        return "cursor left, then button down"
    if enter is not None and stray < enter:
        return "button down, then cursor arrived"
    return "both, and still nothing judged"


ORDER = ["button down on target", "cursor left, then button down",
         "button down, then cursor arrived", "both, and still nothing judged",
         "cursor reached it, no stray press", "cursor never reached it", "no cursor data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="player", choices=sorted(SOURCES))
    parser.add_argument("--min-spacing", type=float, default=3.5,
                        help="only jumps this wide, in radii; the regime the player watched")
    parser.add_argument("--by-bpm", action="store_true",
                        help="band by the map's tempo instead of by turn, over acute jumps "
                             "only. Tests the claim that the cursor-leaves-first mechanism "
                             "disappears below some tempo. BPM comes from the rhythm pass's "
                             "beatLength, so it is the map's tempo and not inferred from the "
                             "gap between two objects")
    parser.add_argument("--turn-max", type=float, default=50.0,
                        help="with --by-bpm, the sharpest turn to include, degrees")
    args = parser.parse_args()

    geometry, clicks, rhythm, prefix = SOURCES[args.source]
    geometry = os.path.join(ROOT, geometry)
    clicks = os.path.join(ROOT, clicks)
    rhythm = os.path.join(ROOT, rhythm)

    # Pass one: the geometry of every wide jump, so pass two can band it. Keyed to a tenth
    # of a millisecond, which is how rush.py joins these two files.
    wanted: dict = {}
    with open(geometry) as f:
        for r in csv.DictReader(f):
            if r["kind"] != "circle" or r["exitSlackRadii"]:
                continue
            if prefix and not r["replay"].startswith(prefix):
                continue
            spacing, angle = _num(r["spacingRadii"]), _num(r["signedAngle"])
            if spacing is None or angle is None or spacing < args.min_spacing:
                continue
            band = ANGLE.bin(abs(angle) * DEG)
            if band is None:
                continue
            tx, ty = _num(r["targetX"]), _num(r["targetY"])
            if args.by_bpm and abs(angle) * DEG >= args.turn_max:
                continue
            wanted[(sys.intern(r["replay"]), round(float(r["startTime"]), 1))] = (band, tx, ty)
    print(f"{args.source}: {len(wanted):,} jumps of {args.min_spacing}+ radii"
          + (f", turn under {args.turn_max:g} degrees" if args.by_bpm else ""), flush=True)

    if args.by_bpm:
        # The map's tempo, from the rhythm pass. Only for objects already selected, so the
        # table stays the size of the regime rather than the size of the corpus.
        found = 0
        with open(rhythm) as f:
            for r in csv.DictReader(f):
                if r["tolerance"] != "0.02" or r["clickIndex"] == "-1":
                    continue
                key = (r["replay"], round(float(r["time"]), 1))
                got = wanted.get(key)
                if got is None:
                    continue
                beat = _num(r["beatLength"])
                if beat is None or beat <= 0:
                    continue
                bpm = 60000.0 / beat
                for i in range(len(BPM_EDGES) - 1):
                    if BPM_EDGES[i] <= bpm < BPM_EDGES[i + 1]:
                        lo, hi = BPM_EDGES[i], BPM_EDGES[i + 1]
                        label = (f"under {hi:g}" if i == 0 else
                                 f"{lo:g}+" if i == len(BPM_EDGES) - 2 else
                                 f"{lo:g}-{hi:g}")
                        wanted[key] = (label, got[1], got[2])
                        found += 1
                        break
        # Anything the rhythm pass did not cover keeps its turn band and is dropped here,
        # so a missing tempo cannot quietly land in a tempo bucket.
        wanted = {k: v for k, v in wanted.items() if v[0] in LABELS}
        print(f"  tempo found for {len(wanted):,} of them\n", flush=True)
    else:
        print(flush=True)

    # Per turn band and class: counts, then (sum, n) for each measured quantity.
    #
    # The miss classes are the extreme case. The same phenomenon in milder form lands inside
    # the circle and judges perfectly -- cursor arrives, dwells, starts away, and the button
    # goes down while it is still on the object but no longer at the centre. So the press is
    # also measured against the cursor's visit, not just classified by it: PRESS_AFTER is how
    # long after the deepest point the button went down, and PRESS_DIST is how far out the
    # cursor was then. A player watching replays sees only the misses; these two columns are
    # where the same thing shows up among the hits.
    CLOSEST, WHEN, TURN, STRAY, PRESS_AFTER, PRESS_DIST, VISITED = range(2, 9)

    def cell():
        return [0, 0] + [[0.0, 0] for _ in range(7)]

    acc: dict = collections.defaultdict(cell)
    with open(clicks) as f:
        for r in csv.DictReader(f):
            got = wanted.get((r["replay"], round(float(r["startTime"]), 1)))
            if got is None:
                continue
            band, tx, ty = got
            c = acc[(band, classify(r))]
            c[0] += 1
            c[1] += 1 if r["result"] in MISSED else 0
            start_time = float(r["startTime"])
            radius = _num(r["radius"]) or 0.0
            press, closest_at = _num(r["pressTime"]), _num(r["closestTime"])
            enter, exit_ = _num(r["enterTime"]), _num(r["exitTime"])

            def add(slot, value):
                if value is not None:
                    c[slot][0] += value
                    c[slot][1] += 1

            add(CLOSEST, _num(r["closestDist"]))
            add(WHEN, None if closest_at is None else closest_at - start_time)
            add(TURN, None if _num(r["turnDist"]) is None or not radius
                else _num(r["turnDist"]) / radius)
            add(PRESS_AFTER, None if None in (press, closest_at) else press - closest_at)
            if enter is not None and exit_ is not None and press is not None \
                    and exit_ > enter:
                add(VISITED, (press - enter) / (exit_ - enter))
            for slot, x, y in ((STRAY, _num(r["strayX"]), _num(r["strayY"])),
                               (PRESS_DIST, _num(r["pressX"]), _num(r["pressY"]))):
                if None not in (x, y, tx, ty) and radius:
                    add(slot, ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5 / radius)

    print("What each hand did at wide jumps, by how sharp the turn is. No timing word appears "
          "without\nthe hand it belongs to: either hand can run ahead of the music or behind "
          "it, so 'rushed' and\n'late' alone are banned here.\n\n"
          "  closest  how near the cursor ever got to the centre, in radii. A true minimum,\n"
          "           not the moment it crossed in -- that is enterTime.\n"
          "  when     that moment against the object's time; negative means the cursor was\n"
          "           deepest before the beat.\n"
          "  turn     how far from the centre the cursor reversed, in radii: the overshoot in\n"
          "           one dimension along the path, where doubling back past the centre is\n"
          "           still having gone too far.\n"
          "  after    ms between the cursor's deepest point and the button going down.\n"
          "           Positive means the button went down while the cursor was on its way out.\n"
          "  out      how far from the centre the cursor sat when the button went down -- at\n"
          "           the judging press, or at the press that judged nothing.\n"
          "  visit    where in the cursor's stay inside the circle the button fell, 0 at the\n"
          "           moment it crossed in and 1 at the moment it crossed out.\n")
    for band in (LABELS if args.by_bpm else ANGLE.labels):
        total = sum(acc[(band, name)][0] for name in ORDER)
        if total < 500:
            continue
        print(f"{'bpm' if args.by_bpm else 'turn'} {band}  ({total:,} jumps)")
        print(f"  {'what the two hands did':<34}{'n':>8}{'share':>7}{'miss%':>7}"
              f"{'closest':>8}{'when':>6}{'turn':>6}{'after':>7}{'out':>6}{'visit':>7}")
        for name in ORDER:
            c = acc[(band, name)]
            if not c[0]:
                continue
            row = f"  {name:<34}{c[0]:>8,}{c[0] / total:>6.1%}{c[1] / c[0]:>7.1%}"
            for slot, width, fmt in ((CLOSEST, 8, "8.3f"), (WHEN, 6, "+6.0f"),
                                     (TURN, 6, "6.2f"), (PRESS_AFTER, 7, "+7.0f"),
                                     (STRAY if name != "button down on target" else PRESS_DIST,
                                      6, "6.2f"), (VISITED, 7, "7.2f")):
                summed, n = c[slot]
                row += format(summed / n, fmt) if n else f"{'-':>{width}}"
            print(row)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
