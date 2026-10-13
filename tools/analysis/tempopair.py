"""Does the two hands' coupling survive a rate mod? The same 40 beatmaps, nomod and sped up.

The open question from the coupling work: `arrival.py --coupling` found the two hands
correlated in both corpora and the *better* players less so, flat across tempo bands. But
every band there was a different set of beatmaps played by a different set of players, so
"tempo" and "who plays maps at that tempo" were the same axis. This file removes that.

`tools/reference/fetch.py --mods DT,HD` pulled the rate-mod boards of 40 beatmaps that the
nomod reference corpus already covers. Same 40 maps both sides, so the geometry is identical
object for object, and only the real-time tempo differs.

Glossary, one word per concept, as `streamfail.py` does:

    button error  ms, pressTime - startTime. Signed, negative is the button early.
    cursor error  ms, closestTime - startTime: when the cursor was deepest inside the circle.
                  Never "arrival" -- that is `enterTime`, a different column.
    rho           correlation between the two errors within a group. 0 is two hands each
                  independently locked to the music; 1 is one movement carrying both.
    condition     "nomod" or "rate" -- the same maps at 1.0x and under DT/NC.
    floor         an accuracy floor applied to both sides, to match play quality.

**Why no rate correction appears anywhere here.** Replay frame times are beatmap time, not
real time: lazer's rate mods drive the track, and `Rhythm.cs` reads `BeatLength` straight off
the beatmap's control points, so a 1/4 snap on a 190 BPM map is 79 ms in both corpora. The
beatmap-time demand is identical and the real-time demand is the mod's rate times harder.
rho is scale-free on top of that, so even a freely-set rate multiplier -- which the charter
warns never to assume is 1.5, and which the manifest does not record -- cannot move it. What
a missing rate *would* corrupt is a BPM label or any sigma in ms, so neither is a result
here; the sigma columns are printed for scale only and are not comparable across conditions.

**Two confounds, controlled rather than argued away.**

- *Population.* The rate boards are not the same players as the nomod boards: 818 against
  3,568, overlapping in 133. `--paired` keeps only those 133 and pairs within player, which
  costs most of the sample and answers the question the pooled table cannot.
- *Play quality.* This is the sharp one, and it runs the same way as the hypothesis. The
  nomod side is almost all S and X, median accuracy 0.998; the rate side runs down to D,
  median 0.962, because a nomod top-50 board is near-perfect plays and a DT board is
  whoever cleared it. Comparing those pooled would read a difference in sloppiness as a
  difference in tempo. `--floor` applies an accuracy floor to both sides and `--sweep`
  moves it, because one floor is a coincidence and a monotone trend across floors is not --
  the same discipline the pattern work needed when a single training floor inverted an answer.
"""
import argparse
import collections
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (geometry, clicks, rhythm, manifest, manifest filter) per condition.
CONDITIONS = {
    "nomod": ("build/geometry-reference-nm.csv", "build/clicks-reference-nm.csv",
              "build/rhythm-reference-nm.csv", "build/reference/manifest.json", "NM"),
    "rate": ("build/geometry-reference-dt.csv", "build/clicks-reference-dt.csv",
             "build/rhythm-reference-dt.csv", "build/reference-dt/manifest.json", None),
}
# Snap and spacing, named the way the schema names them. A class is only a class if both
# conditions populate it, which `report` enforces rather than assumes.
SNAPS = ((0.25, "1/4"), (0.5, "1/2"), (1.0, "1/1"))
SPACINGS = ((0.0, 2.0, "0-2 radii"), (2.0, 1e9, "2+ radii"))
ALL = "every circle"
WINDOW = 10_000.0
LEAST = 5


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def replays_of(manifest_path, keep_filter, maps, floor):
    """Which replay files of a corpus to read: on the shared maps, above the accuracy floor.

    Returns file name -> (user_id, beatmap_md5, accuracy). The nomod corpus holds every
    reference pull, so it is filtered to the no-mod subset the way `extend_nm.sh` does:
    manifest filter NM *and* an empty mod list.
    """
    out = {}
    for v in json.load(open(os.path.join(ROOT, manifest_path))).values():
        if keep_filter is not None and (v.get("filter") != keep_filter or v["mods"]):
            continue
        if maps is not None and v["beatmap_md5"] not in maps:
            continue
        if floor is not None and (v["accuracy"] or 0.0) < floor:
            continue
        out[v["file"]] = (v["user_id"], v["beatmap_md5"], v["accuracy"])
    return out


def classify(geometry, rhythm, allowed):
    """Key every circle of the allowed replays to its (snap, spacing) class.

    Two passes, because spacing is in the geometry table and snap is in the rhythm one.
    Keyed to a tenth of a millisecond, which is how `rush.py` and `arrival.py` join these.
    A circle whose snap the rhythm pass did not cover is dropped, so a missing snap cannot
    quietly land in a class.
    """
    spacing = {}
    with open(os.path.join(ROOT, geometry)) as f:
        for r in csv.DictReader(f):
            if r["replay"] not in allowed or r["kind"] != "circle" or r["exitSlackRadii"]:
                continue
            value = _num(r["spacingRadii"])
            if value is None:
                continue
            spacing[(sys.intern(r["replay"]), round(float(r["startTime"]), 1))] = value

    classes = {}
    with open(os.path.join(ROOT, rhythm)) as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["clickIndex"] == "-1":
                continue
            key = (r["replay"], round(float(r["time"]), 1))
            value = spacing.get(key)
            if value is None:
                continue
            snap = _num(r["snap"])
            if snap is None:
                continue
            name = next((n for s, n in SNAPS if abs(snap - s) < 1e-6), None)
            if name is None:
                continue
            band = next(n for lo, hi, n in SPACINGS if lo <= value < hi)
            classes[(sys.intern(key[0]), key[1])] = f"{name}, {band}"
    return classes


def accumulate(clicks, classes, allowed):
    """Six sufficient statistics per (class, replay, section), for every centring level.

    Everything the report needs derives from this one accumulator: pooled sums the raw
    moments and centres once, within-replay centres each replay, within-section each section.
    """
    acc = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0])
    with open(os.path.join(ROOT, clicks)) as f:
        for r in csv.DictReader(f):
            name = r["replay"]
            if name not in allowed or r["pressInside"] != "1":
                continue
            start = _num(r["startTime"])
            press, deep = _num(r["pressTime"]), _num(r["closestTime"])
            if start is None or press is None or deep is None:
                continue
            got = classes.get((name, round(start, 1)))
            if got is None:
                continue
            x, y = press - start, deep - start
            section = int(start // WINDOW)
            for label in (got, ALL):
                c = acc[(label, name, section)]
                c[0] += 1
                c[1] += x
                c[2] += y
                c[3] += x * x
                c[4] += y * y
                c[5] += x * y
    return acc


def correlate(acc, label, level, pick=None):
    """Pooled within-group rho at one centring level, from each group's own centred moments.

    `level` is how much is removed first. "pooled" centres once over everything and so still
    carries whatever displaces *both* hands together -- a mistimed section, a map's own
    offset, the audio path. Those are common causes, not bilateral coordination, and they
    inflate rho with none involved, which is why the controlled columns are the other two.
    """
    groups = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for (name, replay, section), c in acc.items():
        if name != label or (pick is not None and replay not in pick):
            continue
        key = {"pooled": 0, "within replay": replay,
               "within section": (replay, section)}[level]
        g = groups[key]
        for i in range(6):
            g[i] += c[i]
    sxx = syy = sxy = 0.0
    n = 0
    for g in groups.values():
        if g[0] < LEAST:
            continue
        sxx += g[3] - g[1] * g[1] / g[0]
        syy += g[4] - g[2] * g[2] / g[0]
        sxy += g[5] - g[1] * g[2] / g[0]
        n += g[0]
    if n < LEAST or sxx <= 0 or syy <= 0:
        return None, 0, 0.0, 0.0
    return sxy / (sxx * syy) ** 0.5, n, (sxx / n) ** 0.5, (syy / n) ** 0.5


def load(condition, maps, floor):
    geometry, clicks, rhythm, manifest, keep = CONDITIONS[condition]
    allowed = replays_of(manifest, keep, maps, floor)
    classes = classify(geometry, rhythm, allowed)
    return allowed, accumulate(clicks, classes, allowed)


def shared_maps():
    """The beatmaps the rate corpus covers, which is what both sides are restricted to."""
    _, _, _, manifest, _ = CONDITIONS["rate"]
    return {v["beatmap_md5"] for v in
            json.load(open(os.path.join(ROOT, manifest))).values()}


def labels(accs):
    """Classes populated in every condition, in schema order, with `every circle` last."""
    order = [f"{n}, {b}" for _, n in SNAPS for _, _, b in SPACINGS] + [ALL]
    present = [set(k[0] for k in acc) for acc in accs]
    return [c for c in order if all(c in p for p in present)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--floor", type=float, default=0.98,
                        help="accuracy floor applied to both conditions (default 0.98, "
                             "which matches the rate side's quality to the nomod side's)")
    parser.add_argument("--sweep", action="store_true",
                        help="move the accuracy floor instead of fixing it; one floor is a "
                             "coincidence and a monotone trend across floors is not")
    parser.add_argument("--paired", action="store_true",
                        help="keep only players present in both corpora and pair within "
                             "player, which controls the population outright")
    parser.add_argument("--by-accuracy", action="store_true",
                        help="rho against accuracy inside the rate corpus alone, to measure "
                             "how much of any gap play quality could account for")
    parser.add_argument("--level", default="within replay",
                        choices=("pooled", "within replay", "within section"))
    args = parser.parse_args()

    maps = shared_maps()
    print(f"the {len(maps)} beatmaps the rate corpus covers, both conditions restricted "
          f"to them\n", flush=True)

    if args.sweep:
        floors = (0.95, 0.97, 0.98, 0.99)
        print("rho by accuracy floor. A gap that holds as the floor rises is a tempo "
              "effect;\none that closes was play quality.\n")
        print(f"  {'floor':<7}{'class':<18}"
              + "".join(f"{c:>12}" for c in ("nomod n", "nomod", "rate n", "rate", "gap")))
        for floor in floors:
            loaded = [load(c, maps, floor) for c in ("nomod", "rate")]
            for label in labels([a for _, a in loaded]):
                cells = [correlate(a, label, args.level) for _, a in loaded]
                if any(c[0] is None for c in cells):
                    continue
                print(f"  {floor:<7.2f}{label:<18}"
                      f"{cells[0][1]:>12,}{cells[0][0]:>12.2f}"
                      f"{cells[1][1]:>12,}{cells[1][0]:>12.2f}"
                      f"{cells[1][0] - cells[0][0]:>12.2f}")
            print()
        return 0

    floor = args.floor
    loaded = [load(c, maps, floor) for c in ("nomod", "rate")]
    for condition, (allowed, _) in zip(("nomod", "rate"), loaded):
        print(f"  {condition:<6} {len(allowed):,} replays, "
              f"{len({u for u, _, _ in allowed.values()}):,} players, accuracy >= {floor}")
    print()

    if args.by_accuracy:
        _, acc = loaded[1]
        allowed = loaded[1][0]
        bands = ((0.0, 0.93, "under .93"), (0.93, 0.96, ".93-.96"),
                 (0.96, 0.98, ".96-.98"), (0.98, 1.01, ".98+"))
        print("Inside the rate corpus only: rho against how clean the play was. If rho "
              "tracks\naccuracy here, then a nomod-to-rate gap measured at one floor is "
              "partly quality.\n")
        print(f"  {'accuracy':<12}{'replays':>9}" + "".join(f"{c:>18}" for c in
                                                            labels([acc])))
        for lo, hi, name in bands:
            pick = {f for f, (_, _, a) in allowed.items() if lo <= (a or 0) < hi}
            if not pick:
                continue
            line = f"  {name:<12}{len(pick):>9,}"
            for label in labels([acc]):
                rho, n, _, _ = correlate(acc, label, args.level, pick)
                line += f"{rho:>18.2f}" if rho is not None else f"{'-':>18}"
            print(line)
        return 0

    if args.paired:
        people = [collections.defaultdict(set) for _ in loaded]
        for (allowed, _), by in zip(loaded, people):
            for name, (user, _, _) in allowed.items():
                by[user].add(name)
        both = set(people[0]) & set(people[1])
        print(f"{len(both):,} players with replays in both corpora at this floor. Each "
              f"player's rho is\nmeasured in each condition and the pair differenced, so "
              f"nothing here compares one\nplayer to another.\n")
        print(f"  {'class':<18}{'players':>9}{'nomod':>9}{'rate':>9}{'gap':>9}"
              f"{'rate lower':>12}")
        for label in labels([a for _, a in loaded]):
            gaps = []
            for user in sorted(both):
                pair = [correlate(acc, label, args.level, people[i][user])
                        for i, (_, acc) in enumerate(loaded)]
                if any(p[0] is None for p in pair):
                    continue
                gaps.append((pair[0][0], pair[1][0]))
            if len(gaps) < 10:
                continue
            nomod = sum(a for a, _ in gaps) / len(gaps)
            rate = sum(b for _, b in gaps) / len(gaps)
            lower = sum(1 for a, b in gaps if b < a) / len(gaps)
            print(f"  {label:<18}{len(gaps):>9,}{nomod:>9.2f}{rate:>9.2f}"
                  f"{rate - nomod:>9.2f}{lower:>11.0%}")
        return 0

    print("Correlation between the two hands' errors against the beat, on the same maps at "
          "two\ntempos. sigma is in beatmap-time ms and is printed for scale only -- the "
          "conditions\nmeasure it in different real-time units, so only rho compares.\n")
    print(f"  {'class':<18}" + "".join(f"{c:>11}" for c in
                                       ("nomod n", "sb", "sc", "rho",
                                        "rate n", "sb", "sc", "rho", "gap")))
    for label in labels([a for _, a in loaded]):
        cells = [correlate(a, label, args.level) for _, a in loaded]
        if any(c[0] is None for c in cells):
            continue
        line = f"  {label:<18}"
        for rho, n, sb, sc in cells:
            line += f"{n:>11,}{sb:>11.1f}{sc:>11.1f}{rho:>11.2f}"
        line += f"{cells[1][0] - cells[0][0]:>11.2f}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
