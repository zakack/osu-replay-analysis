"""Pick one set of band edges by optimising held-out score directly, across every corpus.

`fitedges.py` scores a handful of candidate edge sets that somebody thought of. This
searches all of them, exactly, and answers the question those candidates were standing in
for: should aim error and miss rate share an angle axis, or get one each?

The objective is the mean held-out R^2 over every configuration available -- each corpus,
each direction of its train/test split, each metric -- and R^2 is a sum over segments
divided by a constant, so the mean is itself a sum over segments. That makes the best
partition reachable by dynamic programming rather than by trying C(35,5) = 324,632 sets:
an optimal partition of the first j bins is built from an optimal partition of the first i.

Every configuration is normalised by its own denominator before being summed, so a corpus
with ten times the objects does not simply outvote the others.

Three optima are reported -- one axis serving both metrics, one fitted to misses alone, one
to aim alone -- and the cost of sharing is the difference between them. That number is the
whole answer: if it is small, one axis keeps the cell a single thing that every metric can
be read against, which is what the charter's groupby and the viewer and the clips all
assume. If it is large, the metrics genuinely want different cells and that assumption has
to be paid for somewhere.

    PYTHONPATH=tools python3 tools/analysis/jointedges.py \
        --file build/anglebands.json byMap \
        --file build/anglebands-dailies-byplayer.json byPlayer \
        --file build/anglebands-firehose.json firehoseByMap
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIN_N = 2000


def curves(half, metric, bands):
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
    wt = sum(w for _, _, w in rows)
    mu = sum(v * w for _, v, w in rows) / wt
    return [(i, v - mu, w) for i, v, w in rows]


def config_cost(train, test, nb):
    """cost[lo][hi] for one (train, test) pair, and the denominator it is scored against.

    The train half supplies a segment's deviation from its band mean; the test half supplies
    the values that deviation is judged on. Both are centred first, so only the shape is
    being asked to carry over -- the level belongs to whichever set of maps or players it
    was measured on.
    """
    cost = [[0.0] * (nb + 1) for _ in range(nb + 1)]
    den = 0.0
    for band in test:
        if band not in train:
            continue
        tr = {i: (v, w) for i, v, w in centred(train[band])}
        te = {i: (v, w) for i, v, w in centred(test[band])}
        den += sum(w * v * v for v, w in te.values())
        for lo in range(nb):
            twsum = tvsum = 0.0
            ewsum = evsum = ev2sum = 0.0
            for hi in range(lo + 1, nb + 1):
                i = hi - 1
                if i in tr:
                    v, w = tr[i]; twsum += w; tvsum += v * w
                if i in te:
                    v, w = te[i]; ewsum += w; evsum += v * w; ev2sum += v * v * w
                delta = tvsum / twsum if twsum else 0.0
                # sum w (v - delta)^2 over the test rows in [lo, hi), ACCUMULATED over the
                # spacing bands. Assigning here instead silently keeps only the last band's
                # error against every band's denominator, which reads as a far better model
                # than it is -- 94% where the truth was 66%.
                cost[lo][hi] += ev2sum - 2 * delta * evsum + delta * delta * ewsum
    return cost, den


def best(costs, nb, k):
    """The k edges minimising the summed cost. Exact, by DP."""
    total = [[0.0] * (nb + 1) for _ in range(nb + 1)]
    for cost, den in costs:
        for lo in range(nb):
            for hi in range(lo + 1, nb + 1):
                total[lo][hi] += cost[lo][hi] / den
    dp = [[math.inf] * (nb + 1) for _ in range(k + 2)]
    back = [[0] * (nb + 1) for _ in range(k + 2)]
    for j in range(1, nb + 1):
        dp[1][j] = total[0][j]
    for pieces in range(2, k + 2):
        for j in range(pieces, nb + 1):
            for i in range(pieces - 1, j):
                c = dp[pieces - 1][i] + total[i][j]
                if c < dp[pieces][j]:
                    dp[pieces][j] = c
                    back[pieces][j] = i
    edges, j = [], nb
    for pieces in range(k + 1, 1, -1):
        j = back[pieces][j]
        edges.append(j)
    return tuple(sorted(edges))


def evaluate(costs, edges, nb):
    out = []
    for cost, den in costs:
        cuts = [0] + list(edges) + [nb]
        num = sum(cost[lo][hi] for lo, hi in zip(cuts, cuts[1:]))
        out.append(1 - num / den)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", nargs=2, action="append", metavar=("PATH", "LABEL"),
                    required=True)
    ap.add_argument("--edges", type=int, default=5)
    ap.add_argument("--also", action="append", default=[],
                    help="a fixed set to report alongside, e.g. 5,50,65,125,160")
    args = ap.parse_args()

    configs = []        # (label, metric, cost, den)
    width = nb = None
    for path, label in args.file:
        blob = json.load(open(os.path.join(ROOT, path)))
        width = blob["width"]; nb = int(180 / width)
        bands = [b for b in blob["behave"] if sum(s[2] for s in blob["behave"][b]) > 50000]
        for metric in ("miss", "aim"):
            data = [curves(blob["halves"][h], metric, bands) for h in (0, 1)]
            if not all(data):
                continue
            for tr, te in ((0, 1), (1, 0)):
                cost, den = config_cost(data[tr], data[te], nb)
                if den > 0:
                    configs.append((label, metric, cost, den))

    def pick(which):
        chosen = [(c, d) for lab, m, c, d in configs if which in (m, "both")]
        return best(chosen, nb, args.edges)

    print(f"{len(configs)} held-out configurations "
          f"({len({l for l,_,_,_ in configs})} corpora x 2 directions x 2 metrics)\n")

    results = {}
    for name, which in (("one axis, both metrics", "both"),
                        ("fitted to misses only", "miss"),
                        ("fitted to aim only", "aim")):
        edges = pick(which)
        results[name] = edges
        print(f"{name:<24}{'  '.join(f'{e*width:.0f}' for e in edges)}")
    for extra in args.also:
        edges = tuple(int(float(d) / width) for d in extra.split(","))
        results[f"fixed {extra}"] = edges

    print(f"\n{'edge set':<24}", end="")
    labels = sorted({l for l, _, _, _ in configs})
    for metric in ("miss", "aim"):
        for lab in labels:
            print(f"{(lab[:9] + '/' + metric):>17}", end="")
    print(f"{'mean':>8}")

    for name, edges in results.items():
        print(f"{name:<24}", end="")
        allscores = []
        for metric in ("miss", "aim"):
            for lab in labels:
                sel = [(c, d) for l, m, c, d in configs if l == lab and m == metric]
                if not sel:
                    print(f"{'--':>17}", end=""); continue
                vals = evaluate(sel, edges, nb)
                allscores += vals
                print(f"{sum(vals)/len(vals):>16.1%} ", end="")
        print(f"{sum(allscores)/len(allscores):>7.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
