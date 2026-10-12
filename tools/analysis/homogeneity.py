"""Does the schema 4 cut group turns into more self-similar shapes than schema 3 did?

The held-out score that chose the band edges measured *prediction of error*. This asks a
different question, the one a contact sheet raises: are the instances the atlas draws
inside one cell more alike than they used to be?

Two traps make the obvious version of this worthless.

* **Within-cell variance always falls as cells are added.** Schema 3 had 1,188 cells in
  angle-spacing-snap and schema 4 has 495, so raw dispersion flatters schema 3 for free.
  The fix is the same as everywhere else here: fit the cell means on one half of the
  beatmaps and score them on the other. An extra cell that is only slicing noise then
  costs rather than pays, and partitions of different granularity become comparable.
* **Binning a dimension trivially tightens that dimension.** Comparing the spread of the
  turn, or of the exit radius, would measure the partition against its own definition.

So the target is the **entry radius** -- the length of the leg arriving at the apex, in
circle radii. It is half the shape the atlas draws and no scheme bins it; atlas.py says so
in as many words ("the entry radius is unconstrained"). A cell whose members agree about
the leg nobody binned is a cell that caught a real shape rather than a coordinate box.

Chaining comes from atlas.links, so the turns here are exactly the turns the pictures draw,
including its rejections for spinner gaps, zero-length legs and clamped delta times.

    PYTHONPATH=tools python3 tools/analysis/homogeneity.py
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "viewer"))

from bins import load                      # noqa: E402
from bins.schema import SPACING, SNAP      # noqa: E402
import atlas                               # noqa: E402

# The two angle axes being compared, as edges. Schema 3's is written out rather than read
# from anywhere, because it no longer exists in the schema and the point is to compare.
GRID_3 = tuple(float(d) for d in range(-180, 181, 30))
BANDS_4 = (0.0, 5.0, 50.0, 135.0, 160.0, 180.0)


def band(value, edges):
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return f"{edges[i]:g}:{edges[i+1]:g}"
    return f"{edges[-2]:g}:{edges[-1]:g}" if value == edges[-1] else None


def half(md5):
    return int(hashlib.blake2b(md5.encode(), digest_size=8).hexdigest(), 16) & 1


def score(groups):
    """Out-of-sample R^2 predicting entry radius from the other half's cell mean.

    Both directions, pooled. A cell absent from the training half falls back to its grand
    mean, which is what an unseen cell would honestly offer.
    """
    grand = [[0, 0.0], [0, 0.0]]
    for halves in groups.values():
        for h in (0, 1):
            grand[h][0] += halves[h][0]
            grand[h][1] += halves[h][1]

    num = den = 0.0
    for train, test in ((0, 1), (1, 0)):
        base = grand[train][1] / grand[train][0]
        mu_test = grand[test][1] / grand[test][0]
        for halves in groups.values():
            n_tr, s_tr = halves[train][:2]
            n_te, s_te, q_te = halves[test]
            if n_te == 0:
                continue
            pred = s_tr / n_tr if n_tr >= 20 else base
            num += q_te - 2 * pred * s_te + n_te * pred * pred
            den += q_te - 2 * mu_test * s_te + n_te * mu_test * mu_test
    return 1 - num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", default="zaksynack")
    args = ap.parse_args()

    drops: collections.Counter = collections.Counter()
    chained = atlas.links(load.PLAYER, drops, players={args.player},
                          note=lambda m: print(m, file=sys.stderr))

    schemes = {
        "schema 3  signed 30-degree grid": lambda o: band(o.angle, GRID_3),
        "schema 4  |turn| 5 50 135 160":   lambda o: band(abs(o.angle), BANDS_4),
    }
    groups = {k: collections.defaultdict(lambda: [[0, 0.0, 0.0], [0, 0.0, 0.0]])
              for k in schemes}
    raw = {k: collections.defaultdict(lambda: [0, 0.0, 0.0]) for k in schemes}

    used = 0
    for link in chained:
        o = link.obs
        if o.target != "circle" or o.from_slider != "0":
            continue
        sp, sn = SPACING.bin(o.spacing), SNAP.bin(o.snap)
        if sp is None or sn is None or o.angle is None:
            continue
        h = half(o.beatmap_md5)
        e = link.entry
        used += 1
        for name, keyer in schemes.items():
            a = keyer(o)
            if a is None:
                continue
            slot = groups[name][(a, sp, sn)][h]
            slot[0] += 1; slot[1] += e; slot[2] += e * e
            r = raw[name][(a, sp, sn)]
            r[0] += 1; r[1] += e; r[2] += e * e

    print(f"\n{used:,} chained circle turns, entry radius as the target "
          f"(no scheme bins it)\n")
    print(f"{'partition':<34}{'cells':>7}{'used':>7}{'within-cell SD':>16}{'held-out R^2':>14}")
    for name in schemes:
        cells_with = [v for v in raw[name].values() if v[0] >= 20]
        n = sum(v[0] for v in cells_with)
        within = math.sqrt(sum(v[2] - v[1] * v[1] / v[0] for v in cells_with) / n)
        print(f"{name:<34}{len(raw[name]):>7}{len(cells_with):>7}{within:>16.4f}"
              f"{score(groups[name]):>13.1%}")
    print("\nWithin-cell SD favours whichever partition has more cells and is shown only "
          "for context;\nthe held-out column is the comparable one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
