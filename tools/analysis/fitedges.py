"""Choose the turn axis's band edges, and prove the choice survives maps it was not fitted to.

`anglebands.py` accumulates the behaviour curves in two halves split on the beatmap. This
fits piecewise-constant edges on one half and scores them on the other, both ways round,
so an edge set is judged on maps that had no say in choosing it. In-sample fitting always
prefers more edges; held-out fitting is what says when to stop.

Edges are shared across spacing bands because the schema needs one angle axis, while each
spacing band keeps its own level inside each band -- spacing sets the overall difficulty and
only the shape in angle is being located.

Scored on miss rate and aim error, never hit error. A turn is an aiming demand.

    PYTHONPATH=tools python3 tools/analysis/fitedges.py
    PYTHONPATH=tools python3 tools/analysis/fitedges.py --file build/anglebands-ref.json
"""
from __future__ import annotations

import argparse
import itertools
import json
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


def rows(table, metric, width):
    out = []
    for i, (n_aim, sum_aim, n_all, miss) in enumerate(table):
        if metric == "miss":
            if n_all >= MIN_N:
                out.append((i, miss / n_all, n_all))
        elif n_aim >= MIN_N:
            out.append((i, sum_aim / n_aim, n_aim))
    return out


def sse(data, edges, nb, levels=None):
    """Weighted SSE. With `levels`, score against levels fitted elsewhere; else fit here."""
    total = 0.0
    cuts = [0] + list(edges) + [nb]
    for name, rs in data.items():
        for lo, hi in zip(cuts, cuts[1:]):
            seg = [(v, w) for i, v, w in rs if lo <= i < hi]
            if not seg:
                continue
            if levels is None:
                wt = sum(w for _, w in seg)
                mu = sum(v * w for v, w in seg) / wt
            else:
                mu = levels.get((name, lo))
                if mu is None:
                    continue
            total += sum(w * (v - mu) ** 2 for v, w in seg)
    return total


def fit_levels(data, edges, nb):
    cuts = [0] + list(edges) + [nb]
    out = {}
    for name, rs in data.items():
        for lo, hi in zip(cuts, cuts[1:]):
            seg = [(v, w) for i, v, w in rs if lo <= i < hi]
            if seg:
                wt = sum(w for _, w in seg)
                out[(name, lo)] = sum(v * w for v, w in seg) / wt
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="build/anglebands.json")
    ap.add_argument("--max-edges", type=int, default=5)
    args = ap.parse_args()

    blob = json.load(open(os.path.join(ROOT, args.file)))
    width, nb = blob["width"], int(180 / blob["width"])
    halves = blob["halves"]
    keep = [b for b in blob["behave"] if sum(s[2] for s in blob["behave"][b]) > 50000]

    print(f"source {blob.get('source','?')}, {width:g}-degree bins, "
          f"spacing bands {', '.join(keep)}\n")

    for metric, label in (("miss", "miss rate"), ("aim", "aim error")):
        data = [{b: rows(halves[h][b], metric, width) for b in keep} for h in (0, 1)]
        base = [sse(data[h], (), nb) for h in (0, 1)]

        print(f"{label}: searched edges, fitted on one half of the maps and scored on the "
              f"other")
        print(f"{'bands':>6}{'edges chosen (each direction)':>42}{'held-out':>10}")
        for k in range(1, args.max_edges + 1):
            got, chosen = [], []
            for train, test in ((0, 1), (1, 0)):
                best = min(itertools.combinations(range(1, nb), k),
                           key=lambda e: sse(data[train], e, nb))
                levels = fit_levels(data[train], best, nb)
                got.append(1 - sse(data[test], best, nb, levels) / base[test])
                chosen.append("/".join(f"{e*width:.0f}" for e in best))
            print(f"{k+1:>6}{'   '.join(chosen):>42}{sum(got)/2:>9.1%}")

        print(f"\n{'':>6}{'fixed candidates':>42}{'held-out':>10}")
        for name, deg in CANDIDATES.items():
            e = tuple(int(d / width) for d in deg)
            got = []
            for train, test in ((0, 1), (1, 0)):
                levels = fit_levels(data[train], e, nb)
                got.append(1 - sse(data[test], e, nb, levels) / base[test])
            print(f"{len(deg)+1:>6}{'  '.join(str(d) for d in deg):>42}"
                  f"{sum(got)/2:>9.1%}   {name}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
