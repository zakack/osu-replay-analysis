"""Where should the turn axis be cut? Asked of map content and of play, over the whole corpus.

Band edges are the kind of decision that is made once, baked into a schema version, and then
never revisited because nobody remembers it was a choice. The current 30-degree grid is
arbitrary; ppy's 40 and 140 are a published model's knees but ppy does not bin at all, it
ramps. So this asks two separate questions and reports them side by side:

* **Composition.** How often does each turn magnitude occur, across every beatmap in the
  corpus rather than a handful? One replay per beatmap, because a map played two hundred
  times is still one map and weighting by plays measures the player's habits instead.
* **Behaviour.** Where does performance actually change with the turn? Aim error and miss
  rate, not hit error -- a turn is an aiming demand, and `unitsize.py` already showed hit
  error is mostly indifferent to geometry. A band edge is only worth having where one of
  these bends.

Two confounds are handled rather than hoped away. Aim error rises steeply with spacing, so
the behaviour curves are computed *within* spacing bands and never pooled across them.
And a turn needs two real legs: through a stack the cursor barely moves, the angle is a
ratio of two tiny numbers, and it piles up at the straight-through end for no reason to do
with how maps are designed. The composition histogram is reported with and without that
floor, because the difference between them is the size of the artefact.

    PYTHONPATH=tools python3 tools/analysis/anglebands.py
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEG = 180.0 / math.pi

# Both legs must be a real movement before the turn between them means anything.
STACK_FLOOR = 0.35      # circle radii

# Coarse, and chosen to be uncontroversial: the question here is the angle axis, and these
# only exist to stop spacing masquerading as it.
SPACING_BANDS = ((0.0, 2.0, "under 2"), (2.0, 4.0, "2 to 4"),
                 (4.0, 6.0, "4 to 6"), (6.0, 1e9, "over 6"))


def band(spacing):
    for lo, hi, name in SPACING_BANDS:
        if lo <= spacing < hi:
            return name
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--width", type=float, default=5.0, help="histogram bin, degrees")
    parser.add_argument("--out", default="build/anglebands.json")
    parser.add_argument("--source", default="player",
                        choices=("player", "reference", "collected"),
                        help="whose play the behaviour curves come from")
    parser.add_argument("--archive", default="both",
                        choices=("both", "dailies", "firehose"),
                        help="collected source only; the two have very different shapes")
    parser.add_argument("--max-acc", type=float, default=1.1,
                        help="collected source only: keep runs at or below this accuracy")
    parser.add_argument("--min-acc", type=float, default=0.0,
                        help="collected source only: keep runs at or above this accuracy")
    parser.add_argument("--player", default="zaksynack",
                        help="player source only; the corpus holds other people's imports")
    parser.add_argument("--quiet", action="store_true", help="write the JSON, print little")
    parser.add_argument("--split", default="beatmap", choices=("beatmap", "player"),
                        help="what the two halves are split on. Splitting on the beatmap "
                             "asks whether a shape survives unseen maps; splitting on the "
                             "player, with the same maps both sides, asks whether it is "
                             "this population's or just these people's")
    args = parser.parse_args()

    # Behaviour is accumulated in two halves split on the beatmap, so band edges can be
    # chosen on one half and scored on the other. Edges fitted and judged on the same maps
    # are edges fitted to this corpus's noise, and the whole point of the exercise is that
    # they are about to be frozen into a schema version.
    def half(key):
        return int(hashlib.blake2b(str(key).encode(), digest_size=8).hexdigest(), 16) & 1

    which = {"player": "corpus.json", "reference": "corpus-nm.json",
             "collected": "corpus-collected.json"}[args.source]
    corpus = json.load(open(os.path.join(ROOT, which)) if False
                       else open(os.path.join(ROOT, "build", which)))

    if args.source == "player":
        keep = [r for r in corpus if r.get("Paired")
                and args.player in os.path.basename(r["Path"])]
    elif args.source == "collected":
        keep = [r for r in corpus
                if (args.archive == "both" or r.get("Source") == args.archive)
                and args.min_acc <= (r.get("Accuracy") or 0) <= args.max_acc]
    else:
        keep = list(corpus)

    one_per_map = {}
    side = {}
    for record in keep:
        name = os.path.basename(record["Path"])
        one_per_map.setdefault(record["BeatmapMd5"], name)
        if args.split == "player":
            who = record.get("UserId")
            if who is None:
                continue
            side[name] = half(who)
        else:
            side[name] = half(record["BeatmapMd5"])
    representative = set(one_per_map.values())

    nb = int(round(180.0 / args.width))

    comp_all = [0] * nb          # every object
    comp_real = [0] * nb         # both legs a real movement
    # behaviour: per half, per spacing band, per angle bin -> [n aim, sum aim, n, misses]
    behave = [{name: [[0, 0.0, 0, 0] for _ in range(nb)] for _, _, name in SPACING_BANDS}
              for _ in (0, 1)]

    rows = 0
    geometry = {"player": "geometry.csv",
                "reference": "geometry-reference-nm.csv",
                "collected": "geometry-collected.csv"}[args.source]
    with open(os.path.join(ROOT, "build", geometry)) as f:
        for r in csv.DictReader(f):
            raw = r["signedAngle"]
            if not raw or r["result"] == "Unjudged":
                continue
            where = side.get(r["replay"])
            if where is None:
                continue
            spacing = r["spacingRadii"]
            if not spacing:
                continue
            spacing = float(spacing)
            d = abs(float(raw)) * DEG
            i = min(nb - 1, int(d / args.width))
            rows += 1

            if r["replay"] in representative:
                comp_all[i] += 1
                if spacing >= STACK_FLOOR:
                    comp_real[i] += 1

            if spacing < STACK_FLOOR:
                continue
            name = band(spacing)
            if name is None:
                continue
            slot = behave[where][name][i]
            slot[2] += 1
            if r["result"] == "Miss":
                slot[3] += 1
            aim = r["aimErrorRadii"]
            if aim:
                slot[0] += 1
                slot[1] += float(aim)

    pooled = {name: [[a + b for a, b in zip(x, y)]
                     for x, y in zip(behave[0][name], behave[1][name])]
              for _, _, name in SPACING_BANDS}
    json.dump({"width": args.width, "source": args.source,
               "compAll": comp_all, "compReal": comp_real,
               "behave": pooled, "halves": behave},
              open(os.path.join(ROOT, args.out), "w"))

    print(f"{rows:,} judged objects, {len(representative):,} beatmaps, "
          f"{len(keep):,} replays, source {args.source}"
          + (f"/{args.archive}" if args.source == "collected" else "") + "\n")
    if args.quiet:
        print(f"written: {args.out}")
        return 0

    peak = max(comp_real) or 1
    print(f"composition, one replay per beatmap   ({sum(comp_real):,} objects "
          f"with both legs over {STACK_FLOOR} radii; "
          f"{sum(comp_all) - sum(comp_real):,} dropped as stacks)")
    print(f"{'deg':>8}{'all':>9}{'real':>9}  distribution of real turns")
    for i in range(nb):
        lo = i * args.width
        print(f"{lo:>4.0f}-{lo+args.width:<3.0f}{comp_all[i]:>9,}{comp_real[i]:>9,}  "
              + "#" * round(comp_real[i] / peak * 40))

    for _, _, name in SPACING_BANDS:
        table = pooled[name]
        if sum(s[2] for s in table) < 5000:
            continue
        print(f"\nbehaviour, spacing {name} radii")
        print(f"{'deg':>8}{'n':>9}{'aim err':>10}{'miss %':>9}")
        for i in range(nb):
            n_aim, sum_aim, n_all, miss = table[i]
            if n_all < 300:
                continue
            lo = i * args.width
            print(f"{lo:>4.0f}-{lo+args.width:<3.0f}{n_all:>9,}"
                  f"{sum_aim/max(n_aim,1):>10.3f}{miss/n_all*100:>8.2f}%")

    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
