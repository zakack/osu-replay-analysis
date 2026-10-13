"""Does pattern membership predict aim error and misses on held-out beatmaps?

`patterns.py` finds repeated multi-object figures and says how much of a map they cover.
Coverage alone cannot settle whether the pattern is the right diagnostic unit: a scheme can
tile a map perfectly and still group objects that behave differently. The test that settles
it is the one `unitsize.py` used for cells -- out-of-sample explained variance, per
observation, train and test on disjoint *beatmaps* -- run here against the two targets the
unit is supposed to locate.

**Targets, and why these two.** Aim error in radii, and miss as a 0/1 indicator. Not hit
error: a pattern is a claim about where the cursor has to go, and tapping accuracy is a
different faculty that would reward the scheme for the wrong reason. The two targets are not
measured on the same objects, and the asymmetry is not a defect: aim error is recorded at
hit time, so a missed object has none. Aim error therefore asks "when this was hit, how
far off was the cursor", over hits only; miss asks "was this hit at all", over every judged
object. A scheme can win one and lose the other, and which it wins is informative.

**The corpus has to be the collected one.** Misses are the point, and a corpus of one
player's completed passes has almost none that are not self-selected -- lazer imports a pass
automatically and a fail has to be saved by hand. `build/geometry-collected.csv` is 4.3M
objects over 6,232 server-side replays by 4,978 players, which is where a miss rate means
something.

**Two traps, both of which would manufacture a win.**

*The vocabulary has to come from the training maps only.* A pattern qualifies for the shared
vocabulary by appearing on enough beatmaps. If the test map is one of its voters, the scheme
has seen the test map, and "held-out" is a fiction. So the vocabulary is rebuilt per
direction from that direction's training half, and the two directions are then pooled.

*Uncovered objects fall back to the cell baseline, not to the grand mean.* Patterns cover
around half a map. Falling back to the grand mean would score the pattern schemes down for
their coverage, which is a number already measured and reported elsewhere, and would hide
whether the covered part is better grouped. With the fallback, every pattern scheme is the
baseline plus a refinement on the objects it claims, so the difference in R^2 is attributable
to the claim and nothing else.

**Measured 2026-10-12** on 1,462,778 circles from 5,720 server-side replays over 1,454
beatmaps, R^2 on held-out beatmaps, at three training floors:

                                         floor 50          floor 200         floor 500
    scheme                    groups  claim   aim    miss  claim   aim    miss  claim   aim    miss
    cell |angle|                 462    97% .0277 -.0009    95% .0280  .0004    93% .0282  .0007
    cell run position            397    96% .0297  .0045    94% .0287  .0046    92% .0272  .0033
    cell |angle| x run position 1375    92% .0253  .0014    88% .0228  .0013    81% .0195  .0012
    schema4 x5                 11580    31% .0255 -.0019    22% .0275  .0004    17% .0282  .0009
    whole schema4 x5            5260    37% .0254 -.0029    31% .0264 -.0005    24% .0273  .0003
    schema4+flow x25            1313    13% .0266 -.0016     9% .0280  .0002     5% .0282  .0007
    naming x25                  1526    19% .0275 -.0012    14% .0282  .0004    11% .0287  .0009
    naming+flow x25              743     9% .0270 -.0013     8% .0280  .0002     5% .0282  .0007

(Eleven more pattern rows in the full output; they sit between these and change nothing.)

Four readings.

* **The pattern adds nothing the cell did not already have.** At the floor where groups are
  too small to estimate, every pattern scheme sits a touch below the cell it falls back to;
  as the floor rises that deficit closes monotonically and by 500 the schemes are *on* the
  cell's numbers -- `naming x25` reaches .0287 aim and .0009 miss against the cell's .0282
  and .0007. So the figures are not harmful and not helpful: they tie, while claiming 5-24%
  of the objects, which means the tie is the fallback doing the work. The first version of
  this measurement ran at floor 50 only and read the deficit as evidence that patterns were
  *worse*. That was estimation noise -- a miss rate near 5% estimated from 50 rows has a
  standard error the size of the between-group spread -- and the sweep is here so the same
  mistake is not available next time.

* **Run position beats the turn on misses at every floor, and both halves agree.** .0045,
  .0046, .0033 against the |angle| cell's -.0009, .0004, .0007; split by half it is .0032
  and .0056, best of all schemes on each. On aim error it wins at the two lower floors and
  loses at 500 -- but see the next reading: that win does not survive cleaning up press
  attribution, and the miss win does. `unitsize.py` found the same
  ordering on one player's squared hit error; this is 4,978 players and two different
  targets, on a corpus sharing no replays. That is as much corroboration as this project can
  produce for anything.

* **Run position's aim-error edge was stream shifting, and the miss edge was not.**
  `aimErrorRadii` is measured at whichever press the simulator attributed to the object, so
  a stream that shifted reports a real number taken from a tap meant for a neighbour. The
  displacement is about one tap interval, so `--own-tap 0.4` drops a press whose hit error
  exceeds 0.4 of the gap to the previous object. 126,554 presses go, 8.7% of the scored set,
  and they are not spread evenly:

      run position       presses   shifted    rate  mean aim
      0:2                482,345    41,058    8.5%    0.3631
      2:5                414,124    32,894    7.9%    0.3780
      5:9                207,132    16,398    7.9%    0.3884
      9:14               105,358     9,279    8.8%    0.3994
      14:inf             168,826    27,004   16.0%    0.4121

  Flat to position 14 and double beyond it, against a mean aim error that climbs the whole
  way -- so the deepest run positions, which is where the axis earns its score, are twice as
  likely to be scored from the wrong tap. Remove them and the aim ordering inverts: at floor
  200 the |angle| cell goes .0280 -> .0304 and run position .0287 -> .0302, so |angle| is
  the better aim key once the attribution is clean. The miss column does not move at all
  (.0004 and .0046), by construction: the filter drops only the aim contribution, a miss has
  no press to misattribute, and the rows and denominator are identical either way.

  Caveat on the instrument. This is a proxy, not `drift.py`'s self/next/prev/far/extra
  classification, and it also discards taps that were genuinely late but the object's own.
  The proper filter needs a clicks pass over the collected corpus, which does not exist yet.
  What the proxy establishes is the *direction*: part of the aim-error result was
  attribution, and none of the miss result was.

* **Misses are an order of magnitude harder to predict than aim error, for everything.**
  Best aim R^2 .0297, best miss R^2 .0046. The |angle| cell in particular -- the key
  `cells.py` actually bins on -- reaches .0007 on misses against run position's .0033, five
  times weaker, while being the better of the two on aim error. Both numbers are real and
  both are small; the point is the ratio, not the absolute.

* **``whole`` is not the control it looks like.** Dropping the position inside the figure
  pools objects that sit in different cells, so it cuts across the cell rather than
  coarsening it, and losing to the cell is what that should do if the cell carries anything.
  The floor sweep is the control for "cut too fine"; this row only shows that position
  inside the figure is not where the missing information was either.

Spelling needs the rhythm pass, and the collected side did not have one. It is a map
property -- `Rhythm.Build` walks the playable beatmap, so a run that ended early still
writes a row for every object and only the judgement columns differ -- so one replay per
beatmap is enough, and 1,456 is a twentieth of the work of 6,232. To rebuild from scratch:

    python3 tools/analysis/patternpredict.py --prepare
    dotnet run --project src/Cli -c Release -- rhythm \
        --corpus build/corpus-collected-onepermap.json --out build/rhythm-collected.csv
    PYTHONPATH=tools python3 tools/analysis/patternpredict.py

The middle step takes about ten minutes and must report 0 failed. Check the row count too:
a short CSV that still parses is how the delta geometry run came back a third light while
exiting 0.
"""
from __future__ import annotations

import argparse
from array import array
import collections
import csv
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.patterns import ANGLE_BANDS, angle_symbol, flow, repeats, vocabulary  # noqa: E402
from bins.schema import ANGLE, RUN_POSITION, SPACING  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEOMETRY = os.path.join(ROOT, "build", "geometry-collected.csv")
RHYTHM = os.path.join(ROOT, "build", "rhythm-collected.csv")
CORPUS = os.path.join(ROOT, "build", "corpus-collected.json")
ONE_PER_MAP = os.path.join(ROOT, "build", "corpus-collected-onepermap.json")
TOLERANCE = "0.02"
DEG = 180.0 / math.pi
MIN_TRAIN = 50
MISSED = ("Miss", "LargeTickMiss")

# Mods that make a replay incomparable, for two separate reasons.
#
# Relax and Autopilot replace one of the two faculties being measured, so their aim error
# and miss rate are not a player's. HardRock, Easy and DifficultyAdjust change the circle
# size, and spacing is measured in radii -- the same jump lands in a different band, so the
# replay's geometry would stop matching the spelling of the map it is being scored against.
#
# Mirror is deliberately not here. A reflection preserves every distance and every turn
# magnitude, chirality is folded out of the spelling, and the flow bit was built to be
# mirror-invariant: flipping a map flips both signs, so agreement survives. 754 replays.
EXCLUDE = frozenset(("RX", "AP", "HR", "EZ", "DA"))

# Which spellings to judge, and at what corpus support. The schema's own bands are what the
# findings table uses; the naming bands are a strict refinement of them that tells a triangle
# from a square. The flow bit tells a star from a zigzag, and without it they are one symbol.
SPELLINGS = [("schema4", False), ("schema4", True), ("naming", False), ("naming", True)]
SUPPORT = (5, 25)
# Prefix marking the coarser of the two keys a spelling is scored under: the figure alone,
# with no position inside it.
WHOLE = "whole "


def fold(key: str) -> int:
    return int(hashlib.blake2b(key.encode(), digest_size=8).hexdigest(), 16) & 1


def _num(raw):
    return float(raw) if raw not in ("", None) else None


def read_rhythm() -> dict[str, dict[float, tuple]]:
    """md5 -> object time -> (snap, group, run index, run size).

    Keyed on the beatmap rather than the replay because the file holds one representative
    replay per map, and because the rhythm *is* a map property: `Rhythm.Build` walks the
    playable beatmap's objects, so a run that ended early still writes a row for every
    object and only the judgement columns differ.
    """
    by_replay = {}
    for record in json.load(open(CORPUS)):
        by_replay.setdefault(os.path.basename(record["Path"]), record["BeatmapMd5"])

    table: dict[str, dict[float, tuple]] = collections.defaultdict(dict)
    short = 0
    with open(RHYTHM) as f:
        for r in csv.DictReader(f):
            # A row with a missing field is a truncated write, not data. Counted and
            # reported rather than skipped quietly: a short CSV that still parses is how
            # the delta geometry run came back a third of a corpus light while exiting 0.
            if r["runSize"] is None or r["time"] is None:
                short += 1
                continue
            if r["tolerance"] != TOLERANCE or r["clickIndex"] == "-1":
                continue
            md5 = by_replay.get(r["replay"])
            if md5 is None:
                continue
            table[md5][round(float(r["time"]), 3)] = (
                r["snap"] or None, int(r["groupId"]), int(r["runIndex"]), int(r["runSize"]))
    if short:
        print(f"WARNING: {short:,} truncated rows in {os.path.basename(RHYTHM)} -- "
              f"the file is incomplete", flush=True)
    return table, by_replay


def read_shapes(rhythm, by_replay) -> dict[str, list[dict]]:
    """Each map's clicked objects in map order, from the one replay the rhythm pass covered.

    Only what spelling needs. Scoring reads the file again for every replay; this pass is
    about the map's geometry, which is the same whoever played it.
    """
    representative = {}
    for name, md5 in by_replay.items():
        if md5 in rhythm:
            representative.setdefault(md5, name)
    wanted = {name: md5 for md5, name in representative.items()}

    out: dict[str, list[dict]] = collections.defaultdict(list)
    with open(GEOMETRY) as f:
        for r in csv.DictReader(f):
            md5 = wanted.get(r["replay"])
            if md5 is None:
                continue
            t = round(float(r["startTime"]), 3)
            got = rhythm[md5].get(t)
            if got is None:
                continue
            snap, group, run_index, _ = got
            out[md5].append({"t": t, "snap": snap, "group": group, "run": run_index,
                             "angle": _num(r["signedAngle"]),
                             "spacing": _num(r["spacingRadii"])})
    for rows in out.values():
        rows.sort(key=lambda o: o["t"])
    return out


def spell_one(row, edges):
    """One object's symbol without the kind, or None. Mirrors `patterns.spell`."""
    if not row["snap"]:
        return None
    band = SPACING.bin(row["spacing"]) if row["spacing"] is not None else None
    if band is None:
        return None
    parts = [row["snap"], band]
    if edges is not None:
        if row["angle"] is None:
            return None
        parts.append(angle_symbol(abs(row["angle"]) * DEG, edges))
    return "|".join(parts)


def segment(rows, edges, with_flow):
    """Spelled stretches, cut at group boundaries and wherever a symbol is missing.

    Same rule as `patterns.segments`, over this module's lighter rows.
    """
    out, current, start = [], [], 0
    last_group = None
    previous = None
    for i, row in enumerate(rows):
        sym = spell_one(row, edges)
        if sym is None or row["group"] != last_group:
            if len(current) >= 2:
                out.append((start, current))
            current, start = [], i
            previous = None
            last_group = row["group"] if sym is not None else None
            if sym is None:
                continue
        if with_flow:
            sym += "|" + flow(previous, row["angle"])
        previous = row["angle"]
        if not current:
            start = i
        current.append(sym)
    if len(current) >= 2:
        out.append((start, current))
    return out




def label(shapes, spelled, shared, min_len, max_len):
    """md5 -> (pattern id per object, index within the occurrence), as parallel arrays.

    Patterns are laid down best-first and claim only objects no earlier pattern claimed,
    ranked by how many beatmaps vouch for the pattern and then by how much of this map it
    would explain -- the same order `patterns.py --cross` tiles in.

    Arrays rather than dicts of tuples: there are eight spellings times two directions times
    a million objects to hold at once. ``-1`` means claimed by nothing; the ids are private
    to this one labelling, because the vocabulary differs between directions.
    """
    ids: dict[tuple, int] = {}
    labels: dict[str, tuple] = {}
    for md5, rows in shapes.items():
        pid = array("i", [-1]) * len(rows)
        at = array("b", [0]) * len(rows)
        found = repeats(spelled[md5], min_len=min_len, max_len=max_len, keep=shared)
        if found:
            order = sorted(found, key=lambda p: (-shared[p], -len(p) * len(found[p])))
            for pattern in order:
                for start in sorted(found[pattern]):
                    span = range(start, start + len(pattern))
                    if any(pid[j] >= 0 for j in span):
                        continue
                    number = ids.setdefault(pattern, len(ids))
                    for k, j in enumerate(span):
                        pid[j] = number
                        at[j] = k
        labels[md5] = (pid, at)
    return labels, len(ids)


def triples():
    """[n, sum y, sum y^2] for each target: aim error, then miss."""
    return [[0, 0.0, 0.0], [0, 0.0, 0.0]]


def add(into, y_aim, miss):
    if y_aim is not None:
        a = into[0]
        a[0] += 1
        a[1] += y_aim
        a[2] += y_aim * y_aim
    b = into[1]
    b[0] += 1
    b[1] += miss
    b[2] += miss * miss


def score(n, total, square_total, pred):
    """sum (y - pred)^2, from sufficient statistics."""
    return square_total - 2 * pred * total + n * pred * pred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-len", type=int, default=3)
    parser.add_argument("--max-len", type=int, default=8)
    parser.add_argument("--cache", help="parsed map shapes, so re-runs skip the first pass")
    parser.add_argument("--max-maps", type=int, default=0,
                        help="cap the beatmap count, for a cheap smoke run")
    parser.add_argument("--own-tap", type=float, default=0.0, metavar="FRACTION",
                        help="keep only objects whose attributed press is plausibly their "
                             "own: |hit error| within this fraction of the gap to the "
                             "previous object. A shifted press in a stream is displaced by "
                             "about one whole tap interval, so 0.4 excludes it. Guards the "
                             "aim-error numbers against stream shifting, which inflates aim "
                             "error and does so deeper into runs -- exactly where it would "
                             "manufacture the run-position result")
    parser.add_argument("--prepare", action="store_true",
                        help="write the one-replay-per-beatmap corpus the rhythm pass needs")
    parser.add_argument("--min-train", type=int, nargs="*", default=[50, 200, 500],
                        help="training-set floors below which a group is not used. Swept "
                             "rather than fixed because a scheme with many small groups "
                             "and one with few large ones are not otherwise comparable: a "
                             "deficit that closes as the floor rises was estimation noise")
    args = parser.parse_args()

    if args.prepare:
        # Any paired record would do; the most accurate run is chosen so nothing hinges on
        # an abandoned one, and the choice is deterministic for a rerun.
        best: dict[str, tuple] = {}
        for record in json.load(open(CORPUS)):
            if not record.get("Paired"):
                continue
            md5 = record["BeatmapMd5"]
            # Not named `score`: `report` closes over the module-level `score()` and a
            # local of that name in `main` shadows it for the whole function.
            rank = (record.get("Accuracy") or 0, os.path.basename(record["Path"]))
            if md5 not in best or rank > best[md5][0]:
                best[md5] = (rank, record)
        json.dump([r for _, r in best.values()], open(ONE_PER_MAP, "w"))
        print(f"{len(best):,} beatmaps written to {os.path.relpath(ONE_PER_MAP, ROOT)}")
        return 0

    if args.cache and os.path.exists(args.cache):
        shapes = json.load(open(args.cache))
    else:
        rhythm, by_replay = read_rhythm()
        shapes = read_shapes(rhythm, by_replay)
        if args.cache:
            json.dump(shapes, open(args.cache, "w"))
    if args.max_maps:
        shapes = dict(sorted(shapes.items())[:args.max_maps])
    objects = sum(len(v) for v in shapes.values())
    print(f"{len(shapes):,} beatmaps spelled, {objects:,} clicked objects", flush=True)

    # Where each object sits in its map's list, so a geometry row from any replay can find
    # its label by time -- which is the join key across players everywhere else here too.
    position = {md5: {row["t"]: i for i, row in enumerate(rows)}
                for md5, rows in shapes.items()}
    collisions = objects - sum(len(p) for p in position.values())
    if collisions:
        print(f"  {collisions:,} objects share a rounded time with another and cannot be "
              f"indexed unambiguously ({collisions / objects:.3%})", flush=True)

    # Pattern labels, per spelling, per support threshold, per direction. The vocabulary of
    # direction d is built from the maps in fold d alone; see the docstring.
    labelled: dict[str, list] = {}
    for name, with_flow in SPELLINGS:
        edges = ANGLE_BANDS[name]
        spelled = {md5: segment(rows, edges, with_flow) for md5, rows in shapes.items()}
        for support in SUPPORT:
            key = f"{name}{'+flow' if with_flow else ''} x{support}"
            per_direction = []
            for train in (0, 1):
                half = {md5: segs for md5, segs in spelled.items() if fold(md5) == train}
                shared = vocabulary(half, args.min_len, args.max_len, support)
                marks, used = label(shapes, spelled, shared, args.min_len, args.max_len)
                per_direction.append(marks)
                print(f"  {key:<22} train half {train}: {len(shared):>7,} shared "
                      f"sequences, {used:,} tiled", flush=True)
            labelled[key] = per_direction

    CELLS = ("cell |angle|", "cell run position", "cell |angle| x run position")
    # Per direction: training group sums, and test sums keyed by the pair (pattern, cell) so
    # the fallback is resolvable exactly rather than per observation.
    cell_train = [collections.defaultdict(triples) for _ in range(2)]
    cell_test = [{c: collections.defaultdict(triples) for c in CELLS} for _ in range(2)]
    keyed = [k for scheme in labelled for k in (scheme, WHOLE + scheme)]
    pat_train = [{k: collections.defaultdict(triples) for k in keyed} for _ in range(2)]
    pat_test = [{k: collections.defaultdict(triples) for k in keyed} for _ in range(2)]
    grand = [triples() for _ in range(2)]

    records = json.load(open(CORPUS))
    corpus = {os.path.basename(r["Path"]): r["BeatmapMd5"] for r in records
              if not (EXCLUDE & set(r.get("Mods") or ()))}
    print(f"{len(corpus):,} of {len(records):,} replays kept; "
          f"{len(records) - len(corpus):,} dropped for {'/'.join(sorted(EXCLUDE))}\n",
          flush=True)
    rows = kept = dropped = 0
    with open(GEOMETRY) as f:
        for r in csv.DictReader(f):
            rows += 1
            if r["kind"] != "circle" or r["exitSlackRadii"] or r["result"] == "Unjudged":
                continue
            md5 = corpus.get(r["replay"])
            if md5 is None or md5 not in shapes:
                continue
            index = position.get(md5, {}).get(round(float(r["startTime"]), 3))
            if index is None:
                continue
            shape = shapes[md5][index]
            spacing = SPACING.bin(shape["spacing"]) if shape["spacing"] is not None else None
            turn = ANGLE.bin(abs(shape["angle"]) * DEG) if shape["angle"] is not None else None
            run = RUN_POSITION.bin(shape["run"]) if shape["run"] is not None else None
            if not shape["snap"] or spacing is None or turn is None or run is None:
                continue

            y_aim = _num(r["aimErrorRadii"])
            miss = 1.0 if r["result"] in MISSED else 0.0

            if args.own_tap:
                # Aim error is measured at whichever press the simulator attributed to this
                # object, so a stream that shifted reports a real number taken from a tap
                # meant for a neighbour. The displacement is about one tap interval, which
                # `deltaTime` is, so a hit error well inside that interval is its own tap.
                # A miss has no press and nothing to misattribute, so it is kept.
                error, gap = _num(r["hitError"]), _num(r["deltaTime"])
                if y_aim is not None and (error is None or gap is None
                                          or abs(error) > args.own_tap * gap):
                    # Only the aim contribution goes: the row still counts for the miss
                    # target, which has no press to misattribute, so that column stays
                    # comparable with the unfiltered run instead of changing denominator.
                    dropped += 1
                    y_aim = None
            kept += 1

            base = (shape["snap"], spacing, turn)
            keys = {"cell |angle|": base,
                    "cell run position": (shape["snap"], spacing, run),
                    "cell |angle| x run position": base + (run,)}

            # Each row is training data for the direction its own fold trains, and a test
            # observation for the other. So one row lands on each side exactly once.
            h = fold(md5)
            other = 1 - h
            add(grand[h], y_aim, miss)
            add(cell_train[h][base], y_aim, miss)
            for name, key in keys.items():
                add(cell_test[other][name][key], y_aim, miss)
            for scheme, directions in labelled.items():
                # Two keys per spelling. The figure *and* where in it this object sits, which
                # is the pattern-level analogue of run position and the reason to expect a
                # pattern to group anything; and the figure alone, which is far coarser and
                # so has far more observations per group. Without both, a loss is ambiguous
                # between "the figure does not predict" and "the key was cut too fine".
                pid, at = directions[h][md5]
                if pid[index] >= 0:
                    add(pat_train[h][scheme][(pid[index], at[index])], y_aim, miss)
                    add(pat_train[h][WHOLE + scheme][(pid[index],)], y_aim, miss)
                pid, at = directions[other][md5]
                if pid[index] >= 0:
                    add(pat_test[other][scheme][((pid[index], at[index]), base)], y_aim, miss)
                    add(pat_test[other][WHOLE + scheme][((pid[index],), base)], y_aim, miss)
                else:
                    add(pat_test[other][scheme][(None, base)], y_aim, miss)
                    add(pat_test[other][WHOLE + scheme][(None, base)], y_aim, miss)
            if kept % 500_000 == 0:
                print(f"  {kept:,} scored", flush=True)

    print(f"\n{rows:,} geometry rows, {kept:,} scored"
          + (f", {dropped:,} presses excluded as not their object's own "
             f"(|hit error| over {args.own_tap:g} x the gap)" if args.own_tap else "") + "\n")

    def report(train_groups, test_groups, fallback, min_train, only=None):
        """R^2 per target, plus how much of the test set the groups actually claimed.

        ``only`` restricts to one direction, so the two halves can be read separately;
        without it the two are pooled, which is what the table reports.
        """
        out = []
        for target in (0, 1):
            num = den = 0.0
            used = covered = seen = 0
            directions = (0, 1) if only is None else (only,)
            for d in directions:
                g = grand[d][target]
                mean = g[1] / g[0] if g[0] else 0.0
                for key, s in test_groups[d].items():
                    stat = s[target]
                    if stat[0] == 0:
                        continue
                    seen += stat[0]
                    group, cell = key if fallback else (key, None)
                    train = train_groups[d].get(group)
                    t = train[target] if train else None
                    if t and t[0] >= min_train:
                        pred = t[1] / t[0]
                        used += 1
                        covered += stat[0]
                    elif fallback:
                        back = fallback[d].get(cell)
                        b = back[target] if back else None
                        pred = b[1] / b[0] if b and b[0] >= min_train else mean
                    else:
                        pred = mean
                    num += score(stat[0], stat[1], stat[2], pred)
                    den += score(stat[0], stat[1], stat[2], mean)
            out.append((1 - num / den if den else 0.0, covered / max(seen, 1),
                        used // len(directions)))
        return out

    # Every scheme as (name, train accumulator, test accumulator, fallback or None), so the
    # sweep and the per-direction table iterate one list instead of two special cases.
    arms = [(name, [cell_test[1 - d][name] for d in range(2)],
             [cell_test[d][name] for d in range(2)], None) for name in CELLS]
    arms += [(scheme, [pat_train[d][scheme] for d in range(2)],
              [pat_test[d][scheme] for d in range(2)], cell_train) for scheme in keyed]

    def group_count(name, train, fallback):
        if fallback is None:
            # Cell keys mean the same thing in both directions, so the union is the count.
            return len(set(train[0]) | set(train[1]))
        # Pattern ids are private to each direction's vocabulary, so the two key sets are
        # not comparable and their union is not a group count. The mean is.
        return (len(train[0]) + len(train[1])) // 2

    print(f"{len(shapes):,} beatmaps, {kept:,} scored objects, train and test on disjoint "
          f"beatmaps.\nAim error is over hits only -- a miss has no cursor error at hit "
          f"time. Pattern schemes fall\nback to the |angle| cell where no pattern claims "
          f"the object, so the difference is\nattributable to the grouping and not to "
          f"coverage. 'claimed' is the share of scored\nobjects whose own group cleared "
          f"the floor, which is narrower than pattern coverage:\ncircles not following a "
          f"slider, and only where the group had enough training rows.")

    for min_train in args.min_train:
        print(f"\ntraining floor {min_train}\n")
        print(f"{'scheme':<28}{'groups':>8}{'used':>7}{'claimed':>9}"
              f"{'aim R^2':>10}{'miss R^2':>10}")
        for name, train, test, fallback in arms:
            aim, miss = report(train, test, fallback, min_train)
            print(f"{name:<28}{group_count(name, train, fallback):>8}{miss[2]:>7}"
                  f"{miss[1]:>8.0%}{aim[0]:>10.4f}{miss[0]:>10.4f}")

    # The two halves separately, at the first floor. Pooling can hide a scheme that wins on
    # one half and loses on the other, which is a different claim from winning overall.
    floor = args.min_train[0]
    print(f"\neach half on its own, training floor {floor}. The halves are disjoint sets "
          f"of beatmaps,\nso agreement between them is the robustness statement the "
          f"pooled number cannot make.\n")
    print(f"{'scheme':<28}{'aim A':>9}{'aim B':>9}{'miss A':>9}{'miss B':>9}")
    for name, train, test, fallback in arms:
        a = report(train, test, fallback, floor, only=0)
        b = report(train, test, fallback, floor, only=1)
        print(f"{name:<28}{a[0][0]:>9.4f}{b[0][0]:>9.4f}{a[1][0]:>9.4f}{b[1][0]:>9.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
