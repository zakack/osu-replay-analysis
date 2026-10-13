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
      interval ms        94.611   92.818    0.98   -1.2
      along radii         0.173    0.187    1.08    2.0
      along residual      0.170    0.187    1.10    2.5
      along slope         0.061    0.054    0.89   -2.8
      cross radii         0.104    0.114    1.10    2.4
      cross slope         0.044    0.036    0.82   -4.7

    snap 0.5: 2,988 runs hold, 761 break at position 10+
      metric              holds   breaks   ratio      t
      rate ms/object      2.751    2.799    1.02    0.4
      rate %              1.277    1.470    1.15    3.7
      hit error ms       11.133   11.513    1.03    0.9
      interval ms       221.942  189.997    0.86  -15.6
      along radii         0.117    0.130    1.11    3.3
      along residual      0.117    0.129    1.10    3.0
      along slope         0.049    0.050    1.02    0.5
      cross radii         0.086    0.092    1.07    1.9
      cross slope         0.040    0.038    0.94   -1.5

**The same test on one player's own runs** (`--source player`, 17,824 replays, 15,608
qualifying runs), which is the question a diagnosis actually asks:

    snap 0.25: 5,569 runs hold, 2,414 break    snap 0.5: 5,072 hold, 2,553 break
      metric              holds   breaks  ratio     t      holds   breaks  ratio     t
      rate ms/object      2.138    2.342   1.10   4.5      2.358    2.325   0.99  -0.7
      rate %              2.310    2.541   1.10   4.6      1.297    1.267   0.98  -1.1
      interval ms        97.735  102.260   1.05   2.7    188.454  195.138   1.04   2.7
      along radii         0.203    0.217   1.07   3.7      0.146    0.169   1.16   7.9
      along residual      0.197    0.214   1.09   4.5      0.146    0.169   1.16   7.8
      along slope         0.063    0.062   0.98  -0.8      0.053    0.055   1.04   1.8
      cross radii         0.126    0.130   1.04   1.8      0.107    0.116   1.08   3.9
      cross slope         0.046    0.049   1.06   3.0      0.049    0.051   1.06   2.5

Restricted to one button era (`--since 2026-01-03`, the doubletap regime, 5,200 replays and
7,601 runs), since the tempo metrics are exactly what `setup-timeline.csv`'s eras move:

    snap 0.25: 3,114 hold, 1,400 break      snap 0.5: 2,005 hold, 1,082 break
      metric              ratio       t        ratio       t
      rate ms/object       1.13     4.5         1.03     0.8
      rate %               1.10     3.5         1.02     0.5
      interval ms          1.10     3.5         1.02     1.1
      along residual       1.09     3.7         1.10     3.6
      along slope          0.96    -1.4         1.04     1.1
      cross radii          1.02     0.9         1.05     1.7
      cross slope          1.03     1.3         1.04     1.2

Five readings, two of which are corrections to the population reading.

* **A chain of jumps breaks on where the cursor is, in both corpora, and more clearly in
  one player's.** At 1/2 the along-track offset is the strongest term anywhere in either
  table -- ratio 1.16 at t=7.9 -- and tapping tempo is flatly irrelevant, t=-0.7 per object
  and -1.1 as a percentage. The coupling coefficient is -0.02, so none of it is tap timing
  in spatial clothing. This is the firmest result here.

* **The population's spurious 1/2 tempo effect is confirmed spurious.** It read t=3.7 as a
  percentage there only because the breaking runs were faster. Within one player the interval
  difference goes the other way (1.04, t=2.7) and the percentage effect vanishes with it
  (t=-1.1), which is what an artifact of the denominator does and a real effect does not.

* **For streams, tempo and cursor placement matter equally** -- t=4.5 each at 1/4. The
  population table had tempo dominant (t=5.5 against 2.5), and the gap closes when the
  between-player variation is removed, which is exactly the confound the population table was
  warned to carry: players who tap off-tempo also fail more, and that inflates tempo without
  tempo causing any individual failure. The honest claim is that a stream breaks on both, and
  a chain of jumps on the cursor alone.

* **The "runs that drift are the ones that hold" result does not replicate and should be
  dropped.** It was the most interesting row in the population table: breaking runs there had
  smaller growth in cursor error, along slope t=-2.8 and cross slope t=-4.6. Within one
  player the along slope difference disappears (t=-0.8) and the cross slope *reverses* to
  t=+3.0 -- breaking runs drift more, which is the ordinary expectation. So it was a
  between-player artifact, and the hypothesis logged in INTERESTING.md about the optimal
  stream path not being centre-to-centre loses this as motivation. The hypothesis is still
  worth testing; it just is not evidenced by that row.

* **Cross-track is the weakest term everywhere and does not survive holding the era fixed.**
  1/4 cross-track goes t=2.4 in the population, 1.8 over the player's whole history, 0.9
  inside one button era. Of the three candidate mechanisms, leaving the path sideways is the
  one this test cannot show mattering. Which does not mean it never does -- it means a run
  that is about to break is not, on average, further off the path sideways beforehand.

* **The player is less precise than the population, in level rather than in pattern.**
  Along-track 0.203 against 0.173 at 1/4, cross-track 0.126 against 0.104, with the same
  ordering of effects. Map mix and skill both plausibly explain that and it is not separated
  here.

**Which way, not just how far** (the signed tables). Magnitudes answer "how far off" and
discard the thing a diagnosis needs most. Positive rate is tapping progressively later; `+%`
is the share of runs above zero.

    1/4 streams            holds   breaks  holds +%  breaks +%      t
    population             0.416    1.260     56.5%      67.1%    5.6
    player, all-time      -0.005    0.447     49.7%      56.4%    6.3
    player, one era        0.233    0.431     53.2%      56.4%    2.5

    1/2 along-track        holds   breaks  holds +%  breaks +%      t
    population            -0.027   -0.018     46.5%      46.5%    1.4
    player, all-time      -0.082   -0.118     30.4%      23.2%   -8.5
    player, one era       -0.099   -0.117     27.1%      24.7%   -2.8

* **A run that breaks on tempo breaks late, in both corpora.** Not symmetric at all: 67.1% of
  the population's breaking stream runs were drifting later against 56.5% of the holding ones,
  and for the player 56.4% against 49.7%. Falling behind the beat is what precedes a break;
  running ahead of it does not, which is a different claim from "tempo error precedes a break"
  and a more useful one. The player's holding runs sit at 49.7% -- a coin flip, so centred --
  where the population's already lean late at 56.5%, so the player is better centred than the
  field and still fails the same way when it goes.

* **On jump chains this player's cursor sits *behind* the note, and breaking runs sit further
  behind.** The signed along-track is the largest single effect in any table here: only 23.2%
  of breaking 1/2 runs have the cursor ahead, against 30.4% of holding ones, t=-8.5, and it
  replicates inside one era at 24.7% against 27.1%. The population is near-centred on the same
  measure (46.5%), so this is the player's and not the game's. The magnitude table said only
  "further off"; signed, it says short of the target along the direction of travel -- which is
  the charter's own example of a target finding, arrived at from the other end.

**Lag against cut** (`--path`). Negative along-track has two causes with opposite advice: a
hand falling short of the target, and a hand taking the shorter route through a turn. Cutting
needs a corner, so the two separate on how the offset depends on turn magnitude (180 straight
through, 0 doubling back). Offsets in radii, at the press, positions 0-9:

    1/4, runs that hold      population              player
    turn              n     lag   inside        n     lag   inside
    0:5               8  -0.073   +0.064      154  -0.272   +0.078
    5:50            255  -0.052   +0.077      964  -0.128   +0.063
    50:135        1,320  -0.060   +0.061    6,042  -0.111   +0.080
    135:160       2,918  -0.060   +0.091   22,421  -0.024   +0.116
    160:180       4,175  -0.067   +0.032   20,007  -0.036   +0.082

* **The field's lag is flat in turn magnitude; this player's is not.** The population sits at
  about -0.06 radii at every turn band, from doubling back to straight through -- a generic
  lag with no relation to the corner. The player runs from -0.272 at a reversal to -0.024 at
  135-160 degrees, a tenfold gradient, and is four to five times the field's at the sharpest
  turns while being *better* than the field at the shallow ones. So this player's negative
  along-track is corner-related and the field's is not, which is the difference the signed
  table found and could not explain.

* **Everybody cuts to the inside, and nobody aims at circle centres.** `inside` is positive
  in almost every cell of both corpora, 50-70% of presses, peaking at 135-160 degrees. That
  is the direct evidence for the INTERESTING.md hypothesis: the path players actually take
  through a stream is not the polyline joining circle centres, so cross-track error measured
  against those centres is partly measuring ordinary play. The player cuts harder than the
  field at every band (+0.116 against +0.091 at the peak).

* **Breaking runs cut further in and lag further back.** At 1/4 the player's breaking runs
  reach +0.147 inside at 135-160 against +0.116 for holding ones, and -0.333 lag at a reversal
  against -0.272. Which way that causes is not settled here: cutting may be the technique that
  usually works and occasionally overshoots into a miss, or a run already going wrong may
  produce both.

**The control this wants before it is phrased for a player.** Sharp turns at 1/4 are mostly
tight and stacked, so turn magnitude and spacing are correlated in the map pool, and lag is
normalised by the radius rather than by the movement. A fixed lag in radii therefore looks
larger where the jump is smaller, and the player's map pool is not the field's. Crossing
spacing into the `--path` key would separate "lags at sharp corners" from "lags on short
movements"; until then the gradient is real and its axis is not certain.

Caveats that bound all of the above. Every ratio is between 0.98 and 1.16 on the player side,
so all three mechanisms are present in both rhythms and this is which one moves first rather
than which is to blame. Only circles are visible, so a miss on a slider head inside positions
0-9 does not disqualify a run from the clean group. The cursor is only ever sampled at a press
here, which is why "the cursor travelled the path too fast" is not yet measured as such --
that needs the replay frames between presses, not the press samples, and it is also what the
centre-to-centre hypothesis in INTERESTING.md would need. And the player run is all-time, so
it spans every button era in `setup-timeline.csv`; the tempo metrics are precisely what those
eras move, which is what `--since` is for.

    PYTHONPATH=tools python3 tools/analysis/streamfail.py --source player
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

from analysis.patternpredict import EXCLUDE, MISSED, _num, read_rhythm  # noqa: E402
from bins.load import TIMESTAMP  # noqa: E402
from bins.schema import ANGLE  # noqa: E402

DEG = 180.0 / math.pi


def cross2(a, b) -> float:
    """z of the cross product. Sign says which rotational side b is on, relative to a."""
    return a[0] * b[1] - a[1] * b[0]


def corner(previous, here, nxt, cursor, aim):
    """Split the cursor's offset into lagging behind and cutting the corner.

    Negative along-track has two completely different causes that the magnitude cannot
    separate, and that imply opposite advice. A hand that falls short of the target is
    *lagging*. A hand that takes a shorter route through a turn is *cutting*, which is not an
    error at all if the shorter route keeps the cursor inside successive hitboxes -- the
    hypothesis in INTERESTING.md about the optimal path not being centre to centre.

    They are distinguishable because cutting needs a corner and lagging does not:

    * ``lag`` is the offset along the incoming direction. Negative is short of the target.
      A lagging hand does this whether or not the path turns.
    * ``inward`` is the offset across the incoming direction, with its sign flipped into the
      turn's own frame, so positive always means *toward the inside of the corner*. Cutting
      produces this and only at a real turn; a lagging hand has no reason to prefer the
      inside over the outside.

    ``inward`` is mirror-invariant by construction -- reflecting a map flips the turn and the
    offset together -- so it is not the per-object chirality Mirror week settled and found
    empty. It is a relationship between two consecutive legs, which that week never tested.

    Returns (lag, inward), both None if the geometry is degenerate. The turn magnitude comes
    off the row rather than being recomputed here: the convention (180 straight through, 0
    doubling back) is pinned by GeometryTests, and reconstructing it from travel directions
    gets it exactly backwards.
    """
    offset = (cursor[0] - here[0], cursor[1] - here[1])
    length = math.hypot(*offset)
    if not aim or aim <= 0 or length == 0:
        return None, None
    radius = length / aim
    incoming = (here[0] - previous[0], here[1] - previous[1])
    outgoing = (nxt[0] - here[0], nxt[1] - here[1])
    din, dout = math.hypot(*incoming), math.hypot(*outgoing)
    if din == 0 or dout == 0:
        return None, None
    u = (incoming[0] / din, incoming[1] / din)
    v = (outgoing[0] / dout, outgoing[1] / dout)
    side = 1.0 if cross2(u, v) >= 0 else -1.0
    lag = (offset[0] * u[0] + offset[1] * u[1]) / radius
    inward = cross2(u, offset) * side / radius
    return lag, inward

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The two corpora answer different questions. `collected` is 4,978 players, so it says what
# breaks a run in the population; `player` is one player's own runs, so it says what breaks
# *his*, which is the question a diagnosis actually asks and the one the population cannot
# answer -- a player who taps off-tempo and also fails more produces the population table
# without tempo causing any single failure.
#
# They also differ in what rhythm is available, and that decides how the join works. The
# collected side has one representative replay per beatmap, since the rhythm pass was run
# that way, so its runs are looked up by beatmap md5 and a replay whose mods changed the
# timing simply fails to join. The player side has rhythm for every replay, so runs are
# looked up by replay and time -- strictly better, because each replay then carries its own
# mods and nothing has to match across them.
SOURCES = {
    "collected": ("build/geometry-collected.csv", "build/rhythm-collected.csv",
                  "build/corpus-collected.json"),
    "player": ("build/geometry.csv", "build/rhythm.csv", "build/corpus.json"),
}

BEFORE = 10        # positions 0..9: measured, and required to be clean in both groups
MIN_SIZE = 14      # a run has to be long enough for "breaks at 10 or later" to be possible
MIN_POINTS = 6     # own-tap presses needed inside positions 0..9 to fit a slope
MIN_EARLY = 6      # measurable circles needed inside positions 0..9
OWN_TAP = 0.4      # |hit error| within this fraction of the gap is the object's own press


def read_player_runs(path, replays, snaps) -> dict:
    """(replay, object time) -> (run id, run index, run size, snap), long runs only.

    Keyed on the replay rather than the beatmap, which is what makes mods a non-issue here:
    a rate mod scales a replay's object times and its rhythm together, so each replay joins
    against its own numbers and nothing has to agree across replays.

    Filtered while reading because the file is 6.1GB and only runs long enough to break late,
    in the rhythms being asked about, are ever looked up.
    """
    table: dict = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["clickIndex"] == "-1":
                continue
            if r["snap"] not in snaps or r["replay"] not in replays:
                continue
            if int(r["runSize"]) < MIN_SIZE:
                continue
            table[(r["replay"], round(float(r["time"]), 3))] = (
                int(r["runId"]), int(r["runIndex"]), int(r["runSize"]), r["snap"])
    return table


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


def signed(a: list[float], b: list[float]) -> tuple:
    """Means with the sign kept, which way each group leans, and Welch's t on the difference.

    The magnitude comparison answers "how far off", and deliberately discards the thing a
    diagnosis needs most: *which* way. Tapping progressively late and progressively early are
    equally off tempo and are opposite problems with opposite fixes.
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / (len(a) - 1)
    vb = sum((v - mb) ** 2 for v in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma, mb, sum(1 for v in a if v > 0) / len(a),
            sum(1 for v in b if v > 0) / len(b), ((mb - ma) / se if se else 0.0))


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
    parser.add_argument("--source", default="collected", choices=sorted(SOURCES),
                        help="which corpus: the population, or one player's own runs")
    parser.add_argument("--player", default="zaksynack",
                        help="player source only; the corpus holds a few others' imports")
    parser.add_argument("--since", help="player source only: keep replays on or after this "
                                        "date, YYYY-MM-DD. The tapping-hand metrics here are "
                                        "exactly what setup-timeline.csv's eight button eras "
                                        "move, so an all-time tempo number mixes regimes -- "
                                        "the QMK toggle era alone made the two presses "
                                        "mutually exclusive")
    parser.add_argument("--path", action="store_true",
                        help="instead of the outcome tables, split the cursor's offset into "
                             "lagging behind the target and cutting the inside of the corner, "
                             "by turn magnitude. Negative along-track has those two causes and "
                             "they imply opposite advice; cutting needs a corner and lagging "
                             "does not, which is what separates them")
    parser.add_argument("--snaps", nargs="*", default=["0.25", "0.5"])
    args = parser.parse_args()

    geometry, rhythm_path, corpus_path = (os.path.join(ROOT, p)
                                          for p in SOURCES[args.source])
    records = json.load(open(corpus_path))
    if args.source == "player":
        wanted = {os.path.basename(r["Path"]) for r in records if r.get("Paired")
                  and os.path.basename(r["Path"]).startswith(args.player + " playing ")}
        if args.since:
            dated = {}
            for name in wanted:
                found = TIMESTAMP.search(name)
                if found and found.group(1) >= args.since:
                    dated[name] = found.group(1)
            print(f"{len(dated):,} of {len(wanted):,} replays on or after {args.since}")
            wanted = set(dated)
        corpus = {}
    else:
        # Mods are recorded only on the collected side. Relax and Autopilot replace one of
        # the two hands being compared; HardRock, Easy and DifficultyAdjust change the
        # radius that along- and cross-track are measured in.
        corpus = {os.path.basename(r["Path"]): r["BeatmapMd5"] for r in records
                  if not (EXCLUDE & set(r.get("Mods") or ()))}
        wanted = set(corpus)
    print(f"{args.source}: {len(wanted):,} replays\n", flush=True)

    if args.source == "player":
        runs = read_player_runs(rhythm_path, wanted, set(args.snaps))
        def find(replay, t):
            return runs.get((replay, t))
    else:
        by_md5, _ = read_rhythm()
        def find(replay, t):
            md5 = corpus.get(replay)
            got = by_md5.get(md5, {}).get(t) if md5 else None
            if got is None:
                return None
            snap, _group, run_id, run_index, run_size = got
            return (run_id, run_index, run_size, snap)

    # snap -> outcome -> metric -> list of per-run values
    acc: dict = collections.defaultdict(lambda: collections.defaultdict(
        lambda: collections.defaultdict(list)))
    # (snap, turn band, outcome) -> n, sum lag, sum inward, count inward > 0
    path: dict = collections.defaultdict(lambda: [0, 0.0, 0.0, 0])
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

            if args.path:
                # Per object rather than per run: the question is about the shape of the
                # offset, not about the run's summary. Still confined to positions 0-9, so
                # nothing here is measured after a miss.
                for i, row in enumerate(run):
                    if row["run"] >= BEFORE or i == 0 or i + 1 >= len(run):
                        continue
                    before, after = run[i - 1], run[i + 1]
                    if (before["run"] != row["run"] - 1 or after["run"] != row["run"] + 1
                            or row["angle"] is None or None in (row["cx"], row["cy"])):
                        continue
                    lag, inward = corner((before["tx"], before["ty"]),
                                         (row["tx"], row["ty"]), (after["tx"], after["ty"]),
                                         (row["cx"], row["cy"]), row["aim"])
                    if lag is None:
                        continue
                    band = ANGLE.bin(row["angle"])
                    if band is None:
                        continue
                    cell = path[(snap, band, outcome)]
                    cell[0] += 1
                    cell[1] += lag
                    cell[2] += inward
                    cell[3] += 1 if inward > 0 else 0
                continue

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
            # The interval itself, because a "percentage tempo error" difference between the
            # groups can be an interval difference rather than a tempo one.
            if gap:
                here["interval ms"].append(sum(gap) / len(gap))
            # Along-track is measured at the press, so a late tap on a cursor tracking the
            # path perfectly already shows as the cursor being ahead: along ~ velocity x hit
            # error. Both parts are recorded per run so the coupling can be divided out
            # instead of assumed away.
            coupled = [(r["along"], (r["velocity"] or 0.0) * r["error"]) for r in early
                       if r["along"] is not None and r["own"] and r["error"] is not None
                       and r["velocity"] is not None]
            if coupled:
                here["along pairs"].append((sum(a for a, _ in coupled) / len(coupled),
                                            sum(x for _, x in coupled) / len(coupled)))
            for name, key in (("along radii", "along"), ("cross radii", "cross")):
                vals = [r[key] for r in early if r[key] is not None]
                if vals:
                    here[name].append(sum(vals) / len(vals))
                    here[name.replace("radii", "slope")].append(
                        slope([(float(r["run"]), r[key]) for r in early
                               if r[key] is not None]) or 0.0)

    held: list[dict] = []
    current_replay = None
    with open(geometry) as f:
        for r in csv.DictReader(f):
            if r["replay"] != current_replay:
                if held:
                    flush(held)
                held, current_replay = [], r["replay"]
            if r["kind"] != "circle" or r["exitSlackRadii"] or r["result"] == "Unjudged":
                continue
            if r["replay"] not in wanted:
                continue
            t = round(float(r["startTime"]), 3)
            got = find(r["replay"], t)
            if got is None:
                continue
            run_id, run_index, run_size, snap = got
            if run_size < MIN_SIZE:
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
                previous = (held[-1] if held and held[-1]["runid"] == run_id
                            and held[-1]["run"] == run_index - 1 else None)
                if radius and previous is not None:
                    dx, dy = tx - previous["tx"], ty - previous["ty"]
                    travel = math.hypot(dx, dy)
                    if travel > 0:
                        ux, uy = dx / travel, dy / travel
                        along = (offset[0] * ux + offset[1] * uy) / radius
                        cross = (offset[0] * -uy + offset[1] * ux) / radius

            held.append({"t": t, "run": run_index, "runid": run_id, "snap": snap,
                         "cx": cx, "cy": cy, "aim": aim,
                         "angle": (abs(_num(r["signedAngle"]) or 0.0) * DEG
                                   if r["signedAngle"] else None),
                         "size": run_size, "error": error, "gap": gap, "own": own,
                         "miss": r["result"] in MISSED, "along": along, "cross": cross,
                         "velocity": _num(r["requiredVelocity"]),
                         "tx": tx, "ty": ty})
    if held:
        flush(held)

    print(f"{counted:,} runs of {MIN_SIZE}+ objects, clean through position {BEFORE - 1}, "
          f"every quantity measured\nover positions 0-{BEFORE - 1} only -- before any miss, "
          f"so what shows up here is a cause and not\na consequence. Magnitudes, since a run "
          f"tapped progressively early and one tapped\nprogressively late are equally off "
          f"tempo. t is Welch's on the difference of the two.\n")

    if args.path:
        print(f"The cursor's offset at the press, split into lagging behind the target and "
              f"cutting the\ninside of the corner, by how sharp the turn is. 180 is straight "
              f"through and 0 doubles\nback. Cutting needs a corner, so a lagging hand gives "
              f"a flat 'inside' column and a cutter\ngives one that grows as the turn "
              f"sharpens. 'inside' is mirror-invariant: reflecting a map\nflips the turn and "
              f"the offset together, so it is not the per-object chirality Mirror week\n"
              f"settled -- it is a relationship between two consecutive legs.\n")
        for snap in args.snaps:
            bands = [b for b in ANGLE.labels
                     if any((snap, b, o) in path for o in ("holds", "breaks at 10+"))]
            if not bands:
                continue
            print(f"snap {snap}")
            print(f"  {'turn':<12}" + "".join(f"{h:>11}" for h in
                  ("n holds", "lag", "inside", "inside>0", "n breaks", "lag", "inside",
                   "inside>0")))
            for band in bands:
                cells = ""
                for outcome in ("holds", "breaks at 10+"):
                    n, lag, inward, positive = path[(snap, band, outcome)]
                    if not n:
                        cells += f"{0:>11}{'-':>11}{'-':>11}{'-':>11}"
                        continue
                    cells += (f"{n:>11,}{lag / n:>+11.3f}{inward / n:>+11.3f}"
                              f"{positive / n:>10.1%} ")
                print(f"  {band:<12}{cells}")
            print()
        return 0

    # Along-track with the tap-timing part removed. The coefficient is fitted over both
    # groups of a snap together, so it cannot absorb the difference being tested.
    for snap in acc:
        pairs = [p for outcome in acc[snap] for p in acc[snap][outcome].get("along pairs", [])]
        if len(pairs) < 20:
            continue
        mx = sum(x for _, x in pairs) / len(pairs)
        my = sum(a for a, _ in pairs) / len(pairs)
        var = sum((x - mx) ** 2 for _, x in pairs)
        beta = (sum((x - mx) * (a - my) for a, x in pairs) / var) if var else 0.0
        for outcome in acc[snap]:
            acc[snap][outcome]["along residual"] = [
                a - beta * x for a, x in acc[snap][outcome].get("along pairs", [])]
        print(f"snap {snap}: along-track regressed on velocity x hit error, "
              f"coefficient {beta:.2f} (1.00 would be pure coupling)")
    print()

    metrics = [("rate ms/object", "tapping tempo error, ms of phase per object"),
               ("rate %", "the same as a percentage of the interval"),
               ("hit error ms", "mean hit error over the stretch, for reference"),
               ("interval ms", "the gap itself: a control for the percentage above"),
               ("along radii", "cursor ahead of or behind the note, measured at the press"),
               ("along residual", "the same with the tap-timing part divided out"),
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

        print(f"\n  signed, so which way each group leans. '+%' is the share of runs above "
              f"zero;\n  for rate, positive is tapping progressively later.\n")
        print(f"  {'metric':<16}{'holds':>9}{'breaks':>9}{'holds +%':>10}"
              f"{'breaks +%':>11}{'t':>7}")
        for metric, _ in metrics:
            a = acc[snap]["holds"].get(metric, [])
            b = acc[snap]["breaks at 10+"].get(metric, [])
            if metric == "interval ms":
                continue
            ma, mb, pa, pb, t = signed(a, b)
            print(f"  {metric:<16}{ma:>9.3f}{mb:>9.3f}{pa:>9.1%}{pb:>10.1%}{t:>7.1f}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
