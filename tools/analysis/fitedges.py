"""Choose the turn axis's band edges, and prove the choice survives maps it was not fitted to.

`anglebands.py` accumulates the behaviour curves in halves split on the beatmap. This fits
piecewise-constant edges on one half and scores them on the other, both ways round, so an
edge set is judged on maps that had no say in choosing it.

**Only the shape transfers, not the level.** Two halves of a corpus differ in overall
difficulty, in who played them and in how much they miss, so a model that carries an
absolute miss rate from one to the other is scored mostly on that offset, which has nothing
to do with where the angle axis should be cut. The first version of this did exactly that
and reported -250% on perfectly reasonable edges. The model fitted here is

    value(spacing band b, angle bin i) = level(b, test set) + shape(b, segment of i, train)

with the shape summing to zero over each spacing band, so the baseline it is measured
against -- predicting the test set's own mean for that spacing band -- is exactly the model
with no angle structure at all. Edges are shared across spacing bands because the schema
needs one angle axis; levels and shapes are per band because spacing sets the difficulty and
only the shape in angle is being located.

Segmentation is exact, by dynamic programming over the bins rather than a search over every
combination of edges: the cost of a partition is a sum over segments, so the best partition
of the first j bins into k pieces is built from the best partition of the first i.

Scored on miss rate and aim error, never hit error. A turn is an aiming demand.

    PYTHONPATH=tools python3 tools/analysis/fitedges.py --file build/anglebands-both.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIN_N = 2000     # per angle bin per spacing band, before a point is worth fitting to

CANDIDATES = {
    "ppy knees": (40, 140),
    "current grid": (30, 60, 90, 120, 150),
    "miss knee": (50,),
    "both knees": (50, 160),
    "union of fits": (45, 65, 125, 160),
    "union + reversal": (5, 50, 65, 125, 160),
}


def curves(half, metric, bands):
    """{spacing band: [(angle bin, value, weight)]} for one half of the maps."""
    out = {}
    for b in bands:
        rows = []
        for i, (n_aim, sum_aim, n_all, miss) in enumerate(half[b]):
            if metric == "miss":
                if n_all >= MIN_N:
                    rows.append((i, miss / n_all, n_all))
            elif n_aim >= MIN_N:
                rows.append((i, sum_aim / n_aim, n_aim))
        if rows:
            out[b] = rows
    return out


def centred(rows):
    """Values with the band's weighted mean removed, and that mean."""
    wt = sum(w for _, _, w in rows)
    mu = sum(v * w for _, v, w in rows) / wt
    return [(i, v - mu, w) for i, v, w in rows], mu


def seg_cost(rows, nb):
    """cost[lo][hi] = weighted SSE of one segment about its own mean, on centred values."""
    cost = [[0.0] * (nb + 1) for _ in range(nb + 1)]
    for lo in range(nb):
        wsum = vsum = v2sum = 0.0
        by_bin = {i: (v, w) for i, v, w in rows}
        for hi in range(lo + 1, nb + 1):
            got = by_bin.get(hi - 1)
            if got:
                v, w = got
                wsum += w; vsum += v * w; v2sum += v * v * w
            cost[lo][hi] = v2sum - (vsum * vsum / wsum if wsum else 0.0)
    return cost


def best_edges(data, nb, k):
    """The k edges minimising total weighted SSE across spacing bands. Exact, by DP."""
    total = [[0.0] * (nb + 1) for _ in range(nb + 1)]
    for rows in data.values():
        c = seg_cost(rows, nb)
        for lo in range(nb):
            for hi in range(lo + 1, nb + 1):
                total[lo][hi] += c[lo][hi]

    best = [[math.inf] * (nb + 1) for _ in range(k + 2)]
    back = [[0] * (nb + 1) for _ in range(k + 2)]
    for j in range(1, nb + 1):
        best[1][j] = total[0][j]
    for pieces in range(2, k + 2):
        for j in range(pieces, nb + 1):
            for i in range(pieces - 1, j):
                cand = best[pieces - 1][i] + total[i][j]
                if cand < best[pieces][j]:
                    best[pieces][j] = cand
                    back[pieces][j] = i
    edges, j = [], nb
    for pieces in range(k + 1, 1, -1):
        j = back[pieces][j]
        edges.append(j)
    return tuple(sorted(edges))


def shapes(data, edges, nb):
    """Per spacing band, the segment deviations from that band's mean."""
    out = {}
    cuts = [0] + list(edges) + [nb]
    for name, rows in data.items():
        rows_c, _ = centred(rows)
        for lo, hi in zip(cuts, cuts[1:]):
            seg = [(v, w) for i, v, w in rows_c if lo <= i < hi]
            if seg:
                wt = sum(w for _, w in seg)
                out[(name, lo)] = sum(v * w for v, w in seg) / wt
    return out


def score(train, test, edges, nb):
    """Variance in the angle direction that the train-fitted shape explains on the test."""
    shape = shapes(train, edges, nb)
    cuts = [0] + list(edges) + [nb]
    num = den = 0.0
    for name, rows in test.items():
        rows_c, _ = centred(rows)          # the test set's own level, removed
        for lo, hi in zip(cuts, cuts[1:]):
            for i, v, w in rows_c:
                if lo <= i < hi:
                    delta = shape.get((name, lo), 0.0)
                    num += w * (v - delta) ** 2
                    den += w * v * v
    return 1 - num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="build/anglebands.json")
    ap.add_argument("--max-edges", type=int, default=5)
    args = ap.parse_args()

    blob = json.load(open(os.path.join(ROOT, args.file)))
    width, nb = blob["width"], int(180 / blob["width"])
    halves = blob["halves"]
    bands = [b for b in blob["behave"] if sum(s[2] for s in blob["behave"][b]) > 50000]

    print(f"source {blob.get('source','?')}, {width:g}-degree bins, "
          f"spacing bands {', '.join(bands)}")
    print("held-out = shape fitted on one half of the maps, scored on the other, both ways\n")

    for metric, label in (("miss", "miss rate"), ("aim", "aim error")):
        data = [curves(halves[h], metric, bands) for h in (0, 1)]
        if not all(data):
            print(f"{label}: not enough data\n")
            continue

        print(f"{label}")
        print(f"{'bands':>6}{'edges chosen (each direction)':>40}{'held-out':>10}")
        for k in range(1, args.max_edges + 1):
            got, chosen = [], []
            for tr, te in ((0, 1), (1, 0)):
                e = best_edges(data[tr], nb, k)
                got.append(score(data[tr], data[te], e, nb))
                chosen.append("/".join(f"{x*width:.0f}" for x in e))
            print(f"{k+1:>6}{'   '.join(chosen):>40}{sum(got)/2:>9.1%}")

        print(f"\n{'':>6}{'fixed candidates':>40}{'held-out':>10}")
        for name, deg in CANDIDATES.items():
            e = tuple(int(d / width) for d in deg)
            got = [score(data[tr], data[te], e, nb) for tr, te in ((0, 1), (1, 0))]
            print(f"{len(deg)+1:>6}{'  '.join(str(d) for d in deg):>40}"
                  f"{sum(got)/2:>9.1%}   {name}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
