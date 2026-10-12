"""How fine should the diagnostic unit be, and is the object the right unit at all?

Scored as out-of-sample explained variance, per observation, so that partitions of very
different granularity are comparable -- correlating per-group statistics is not, because a
coarse partition is scored over a handful of widely-spaced groups and a fine one over
hundreds of near-identical ones, and the correlation then reads the spread of the groups as
much as the quality of the split. That mistake was made first and reversed the answer.

Train and test are disjoint sets of *beatmaps*. A diagnosis is a claim about the player, so
the bar is that it survives on maps it was not fitted to; splitting on replays leaves the
same maps on both sides and lets anything map-specific score as reproducible for free. The
replay split rates every partition higher and ranks them differently.

The target is squared hit error, because the thing being located is where error is *large*,
which is a statement about spread rather than about sign.

Measured 2026-10-12 on 3,121,474 of one player's circles, R^2 on held-out beatmaps:

    snap                                 54 groups   0.0247
    snap x spacing                      201         0.0387
    snap x spacing x |angle|            687         0.0397
    snap x spacing x angle (signed)    1175         0.0393
    + quadrant                         2013         0.0382
    + previous snap                    1477         0.0409
    + previous snap x previous spacing 5760         0.0421
    snap x spacing x run position       503         0.0440   <- best, and less than half
    snap x spacing x |angle| x run pos 1868         0.0424      the groups of the current
    snap x run position                 118         0.0349      scheme
    snap x run position x run size      206         0.0351

Four readings, in descending order of how much they should change:

* **Signed angle is strictly dominated.** Folding to |angle| scores the same or better with
  40% fewer groups, on this test and on both split-half tests. Keep the sign in the record,
  off the key.
* **Where in the phrase beats what shape the turn is.** Adding |angle| to snap x spacing
  buys +0.0010 for 3.4x the groups; adding run position buys +0.0053 for 2.5x. The object's
  geometry is not the most informative thing about it -- how deep into a run it sits is.
* **Quadrant costs.** It is negative here and on both split-half tests. This is a test of
  *spread* on the player side and says nothing about misses, which is where the charter's
  screen-position evidence lives; test it there before concluding anything.
* **A one-step lookback is the expensive way to buy what run position gives.** It helps, but
  scheme 7 needs 5,760 groups to score below a 503-group phrase scheme. The phrase is an
  aggregate -- "fourteenth note of a run" -- that a Markov context cannot express.

Absolute R^2 is small for all of them, around 4%, which is expected: most of one object's
error is irreducible. The comparison between partitions is the number that means something,
not its distance from 1.

    PYTHONPATH=tools python3 tools/analysis/unitsize.py
"""
import collections, hashlib, math, sys
sys.path.insert(0, 'tools')
from bins import load
from bins.schema import ANGLE, SPACING, RUN_POSITION

def fold(key):
    return int(hashlib.blake2b(key.encode(), digest_size=8).hexdigest(), 16) & 1

def sp(v): return SPACING.bin(v) if v is not None else None
def ab(v): return ANGLE.bin(abs(v)) if v is not None else None
def sg(v): return ANGLE.bin(v) if v is not None else None
def rp(v): return RUN_POSITION.bin(v) if v is not None else None

SCHEMES = {
    "1. snap":                              lambda o, p: (o.snap,),
    "2. snap x spacing":                    lambda o, p: (o.snap, sp(o.spacing)),
    "3. snap x spacing x |angle|":          lambda o, p: (o.snap, sp(o.spacing), ab(o.angle)),
    "4. snap x spacing x angle (current)":  lambda o, p: (o.snap, sp(o.spacing), sg(o.angle)),
    "5. 3 + quadrant":                      lambda o, p: None if o.target_x is None else (
        o.snap, sp(o.spacing), ab(o.angle),
        ("L" if o.target_x < 256 else "R") + ("T" if o.target_y < 192 else "B")),
    "6. 3 + previous snap":                 lambda o, p: None if p is None else (
        o.snap, sp(o.spacing), ab(o.angle), p.snap),
    "7. 3 + prev snap x prev spacing":      lambda o, p: None if p is None else (
        o.snap, sp(o.spacing), ab(o.angle), p.snap, sp(p.spacing)),
    # The phrase, not the object. Run position is an aggregate over the whole contiguous
    # run of constant rhythm -- how deep into it this object sits -- which a one-step
    # lookback cannot express at all.
    "8. snap x run position":               lambda o, p: (o.snap, rp(o.run_index)),
    "9. snap x spacing x run position":     lambda o, p: (o.snap, sp(o.spacing), rp(o.run_index)),
    "10. 3 + run position":                 lambda o, p: (o.snap, sp(o.spacing), ab(o.angle), rp(o.run_index)),
    "11. snap x run position x run size":   lambda o, p: (o.snap, rp(o.run_index), rp(o.run_size)),
}

# per scheme, per group, per fold: n, sum y, sum y^2   (y = squared hit error)
acc = {k: collections.defaultdict(lambda: [[0, 0.0, 0.0], [0, 0.0, 0.0]]) for k in SCHEMES}
grand = [[0, 0.0, 0.0], [0, 0.0, 0.0]]
prev = None
for o in load.observations(load.PLAYER, players={'zaksynack'}):
    p = prev if (prev and prev.replay == o.replay) else None
    prev = o
    if o.target != "circle" or o.from_slider != "0" or o.hit_error is None:
        continue
    h = fold(o.beatmap_md5)
    y = o.hit_error * o.hit_error
    g = grand[h]; g[0] += 1; g[1] += y; g[2] += y*y
    for name, f in SCHEMES.items():
        k = f(o, p)
        if k is None or None in k: continue
        a = acc[name][k][h]; a[0] += 1; a[1] += y; a[2] += y*y

MIN_TRAIN = 50
print(f"{grand[0][0]+grand[1][0]:,} observations, train/test split on beatmap\n")
print(f"{'partition':<38}{'groups':>8}{'used':>7}{'covered':>9}{'out-of-sample R^2':>20}")

for name in SCHEMES:
    # Both directions, so neither half is privileged, then pooled.
    num = den = 0.0
    used = covered = seen = 0
    for train, test in ((0, 1), (1, 0)):
        base = grand[train][1] / grand[train][0]
        for k, halves in acc[name].items():
            A, B = halves[train], halves[test]
            if B[0] == 0: continue
            seen += B[0]
            pred = A[1]/A[0] if A[0] >= MIN_TRAIN else base
            if A[0] >= MIN_TRAIN: used += 1; covered += B[0]
            # sum (y - pred)^2  -  sum (y - base)^2, from sufficient statistics
            num += B[2] - 2*pred*B[1] + B[0]*pred*pred
            den += B[2] - 2*base*B[1] + B[0]*base*base
    r2 = 1 - num/den if den else 0.0
    print(f"{name:<38}{len(acc[name]):>8}{used//2:>7}{covered/max(seen,1):>8.0%}{r2:>20.4f}")
